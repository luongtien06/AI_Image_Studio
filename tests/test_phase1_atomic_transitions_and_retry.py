# -*- coding: utf-8 -*-
"""
Unit and regression tests for Phase 1: Atomic SQLite state transitions & retry scheduling.
Uses isolated temporary SQLite databases to guarantee zero impact on production data.
"""

import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from typing import List

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
    VALID_TRANSITIONS,
)


class TestAtomicStateTransitionsAndRetry(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_phase1_atomic_")
        self.db_path = Path(self.temp_dir) / "test_jobs.db"
        self.mgr = JobManager(db_path=self.db_path)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_atomic_state_transition_valid(self):
        """Test standard state machine transitions work correctly."""
        jid = "job_valid_1"
        self.mgr.add_job(jid, "img.png", "/tmp/img.png")

        # QUEUED -> VALIDATING
        self.assertTrue(self.mgr.transition_status(jid, STATUS_VALIDATING))
        self.assertEqual(self.mgr.get_job(jid)["status"], STATUS_VALIDATING)

        # VALIDATING -> PROCESSING
        self.assertTrue(self.mgr.transition_status(jid, STATUS_PROCESSING))
        self.assertEqual(self.mgr.get_job(jid)["status"], STATUS_PROCESSING)
        self.assertIsNotNone(self.mgr.get_job(jid)["started_at"])

        # PROCESSING -> COMPLETED
        self.assertTrue(self.mgr.transition_status(jid, STATUS_COMPLETED, output_path="/tmp/out.png"))
        j = self.mgr.get_job(jid)
        self.assertEqual(j["status"], STATUS_COMPLETED)
        self.assertEqual(j["progress"], 100)
        self.assertIsNotNone(j["completed_at"])

    def test_02_illegal_transition_rejected_atomically(self):
        """Illegal transitions according to VALID_TRANSITIONS must return False and not modify state."""
        jid = "job_illegal_1"
        self.mgr.add_job(jid, "img.png", "/tmp/img.png")

        # QUEUED -> COMPLETED is illegal
        self.assertFalse(self.mgr.transition_status(jid, STATUS_COMPLETED))
        self.assertEqual(self.mgr.get_job(jid)["status"], STATUS_QUEUED)

        # Terminal state COMPLETED cannot transition to anything
        self.mgr.transition_status(jid, STATUS_PROCESSING)
        self.mgr.transition_status(jid, STATUS_COMPLETED)
        self.assertFalse(self.mgr.transition_status(jid, STATUS_PROCESSING))
        self.assertFalse(self.mgr.transition_status(jid, STATUS_QUEUED))
        self.assertEqual(self.mgr.get_job(jid)["status"], STATUS_COMPLETED)

    def test_03_concurrent_state_transitions_race_safety(self):
        """
        Multiple concurrent threads attempting contradictory transitions on the same job.
        Atomic conditional updates ensure only valid transitions succeed and no corrupted state occurs.
        """
        jid = "job_concurrent_1"
        self.mgr.add_job(jid, "img.png", "/tmp/img.png")
        self.mgr.transition_status(jid, STATUS_PROCESSING)

        results = []
        lock = threading.Lock()

        def try_complete():
            res = self.mgr.transition_status(jid, STATUS_COMPLETED, output_path="/tmp/out.png")
            with lock:
                results.append(("complete", res))

        def try_cancel():
            res = self.mgr.cancel_job(jid)
            with lock:
                results.append(("cancel", res))

        def try_fail():
            res = self.mgr.transition_status(jid, STATUS_FAILED, error_message="Simulated error")
            with lock:
                results.append(("fail", res))

        threads = [
            threading.Thread(target=try_complete),
            threading.Thread(target=try_cancel),
            threading.Thread(target=try_fail)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        final_job = self.mgr.get_job(jid)
        self.assertIn(final_job["status"], (STATUS_COMPLETED, STATUS_CANCEL_REQUESTED, STATUS_CANCELLED, STATUS_FAILED))

    def test_04_schedule_retry_atomic_increment_and_limit(self):
        """
        schedule_retry must atomically increment retry_count up to max_retries,
        then permanently fail on exceeding max_retries.
        """
        jid = "job_retry_limit_1"
        self.mgr.add_job(jid, "img.png", "/tmp/img.png", max_retries=3)
        self.mgr.transition_status(jid, STATUS_PROCESSING)

        # Attempt 1: retry 1
        should_retry, count, reason = self.mgr.schedule_retry(jid, "Transient CUDA error 1")
        self.assertTrue(should_retry)
        self.assertEqual(count, 1)
        self.assertEqual(self.mgr.get_job(jid)["status"], STATUS_RETRY_WAIT)

        # Move to processing for attempt 2
        self.mgr.transition_status(jid, STATUS_PROCESSING)
        should_retry, count, reason = self.mgr.schedule_retry(jid, "Transient CUDA error 2")
        self.assertTrue(should_retry)
        self.assertEqual(count, 2)

        # Move to processing for attempt 3
        self.mgr.transition_status(jid, STATUS_PROCESSING)
        should_retry, count, reason = self.mgr.schedule_retry(jid, "Transient CUDA error 3")
        self.assertTrue(should_retry)
        self.assertEqual(count, 3)

        # Move to processing for attempt 4 (exceeds max_retries=3)
        self.mgr.transition_status(jid, STATUS_PROCESSING)
        should_retry, count, reason = self.mgr.schedule_retry(jid, "Fatal CUDA error")
        self.assertFalse(should_retry)
        self.assertEqual(count, 3)
        final_job = self.mgr.get_job(jid)
        self.assertEqual(final_job["status"], STATUS_FAILED)
        self.assertIn("Max retries 3 exceeded", final_job["error_message"])

    def test_05_schedule_retry_does_not_overwrite_cancellation(self):
        """
        If a job is CANCEL_REQUESTED or CANCELLED, schedule_retry must NOT
        overwrite the status with RETRY_WAIT, and must NOT increment retry_count.
        """
        jid = "job_cancel_race_1"
        self.mgr.add_job(jid, "img.png", "/tmp/img.png", max_retries=3)
        self.mgr.transition_status(jid, STATUS_PROCESSING)

        # User requests cancellation
        self.mgr.cancel_job(jid)
        self.assertEqual(self.mgr.get_job(jid)["status"], STATUS_CANCEL_REQUESTED)

        # Worker finishes failed attempt and tries to schedule retry
        should_retry, count, reason = self.mgr.schedule_retry(jid, "OOM error")
        self.assertFalse(should_retry)
        self.assertEqual(count, 0)  # retry_count NOT incremented
        self.assertIn("cancelled", reason.lower())

        # Status must remain CANCEL_REQUESTED
        job_after = self.mgr.get_job(jid)
        self.assertEqual(job_after["status"], STATUS_CANCEL_REQUESTED)
        self.assertEqual(job_after["retry_count"], 0)

    def test_06_non_retryable_error_fails_immediately(self):
        """Non-retryable error keywords must transition to FAILED without scheduling retries."""
        jid = "job_non_retryable_1"
        self.mgr.add_job(jid, "corrupt.png", "/tmp/corrupt.png", max_retries=3)
        self.mgr.transition_status(jid, STATUS_VALIDATING)

        should_retry, count, reason = self.mgr.schedule_retry(jid, "Corrupted container: UnidentifiedImageError")
        self.assertFalse(should_retry)
        self.assertEqual(count, 0)
        self.assertEqual(self.mgr.get_job(jid)["status"], STATUS_FAILED)
        self.assertIn("Non-retryable", reason)

    def test_07_force_cannot_overwrite_cancelled_or_completed_with_processing(self):
        """Even with force=True, transition_status to PROCESSING/RETRY_WAIT cannot overwrite CANCELLED or COMPLETED."""
        jid1 = "job_term_1"
        self.mgr.add_job(jid1, "done.png", "/tmp/done.png")
        self.mgr.transition_status(jid1, STATUS_PROCESSING)
        self.mgr.transition_status(jid1, STATUS_COMPLETED)

        # Force transition to PROCESSING on completed job must fail
        self.assertFalse(self.mgr.transition_status(jid1, STATUS_PROCESSING, force=True))
        self.assertEqual(self.mgr.get_job(jid1)["status"], STATUS_COMPLETED)

        jid2 = "job_term_2"
        self.mgr.add_job(jid2, "canc.png", "/tmp/canc.png")
        self.mgr.cancel_job(jid2)
        self.assertEqual(self.mgr.get_job(jid2)["status"], STATUS_CANCELLED)

        # Force transition to RETRY_WAIT on cancelled job must fail
        self.assertFalse(self.mgr.transition_status(jid2, STATUS_RETRY_WAIT, force=True))
        self.assertEqual(self.mgr.get_job(jid2)["status"], STATUS_CANCELLED)


if __name__ == "__main__":
    unittest.main()
