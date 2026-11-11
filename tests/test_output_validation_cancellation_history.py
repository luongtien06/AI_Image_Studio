# -*- coding: utf-8 -*-
"""
Unit and regression tests for:
- Output validation before COMPLETED
- Cancellation checkpoints and history consistency
- SQLite & RAM synchronization
"""

import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

import app
from app import (
    app as flask_app,
    jobs,
    jobs_lock,
    save_history_entry,
    load_history,
    HISTORY_FILE,
    validate_image_file,
    process_single_image,
    cancel_flag,
    get_job_cancel_event,
)
from job_manager import (
    JobManager,
    STATUS_QUEUED,
    STATUS_PROCESSING,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_CANCELLED,
)


class TestOutputValidationCancellationHistory(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_val_hist_")
        self.db_path = Path(self.temp_dir) / "test_val.db"
        self.mgr = JobManager(db_path=self.db_path)
        self.app_client = flask_app.test_client()

        # Isolate history file to temp directory
        self.hist_file = Path(self.temp_dir) / "history.json"
        self.hist_patcher = patch("app.HISTORY_FILE", self.hist_file)
        self.mgr_patcher = patch("app.job_mgr", self.mgr)
        self.hist_patcher.start()
        self.mgr_patcher.start()

        with jobs_lock:
            jobs.clear()
        cancel_flag.clear()

    def tearDown(self):
        self.hist_patcher.stop()
        self.mgr_patcher.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_valid_output_marked_completed(self):
        """A valid PNG output produced by inference is successfully validated and marked COMPLETED."""
        input_img = Path(self.temp_dir) / "input.png"
        img = Image.new("RGB", (32, 32), color="blue")
        img.save(str(input_img), "PNG")

        output_img = Path(self.temp_dir) / "output.png"
        out = Image.new("RGB", (128, 128), color="blue")
        out.save(str(output_img), "PNG")

        jid = "job_val_png_1"
        job_dict = {
            "id": jid,
            "filename": "input.png",
            "input_path": str(input_img),
            "output_dir": self.temp_dir,
            "status": "pending",
            "dimensions": "32x32"
        }
        with jobs_lock:
            jobs[jid] = dict(job_dict)

        mock_worker = MagicMock()
        mock_worker.process_image.return_value = {
            "output_path": str(output_img),
            "elapsed_seconds": 0.5,
            "tile_used": 128,
            "output_dimensions": "128x128",
            "output_size": output_img.stat().st_size,
            "oom_recovered": False
        }

        with patch("app.get_ai_worker", return_value=mock_worker):
            success = process_single_image(job_dict)

        self.assertTrue(success)
        self.assertEqual(self.mgr.get_job(jid)["status"], STATUS_COMPLETED)
        with jobs_lock:
            self.assertEqual(jobs[jid]["status"], "completed")

    def test_02_zero_byte_output_cannot_become_completed(self):
        """A zero-byte output file produced after inference MUST NOT be marked COMPLETED."""
        input_img = Path(self.temp_dir) / "input.png"
        img = Image.new("RGB", (32, 32), color="red")
        img.save(str(input_img), "PNG")

        zero_out = Path(self.temp_dir) / "zero_out.png"
        zero_out.write_bytes(b"")

        jid = "job_zero_out_1"
        job_dict = {
            "id": jid,
            "filename": "input.png",
            "input_path": str(input_img),
            "output_dir": self.temp_dir,
            "status": "pending"
        }
        with jobs_lock:
            jobs[jid] = dict(job_dict)

        mock_worker = MagicMock()
        mock_worker.process_image.return_value = {
            "output_path": str(zero_out),
            "elapsed_seconds": 0.5,
            "tile_used": 128,
            "output_dimensions": "128x128",
            "output_size": 0,
            "oom_recovered": False
        }

        with patch("app.get_ai_worker", return_value=mock_worker):
            success = process_single_image(job_dict)

        self.assertFalse(success)
        self.assertEqual(self.mgr.get_job(jid)["status"], STATUS_FAILED)
        with jobs_lock:
            self.assertEqual(jobs[jid]["status"], "error")
            self.assertIsNone(jobs[jid].get("output_path"))

    def test_03_corrupt_output_cannot_become_completed(self):
        """Corrupt garbage output bytes produced after inference MUST NOT be marked COMPLETED."""
        input_img = Path(self.temp_dir) / "input.png"
        img = Image.new("RGB", (32, 32), color="green")
        img.save(str(input_img), "PNG")

        corrupt_out = Path(self.temp_dir) / "corrupt_out.png"
        corrupt_out.write_bytes(b"\x89PNG\r\n\x1a\ncorrupt_payload_data_not_valid_image")

        jid = "job_corrupt_out_1"
        job_dict = {
            "id": jid,
            "filename": "input.png",
            "input_path": str(input_img),
            "output_dir": self.temp_dir,
            "status": "pending"
        }
        with jobs_lock:
            jobs[jid] = dict(job_dict)

        mock_worker = MagicMock()
        mock_worker.process_image.return_value = {
            "output_path": str(corrupt_out),
            "elapsed_seconds": 0.5,
            "tile_used": 128,
            "output_dimensions": "128x128",
            "output_size": 100,
            "oom_recovered": False
        }

        with patch("app.get_ai_worker", return_value=mock_worker):
            success = process_single_image(job_dict)

        self.assertFalse(success)
        self.assertEqual(self.mgr.get_job(jid)["status"], STATUS_FAILED)
        with jobs_lock:
            self.assertEqual(jobs[jid]["status"], "error")

    def test_04_api_result_rejects_uncompleted_job(self):
        """Endpoint /api/result/<job_id> must reject non-completed jobs with 404."""
        jid = "11111111-1111-1111-1111-111111111111"
        with jobs_lock:
            jobs[jid] = {
                "id": jid,
                "status": "error",
                "output_path": str(Path(self.temp_dir) / "nonexistent.png")
            }

        res = self.app_client.get(f"/api/result/{jid}")
        self.assertEqual(res.status_code, 404)

    def test_05_cancellation_saves_history_entry(self):
        """Cancellation during execution must call save_history_entry with status 'cancelled'."""
        input_img = Path(self.temp_dir) / "input.png"
        img = Image.new("RGB", (32, 32), color="yellow")
        img.save(str(input_img), "PNG")

        jid = "job_cancel_hist_1"
        job_dict = {
            "id": jid,
            "filename": "input.png",
            "input_path": str(input_img),
            "output_dir": self.temp_dir,
            "status": "pending"
        }
        with jobs_lock:
            jobs[jid] = dict(job_dict)

        # Trigger per-job cancellation before inference starts
        cancel_evt = get_job_cancel_event(jid)
        cancel_evt.set()

        success = process_single_image(job_dict)
        self.assertFalse(success)
        self.assertEqual(self.mgr.get_job(jid)["status"], STATUS_CANCELLED)

        # Verify history file has record
        history = load_history(resolve_paths=False)
        self.assertTrue(any(h.get("job_id") == jid and h.get("status") == "cancelled" for h in history))

    def test_06_history_deduplication(self):
        """Duplicate history entries for the same job and status are prevented."""
        entry = {
            "job_id": "job_dup_1",
            "status": "completed",
            "filename": "test.png",
            "elapsed_seconds": 1.2
        }
        save_history_entry(entry)
        save_history_entry(entry)  # Duplicate save call

        hist = load_history(resolve_paths=False)
        count = sum(1 for h in hist if h.get("job_id") == "job_dup_1")
        self.assertEqual(count, 1)

    def test_07_api_retry_sqlite_sync(self):
        """api_retry only enqueues and updates in-memory status if SQLite reset_for_retry succeeds."""
        # Non-existent job in SQLite
        bad_jid = "non_existent_uuid"
        with jobs_lock:
            jobs[bad_jid] = {"id": bad_jid, "status": "error"}

        res = self.app_client.post("/api/retry", json={"job_ids": [bad_jid]})
        self.assertIn(res.status_code, (200, 409))
        if res.status_code == 200:
            data = res.get_json()
            self.assertEqual(data.get("requeued", 0), 0)
        with jobs_lock:
            self.assertEqual(jobs[bad_jid]["status"], "error")


if __name__ == "__main__":
    unittest.main()
