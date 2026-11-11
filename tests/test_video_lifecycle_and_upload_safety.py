# -*- coding: utf-8 -*-
"""
Unit and regression tests for Video lifecycle, workspace safety, and upload safety.
Uses isolated temp directories, mock subprocesses, and synthetic payloads.
Zero interaction with real GPU or video files.
"""

import io
import shutil
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

from app import app as flask_app, UPLOAD_DIR
from video.video_job_manager import VideoJobManager, STATUS_PROCESSING, STATUS_QUEUED, STATUS_CANCELLED
from video.storage_manager import VideoStorageManager
from video.video_pipeline import VideoPipeline


class TestVideoLifecycleAndUploadSafety(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_video_safety_")
        self.db_path = Path(self.temp_dir) / "test_video.db"
        self.ws_dir = Path(self.temp_dir) / "workspaces"
        self.upload_dir = Path(self.temp_dir) / "uploads"
        self.output_dir = Path(self.temp_dir) / "output"

        self.ws_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.job_mgr = VideoJobManager(db_path=self.db_path)
        self.storage_mgr = VideoStorageManager(base_workspaces_dir=self.ws_dir)
        self.pipeline = VideoPipeline(
            base_dir=Path(self.temp_dir),
            job_manager=self.job_mgr
        )

        self.app_client = flask_app.test_client()

        # Patch app singletons with test instances
        self.mgr_patcher = patch("app.video_job_mgr", self.job_mgr)
        self.storage_patcher = patch("app.video_storage_mgr", self.storage_mgr)
        self.pipeline_patcher = patch("app.video_pipeline", self.pipeline)
        self.upload_patcher = patch("app.UPLOAD_DIR", self.upload_dir)

        self.mgr_patcher.start()
        self.storage_patcher.start()
        self.pipeline_patcher.start()
        self.upload_patcher.start()

    def tearDown(self):
        self.mgr_patcher.stop()
        self.storage_patcher.stop()
        self.pipeline_patcher.stop()
        self.upload_patcher.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_video_delete_waits_or_rejects_when_thread_active(self):
        """DELETE /api/video/jobs/<job_id> must not delete workspace if worker thread is actively running."""
        jid = str(uuid.uuid4())
        ws = self.storage_mgr.get_workspace(jid)
        sample_file = ws / "extracted" / "frame_0001.png"
        sample_file.write_bytes(b"dummy_frame_data")

        self.job_mgr.create_video_job(
            job_id=jid,
            filename="test.mp4",
            source_path=str(self.temp_dir / Path("test.mp4")),
            workspace_dir=str(ws)
        )
        self.job_mgr.transition_status(jid, STATUS_PROCESSING)

        # Simulate a thread actively running and ignoring cancel within 0.1s
        stop_event = threading.Event()
        def mock_running_job():
            stop_event.wait(timeout=2.0)

        t = threading.Thread(target=mock_running_job, name="ActiveTestWorker")
        with self.pipeline._lock:
            self.pipeline._active_threads[jid] = t
        t.start()

        # DELETE should attempt to wait, and return 409 if still running
        with patch.object(self.pipeline, "wait_for_job_stopped", return_value=False):
            res = self.app_client.delete(f"/api/video/jobs/{jid}")
            self.assertEqual(res.status_code, 409)

        # Confirm workspace and DB job were NOT deleted
        self.assertTrue(sample_file.exists())
        self.assertIsNotNone(self.job_mgr.get_video_job(jid))

        # Now let thread stop
        stop_event.set()
        t.join(timeout=2.0)
        with self.pipeline._lock:
            self.pipeline._active_threads.pop(jid, None)

        # DELETE when stopped should succeed
        res2 = self.app_client.delete(f"/api/video/jobs/{jid}")
        self.assertEqual(res2.status_code, 200)
        self.assertFalse(sample_file.exists())
        self.assertIsNone(self.job_mgr.get_video_job(jid))

    def test_02_video_upload_streaming_size_limit(self):
        """Streaming video upload exceeding max size is rejected and partial file cleaned up."""
        # Config limit is mocked to 1 MB for testing
        with patch.dict(flask_app.config, {}, clear=False), \
             patch("app.APP_CONFIG", {"video": {"max_video_size_mb": 0.001, "supported_containers": [".mp4"]}}):
            oversized_data = b"X" * (1024 * 50)  # 50 KB exceeds 0.001 MB (~1 KB)
            data = {
                "file": (io.BytesIO(oversized_data), "big_video.mp4")
            }
            res = self.app_client.post("/api/video/upload", data=data, content_type="multipart/form-data")
            self.assertEqual(res.status_code, 200)
            res_json = res.get_json()
            self.assertEqual(res_json["total_accepted"], 0)
            self.assertEqual(res_json["total_rejected"], 1)
            self.assertIn("exceeds limit", res_json["rejected"][0]["reason"])

            # Verify no partial files or final file exist on disk
            self.assertFalse((self.upload_dir / "big_video.mp4").exists())
            self.assertEqual(len(list(self.upload_dir.glob("*.upload_tmp"))), 0)

    def test_03_video_upload_filename_collision_avoidance(self):
        """Uploading a video with an existing filename must NOT overwrite the existing file."""
        existing_video = self.upload_dir / "clip.mp4"
        existing_video.write_bytes(b"original_existing_video_content")

        with patch("app.APP_CONFIG", {"video": {"max_video_size_mb": 100, "supported_containers": [".mp4"]}}):
            new_data = b"new_second_uploaded_video_content"
            data = {
                "file": (io.BytesIO(new_data), "clip.mp4")
            }
            res = self.app_client.post("/api/video/upload", data=data, content_type="multipart/form-data")
            self.assertEqual(res.status_code, 200)
            res_json = res.get_json()
            self.assertEqual(res_json["total_accepted"], 1)
            saved_name = res_json["accepted"][0]["filename"]

            # Saved name must be collision-free (e.g., clip_1.mp4)
            self.assertEqual(saved_name, "clip_1.mp4")
            # Original file MUST remain untouched
            self.assertEqual(existing_video.read_bytes(), b"original_existing_video_content")
            # New file has new content
            self.assertEqual((self.upload_dir / "clip_1.mp4").read_bytes(), b"new_second_uploaded_video_content")

    def test_04_video_upload_empty_rejected(self):
        """0-byte video upload is rejected."""
        with patch("app.APP_CONFIG", {"video": {"max_video_size_mb": 100, "supported_containers": [".mp4"]}}):
            data = {
                "file": (io.BytesIO(b""), "empty.mp4")
            }
            res = self.app_client.post("/api/video/upload", data=data, content_type="multipart/form-data")
            self.assertEqual(res.status_code, 200)
            res_json = res.get_json()
            self.assertEqual(res_json["total_accepted"], 0)
            self.assertEqual(res_json["total_rejected"], 1)
            self.assertIn("empty", res_json["rejected"][0]["reason"])


if __name__ == "__main__":
    unittest.main()
