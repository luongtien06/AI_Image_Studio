# -*- coding: utf-8 -*-
"""
Phase 5.4 Test Suite: Critical & High Priority Bug Fixes
Covers:
- AUDIT53-02: Video Job Concurrency & Atomic Claim
- AUDIT53-01: AI Worker State Recovery & Lifecycle Management
- AUDIT53-03: Video Cancellation Semantics & Subprocess Cleanup
"""

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
from PIL import Image

import torch

from ai_worker import (
    AIWorker,
    STATE_BUSY,
    STATE_ERROR,
    STATE_READY,
    STATE_RECOVERING,
    STATE_STOPPED,
    get_ai_worker,
)
from app import BASE_DIR, app
from video import (
    FFmpegService,
    FrameProcessor,
    VideoJobManager,
    VideoPipeline,
    VideoRecoveryManager,
    get_ffmpeg_service,
    get_video_job_manager,
)
from video.ffmpeg_service import FFmpegCancelledException
from video.video_job_manager import (
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PAUSED,
    STATUS_PROCESSING,
    STATUS_QUEUED,
)
from video.video_pipeline import VideoJobCancelledException


class TestAudit5302VideoConcurrency(unittest.TestCase):
    """Tests for AUDIT53-02: Video Job Concurrency & Atomic Claim."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="phase5_4_conc_"))
        self.db_path = self.temp_dir / "test_jobs.db"
        self.job_mgr = VideoJobManager(db_path=self.db_path)
        self.pipeline = VideoPipeline(base_dir=BASE_DIR, job_manager=self.job_mgr)
        self.client = app.test_client()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_dummy_job(self, status: str = STATUS_QUEUED) -> str:
        jid = str(uuid.uuid4())
        src = self.temp_dir / "test.mp4"
        src.write_bytes(b"dummy")
        ws = self.temp_dir / "ws" / jid
        ws.mkdir(parents=True, exist_ok=True)
        self.job_mgr.create_video_job(
            job_id=jid,
            filename="test.mp4",
            source_path=str(src),
            workspace_dir=str(ws),
            video_metadata={"width": 64, "height": 64, "fps": 10, "total_frames": 5, "duration": 0.5},
            audio_metadata={},
            processing_config={},
            file_size=100
        )
        if status != STATUS_QUEUED:
            self.job_mgr.transition_status(jid, status, force=True)
        return jid

    def test_01_atomic_claim_success_from_queued(self):
        """Job in QUEUED can be atomically claimed exactly once."""
        jid = self._create_dummy_job(STATUS_QUEUED)
        claimed, msg, job = self.job_mgr.claim_job_for_execution(jid, allowed_statuses=(STATUS_QUEUED,))
        self.assertTrue(claimed)
        self.assertEqual(job["status"], STATUS_PROCESSING)

        # Second claim attempt immediately fails
        claimed2, msg2, job2 = self.job_mgr.claim_job_for_execution(jid, allowed_statuses=(STATUS_QUEUED,))
        self.assertFalse(claimed2)
        self.assertEqual(job2["status"], STATUS_PROCESSING)

    def test_02_concurrent_claim_threads_same_job(self):
        """Two concurrent threads claiming the same job: exactly one succeeds."""
        jid = self._create_dummy_job(STATUS_QUEUED)
        results = []
        barrier = threading.Barrier(2)

        def worker():
            barrier.wait()
            claimed, msg, job = self.job_mgr.claim_job_for_execution(jid, allowed_statuses=(STATUS_QUEUED,))
            results.append((claimed, msg))

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        success_count = sum(1 for c, _ in results if c)
        failure_count = sum(1 for c, _ in results if not c)
        self.assertEqual(success_count, 1)
        self.assertEqual(failure_count, 1)

    def test_03_concurrent_resume_same_job(self):
        """Two concurrent resume claims on PAUSED job: exactly one succeeds."""
        jid = self._create_dummy_job(STATUS_PAUSED)
        results = []
        barrier = threading.Barrier(2)

        def worker():
            barrier.wait()
            claimed, msg, job = self.job_mgr.claim_job_for_execution(jid, allowed_statuses=(STATUS_PAUSED,))
            results.append(claimed)

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(False), 1)

    def test_04_concurrent_start_and_resume_same_job(self):
        """Concurrent start (expects QUEUED) and resume (expects PAUSED): only the valid one succeeds."""
        jid = self._create_dummy_job(STATUS_QUEUED)
        results = {}
        barrier = threading.Barrier(2)

        def do_start():
            barrier.wait()
            c, m, _ = self.job_mgr.claim_job_for_execution(jid, allowed_statuses=(STATUS_QUEUED,))
            results["start"] = c

        def do_resume():
            barrier.wait()
            c, m, _ = self.job_mgr.claim_job_for_execution(jid, allowed_statuses=(STATUS_PAUSED,))
            results["resume"] = c

        t1 = threading.Thread(target=do_start)
        t2 = threading.Thread(target=do_resume)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertTrue(results["start"])
        self.assertFalse(results["resume"])

    def test_05_two_different_jobs_can_claim_concurrently(self):
        """Two distinct video jobs can claim execution without mutual blocking."""
        jid1 = self._create_dummy_job(STATUS_QUEUED)
        jid2 = self._create_dummy_job(STATUS_QUEUED)

        c1, _, _ = self.job_mgr.claim_job_for_execution(jid1, allowed_statuses=(STATUS_QUEUED,))
        c2, _, _ = self.job_mgr.claim_job_for_execution(jid2, allowed_statuses=(STATUS_QUEUED,))
        self.assertTrue(c1)
        self.assertTrue(c2)

    def test_06_claim_fails_for_terminal_statuses(self):
        """Cannot claim jobs that are COMPLETED, FAILED, or CANCELLED."""
        for term_st in (STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED):
            jid = self._create_dummy_job(term_st)
            claimed, msg, _ = self.job_mgr.claim_job_for_execution(jid, allowed_statuses=(STATUS_QUEUED, STATUS_PAUSED))
            self.assertFalse(claimed)
            self.assertIn("Cannot start/resume job", msg)

    def test_07_api_start_duplicate_returns_409(self):
        """API /api/video/jobs/<id>/start returns 409 Conflict when already processing."""
        with patch("app.video_job_mgr", self.job_mgr):
            jid = self._create_dummy_job(STATUS_PROCESSING)
            res = self.client.post(f"/api/video/jobs/{jid}/start")
            self.assertEqual(res.status_code, 409)
            data = json.loads(res.data)
            self.assertEqual(data.get("job_status"), STATUS_PROCESSING)

    def test_08_api_start_terminal_returns_400(self):
        """API /api/video/jobs/<id>/start returns 400 Bad Request for COMPLETED job."""
        with patch("app.video_job_mgr", self.job_mgr):
            jid = self._create_dummy_job(STATUS_COMPLETED)
            res = self.client.post(f"/api/video/jobs/{jid}/start")
            self.assertEqual(res.status_code, 400)

    def test_09_pipeline_prevents_duplicate_active_thread(self):
        """VideoPipeline rejects running a second thread for the same job."""
        jid = self._create_dummy_job(STATUS_QUEUED)

        # Manually register an active fake thread
        fake_thread = threading.Thread(target=lambda: time.sleep(0.5))
        fake_thread.start()
        with self.pipeline._lock:
            self.pipeline._active_threads[jid] = fake_thread

        # Attempt to run pipeline for same job
        res = self.pipeline.run_job(jid, output_dir=self.temp_dir)
        self.assertFalse(res)
        fake_thread.join()

    def test_10_release_job_claim_on_failure(self):
        """Release job claim transitions job to specified failure status."""
        jid = self._create_dummy_job(STATUS_QUEUED)
        self.job_mgr.claim_job_for_execution(jid, (STATUS_QUEUED,))
        self.job_mgr.release_job_claim(jid, STATUS_FAILED, error_message="Worker launch failed")
        job = self.job_mgr.get_video_job(jid)
        self.assertEqual(job["status"], STATUS_FAILED)
        self.assertEqual(job["error_message"], "Worker launch failed")

    def test_11_startup_crash_recovery_resets_processing(self):
        """Startup crash recovery resets processing job to PAUSED or QUEUED."""
        jid = self._create_dummy_job(STATUS_PROCESSING)
        rec_mgr = VideoRecoveryManager(self.job_mgr)
        report = rec_mgr.recover_interrupted_jobs()
        self.assertGreaterEqual(report["recovered_count"], 1)
        job = self.job_mgr.get_video_job(jid)
        self.assertIn(job["status"], [STATUS_PAUSED, STATUS_QUEUED])


class TestAudit5301AIWorkerStateRecovery(unittest.TestCase):
    """Tests for AUDIT53-01: AI Worker State Recovery & Lifecycle Management."""

    def setUp(self):
        self.worker = get_ai_worker(base_dir=BASE_DIR)
        # Ensure worker is in clean READY or STOPPED state
        with self.worker._state_lock:
            self.worker._active_tokens.clear()
            self.worker.state = STATE_READY

    def tearDown(self):
        with self.worker._state_lock:
            self.worker._active_tokens.clear()
            self.worker.state = STATE_READY

    def test_01_token_acquisition_and_release(self):
        """Tokens increment and release safely without negative counters."""
        self.assertEqual(self.worker.state, STATE_READY)
        token1 = self.worker.acquire_busy("video", "job1")
        self.assertEqual(self.worker.state, STATE_BUSY)
        self.assertEqual(len(self.worker._active_tokens), 1)

        token2 = self.worker.acquire_busy("image", "job2")
        self.assertEqual(self.worker.state, STATE_BUSY)
        self.assertEqual(len(self.worker._active_tokens), 2)

        # Release token 1; token 2 still active so state remains BUSY
        self.worker.release_busy(token1)
        self.assertEqual(self.worker.state, STATE_BUSY)
        self.assertEqual(len(self.worker._active_tokens), 1)

        # Double release of token 1 is a no-op (no negative count)
        self.worker.release_busy(token1)
        self.assertEqual(len(self.worker._active_tokens), 1)

        # Release token 2; state becomes READY
        self.worker.release_busy(token2)
        self.assertEqual(self.worker.state, STATE_READY)
        self.assertEqual(len(self.worker._active_tokens), 0)

    def test_02_busy_scope_context_manager(self):
        """busy_scope guarantees release even if exception is raised."""
        self.assertEqual(self.worker.state, STATE_READY)
        try:
            with self.worker.busy_scope("test_task", "job123"):
                self.assertEqual(self.worker.state, STATE_BUSY)
                raise ValueError("Simulated task error")
        except ValueError:
            pass

        self.assertEqual(self.worker.state, STATE_READY)
        self.assertEqual(len(self.worker._active_tokens), 0)

    def test_03_frame_processor_restores_state_on_completion(self):
        """FrameProcessor restores AIWorker state to READY on completed run."""
        proc = FrameProcessor(base_dir=BASE_DIR)
        temp_dir = Path(tempfile.mkdtemp(prefix="phase5_4_fp_comp_"))
        try:
            # Create 2 small test frames
            frames = []
            for i in range(1, 3):
                p = temp_dir / f"frame_{i:06d}.png"
                img = np.zeros((32, 32, 3), dtype=np.uint8)
                cv2.imwrite(str(p), img)
                frames.append(p)

            enh_dir = temp_dir / "enhanced"
            enh_dir.mkdir(parents=True, exist_ok=True)

            self.assertEqual(self.worker.state, STATE_READY)
            res = proc.process_frames(
                extracted_frames=frames,
                enhanced_dir=enh_dir,
                tile_mode="manual",
                requested_tile=64,
                job_id="test_comp_job"
            )
            self.assertTrue(res["success"])
            self.assertEqual(self.worker.state, STATE_READY)
            self.assertEqual(len(self.worker._active_tokens), 0)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_04_frame_processor_restores_state_on_pause(self):
        """FrameProcessor restores AIWorker state to READY on pause."""
        proc = FrameProcessor(base_dir=BASE_DIR)
        temp_dir = Path(tempfile.mkdtemp(prefix="phase5_4_fp_pause_"))
        try:
            frames = [temp_dir / f"frame_{i:06d}.png" for i in range(1, 4)]
            for p in frames:
                cv2.imwrite(str(p), np.zeros((32, 32, 3), dtype=np.uint8))

            enh_dir = temp_dir / "enhanced"
            enh_dir.mkdir(parents=True, exist_ok=True)

            pause_event = threading.Event()
            pause_event.set()  # Pause immediately

            res = proc.process_frames(
                extracted_frames=frames,
                enhanced_dir=enh_dir,
                job_id="test_pause_job",
                pause_event=pause_event
            )
            self.assertEqual(res["status"], "paused")
            self.assertEqual(self.worker.state, STATE_READY)
            self.assertEqual(len(self.worker._active_tokens), 0)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_05_frame_processor_restores_state_on_cancel(self):
        """FrameProcessor restores AIWorker state to READY on cancel."""
        proc = FrameProcessor(base_dir=BASE_DIR)
        temp_dir = Path(tempfile.mkdtemp(prefix="phase5_4_fp_cancel_"))
        try:
            frames = [temp_dir / f"frame_{i:06d}.png" for i in range(1, 4)]
            for p in frames:
                cv2.imwrite(str(p), np.zeros((32, 32, 3), dtype=np.uint8))

            enh_dir = temp_dir / "enhanced"
            enh_dir.mkdir(parents=True, exist_ok=True)

            cancel_event = threading.Event()
            cancel_event.set()

            res = proc.process_frames(
                extracted_frames=frames,
                enhanced_dir=enh_dir,
                job_id="test_cancel_job",
                cancel_event=cancel_event
            )
            self.assertEqual(res["status"], "cancelled")
            self.assertEqual(self.worker.state, STATE_READY)
            self.assertEqual(len(self.worker._active_tokens), 0)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_06_quick_preview_rejected_when_worker_busy(self):
        """Quick preview returns busy error if worker has an active task token."""
        token = self.worker.acquire_busy("video", "active_video_job")
        try:
            res = self.worker.generate_quick_preview(
                input_path=Path("dummy.png"),
                thumbnails_dir=Path("thumbnails")
            )
            self.assertFalse(res["success"])
            self.assertIn("AI Worker đang bận", res["error"])
        finally:
            self.worker.release_busy(token)

    def test_07_quick_preview_succeeds_after_video_releases(self):
        """Quick preview succeeds immediately after video task releases busy token."""
        # 1. Acquire and release video token
        token = self.worker.acquire_busy("video", "prev_video_job")
        self.worker.release_busy(token)
        self.assertEqual(self.worker.state, STATE_READY)

        # 2. Run quick preview on a small synthetic image
        temp_dir = Path(tempfile.mkdtemp(prefix="phase5_4_qp_"))
        try:
            test_img = temp_dir / "test_qp.png"
            cv2.imwrite(str(test_img), np.full((128, 128, 3), 128, dtype=np.uint8))

            res = self.worker.generate_quick_preview(
                input_path=test_img,
                thumbnails_dir=temp_dir,
                patch_size=64
            )
            self.assertTrue(res["success"])
            self.assertEqual(self.worker.state, STATE_READY)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_08_get_status_reflects_active_tasks(self):
        """get_status() includes active_tasks count and state."""
        st = self.worker.get_status()
        self.assertEqual(st["active_tasks"], 0)
        self.assertEqual(st["state"], STATE_READY)

        token = self.worker.acquire_busy("test", "job99")
        st2 = self.worker.get_status()
        self.assertEqual(st2["active_tasks"], 1)
        self.assertEqual(st2["state"], STATE_BUSY)

        self.worker.release_busy(token)
        st3 = self.worker.get_status()
        self.assertEqual(st3["active_tasks"], 0)
        self.assertEqual(st3["state"], STATE_READY)


class TestAudit5303VideoCancellationSemantics(unittest.TestCase):
    """Tests for AUDIT53-03: Video Cancellation Semantics & Subprocess Cleanup."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="phase5_4_cancel_"))
        self.db_path = self.temp_dir / "test_cancel_jobs.db"
        self.job_mgr = VideoJobManager(db_path=self.db_path)
        self.pipeline = VideoPipeline(base_dir=BASE_DIR, job_manager=self.job_mgr)
        self.client = app.test_client()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_dummy_job(self, status: str = STATUS_QUEUED) -> str:
        jid = str(uuid.uuid4())
        src = self.temp_dir / "test.mp4"
        src.write_bytes(b"dummy")
        ws = self.temp_dir / "ws" / jid
        ws.mkdir(parents=True, exist_ok=True)
        self.job_mgr.create_video_job(
            job_id=jid,
            filename="test.mp4",
            source_path=str(src),
            workspace_dir=str(ws),
            video_metadata={"width": 64, "height": 64, "fps": 10, "total_frames": 5, "duration": 0.5},
            audio_metadata={},
            processing_config={},
            file_size=100
        )
        if status != STATUS_QUEUED:
            self.job_mgr.transition_status(jid, status, force=True)
        return jid

    def test_01_ffmpeg_service_raises_cancelled_exception(self):
        """FFmpegService raises FFmpegCancelledException when cancel_event is set."""
        svc = get_ffmpeg_service()
        cancel_event = threading.Event()
        cancel_event.set()

        # Run an ffmpeg command to verify cancellation
        with self.assertRaises(FFmpegCancelledException):
            svc.execute_ffmpeg(
                args=["-f", "lavfi", "-i", "nullsrc", "-t", "5", "-f", "null", "-"],
                cancel_event=cancel_event
            )

    def test_02_pipeline_cancel_during_extraction_sets_cancelled_status(self):
        """Cancellation during frame extraction transitions job to CANCELLED (not FAILED)."""
        jid = self._create_dummy_job(STATUS_QUEUED)
        cancel_event = self.pipeline.get_cancel_event(jid)

        def mock_extract(*args, **kwargs):
            cancel_event.set()
            raise FFmpegCancelledException("FFmpeg process cancelled by user request.")

        with patch.object(self.pipeline.extractor, "extract_frames", side_effect=mock_extract):
            res = self.pipeline.run_job(jid, output_dir=self.temp_dir)
            self.assertFalse(res)

        job = self.job_mgr.get_video_job(jid)
        self.assertEqual(job["status"], STATUS_CANCELLED)
        self.assertNotEqual(job["status"], STATUS_FAILED)

    def test_03_pipeline_cancel_during_reconstruction_sets_cancelled_status(self):
        """Cancellation during video reconstruction transitions job to CANCELLED."""
        jid = self._create_dummy_job(STATUS_QUEUED)
        cancel_event = self.pipeline.get_cancel_event(jid)

        with patch.object(self.pipeline.extractor, "extract_frames", return_value=[Path("f1.png")]), \
             patch.object(self.pipeline.audio_proc, "extract_audio", return_value=Path("audio.m4a")), \
             patch.object(self.pipeline.frame_proc, "process_frames", return_value={"status": "completed", "tile_used": 64}):
            def mock_recon(*args, **kwargs):
                cancel_event.set()
                raise FFmpegCancelledException("FFmpeg reconstruction cancelled.")

            with patch.object(self.pipeline.reconstructor, "reconstruct_video_stream", side_effect=mock_recon):
                res = self.pipeline.run_job(jid, output_dir=self.temp_dir)
                self.assertFalse(res)

        job = self.job_mgr.get_video_job(jid)
        self.assertEqual(job["status"], STATUS_CANCELLED)

    def test_04_pipeline_cancel_during_audio_merge_sets_cancelled_status(self):
        """Cancellation during audio merge transitions job to CANCELLED."""
        jid = self._create_dummy_job(STATUS_QUEUED)
        cancel_event = self.pipeline.get_cancel_event(jid)

        with patch.object(self.pipeline.extractor, "extract_frames", return_value=[Path("f1.png")]), \
             patch.object(self.pipeline.audio_proc, "extract_audio", return_value=Path("audio.m4a")), \
             patch.object(self.pipeline.frame_proc, "process_frames", return_value={"status": "completed", "tile_used": 64}), \
             patch.object(self.pipeline.reconstructor, "reconstruct_video_stream", return_value=True):
            def mock_merge(*args, **kwargs):
                cancel_event.set()
                raise FFmpegCancelledException("Audio merge cancelled.")

            with patch.object(self.pipeline.audio_proc, "merge_audio_and_video", side_effect=mock_merge):
                res = self.pipeline.run_job(jid, output_dir=self.temp_dir)
                self.assertFalse(res)

        job = self.job_mgr.get_video_job(jid)
        self.assertEqual(job["status"], STATUS_CANCELLED)

    def test_05_cancel_immediately_after_start(self):
        """Setting cancel before any stage executes results in clean CANCELLED state."""
        jid = self._create_dummy_job(STATUS_QUEUED)
        cancel_event = self.pipeline.get_cancel_event(jid)
        cancel_event.set()

        res = self.pipeline.run_job(jid, output_dir=self.temp_dir)
        self.assertFalse(res)

        job = self.job_mgr.get_video_job(jid)
        self.assertEqual(job["status"], STATUS_CANCELLED)

    def test_06_cancel_twice_is_safe_idempotent(self):
        """Cancelling twice consecutive via API returns success and remains CANCELLED."""
        with patch("app.video_job_mgr", self.job_mgr), patch("app.video_pipeline", self.pipeline):
            jid = self._create_dummy_job(STATUS_QUEUED)
            res1 = self.client.post(f"/api/video/jobs/{jid}/cancel")
            self.assertEqual(res1.status_code, 200)

            res2 = self.client.post(f"/api/video/jobs/{jid}/cancel")
            self.assertEqual(res2.status_code, 200)
            data2 = json.loads(res2.data)
            self.assertEqual(data2.get("job_status"), STATUS_CANCELLED)

    def test_07_cancel_completed_job_returns_400(self):
        """Cancelling a COMPLETED job is rejected with 400 Bad Request."""
        with patch("app.video_job_mgr", self.job_mgr):
            jid = self._create_dummy_job(STATUS_COMPLETED)
            res = self.client.post(f"/api/video/jobs/{jid}/cancel")
            self.assertEqual(res.status_code, 400)
            job = self.job_mgr.get_video_job(jid)
            self.assertEqual(job["status"], STATUS_COMPLETED)

    def test_08_cancel_failed_job_returns_400(self):
        """Cancelling a FAILED job is rejected with 400 Bad Request."""
        with patch("app.video_job_mgr", self.job_mgr):
            jid = self._create_dummy_job(STATUS_FAILED)
            res = self.client.post(f"/api/video/jobs/{jid}/cancel")
            self.assertEqual(res.status_code, 400)
            job = self.job_mgr.get_video_job(jid)
            self.assertEqual(job["status"], STATUS_FAILED)

    def test_09_independent_error_remains_failed(self):
        """An error without cancel event is recorded as FAILED (not CANCELLED)."""
        jid = self._create_dummy_job(STATUS_QUEUED)

        with patch.object(self.pipeline.extractor, "extract_frames", side_effect=IOError("Disk read error")):
            res = self.pipeline.run_job(jid, output_dir=self.temp_dir)
            self.assertFalse(res)

        job = self.job_mgr.get_video_job(jid)
        self.assertEqual(job["status"], STATUS_FAILED)
        self.assertIn("Disk read error", job["error_message"])

    def test_10_enhanced_frames_preserved_on_cancel(self):
        """When job is cancelled, enhanced frames directory is preserved on disk."""
        jid = self._create_dummy_job(STATUS_QUEUED)
        ws_dir = self.temp_dir / "ws" / jid
        enh_dir = ws_dir / "enhanced"
        enh_dir.mkdir(parents=True, exist_ok=True)
        test_frame = enh_dir / "frame_000001.png"
        test_frame.write_bytes(b"frame1_data")

        cancel_event = self.pipeline.get_cancel_event(jid)
        cancel_event.set()
        self.pipeline.run_job(jid, output_dir=self.temp_dir)

        self.assertTrue(test_frame.exists())
        self.assertEqual(test_frame.read_bytes(), b"frame1_data")


if __name__ == '__main__':
    unittest.main()
