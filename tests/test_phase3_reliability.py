# -*- coding: utf-8 -*-
"""
Phase 3 Test & Verification Suite: Reliability, SQLite Job Queue & Crash Recovery
AI Image Studio (RealESRGAN_x4plus / RTX 2050 4GB)

Covers all 24 required Phase 3 verification scenarios:
Group A: SQLite Job Queue & Lifecycle (Scenarios 1, 2, 3, 4, 18, 20)
Group B: Retry Mechanism & Error Resilience (Scenarios 5, 6, 9, 15, 16, 23)
Group C: Cooperative Cancellation & Sequential In-Memory Execution (Scenarios 7, 8, 21, 22)
Group D: Crash Recovery & Data Protection (Scenarios 10, 11, 12, 13, 14, 17, 19, 24)
"""

import json
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

from PIL import Image

from ai_worker import (
    STATE_BUSY,
    STATE_READY,
    get_ai_worker,
    get_unique_output_path,
)
from app import (
    BASE_DIR,
    HISTORY_FILE,
    app,
    load_history,
    safe_path,
    save_history_entry,
    validate_image_file,
)
from job_logger import JobLogger, get_job_logger
from job_manager import (
    STATUS_CANCEL_REQUESTED,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PROCESSING,
    STATUS_QUEUED,
    STATUS_RETRY_WAIT,
    STATUS_VALIDATING,
    JobManager,
    get_job_manager,
)


class TestGroupA_SQLiteQueueLifecycle(unittest.TestCase):
    """Group A: SQLite Job Queue & Lifecycle (Scenarios 1, 2, 3, 4, 18, 20)."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="phase3_test_a_")
        self.db_path = Path(self.temp_dir) / "test_jobs.db"
        self.mgr = JobManager(db_path=self.db_path)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_single_job_creation(self):
        """Scenario 1: Single job creation with QUEUED status and default fields."""
        jid = str(uuid.uuid4())
        job = self.mgr.add_job(
            job_id=jid,
            filename="sample.png",
            input_path=str(Path(self.temp_dir) / "sample.png"),
            dimensions="512x512",
            file_size=10240,
            max_retries=3
        )
        self.assertIsNotNone(job)
        self.assertEqual(job["id"], jid)
        self.assertEqual(job["status"], STATUS_QUEUED)
        self.assertEqual(job["filename"], "sample.png")
        self.assertEqual(job["progress"], 0)
        self.assertEqual(job["retry_count"], 0)
        self.assertEqual(job["max_retries"], 3)
        self.assertIsNone(job["output_path"])
        self.assertIsNone(job["started_at"])
        self.assertIsNone(job["completed_at"])

    def test_02_batch_job_creation(self):
        """Scenario 2: Batch job creation in a single transaction."""
        batch_id = str(uuid.uuid4())
        jobs_data = []
        for i in range(5):
            jobs_data.append({
                "id": str(uuid.uuid4()),
                "batch_id": batch_id,
                "filename": f"batch_{i}.png",
                "input_path": f"/tmp/batch_{i}.png",
                "dimensions": "256x256",
                "file_size": 5000,
                "max_retries": 3
            })

        results = self.mgr.add_jobs_batch(jobs_data)
        self.assertEqual(len(results), 5)
        all_jobs = self.mgr.get_all_jobs()
        self.assertEqual(len(all_jobs), 5)
        for j in all_jobs:
            self.assertEqual(j["batch_id"], batch_id)
            self.assertEqual(j["status"], STATUS_QUEUED)

    def test_03_job_completion_flow(self):
        """Scenario 3: Job completion lifecycle (QUEUED -> PROCESSING -> COMPLETED)."""
        jid = str(uuid.uuid4())
        self.mgr.add_job(job_id=jid, filename="flow.png", input_path="/tmp/flow.png")

        # Step 1: Transition to PROCESSING
        ok_proc = self.mgr.transition_status(jid, STATUS_PROCESSING)
        self.assertTrue(ok_proc)
        j1 = self.mgr.get_job(jid)
        self.assertEqual(j1["status"], STATUS_PROCESSING)
        self.assertIsNotNone(j1["started_at"])

        # Step 2: Update Progress
        self.mgr.update_job_progress(jid, 60, "Processing tiles 3/5")
        j2 = self.mgr.get_job(jid)
        self.assertEqual(j2["progress"], 60)

        # Step 3: Transition to COMPLETED
        ok_comp = self.mgr.transition_status(
            jid,
            STATUS_COMPLETED,
            output_path="/tmp/flow_4x.png",
            output_dimensions="1024x1024",
            output_size=20480,
            elapsed_seconds=1.85,
            tile_used=128
        )
        self.assertTrue(ok_comp)
        j3 = self.mgr.get_job(jid)
        self.assertEqual(j3["status"], STATUS_COMPLETED)
        self.assertEqual(j3["progress"], 100)
        self.assertEqual(j3["output_path"], "/tmp/flow_4x.png")
        self.assertEqual(j3["output_dimensions"], "1024x1024")
        self.assertEqual(j3["output_size"], 20480)
        self.assertEqual(j3["tile_used"], 128)
        self.assertIsNotNone(j3["completed_at"])

    def test_04_job_failure_handling(self):
        """Scenario 4: Non-retryable error transitions directly to FAILED status."""
        jid = str(uuid.uuid4())
        self.mgr.add_job(job_id=jid, filename="corrupt.png", input_path="/tmp/corrupt.png")
        self.mgr.transition_status(jid, STATUS_PROCESSING)

        err_msg = "Invalid image: UnidentifiedImageError (corrupted container)"
        should_retry, retry_count, reason = self.mgr.schedule_retry(jid, err_msg)
        self.assertFalse(should_retry)
        self.assertEqual(retry_count, 0)

        j = self.mgr.get_job(jid)
        self.assertEqual(j["status"], STATUS_FAILED)
        self.assertIn("Invalid image", j["error_message"])

    def test_18_schema_migration_versioning(self):
        """Scenario 18: Schema versioning tracks migrations and ensures consistency."""
        with self.mgr.get_connection() as conn:
            cur = conn.execute("SELECT version, applied_at FROM schema_version")
            rows = cur.fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["version"], 1)
            self.assertIsNotNone(rows[0]["applied_at"])

        # Re-initializing on existing DB preserves version
        mgr2 = JobManager(db_path=self.db_path)
        with mgr2.get_connection() as conn:
            cur = conn.execute("SELECT MAX(version) FROM schema_version")
            self.assertEqual(cur.fetchone()[0], 1)

    def test_20_wal_mode_and_concurrency(self):
        """Scenario 20: SQLite WAL mode permits concurrent threads reading/writing."""
        with self.mgr.get_connection() as conn:
            cur = conn.execute("PRAGMA journal_mode")
            mode = cur.fetchone()[0].lower()
            self.assertEqual(mode, "wal")

        errors = []

        def worker_task(thread_id):
            try:
                for k in range(5):
                    jid = f"t{thread_id}_{k}"
                    self.mgr.add_job(job_id=jid, filename=f"t{thread_id}_{k}.png", input_path="/tmp/file.png")
                    self.mgr.get_job(jid)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker_task, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Concurrent WAL errors encountered: {errors}")
        self.assertEqual(len(self.mgr.get_all_jobs()), 20)


class TestGroupB_RetryAndErrorResilience(unittest.TestCase):
    """Group B: Retry Mechanism & Error Resilience (Scenarios 5, 6, 9, 15, 16, 23)."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="phase3_test_b_")
        self.db_path = Path(self.temp_dir) / "test_jobs.db"
        self.mgr = JobManager(db_path=self.db_path)
        self.logger = JobLogger(log_dir=Path(self.temp_dir) / "logs")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_05_transient_error_retry_flow(self):
        """Scenario 5: Transient error triggers RETRY_WAIT and increments retry_count."""
        jid = str(uuid.uuid4())
        self.mgr.add_job(job_id=jid, filename="transient.png", input_path="/tmp/t.png", max_retries=3)
        self.mgr.transition_status(jid, STATUS_PROCESSING)

        err_msg = "CUDA error: out of memory during inference"
        should_retry, count, reason = self.mgr.schedule_retry(jid, err_msg, is_oom=True)

        self.assertTrue(should_retry)
        self.assertEqual(count, 1)
        j = self.mgr.get_job(jid)
        self.assertEqual(j["status"], STATUS_RETRY_WAIT)
        self.assertEqual(j["retry_count"], 1)
        self.assertTrue(j["is_oom"])

    def test_06_max_retries_exceeded(self):
        """Scenario 6: Max retries (3/3) exceeded transitions job to FAILED."""
        jid = str(uuid.uuid4())
        self.mgr.add_job(job_id=jid, filename="limit.png", input_path="/tmp/l.png", max_retries=3)
        self.mgr.transition_status(jid, STATUS_PROCESSING)

        for attempt in range(1, 4):
            should_retry, count, reason = self.mgr.schedule_retry(jid, f"GPU error attempt {attempt}")
            self.assertTrue(should_retry)
            self.assertEqual(count, attempt)
            self.mgr.transition_status(jid, STATUS_PROCESSING, force=True)

        # 4th failure exceeds max_retries
        should_retry, count, reason = self.mgr.schedule_retry(jid, "Final GPU timeout")
        self.assertFalse(should_retry)
        j = self.mgr.get_job(jid)
        self.assertEqual(j["status"], STATUS_FAILED)
        self.assertIn("Max retries 3 exceeded", j["error_message"])

    def test_09_corrupted_image_does_not_block_queue(self):
        """Scenario 9: Corrupted image fails gracefully without blocking subsequent jobs."""
        corrupt_file = Path(self.temp_dir) / "bad.png"
        corrupt_file.write_bytes(b"\x89PNG\r\n\x1a\ncorrupted_garbage_bytes")

        is_valid, reason, _, _ = validate_image_file(corrupt_file)
        self.assertFalse(is_valid)

        jid1 = "corrupt_job"
        jid2 = "good_job"
        self.mgr.add_job(job_id=jid1, filename="bad.png", input_path=str(corrupt_file))
        self.mgr.add_job(job_id=jid2, filename="good.png", input_path="/tmp/good.png")

        # Job 1 validation fails -> FAILED
        self.mgr.schedule_retry(jid1, f"Invalid image: {reason}")
        self.assertEqual(self.mgr.get_job(jid1)["status"], STATUS_FAILED)

        # Next job can proceed immediately
        next_job = self.mgr.get_next_queued_job()
        self.assertIsNotNone(next_job)
        self.assertEqual(next_job["id"], jid2)

    def test_15_detect_deleted_input_file(self):
        """Scenario 15: Deleted input file before execution is safely detected and rejected."""
        ghost_path = Path(self.temp_dir) / "deleted_file.png"
        is_valid, reason, _, _ = validate_image_file(ghost_path)
        self.assertFalse(is_valid)
        self.assertIn("does not exist", reason.lower())

    def test_16_detect_corrupted_input_file(self):
        """Scenario 16: Truncated PNG is detected and logged in per-job log."""
        trunc_path = Path(self.temp_dir) / "truncated.png"
        # Partial PNG header without IDAT
        trunc_path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x10\x00\x00\x00\x10\x08\x02\x00\x00\x00")

        is_valid, reason, _, _ = validate_image_file(trunc_path)
        self.assertFalse(is_valid)

        jid = "trunc_job"
        self.logger.start_job(jid, "truncated.png", str(trunc_path))
        self.logger.log_error(jid, f"Validation failure: {reason}")

        log_content = self.logger.get_job_log(jid)
        self.assertIn("START", log_content)
        self.assertIn("ERROR", log_content)
        self.assertIn("Validation failure", log_content)

    def test_23_cuda_oom_recovery_integration(self):
        """Scenario 23: Worker catches CUDA OOM and steps down tile with per-job logging."""
        worker = get_ai_worker(base_dir=BASE_DIR)
        next_tile = worker.get_step_down_tile(256)
        self.assertEqual(next_tile, 192)
        next_tile2 = worker.get_step_down_tile(192)
        self.assertEqual(next_tile2, 128)

        jid = "oom_job"
        self.logger.start_job(jid, "high_res.png", "/tmp/high_res.png", tile_size=256)
        self.logger.log_oom_recovery(jid, 256, 192)
        log_content = self.logger.get_job_log(jid)
        self.assertIn("OOM_RECOVERY", log_content)
        self.assertIn("tile 256 -> Stepping down to tile 192", log_content)


class TestGroupC_CancellationAndSequentialExecution(unittest.TestCase):
    """Group C: Cooperative Cancellation & Sequential In-Memory Execution (Scenarios 7, 8, 21, 22)."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="phase3_test_c_")
        self.db_path = Path(self.temp_dir) / "test_jobs.db"
        self.mgr = JobManager(db_path=self.db_path)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_07_cancel_queued_job(self):
        """Scenario 7: Cancelling a QUEUED job immediately sets CANCELLED status."""
        jid = str(uuid.uuid4())
        self.mgr.add_job(job_id=jid, filename="cancel_queued.png", input_path="/tmp/q.png")
        self.assertEqual(self.mgr.get_job(jid)["status"], STATUS_QUEUED)

        ok = self.mgr.cancel_job(jid)
        self.assertTrue(ok)
        j = self.mgr.get_job(jid)
        self.assertEqual(j["status"], STATUS_CANCELLED)
        self.assertIsNotNone(j["cancelled_at"])

    def test_08_cancel_running_job_cooperative(self):
        """Scenario 8: Cancelling a running job sets CANCEL_REQUESTED for cooperative abort."""
        jid = str(uuid.uuid4())
        self.mgr.add_job(job_id=jid, filename="running.png", input_path="/tmp/r.png")
        self.mgr.transition_status(jid, STATUS_PROCESSING)

        # User requests cancellation mid-flight
        self.mgr.cancel_job(jid)
        j = self.mgr.get_job(jid)
        self.assertEqual(j["status"], STATUS_CANCEL_REQUESTED)
        self.assertTrue(self.mgr.is_cancellation_requested(jid))

        # Checkpoint detects requested cancellation and marks CANCELLED
        self.mgr.transition_status(jid, STATUS_CANCELLED, force=True)
        self.assertEqual(self.mgr.get_job(jid)["status"], STATUS_CANCELLED)

    def test_21_sequential_inference_execution(self):
        """Scenario 21: Worker mutex ensures strictly sequential GPU inference across threads."""
        worker = get_ai_worker(base_dir=BASE_DIR)
        self.assertTrue(hasattr(worker, "_lock"))
        with worker._lock:
            # Different thread cannot acquire the lock simultaneously
            other_thread_acquired = []
            def try_acquire():
                acq = worker._lock.acquire(blocking=False)
                if acq:
                    worker._lock.release()
                other_thread_acquired.append(acq)
            t = threading.Thread(target=try_acquire)
            t.start()
            t.join()
            self.assertFalse(other_thread_acquired[0])

    def test_22_quick_preview_mutual_exclusion(self):
        """Scenario 22: Quick Preview returns busy error if worker is currently in STATE_BUSY."""
        worker = get_ai_worker(base_dir=BASE_DIR)
        worker.initialize_model()

        dummy_img = Path(self.temp_dir) / "dummy.png"
        img = Image.new("RGB", (256, 256), color=(50, 100, 150))
        img.save(str(dummy_img))

        original_state = worker.state
        try:
            worker.state = STATE_BUSY
            res = worker.generate_quick_preview(
                input_path=dummy_img,
                thumbnails_dir=Path(self.temp_dir)
            )
            self.assertFalse(res["success"])
            self.assertIn("đang bận", res["error"])
        finally:
            worker.state = original_state


class TestGroupD_CrashRecoveryAndDataProtection(unittest.TestCase):
    """Group D: Crash Recovery & Data Protection (Scenarios 10, 11, 12, 13, 14, 17, 19, 24)."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="phase3_test_d_")
        self.db_path = Path(self.temp_dir) / "test_jobs.db"
        self.mgr = JobManager(db_path=self.db_path)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_10_startup_recovery_queued_jobs(self):
        """Scenario 10: Unprocessed QUEUED jobs remain untouched on startup."""
        jid = str(uuid.uuid4())
        self.mgr.add_job(job_id=jid, filename="stay_queued.png", input_path="/tmp/stay.png")

        report = self.mgr.recover_on_startup()
        self.assertEqual(self.mgr.get_job(jid)["status"], STATUS_QUEUED)
        self.assertEqual(report["recovered_count"], 0)

    def test_11_startup_recovery_processing_jobs(self):
        """Scenario 11: Interrupted PROCESSING job without output is re-queued for retry."""
        jid = str(uuid.uuid4())
        self.mgr.add_job(job_id=jid, filename="crashed.png", input_path="/tmp/crashed.png")
        self.mgr.transition_status(jid, STATUS_PROCESSING)

        report = self.mgr.recover_on_startup()
        self.assertEqual(report["recovered_count"], 1)
        j = self.mgr.get_job(jid)
        self.assertEqual(j["status"], STATUS_QUEUED)
        self.assertIn("Recovered", j["error_message"])

    def test_12_crash_recovery_missing_output(self):
        """Scenario 12: Interrupted job with orphan .tmp file cleans up temp and re-queues."""
        jid = str(uuid.uuid4())
        out_cand = Path(self.temp_dir) / "partial_output_4x.png"
        tmp_cand = Path(self.temp_dir) / "partial_output_4x.png.tmp"
        tmp_cand.write_bytes(b"unfinished partial data")

        self.mgr.add_job(
            job_id=jid,
            filename="partial.png",
            input_path="/tmp/p.png",
            output_path=str(out_cand)
        )
        self.mgr.transition_status(jid, STATUS_PROCESSING)

        self.assertTrue(tmp_cand.is_file())
        report = self.mgr.recover_on_startup()
        self.assertEqual(report["recovered_count"], 1)
        self.assertFalse(tmp_cand.is_file(), "Orphaned .tmp file must be cleaned up on recovery")
        self.assertEqual(self.mgr.get_job(jid)["status"], STATUS_QUEUED)

    def test_13_crash_recovery_valid_complete_output(self):
        """Scenario 13: Interrupted job with completed valid output on disk is marked COMPLETED."""
        jid = str(uuid.uuid4())
        valid_out = Path(self.temp_dir) / "complete_4x.png"

        # Create valid image file
        img = Image.new("RGB", (128, 128), color=(100, 150, 200))
        img.save(str(valid_out))

        self.mgr.add_job(
            job_id=jid,
            filename="complete.png",
            input_path="/tmp/c.png",
            output_path=str(valid_out)
        )
        self.mgr.transition_status(jid, STATUS_PROCESSING)

        report = self.mgr.recover_on_startup(validator_func=validate_image_file)
        self.assertEqual(report["recovered_count"], 1)
        j = self.mgr.get_job(jid)
        self.assertEqual(j["status"], STATUS_COMPLETED)

    def test_14_never_overwrite_valid_output(self):
        """Scenario 14: Collision-free naming never overwrites existing output."""
        out_dir = Path(self.temp_dir) / "output"
        out_dir.mkdir(parents=True, exist_ok=True)

        f1 = out_dir / "target_4x.png"
        f1.write_text("existing content", encoding="utf-8")

        f2 = get_unique_output_path(out_dir, "target", ".png")
        self.assertEqual(f2.name, "target_4x_1.png")
        f2.write_text("second content", encoding="utf-8")

        f3 = get_unique_output_path(out_dir, "target", ".png")
        self.assertEqual(f3.name, "target_4x_2.png")
        self.assertEqual(f1.read_text(encoding="utf-8"), "existing content")

    def test_17_path_traversal_rejection(self):
        """Scenario 17: UNC, device namespaces, and protected paths are rejected."""
        self.assertIsNone(safe_path(r"\\127.0.0.1\share\img.png"))
        self.assertIsNone(safe_path(r"\\.\COM1"))
        self.assertIsNone(safe_path(r"\\?\C:\Windows\System32"))
        self.assertIsNone(safe_path(r"C:\Windows\System32\cmd.exe"))
        self.assertIsNone(safe_path(r"C:\Program Files\App\img.png"))

    def test_19_database_backup_integrity(self):
        """Scenario 19: Database backup snapshot produces valid SQLite copy."""
        jid = str(uuid.uuid4())
        self.mgr.add_job(job_id=jid, filename="backup_test.png", input_path="/tmp/b.png")

        backup_file = self.mgr.backup_database(backup_dir=Path(self.temp_dir) / "backups")
        self.assertTrue(backup_file.is_file())
        self.assertGreater(backup_file.stat().st_size, 0)

        # Verify integrity of backup DB
        conn = sqlite3.connect(str(backup_file))
        cur = conn.cursor()
        cur.execute("PRAGMA integrity_check")
        self.assertEqual(cur.fetchone()[0], "ok")
        cur.execute("SELECT id, filename FROM jobs WHERE id = ?", (jid,))
        row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[1], "backup_test.png")
        conn.close()

    def test_24_backward_compatibility_history_json(self):
        """Scenario 24: History persistence in history.json remains 100% compatible."""
        hist = load_history(resolve_paths=True)
        self.assertIsInstance(hist, list)
        self.assertGreater(len(hist), 0, "Original history records must be preserved")

        # Test writing a history entry atomically
        test_entry = {
            "job_id": str(uuid.uuid4()),
            "filename": "phase3_compat.png",
            "status": "completed",
            "input_path": str(BASE_DIR / "uploads" / "sample.png"),
            "output_path": str(BASE_DIR / "output" / "sample_4x.png"),
            "rel_input_path": "uploads/sample.png",
            "rel_output_path": "output/sample_4x.png",
            "tile_size": 128,
            "start_time": "2026-11-09T00:00:00",
            "end_time": "2026-11-09T00:00:02",
            "elapsed_seconds": 2.0,
            "error": None,
            "input_size": 1000,
            "output_size": 4000,
            "input_dimensions": "100x100",
            "output_dimensions": "400x400"
        }
        # Save entry and verify it is prepended
        save_history_entry(test_entry)
        hist_updated = load_history(resolve_paths=False)
        self.assertEqual(hist_updated[0]["job_id"], test_entry["job_id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
