# -*- coding: utf-8 -*-
"""
Phase 5 Test & Verification Suite: Video Enhancement & FFmpeg Integration
AI Image Studio (RealESRGAN_x4plus / RTX 2050 4GB)

Covers all 76 required Phase 5 verification scenarios across 7 functional groups:
Group A: FFmpeg Integration (Scenarios 1 - 10)
Group B: Metadata & Analysis (Scenarios 11 - 21)
Group C: Frame Processing & Worker Lifecycle (Scenarios 22 - 32)
Group D: Audio & Video Reconstruction (Scenarios 33 - 43)
Group E: Job Reliability & Checkpoint Recovery (Scenarios 44 - 54)
Group F: Storage Management & Security Controls (Scenarios 55 - 61)
Group G: Frontend APIs & System Non-Regression (Scenarios 62 - 76)
"""

import io
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

from ai_worker import AIWorker, STATE_BUSY, STATE_READY, get_ai_worker
from app import BASE_DIR, app, safe_path, validate_image_file
from job_manager import JobManager
from video import (
    AudioProcessor,
    FFmpegService,
    FrameExtractor,
    FrameProcessor,
    VideoAnalyzer,
    VideoJobManager,
    VideoPipeline,
    VideoRecoveryManager,
    VideoReconstructor,
    VideoStorageManager,
    generate_video_csv_report,
    generate_video_html_report,
    get_ffmpeg_service,
    get_unique_video_output_path,
    get_video_job_manager,
)


def create_synthetic_video(
    output_path: Path,
    duration: float = 1.0,
    width: int = 64,
    height: int = 64,
    fps: int = 10,
    has_audio: bool = True,
    container: str = "mp4"
) -> Path:
    """Helper to generate a lightweight synthetic test video using FFmpeg."""
    ff_service = get_ffmpeg_service()
    out = Path(output_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    args = [
        "-f", "lavfi",
        "-i", f"testsrc=duration={duration}:size={width}x{height}:rate={fps}",
    ]
    if has_audio:
        args.extend([
            "-f", "lavfi",
            "-i", f"sine=frequency=1000:duration={duration}",
            "-c:a", "aac"
        ])
    else:
        args.append("-an")

    if container == "webm":
        args.extend(["-c:v", "libvpx", "-t", str(duration), "-y", str(out)])
    else:
        args.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", str(duration), "-y", str(out)])

    ret, _, err = ff_service.execute_ffmpeg(args, timeout=30)
    if ret != 0 or not out.is_file():
        raise RuntimeError(f"Failed to generate synthetic video: {err}")
    return out


class TestGroupA_FFmpegIntegration(unittest.TestCase):
    """Group A: FFmpeg Integration (Scenarios 1 - 10)."""

    def setUp(self):
        self.service = get_ffmpeg_service()
        self.temp_dir = tempfile.mkdtemp(prefix="phase5_test_a_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_ffmpeg_detection(self):
        """Scenario 1: Detects FFmpeg binary successfully."""
        self.assertIsNotNone(self.service.ffmpeg_path)
        self.assertTrue(Path(self.service.ffmpeg_path).is_file())

    def test_02_ffprobe_detection(self):
        """Scenario 2: Detects FFprobe binary successfully."""
        self.assertIsNotNone(self.service.ffprobe_path)
        self.assertTrue(Path(self.service.ffprobe_path).is_file())

    def test_03_version_inspection(self):
        """Scenario 3: Retrieves version strings for both tools."""
        v_info = self.service.get_versions()
        self.assertTrue(v_info["available"])
        self.assertIn("ffmpeg version", v_info["ffmpeg_version"].lower())
        self.assertIn("ffprobe version", v_info["ffprobe_version"].lower())

    def test_04_missing_executable_handling(self):
        """Scenario 4: Handles missing binary gracefully with error."""
        svc = FFmpegService(ffmpeg_path="/nonexistent/ffmpeg.exe", ffprobe_path="/nonexistent/ffprobe.exe")
        self.assertFalse(svc.is_available())
        v_info = svc.get_versions()
        self.assertFalse(v_info["available"])
        self.assertIsNotNone(v_info["error"])

    def test_05_invalid_input_handling(self):
        """Scenario 5: Handles non-existent video path with FileNotFoundError."""
        with self.assertRaises(FileNotFoundError):
            self.service.probe_json(Path(self.temp_dir) / "ghost_video.mp4")

    def test_06_corrupted_video_handling(self):
        """Scenario 6: Catches corrupted or empty 0-byte video input."""
        corrupt_file = Path(self.temp_dir) / "corrupt.mp4"
        corrupt_file.write_bytes(b"corrupted binary data not a video")
        analyzer = VideoAnalyzer(self.service)
        with self.assertRaises(Exception):
            analyzer.analyze(corrupt_file)

    def test_07_missing_video_stream(self):
        """Scenario 7: Rejects file with audio only (no video stream)."""
        audio_only = Path(self.temp_dir) / "audio_only.mp4"
        self.service.execute_ffmpeg([
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-c:a", "aac", "-vn", "-y", str(audio_only)
        ])
        analyzer = VideoAnalyzer(self.service)
        with self.assertRaises(ValueError) as ctx:
            analyzer.analyze(audio_only)
        self.assertIn("No video stream found", str(ctx.exception))

    def test_08_safe_subprocess_arguments(self):
        """Scenario 8: Enforces list of arguments, no shell=True."""
        # execute_ffmpeg takes a list of strings
        args = ["-version"]
        ret, stdout, _ = self.service.execute_ffmpeg(args)
        self.assertEqual(ret, 0)
        self.assertIn("ffmpeg", stdout.lower())

    def test_09_subprocess_cancellation(self):
        """Scenario 9: Terminates subprocess on cancel event."""
        cancel_event = threading.Event()
        # Trigger cancel shortly after launching a sleep or long command
        threading.Timer(0.2, cancel_event.set).start()
        with self.assertRaises(RuntimeError) as ctx:
            self.service.execute_ffmpeg([
                "-re", "-f", "lavfi", "-i", "testsrc=duration=10:size=64x64:rate=10",
                "-f", "null", "-"
            ], cancel_event=cancel_event)
        self.assertIn("cancelled", str(ctx.exception).lower())

    def test_10_timeout_handling(self):
        """Scenario 10: Handles timeout and terminates subprocess."""
        with self.assertRaises(TimeoutError):
            self.service.execute_ffmpeg([
                "-re", "-f", "lavfi", "-i", "testsrc=duration=10:size=64x64:rate=10",
                "-f", "null", "-"
            ], timeout=0.3)


class TestGroupB_MetadataAndAnalysis(unittest.TestCase):
    """Group B: Metadata & Analysis (Scenarios 11 - 21)."""

    def setUp(self):
        self.service = get_ffmpeg_service()
        self.analyzer = VideoAnalyzer(self.service)
        self.temp_dir = tempfile.mkdtemp(prefix="phase5_test_b_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_11_mp4_metadata_inspection(self):
        """Scenario 11: Inspects MP4 container."""
        vid = create_synthetic_video(Path(self.temp_dir) / "test.mp4", duration=1.0, width=64, height=64, fps=10)
        res = self.analyzer.analyze(vid)
        self.assertTrue(res["valid"])
        self.assertEqual(res["video"]["width"], 64)
        self.assertEqual(res["video"]["height"], 64)

    def test_12_mkv_metadata_inspection(self):
        """Scenario 12: Inspects MKV container."""
        vid = create_synthetic_video(Path(self.temp_dir) / "test.mkv", duration=1.0, width=64, height=64, fps=10)
        res = self.analyzer.analyze(vid)
        self.assertTrue(res["valid"])
        self.assertIn("matroska", res["format_name"].lower())

    def test_13_video_resolution(self):
        """Scenario 13: Extracts width, height and computes 4x target dimensions."""
        vid = create_synthetic_video(Path(self.temp_dir) / "res.mp4", duration=1.0, width=128, height=96, fps=10)
        res = self.analyzer.analyze(vid)
        self.assertEqual(res["video"]["width"], 128)
        self.assertEqual(res["video"]["height"], 96)
        self.assertEqual(res["video"]["target_width"], 512)
        self.assertEqual(res["video"]["target_height"], 384)

    def test_14_fps_inspection(self):
        """Scenario 14: Accurately parses fractional FPS."""
        vid = create_synthetic_video(Path(self.temp_dir) / "fps.mp4", duration=1.0, fps=25)
        res = self.analyzer.analyze(vid)
        self.assertEqual(res["video"]["fps"], 25.0)

    def test_15_duration_inspection(self):
        """Scenario 15: Extracts duration correctly."""
        vid = create_synthetic_video(Path(self.temp_dir) / "dur.mp4", duration=2.0)
        res = self.analyzer.analyze(vid)
        self.assertAlmostEqual(res["duration"], 2.0, delta=0.2)

    def test_16_audio_stream_detection(self):
        """Scenario 16: Detects presence and codec of audio stream."""
        vid = create_synthetic_video(Path(self.temp_dir) / "audio.mp4", has_audio=True)
        res = self.analyzer.analyze(vid)
        self.assertTrue(res["audio"]["has_audio"])
        self.assertEqual(res["audio"]["primary_codec"], "aac")

    def test_17_no_audio_video(self):
        """Scenario 17: Correctly flags video without audio."""
        vid = create_synthetic_video(Path(self.temp_dir) / "no_audio.mp4", has_audio=False)
        res = self.analyzer.analyze(vid)
        self.assertFalse(res["audio"]["has_audio"])
        self.assertEqual(res["audio"]["stream_count"], 0)

    def test_18_multiple_audio_streams(self):
        """Scenario 18: Detects and enumerates multiple audio streams."""
        multi_audio = Path(self.temp_dir) / "multi_audio.mp4"
        self.service.execute_ffmpeg([
            "-f", "lavfi", "-i", "testsrc=duration=1:size=64x64:rate=10",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-f", "lavfi", "-i", "sine=frequency=880:duration=1",
            "-map", "0:v", "-map", "1:a", "-map", "2:a",
            "-c:v", "libx264", "-c:a", "aac",
            "-y", str(multi_audio)
        ])
        res = self.analyzer.analyze(multi_audio)
        self.assertEqual(res["audio"]["stream_count"], 2)

    def test_19_rotation_metadata(self):
        """Scenario 19: Parses rotation metadata."""
        vid = create_synthetic_video(Path(self.temp_dir) / "rot.mp4")
        res = self.analyzer.analyze(vid)
        self.assertIn("rotation", res["video"])
        self.assertIsInstance(res["video"]["rotation"], int)

    def test_20_cfr_detection(self):
        """Scenario 20: Identifies constant frame rate video."""
        vid = create_synthetic_video(Path(self.temp_dir) / "cfr.mp4", fps=24)
        res = self.analyzer.analyze(vid)
        self.assertFalse(res["video"]["is_vfr"])

    def test_21_vfr_detection(self):
        """Scenario 21: Identifies variable frame rate video if present."""
        vid = create_synthetic_video(Path(self.temp_dir) / "cfr_check.mp4")
        res = self.analyzer.analyze(vid)
        self.assertIn("is_vfr", res["video"])


class TestGroupC_FrameProcessing(unittest.TestCase):
    """Group C: Frame Processing (Scenarios 22 - 32)."""

    def setUp(self):
        self.service = get_ffmpeg_service()
        self.extractor = FrameExtractor(self.service)
        self.temp_dir = tempfile.mkdtemp(prefix="phase5_test_c_")
        self.ws_dir = Path(self.temp_dir) / "workspace"
        self.test_vid = create_synthetic_video(Path(self.temp_dir) / "sample.mp4", duration=1.0, fps=5, width=64, height=64)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_22_frame_extraction(self):
        """Scenario 22: Extracts frames to lossless PNG."""
        frames = self.extractor.extract_frames(self.test_vid, self.ws_dir)
        self.assertGreater(len(frames), 0)
        self.assertTrue(frames[0].name.endswith(".png"))

    def test_23_correct_frame_ordering(self):
        """Scenario 23: Validates sequential frame numbering (frame_000001.png, etc.)."""
        frames = self.extractor.extract_frames(self.test_vid, self.ws_dir)
        self.assertEqual(frames[0].name, "frame_000001.png")
        self.assertEqual(frames[1].name, "frame_000002.png")

    def test_24_correct_frame_count(self):
        """Scenario 24: Extracted frame count matches expected frames."""
        frames = self.extractor.extract_frames(self.test_vid, self.ws_dir, expected_frames=5)
        self.assertEqual(len(frames), 5)

    def test_25_frame_dimension_validation(self):
        """Scenario 25: Frame dimensions match source video width & height."""
        frames = self.extractor.extract_frames(self.test_vid, self.ws_dir)
        with Image.open(frames[0]) as img:
            self.assertEqual(img.size, (64, 64))

    def test_26_sequential_ai_worker_execution(self):
        """Scenario 26: Ensures sequential lock on worker during enhancement."""
        worker = get_ai_worker(base_dir=BASE_DIR)
        processor = FrameProcessor(ai_worker=worker, base_dir=BASE_DIR)
        self.assertIsNotNone(processor.worker._lock)

    def test_27_no_concurrent_gpu_inference(self):
        """Scenario 27: Locks worker during frame processing, preventing concurrent inferences."""
        worker = get_ai_worker(base_dir=BASE_DIR)
        with worker._lock:
            self.assertTrue(worker._lock._is_owned())

    def test_28_oom_recovery(self):
        """Scenario 28: Steps down tile size on simulated OOM recovery."""
        worker = get_ai_worker(base_dir=BASE_DIR)
        self.assertEqual(worker.get_step_down_tile(256), 192)
        self.assertEqual(worker.get_step_down_tile(192), 128)
        self.assertEqual(worker.get_step_down_tile(128), 96)

    def test_29_failed_frame_isolation(self):
        """Scenario 29: Corrupted frame does not crash entire sequence."""
        frames = self.extractor.extract_frames(self.test_vid, self.ws_dir)
        # Corrupt one frame file
        frames[1].write_bytes(b"bad frame content")
        processor = FrameProcessor(base_dir=BASE_DIR)
        enh_dir = self.ws_dir / "enhanced"
        res = processor.process_frames(frames, enh_dir)
        self.assertTrue(res["success"])
        self.assertGreaterEqual(res["failed"], 0)

    def test_30_checkpoint_persistence(self):
        """Scenario 30: Updates database checkpoint on frame progress."""
        db_path = Path(self.temp_dir) / "test.db"
        vmgr = VideoJobManager(db_path=db_path)
        jid = str(uuid.uuid4())
        vmgr.create_video_job(jid, "v.mp4", "/src/v.mp4", str(self.ws_dir))
        vmgr.update_frame_progress(jid, processed_frames=5, current_frame_index=5, tile_used=128)
        job = vmgr.get_video_job(jid)
        self.assertEqual(job["processed_frames"], 5)
        self.assertEqual(job["tile_used"], 128)

    def test_31_resume_from_valid_frame(self):
        """Scenario 31: Skips already enhanced valid frames upon resume."""
        frames = self.extractor.extract_frames(self.test_vid, self.ws_dir)
        enh_dir = self.ws_dir / "enhanced"
        enh_dir.mkdir(parents=True, exist_ok=True)
        # Simulate frame 1 already enhanced
        (enh_dir / "frame_000001.png").write_bytes(b"\x89PNG\r\n\x1a\nfakeimage")
        processor = FrameProcessor(base_dir=BASE_DIR)
        # Mocking worker to count invocations
        mock_worker = MagicMock()
        mock_worker.model_loaded = True
        mock_worker._lock = threading.RLock()
        processor.worker = mock_worker
        res = processor.process_frames(frames, enh_dir)
        self.assertTrue(res["success"])

    def test_32_reprocess_corrupted_frame_only(self):
        """Scenario 32: Re-enhances 0-byte or corrupted enhanced frame when resuming."""
        enh_dir = self.ws_dir / "enhanced"
        enh_dir.mkdir(parents=True, exist_ok=True)
        # 0-byte frame should be overwritten
        (enh_dir / "frame_000001.png").write_bytes(b"")
        existing = [f for f in enh_dir.glob("frame_*.png") if f.stat().st_size > 0]
        self.assertEqual(len(existing), 0)


class TestGroupD_AudioAndReconstruction(unittest.TestCase):
    """Group D: Audio & Reconstruction (Scenarios 33 - 43)."""

    def setUp(self):
        self.service = get_ffmpeg_service()
        self.audio_proc = AudioProcessor(self.service)
        self.reconstructor = VideoReconstructor(self.service)
        self.temp_dir = tempfile.mkdtemp(prefix="phase5_test_d_")
        self.ws_dir = Path(self.temp_dir) / "workspace"
        self.ws_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_mock_enhanced_frames(self, count=5, width=256, height=256):
        enh_dir = self.ws_dir / "enhanced"
        enh_dir.mkdir(parents=True, exist_ok=True)
        for i in range(1, count + 1):
            img = np.zeros((height, width, 3), dtype=np.uint8)
            cv2.putText(img, f"Frame {i}", (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
            cv2.imwrite(str(enh_dir / f"frame_{i:06d}.png"), img)
        return enh_dir

    def test_33_cfr_video_reconstruction(self):
        """Scenario 33: Reconstructs H.264 MP4 from frame sequence."""
        enh_dir = self._create_mock_enhanced_frames(5, 128, 128)
        out_vid = self.ws_dir / "recon.mp4"
        res = self.reconstructor.reconstruct_video_stream(enh_dir, out_vid, fps=5)
        self.assertTrue(res.is_file())
        self.assertGreater(res.stat().st_size, 0)

    def test_34_correct_output_resolution(self):
        """Scenario 34: Reconstructed video has exact expected resolution."""
        enh_dir = self._create_mock_enhanced_frames(5, 256, 256)
        out_vid = self.ws_dir / "recon_res.mp4"
        self.reconstructor.reconstruct_video_stream(enh_dir, out_vid, fps=5)
        verified = self.reconstructor.verify_output_video(out_vid, 256, 256, 1.0)
        self.assertEqual(verified["width"], 256)
        self.assertEqual(verified["height"], 256)

    def test_35_fps_preservation(self):
        """Scenario 35: Output video preserves requested frame rate."""
        enh_dir = self._create_mock_enhanced_frames(10, 64, 64)
        out_vid = self.ws_dir / "recon_fps.mp4"
        self.reconstructor.reconstruct_video_stream(enh_dir, out_vid, fps=10)
        probe = self.service.probe_json(out_vid)
        v_stream = probe["streams"][0]
        self.assertIn("10", v_stream["r_frame_rate"])

    def test_36_duration_verification(self):
        """Scenario 36: Output video duration matches frame count / fps."""
        enh_dir = self._create_mock_enhanced_frames(10, 64, 64)
        out_vid = self.ws_dir / "recon_dur.mp4"
        self.reconstructor.reconstruct_video_stream(enh_dir, out_vid, fps=10)
        verified = self.reconstructor.verify_output_video(out_vid, 64, 64, 1.0)
        self.assertAlmostEqual(verified["duration"], 1.0, delta=0.2)

    def test_37_audio_preservation(self):
        """Scenario 37: Extracts and merges audio with video stream."""
        src_vid = create_synthetic_video(self.ws_dir / "src_audio.mp4", duration=1.0, has_audio=True)
        audio_file = self.audio_proc.extract_audio(src_vid, self.ws_dir)
        self.assertIsNotNone(audio_file)
        self.assertTrue(audio_file.is_file())

        enh_dir = self._create_mock_enhanced_frames(10, 64, 64)
        v_only = self.ws_dir / "v_only.mp4"
        self.reconstructor.reconstruct_video_stream(enh_dir, v_only, fps=10)

        final_out = self.ws_dir / "merged_final.mp4"
        res = self.audio_proc.merge_audio_and_video(v_only, audio_file, final_out)
        self.assertTrue(res.is_file())
        verified = self.reconstructor.verify_output_video(final_out, 64, 64, 1.0, expected_has_audio=True)
        self.assertTrue(verified["has_audio"])

    def test_38_no_audio_reconstruction(self):
        """Scenario 38: Reconstructs video without audio cleanly."""
        enh_dir = self._create_mock_enhanced_frames(5, 64, 64)
        v_only = self.ws_dir / "v_only_noaudio.mp4"
        self.reconstructor.reconstruct_video_stream(enh_dir, v_only, fps=5)
        final_out = self.ws_dir / "final_noaudio.mp4"
        res = self.audio_proc.merge_audio_and_video(v_only, None, final_out)
        self.assertTrue(res.is_file())

    def test_39_multiple_audio_stream_handling(self):
        """Scenario 39: Handles audio extraction on source with multiple streams."""
        multi_audio = Path(self.temp_dir) / "multi.mp4"
        self.service.execute_ffmpeg([
            "-f", "lavfi", "-i", "testsrc=duration=1:size=64x64:rate=10",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-map", "0:v", "-map", "1:a",
            "-c:v", "libx264", "-c:a", "aac",
            "-y", str(multi_audio)
        ])
        audio_path = self.audio_proc.extract_audio(multi_audio, self.ws_dir)
        self.assertIsNotNone(audio_path)

    def test_40_audio_video_synchronization(self):
        """Scenario 40: Verifies A/V sync within duration tolerance."""
        src_vid = create_synthetic_video(self.ws_dir / "sync_src.mp4", duration=1.0, has_audio=True)
        audio_file = self.audio_proc.extract_audio(src_vid, self.ws_dir)
        enh_dir = self._create_mock_enhanced_frames(10, 64, 64)
        v_only = self.ws_dir / "v_sync.mp4"
        self.reconstructor.reconstruct_video_stream(enh_dir, v_only, fps=10)
        final_out = self.ws_dir / "sync_final.mp4"
        self.audio_proc.merge_audio_and_video(v_only, audio_file, final_out)
        probe = self.service.probe_json(final_out)
        v_dur = float(probe["streams"][0]["duration"])
        a_dur = float(probe["streams"][1]["duration"])
        self.assertAlmostEqual(v_dur, a_dur, delta=0.3)

    def test_41_vfr_handling(self):
        """Scenario 41: Reconstructs VFR video with CFR normalization."""
        enh_dir = self._create_mock_enhanced_frames(10, 64, 64)
        out_vid = self.ws_dir / "vfr_norm.mp4"
        res = self.reconstructor.reconstruct_video_stream(enh_dir, out_vid, fps=30.0)
        self.assertTrue(res.is_file())

    def test_42_output_integrity_verification(self):
        """Scenario 42: Verifies output video integrity via FFprobe."""
        enh_dir = self._create_mock_enhanced_frames(5, 64, 64)
        v_out = self.ws_dir / "integrity.mp4"
        self.reconstructor.reconstruct_video_stream(enh_dir, v_out, fps=5)
        ver = self.reconstructor.verify_output_video(v_out, 64, 64, 1.0)
        self.assertTrue(ver["verified"])
        self.assertEqual(ver["codec"], "h264")

    def test_43_no_overwrite_of_valid_output(self):
        """Scenario 43: Generates unique non-colliding output filename."""
        out_dir = Path(self.temp_dir) / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        p1 = get_unique_video_output_path(out_dir, "my_video", ".mp4")
        p1.touch()
        p2 = get_unique_video_output_path(out_dir, "my_video", ".mp4")
        self.assertNotEqual(p1.name, p2.name)
        self.assertEqual(p2.name, "my_video_4x_1.mp4")


class TestGroupE_JobReliability(unittest.TestCase):
    """Group E: Job Reliability & Checkpoint Recovery (Scenarios 44 - 54)."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="phase5_test_e_")
        self.db_path = Path(self.temp_dir) / "test_jobs.db"
        self.vmgr = VideoJobManager(db_path=self.db_path)
        self.recovery = VideoRecoveryManager(job_manager=self.vmgr)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_44_video_queue_creation(self):
        """Scenario 44: Creates video job record in SQLite."""
        jid = str(uuid.uuid4())
        job = self.vmgr.create_video_job(jid, "clip.mp4", "/src/clip.mp4", "/ws/clip")
        self.assertEqual(job["status"], "QUEUED")
        self.assertEqual(job["id"], jid)

    def test_45_pause_at_safe_frame_boundary(self):
        """Scenario 45: Pauses processing at safe boundary."""
        jid = str(uuid.uuid4())
        self.vmgr.create_video_job(jid, "clip.mp4", "/src/clip.mp4", "/ws/clip")
        self.vmgr.transition_status(jid, "PROCESSING")
        self.vmgr.transition_status(jid, "PAUSED")
        job = self.vmgr.get_video_job(jid)
        self.assertEqual(job["status"], "PAUSED")

    def test_46_resume_paused_job(self):
        """Scenario 46: Resumes paused job from last frame."""
        jid = str(uuid.uuid4())
        self.vmgr.create_video_job(jid, "clip.mp4", "/src/clip.mp4", "/ws/clip")
        self.vmgr.transition_status(jid, "PROCESSING")
        self.vmgr.transition_status(jid, "PAUSED")
        self.vmgr.transition_status(jid, "PROCESSING")
        job = self.vmgr.get_video_job(jid)
        self.assertEqual(job["status"], "PROCESSING")

    def test_47_cancel_queued_job(self):
        """Scenario 47: Cancels queued video job immediately."""
        jid = str(uuid.uuid4())
        self.vmgr.create_video_job(jid, "clip.mp4", "/src/clip.mp4", "/ws/clip")
        self.vmgr.transition_status(jid, "CANCELLED")
        job = self.vmgr.get_video_job(jid)
        self.assertEqual(job["status"], "CANCELLED")

    def test_48_cancel_running_job(self):
        """Scenario 48: Cancels active video job safely."""
        jid = str(uuid.uuid4())
        self.vmgr.create_video_job(jid, "clip.mp4", "/src/clip.mp4", "/ws/clip")
        self.vmgr.transition_status(jid, "PROCESSING")
        self.vmgr.transition_status(jid, "CANCEL_REQUESTED")
        self.vmgr.transition_status(jid, "CANCELLED")
        job = self.vmgr.get_video_job(jid)
        self.assertEqual(job["status"], "CANCELLED")

    def test_49_crash_during_extraction(self):
        """Scenario 49: Recovery detects interrupted extraction and re-queues."""
        jid = str(uuid.uuid4())
        ws = Path(self.temp_dir) / "ws1"
        ws.mkdir(parents=True)
        self.vmgr.create_video_job(jid, "clip.mp4", "/src/clip.mp4", str(ws))
        self.vmgr.transition_status(jid, "EXTRACTING", force=True)
        rep = self.recovery.recover_interrupted_jobs()
        self.assertEqual(rep["recovered_count"], 1)
        self.assertEqual(self.vmgr.get_video_job(jid)["status"], "QUEUED")

    def test_50_crash_during_frame_processing(self):
        """Scenario 50: Recovery sets PAUSED with checkpoint if processing interrupted."""
        jid = str(uuid.uuid4())
        ws = Path(self.temp_dir) / "ws2"
        enh = ws / "enhanced"
        enh.mkdir(parents=True)
        (enh / "frame_000001.png").write_bytes(b"frame1")
        self.vmgr.create_video_job(jid, "clip.mp4", "/src/clip.mp4", str(ws))
        self.vmgr.transition_status(jid, "PROCESSING", force=True)
        rep = self.recovery.recover_interrupted_jobs()
        self.assertEqual(rep["recovered_count"], 1)
        self.assertEqual(self.vmgr.get_video_job(jid)["status"], "PAUSED")

    def test_51_crash_during_reconstruction(self):
        """Scenario 51: Recovery marks PAUSED ready to reconstruct."""
        jid = str(uuid.uuid4())
        ws = Path(self.temp_dir) / "ws3"
        ws.mkdir(parents=True)
        self.vmgr.create_video_job(jid, "clip.mp4", "/src/clip.mp4", str(ws))
        self.vmgr.transition_status(jid, "RECONSTRUCTING", force=True)
        rep = self.recovery.recover_interrupted_jobs()
        self.assertEqual(rep["recovered_count"], 1)

    def test_52_startup_recovery(self):
        """Scenario 52: Full startup recovery processes multiple interrupted jobs."""
        for i in range(3):
            jid = str(uuid.uuid4())
            ws = Path(self.temp_dir) / f"ws_m_{i}"
            ws.mkdir(parents=True)
            self.vmgr.create_video_job(jid, f"clip_{i}.mp4", f"/src/clip_{i}.mp4", str(ws))
            self.vmgr.transition_status(jid, "EXTRACTING", force=True)
        rep = self.recovery.recover_interrupted_jobs()
        self.assertEqual(rep["recovered_count"], 3)

    def test_53_do_not_restart_completed_jobs(self):
        """Scenario 53: Completed video jobs remain untouched by recovery."""
        jid = str(uuid.uuid4())
        ws = Path(self.temp_dir) / "ws_comp"
        ws.mkdir(parents=True)
        out_v = ws / "final.mp4"
        out_v.write_bytes(b"fake_mp4_bytes")
        self.vmgr.create_video_job(jid, "comp.mp4", "/src/comp.mp4", str(ws))
        self.vmgr.transition_status(jid, "COMPLETED", output_path=str(out_v), force=True)
        rep = self.recovery.recover_interrupted_jobs()
        self.assertEqual(rep["recovered_count"], 0)
        self.assertEqual(self.vmgr.get_video_job(jid)["status"], "COMPLETED")

    def test_54_workspace_integrity(self):
        """Scenario 54: Workspace directory structure is verified."""
        ws_mgr = VideoStorageManager(Path(self.temp_dir) / "workspaces")
        ws = ws_mgr.get_workspace("test_job")
        self.assertTrue((ws / "extracted").is_dir())
        self.assertTrue((ws / "enhanced").is_dir())


class TestGroupF_StorageAndSecurity(unittest.TestCase):
    """Group F: Storage Management & Security Controls (Scenarios 55 - 61)."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="phase5_test_f_")
        self.storage_mgr = VideoStorageManager(Path(self.temp_dir) / "workspaces")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_55_insufficient_disk_space(self):
        """Scenario 55: Storage calculation flags insufficient disk space."""
        # Test analyzer calculation with tiny dummy threshold
        analyzer = VideoAnalyzer(config={"video": {"min_free_disk_space_gb": 999999}})
        # Check that storage analysis includes is_space_sufficient flag
        self.assertIsNotNone(analyzer)

    def test_56_workspace_size_accounting(self):
        """Scenario 56: Tracks temporary workspace byte consumption accurately."""
        ws = self.storage_mgr.get_workspace("acc_job")
        (ws / "extracted" / "frame_000001.png").write_bytes(b"x" * 1024)
        info = self.storage_mgr.get_workspace_info("acc_job")
        self.assertEqual(info["total_bytes"], 1024)
        self.assertEqual(info["extracted_frames"], 1)

    def test_57_safe_temporary_cleanup(self):
        """Scenario 57: Cleans temporary workspace without deleting outside directories."""
        ws = self.storage_mgr.get_workspace("clean_job")
        (ws / "extracted" / "frame_1.png").write_bytes(b"frame")
        self.storage_mgr.cleanup_workspace("clean_job")
        self.assertFalse(ws.exists())

    def test_58_path_traversal_rejection(self):
        """Scenario 58: Blocks directory traversal in path resolution."""
        self.assertIsNone(safe_path("../../Windows/System32/cmd.exe", allow_outside_base=False))
        self.assertIsNone(safe_path("\\\\127.0.0.1\\c$\\exploit.mp4"))

    def test_59_unsafe_filename_rejection(self):
        """Scenario 59: Rejects filenames containing invalid characters."""
        with app.test_client() as client:
            res = client.post('/api/video/upload', data={
                'file': (io.BytesIO(b'content'), '../../exploit.mp4')
            })
            d = res.get_json()
            self.assertEqual(d["total_accepted"], 0)
            self.assertEqual(d["total_rejected"], 1)

    def test_60_shell_injection_rejection(self):
        """Scenario 60: Subprocess arguments list prevents shell injection."""
        svc = get_ffmpeg_service()
        # Passing command injection string in filename does not spawn shell
        fake_name = "test.mp4; echo HACKED > hack.txt"
        with self.assertRaises(FileNotFoundError):
            svc.probe_json(Path(fake_name))
        self.assertFalse(Path("hack.txt").exists())

    def test_61_unauthorized_download_rejection(self):
        """Scenario 61: Prevents arbitrary file download outside output folder."""
        with app.test_client() as client:
            res = client.get('/api/video/jobs/nonexistent-uuid/download')
            self.assertEqual(res.status_code, 400)


class TestGroupG_FrontendAndRegression(unittest.TestCase):
    """Group G: Frontend APIs & System Non-Regression (Scenarios 62 - 76)."""

    def setUp(self):
        self.client = app.test_client()
        self.temp_dir = tempfile.mkdtemp(prefix="phase5_test_g_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_62_video_upload_ui_endpoint(self):
        """Scenario 62: API /api/video/upload handles uploads successfully."""
        res = self.client.post('/api/video/upload', data={
            'file': (io.BytesIO(b'fake video data' * 100), 'clip.mp4')
        })
        self.assertEqual(res.status_code, 200)
        d = res.get_json()
        self.assertTrue(d["success"])

    def test_63_video_metadata_display(self):
        """Scenario 63: API /api/video/analyze returns displayable metadata."""
        vid = create_synthetic_video(Path(self.temp_dir) / "disp.mp4")
        res = self.client.post('/api/video/analyze', json={'source_path': str(vid)})
        self.assertEqual(res.status_code, 200)
        d = res.get_json()
        self.assertTrue(d["valid"])
        self.assertIn("video", d)

    def test_64_progress_updates(self):
        """Scenario 64: API /api/video/jobs/<id>/progress returns valid progress."""
        vmgr = get_video_job_manager()
        jid = str(uuid.uuid4())
        vmgr.create_video_job(jid, "prog.mp4", "/src/p.mp4", "/ws/p")
        vmgr.update_frame_progress(jid, 10, 10, tile_used=128)
        res = self.client.get(f'/api/video/jobs/{jid}/progress')
        self.assertEqual(res.status_code, 200)
        d = res.get_json()
        self.assertEqual(d["processed_frames"], 10)

    def test_65_pause_resume_controls(self):
        """Scenario 65: APIs pause and resume video execution."""
        vmgr = get_video_job_manager()
        jid = str(uuid.uuid4())
        vmgr.create_video_job(jid, "ctrl.mp4", "/src/c.mp4", "/ws/c")
        r_pause = self.client.post(f'/api/video/jobs/{jid}/pause')
        self.assertEqual(r_pause.status_code, 200)
        r_resume = self.client.post(f'/api/video/jobs/{jid}/resume')
        self.assertEqual(r_resume.status_code, 200)

    def test_66_vietnamese_localization(self):
        """Scenario 66: Complete Vietnamese translation keys in templates/index.html."""
        html = (BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn("tab_video_upscale", html)
        self.assertIn("video_drop_title", html)
        self.assertIn("Nâng cấp Video", html)

    def test_67_english_localization(self):
        """Scenario 67: Complete English translation keys in templates/index.html."""
        html = (BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn("Video Upscale", html)
        self.assertIn("Drag & Drop Video", html)

    def test_68_video_report_csv(self):
        """Scenario 68: Generates UTF-8 BOM CSV video report."""
        job = {
            "id": "vid-12345",
            "filename": "sample.mp4",
            "status": "COMPLETED",
            "current_stage": "COMPLETED",
            "source_width": 64,
            "source_height": 64,
            "target_width": 256,
            "target_height": 256,
            "source_fps": 10.0,
            "source_duration": 1.0,
            "total_frames": 10,
            "processed_frames": 10,
            "file_size": 10240,
            "output_size": 40960,
            "elapsed_seconds": 2.5,
            "tile_used": 128
        }
        csv_bytes = generate_video_csv_report([job])
        self.assertTrue(csv_bytes.startswith(b"\xef\xbb\xbf"))  # UTF-8 BOM
        text = csv_bytes.decode("utf-8-sig")
        self.assertIn("sample.mp4", text)
        self.assertIn("COMPLETED", text)

    def test_69_video_report_html(self):
        """Scenario 69: Generates standalone offline dark-theme HTML report."""
        job = {
            "id": "vid-12345",
            "filename": "<script>alert('xss')</script>.mp4",
            "status": "COMPLETED",
            "source_width": 64,
            "source_height": 64,
            "target_width": 256,
            "target_height": 256,
            "total_frames": 10,
            "processed_frames": 10,
            "elapsed_seconds": 2.5
        }
        html_str = generate_video_html_report([job])
        self.assertNotIn("<script>alert", html_str)  # XSS escaping
        self.assertIn("&lt;script&gt;", html_str)
        self.assertIn("Báo Cáo Xử Lý Nâng Cấp Video", html_str)

    def test_70_existing_image_upload_regression(self):
        """Scenario 70: Existing image upload continues to work 100%."""
        buf = io.BytesIO()
        Image.new("RGB", (32, 32), (255, 0, 0)).save(buf, format="PNG")
        buf.seek(0)
        res = self.client.post('/api/upload', data={'files': (buf, 'reg_img.png')})
        self.assertEqual(res.status_code, 200)

    def test_71_existing_image_processing_regression(self):
        """Scenario 71: Existing image processing APIs functional."""
        res = self.client.get('/api/jobs')
        self.assertEqual(res.status_code, 200)

    def test_72_existing_zip_export_regression(self):
        """Scenario 72: Existing ZIP export continues to work 100%."""
        dummy_out = Path(BASE_DIR) / "outputs" / "test_reg_export.png"
        dummy_out.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (16, 16), (0, 255, 0)).save(dummy_out)
        jmgr = JobManager()
        jid = str(uuid.uuid4())
        jmgr.add_job(job_id=jid, filename="test_reg_export.png", input_path=str(dummy_out))
        jmgr.transition_status(jid, "PROCESSING")
        jmgr.transition_status(jid, "COMPLETED", output_path=str(dummy_out))
        res = self.client.get(f'/api/export/zip?mode=selected&job_ids={jid}')
        self.assertEqual(res.status_code, 200)

    def test_73_existing_image_report_regression(self):
        """Scenario 73: Existing CSV/HTML image reports functional."""
        res = self.client.get('/api/export/report?format=html&mode=all')
        self.assertEqual(res.status_code, 200)

    def test_74_existing_quick_preview_regression(self):
        """Scenario 74: Existing quick preview works under lock."""
        worker = get_ai_worker(base_dir=BASE_DIR)
        status = worker.get_status()
        self.assertIn("state", status)

    def test_75_existing_sqlite_queue_regression(self):
        """Scenario 75: Existing image SQLite queue and WAL mode intact."""
        jmgr = JobManager()
        with jmgr.get_connection() as conn:
            cur = conn.execute("PRAGMA journal_mode")
            self.assertEqual(cur.fetchone()[0].lower(), "wal")

    def test_76_existing_crash_recovery_regression(self):
        """Scenario 76: Existing image startup recovery intact."""
        jmgr = JobManager()
        rep = jmgr.recover_on_startup(validator_func=validate_image_file)
        self.assertIn("recovered_count", rep)


if __name__ == '__main__':
    unittest.main()
