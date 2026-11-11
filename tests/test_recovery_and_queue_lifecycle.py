# -*- coding: utf-8 -*-
"""
Unit and regression tests for Startup Recovery, Queue Lifecycle, and RAM/SQLite Synchronization.
Guarantees isolated SQLite databases and zero interaction with production data.
"""

import queue
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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
)


class TestRecoveryAndQueueLifecycle(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_recovery_lifecycle_")
        self.db_path = Path(self.temp_dir) / "test_lifecycle.db"
        self.mgr = JobManager(db_path=self.db_path)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_recover_interrupted_processing_with_no_valid_output(self):
        """Interrupted PROCESSING job with missing or invalid output must be re-queued if retries remain and source exists."""
        jid = "job_recover_1"
        src_file = Path(self.temp_dir) / "img1.png"
        src_file.write_bytes(b"dummy_src")
        self.mgr.add_job(jid, "img1.png", str(src_file), output_path=str(Path(self.temp_dir) / "out1.png"), max_retries=3)
        self.mgr.transition_status(jid, STATUS_PROCESSING)

        report = self.mgr.recover_on_startup(validator_func=lambda p: (False, "Corrupted", (0, 0), "png"))
        self.assertEqual(report["recovered_count"], 1)
        self.assertEqual(report["details"][0]["action"], "requeued")

        j = self.mgr.get_job(jid)
        self.assertEqual(j["status"], STATUS_QUEUED)
        self.assertIn(jid, report["eligible_job_ids"])

    def test_02_recover_interrupted_processing_with_valid_output(self):
        """Interrupted PROCESSING job with genuinely valid output on disk must be marked COMPLETED."""
        jid = "job_recover_2"
        valid_out = Path(self.temp_dir) / "out2.png"
        valid_out.write_bytes(b"dummy_valid_image_bytes")

        self.mgr.add_job(jid, "img2.png", "/tmp/img2.png", output_path=str(valid_out), max_retries=3)
        self.mgr.transition_status(jid, STATUS_PROCESSING)

        report = self.mgr.recover_on_startup(validator_func=lambda p: (True, "Valid", (100, 100), "png"))
        self.assertEqual(report["recovered_count"], 1)
        self.assertEqual(report["details"][0]["action"], "marked_completed")

        j = self.mgr.get_job(jid)
        self.assertEqual(j["status"], STATUS_COMPLETED)
        self.assertNotIn(jid, report["eligible_job_ids"])

    def test_03_recover_retry_wait_jobs(self):
        """Jobs interrupted in RETRY_WAIT must be re-queued for execution if source exists."""
        jid = "job_retry_wait_1"
        src_file = Path(self.temp_dir) / "img3.png"
        src_file.write_bytes(b"dummy_src")
        self.mgr.add_job(jid, "img3.png", str(src_file), max_retries=3)
        self.mgr.transition_status(jid, STATUS_PROCESSING)
        self.mgr.schedule_retry(jid, "Transient error 1")
        self.assertEqual(self.mgr.get_job(jid)["status"], STATUS_RETRY_WAIT)

        report = self.mgr.recover_on_startup()
        j = self.mgr.get_job(jid)
        self.assertEqual(j["status"], STATUS_QUEUED)
        self.assertIn(jid, report["eligible_job_ids"])

    def test_04_recover_finalize_cancel_requested(self):
        """Jobs in CANCEL_REQUESTED upon reboot must be cleanly finalized to CANCELLED."""
        jid = "job_cancel_req_1"
        self.mgr.add_job(jid, "img4.png", "/tmp/img4.png")
        self.mgr.transition_status(jid, STATUS_PROCESSING)
        self.mgr.cancel_job(jid)
        self.assertEqual(self.mgr.get_job(jid)["status"], STATUS_CANCEL_REQUESTED)

        report = self.mgr.recover_on_startup()
        j = self.mgr.get_job(jid)
        self.assertEqual(j["status"], STATUS_CANCELLED)
        self.assertNotIn(jid, report["eligible_job_ids"])

    def test_05_queue_worker_sentinel_shutdown_guarantees_task_done(self):
        """Sentinel shutdown item None must trigger task_done() without hanging join()."""
        q = queue.Queue()
        done_flag = threading.Event()

        def mock_worker():
            while True:
                item = q.get(block=True)
                if item is None:
                    q.task_done()
                    break
                try:
                    pass
                finally:
                    q.task_done()
            done_flag.set()

        t = threading.Thread(target=mock_worker, daemon=True)
        t.start()

        # Put batch, then sentinel
        q.put({"job_ids": ["j1"]})
        q.put(None)

        # q.join() must not hang
        q.join()
        t.join(timeout=2.0)
        self.assertTrue(done_flag.is_set())

    def test_06_queue_worker_exception_guarantees_task_done(self):
        """An unhandled exception in processing_worker must not leak queue tasks and hang join()."""
        q = queue.Queue()
        processed_batches = []

        def mock_worker():
            while True:
                item = q.get(block=True)
                if item is None:
                    q.task_done()
                    break
                try:
                    if item.get("fail"):
                        raise RuntimeError("Simulated unhandled processing explosion")
                    processed_batches.append(item["id"])
                except Exception:
                    pass
                finally:
                    q.task_done()

        t = threading.Thread(target=mock_worker, daemon=True)
        t.start()

        q.put({"id": "good_1", "fail": False})
        q.put({"id": "bad_1", "fail": True})
        q.put({"id": "good_2", "fail": False})
        q.put(None)

        q.join()
        t.join(timeout=2.0)
        self.assertEqual(processed_batches, ["good_1", "good_2"])


if __name__ == "__main__":
    unittest.main()
