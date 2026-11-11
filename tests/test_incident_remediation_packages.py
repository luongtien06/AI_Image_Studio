# -*- coding: utf-8 -*-
"""
Tests for Incident Remediation Packages 1 & 2:
- Package 1: Pixel Limit Consistency across Scanner, Upload, and Inference Worker (150M).
- Package 2: Orphan-Safe Startup Recovery & Source File Verification.

All tests run in temporary directories with isolated temporary SQLite databases.
No GPU inference is performed.
"""

import os
import sys
import uuid
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from PIL import Image

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from job_manager import (
    JobManager,
    STATUS_QUEUED,
    STATUS_VALIDATING,
    STATUS_PROCESSING,
    STATUS_RETRY_WAIT,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_CANCEL_REQUESTED,
    STATUS_CANCELLED,
    NON_RETRYABLE_ERRORS
)
import app as flask_app


class TestIncidentRemediationPackages(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)
        self.db_path = self.base_path / "test_jobs.db"
        self.job_mgr = JobManager(db_path=self.db_path)

        # Create a sample valid image for testing
        self.valid_img_path = self.base_path / "sample_valid.png"
        img = Image.new("RGB", (100, 100), color="blue")
        img.save(self.valid_img_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    # ═══════════════════════════════════════════════════════════════
    # Package 2: Orphan-Safe Startup Recovery Cases
    # ═══════════════════════════════════════════════════════════════

    def test_queued_existing_source_eligible(self):
        """1. QUEUED + existing source -> eligible for queue."""
        jid = str(uuid.uuid4())
        self.job_mgr.add_job(
            job_id=jid,
            filename="sample_valid.png",
            input_path=str(self.valid_img_path),
            dimensions="100x100",
            file_size=self.valid_img_path.stat().st_size
        )
        report = self.job_mgr.recover_on_startup()
        self.assertIn(jid, report["eligible_job_ids"])
        job = self.job_mgr.get_job(jid)
        self.assertEqual(job["status"], STATUS_QUEUED)

    def test_queued_missing_source_not_eligible(self):
        """2. QUEUED + missing source -> not eligible, marked FAILED."""
        jid = str(uuid.uuid4())
        missing_path = self.base_path / "deleted_image.png"
        self.job_mgr.add_job(
            job_id=jid,
            filename="deleted_image.png",
            input_path=str(missing_path),
            dimensions="100x100",
            file_size=1024
        )
        report = self.job_mgr.recover_on_startup()
        self.assertNotIn(jid, report["eligible_job_ids"])
        job = self.job_mgr.get_job(jid)
        self.assertEqual(job["status"], STATUS_FAILED)
        self.assertIn("SOURCE_FILE_MISSING", job["error_message"])

    def test_retry_wait_missing_source_not_eligible(self):
        """3. RETRY_WAIT + missing source -> not eligible, marked FAILED."""
        jid = str(uuid.uuid4())
        missing_path = self.base_path / "retry_missing.png"
        self.job_mgr.add_job(
            job_id=jid,
            filename="retry_missing.png",
            input_path=str(missing_path),
            dimensions="100x100",
            file_size=1024
        )
        # Put into RETRY_WAIT
        self.job_mgr.transition_status(jid, STATUS_PROCESSING)
        self.job_mgr.transition_status(jid, STATUS_RETRY_WAIT, error_message="Transient GPU blip")

        report = self.job_mgr.recover_on_startup()
        self.assertNotIn(jid, report["eligible_job_ids"])
        job = self.job_mgr.get_job(jid)
        self.assertEqual(job["status"], STATUS_FAILED)
        self.assertIn("SOURCE_FILE_MISSING", job["error_message"])

    def test_source_deleted_after_enqueue_blocks_inference(self):
        """4. Source deleted after enqueue but before inference -> inference blocked atomically."""
        temp_img = self.base_path / "to_be_deleted.png"
        Image.new("RGB", (64, 64), color="red").save(temp_img)
        jid = str(uuid.uuid4())

        # Mock inference worker to ensure it is never called
        mock_worker = MagicMock()
        mock_worker.upscale_image.side_effect = AssertionError("Inference must not be called for missing source!")

        with patch.object(flask_app, 'job_mgr', self.job_mgr), \
             patch.object(flask_app, 'get_ai_worker', return_value=mock_worker):

            self.job_mgr.add_job(
                job_id=jid,
                filename="to_be_deleted.png",
                input_path=str(temp_img),
                dimensions="64x64",
                file_size=temp_img.stat().st_size
            )

            job_dict = {
                'id': jid,
                'filename': "to_be_deleted.png",
                'input_path': str(temp_img),
                'tile_size': 128
            }
            with flask_app.jobs_lock:
                flask_app.jobs[jid] = {
                    'id': jid,
                    'filename': "to_be_deleted.png",
                    'input_path': str(temp_img),
                    'status': 'pending',
                    'raw_status': STATUS_QUEUED
                }

            # Delete file before worker processes it
            temp_img.unlink()

            result = flask_app.process_single_image(job_dict)
            self.assertFalse(result)
            mock_worker.upscale_image.assert_not_called()

            db_job = self.job_mgr.get_job(jid)
            self.assertEqual(db_job["status"], STATUS_FAILED)
            self.assertIn("SOURCE_FILE_MISSING", db_job["error_message"])

    def test_source_path_points_to_directory_blocks_inference(self):
        """5. Source path points to directory -> inference blocked."""
        dir_path = self.base_path / "not_a_file_dir"
        dir_path.mkdir(exist_ok=True)
        jid = str(uuid.uuid4())

        mock_worker = MagicMock()
        mock_worker.upscale_image.side_effect = AssertionError("Inference must not be called on directory!")

        with patch.object(flask_app, 'job_mgr', self.job_mgr), \
             patch.object(flask_app, 'get_ai_worker', return_value=mock_worker):

            self.job_mgr.add_job(
                job_id=jid,
                filename="dir_job",
                input_path=str(dir_path),
                dimensions="0x0",
                file_size=0
            )

            job_dict = {
                'id': jid,
                'filename': "dir_job",
                'input_path': str(dir_path),
                'tile_size': 128
            }
            with flask_app.jobs_lock:
                flask_app.jobs[jid] = {
                    'id': jid,
                    'filename': "dir_job",
                    'input_path': str(dir_path),
                    'status': 'pending',
                    'raw_status': STATUS_QUEUED
                }

            result = flask_app.process_single_image(job_dict)
            self.assertFalse(result)
            mock_worker.upscale_image.assert_not_called()

            db_job = self.job_mgr.get_job(jid)
            self.assertEqual(db_job["status"], STATUS_FAILED)
            self.assertIn("SOURCE_FILE_MISSING", db_job["error_message"])

    def test_completed_missing_source_remains_completed(self):
        """6. COMPLETED + missing source -> remains COMPLETED."""
        jid = str(uuid.uuid4())
        missing_path = self.base_path / "already_done_deleted.png"
        out_path = self.base_path / "output_done.png"
        Image.new("RGB", (200, 200), color="green").save(out_path)

        self.job_mgr.add_job(
            job_id=jid,
            filename="already_done_deleted.png",
            input_path=str(missing_path),
            dimensions="100x100",
            file_size=1024
        )
        self.job_mgr.transition_status(jid, STATUS_PROCESSING)
        self.job_mgr.transition_status(jid, STATUS_COMPLETED, output_path=str(out_path))

        report = self.job_mgr.recover_on_startup()
        self.assertNotIn(jid, report["eligible_job_ids"])
        job = self.job_mgr.get_job(jid)
        self.assertEqual(job["status"], STATUS_COMPLETED)

    def test_cancelled_missing_source_remains_cancelled(self):
        """7. CANCELLED + missing source -> remains CANCELLED."""
        jid = str(uuid.uuid4())
        missing_path = self.base_path / "cancelled_missing.png"
        self.job_mgr.add_job(
            job_id=jid,
            filename="cancelled_missing.png",
            input_path=str(missing_path),
            dimensions="100x100",
            file_size=1024
        )
        self.job_mgr.transition_status(jid, STATUS_CANCELLED)

        report = self.job_mgr.recover_on_startup()
        self.assertNotIn(jid, report["eligible_job_ids"])
        job = self.job_mgr.get_job(jid)
        self.assertEqual(job["status"], STATUS_CANCELLED)

    def test_cancel_requested_not_resurrected(self):
        """8. CANCEL_REQUESTED -> must not be resurrected, transitions to CANCELLED."""
        jid = str(uuid.uuid4())
        self.job_mgr.add_job(
            job_id=jid,
            filename="cancel_req.png",
            input_path=str(self.valid_img_path),
            dimensions="100x100",
            file_size=self.valid_img_path.stat().st_size
        )
        self.job_mgr.transition_status(jid, STATUS_PROCESSING)
        self.job_mgr.transition_status(jid, STATUS_CANCEL_REQUESTED)

        report = self.job_mgr.recover_on_startup()
        self.assertNotIn(jid, report["eligible_job_ids"])
        job = self.job_mgr.get_job(jid)
        self.assertEqual(job["status"], STATUS_CANCELLED)

    def test_recovery_idempotence(self):
        """Idempotency: Multiple calls to recover_on_startup produce consistent results."""
        jid1 = str(uuid.uuid4())
        jid2 = str(uuid.uuid4())
        missing_path = self.base_path / "idemp_missing.png"

        self.job_mgr.add_job(jid1, "valid.png", str(self.valid_img_path), "100x100", 100)
        self.job_mgr.add_job(jid2, "missing.png", str(missing_path), "100x100", 100)

        # First run
        r1 = self.job_mgr.recover_on_startup()
        self.assertEqual(r1["eligible_job_ids"], [jid1])
        self.assertEqual(self.job_mgr.get_job(jid2)["status"], STATUS_FAILED)

        # Second run immediately after
        r2 = self.job_mgr.recover_on_startup()
        self.assertEqual(r2["eligible_job_ids"], [jid1])
        self.assertEqual(self.job_mgr.get_job(jid2)["status"], STATUS_FAILED)

    # ═══════════════════════════════════════════════════════════════
    # Package 1: Pixel Limit Consistency Cases (150M)
    # ═══════════════════════════════════════════════════════════════

    def test_configured_150m_limit_consistently_applied(self):
        """9. Configured 150M limit is consistently applied at every validation layer."""
        self.assertEqual(flask_app.UPLOAD_MAX_PIXELS, 150000000)
        self.assertEqual(flask_app.MAX_PIXELS, 150000000)

        client = flask_app.app.test_client()
        cfg_resp = client.get('/api/config')
        self.assertEqual(cfg_resp.status_code, 200)
        cfg_data = cfg_resp.get_json()
        self.assertEqual(cfg_data["security"]["max_pixels"], 150000000)
        self.assertEqual(cfg_data["security"]["upload_max_pixels"], 150000000)

    def test_images_at_25m_27m_111m_produce_consistent_decisions(self):
        """10. Images at 25M, 27.16M and 111.88M pixels pass; >150M rejects with exact error format."""
        # 1. 25,000,000 pixels (5000 x 5000)
        # Mock Image.open to avoid huge RAM allocations
        def make_mock_img(width, height, fmt="PNG"):
            m = MagicMock()
            m.format = fmt
            m.size = (width, height)
            m.verify.return_value = None
            m.load.return_value = None
            m.getexif.return_value = None
            m.__enter__.return_value = m
            m.__exit__.return_value = None
            return m

        # 25,000,000 pixels (5000x5000) -> Allowed
        with patch("PIL.Image.open", return_value=make_mock_img(5000, 5000)):
            res = flask_app.validate_image_file(self.valid_img_path)
            self.assertTrue(res.is_valid, f"25M px should be allowed: {res.reason}")

        # 27,161,600 pixels (4244x6400) -> Allowed under 150M
        with patch("PIL.Image.open", return_value=make_mock_img(4244, 6400)):
            res = flask_app.validate_image_file(self.valid_img_path)
            self.assertTrue(res.is_valid, f"27.16M px should be allowed: {res.reason}")

        # 111,882,240 pixels (11520x9712) -> Allowed under 150M
        with patch("PIL.Image.open", return_value=make_mock_img(11520, 9712)):
            res = flask_app.validate_image_file(self.valid_img_path)
            self.assertTrue(res.is_valid, f"111.88M px should be allowed: {res.reason}")

        # 156,250,000 pixels (12500x12500) -> Exceeds 150M limit, must be rejected
        with patch("PIL.Image.open", return_value=make_mock_img(12500, 12500)):
            res = flask_app.validate_image_file(self.valid_img_path)
            self.assertFalse(res.is_valid)
            self.assertEqual(res.category, "too_large")
            self.assertIn("Image exceeds maximum pixel limit: 12500x12500 (156,250,000 pixels, max allowed: 150,000,000)", res.reason)

    def test_retry_count_does_not_increase_indefinitely_for_missing_source(self):
        """11. Retry count does not increase indefinitely for missing-source jobs."""
        missing_path = self.base_path / "never_retry.png"
        jid = str(uuid.uuid4())
        self.job_mgr.add_job(
            job_id=jid,
            filename="never_retry.png",
            input_path=str(missing_path),
            dimensions="100x100",
            file_size=1024
        )

        job_before = self.job_mgr.get_job(jid)
        self.assertEqual(job_before["retry_count"], 0)

        # Attempt recovery
        self.job_mgr.recover_on_startup()
        job_after = self.job_mgr.get_job(jid)
        self.assertEqual(job_after["status"], STATUS_FAILED)
        self.assertEqual(job_after["retry_count"], 0)

        # Attempt recovery second time
        self.job_mgr.recover_on_startup()
        job_after2 = self.job_mgr.get_job(jid)
        self.assertEqual(job_after2["status"], STATUS_FAILED)
        self.assertEqual(job_after2["retry_count"], 0)


if __name__ == "__main__":
    unittest.main()
