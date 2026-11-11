# -*- coding: utf-8 -*-
"""
Video Enhancement Pipeline Orchestrator (Phase 5)
Orchestrates the entire video upscaling lifecycle:
Analysis -> Storage Check -> Frame Extraction -> Audio Extraction ->
Sequential AI Frame Upscaling -> Video Reconstruction -> Audio Multiplexing ->
Verification -> Atomic Finalization -> Cleanup.
"""

import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .audio_processor import AudioProcessor
from .ffmpeg_service import FFmpegCancelledException, FFmpegService, get_ffmpeg_service
from .frame_extractor import FrameExtractor
from .frame_processor import FrameProcessor
from .storage_manager import VideoStorageManager
from .video_analyzer import VideoAnalyzer
from .video_job_manager import (
    STATUS_ANALYZING,
    STATUS_CANCEL_REQUESTED,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_EXTRACTING,
    STATUS_FAILED,
    STATUS_PAUSED,
    STATUS_PROCESSING,
    STATUS_QUEUED,
    STATUS_RECONSTRUCTING,
    STATUS_RESTORING_AUDIO,
    STATUS_VERIFYING,
    VideoJobManager,
    get_video_job_manager,
)
from .video_reconstructor import VideoReconstructor

logger = logging.getLogger(__name__)


def get_unique_video_output_path(output_dir: Path, basename: str, ext: str = ".mp4") -> Path:
    """Generate non-colliding output path for video."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    clean_ext = ext if ext.startswith(".") else f".{ext}"

    candidate = out_dir / f"{basename}_4x{clean_ext}"
    if not candidate.exists():
        return candidate

    counter = 1
    while True:
        candidate = out_dir / f"{basename}_4x_{counter}{clean_ext}"
        if not candidate.exists():
            return candidate
        counter += 1


class VideoJobCancelledException(RuntimeError):
    """Raised when video pipeline is cancelled by user request."""
    pass


class VideoPipeline:
    """
    Executes end-to-end video enhancement jobs with full lifecycle control.
    """

    def __init__(
        self,
        base_dir: Path,
        job_manager: Optional[VideoJobManager] = None,
        config: Optional[Dict[str, Any]] = None
    ):
        self.base_dir = Path(base_dir).resolve()
        self.cfg = config or {}
        v_cfg = self.cfg.get("video", {})
        dirs_cfg = self.cfg.get("directories", {})

        ws_dir = dirs_cfg.get("video_workspaces_dir", "data/video_workspaces")
        self.workspaces_dir = self.base_dir / ws_dir

        self.ffmpeg_service = get_ffmpeg_service(self.cfg)
        self.analyzer = VideoAnalyzer(self.ffmpeg_service, self.cfg)
        self.extractor = FrameExtractor(self.ffmpeg_service)
        self.audio_proc = AudioProcessor(self.ffmpeg_service)
        self.reconstructor = VideoReconstructor(self.ffmpeg_service)
        self.storage_mgr = VideoStorageManager(self.workspaces_dir)
        self.job_mgr = job_manager or get_video_job_manager(self.base_dir / "data" / "jobs.db")
        self.frame_proc = FrameProcessor(base_dir=self.base_dir)

        # Threading event maps for active jobs
        self._pause_events: Dict[str, threading.Event] = {}
        self._cancel_events: Dict[str, threading.Event] = {}
        self._active_threads: Dict[str, threading.Thread] = {}
        self._lock = threading.RLock()

    def get_pause_event(self, job_id: str) -> threading.Event:
        with self._lock:
            if job_id not in self._pause_events:
                self._pause_events[job_id] = threading.Event()
            return self._pause_events[job_id]

    def get_cancel_event(self, job_id: str) -> threading.Event:
        with self._lock:
            if job_id not in self._cancel_events:
                self._cancel_events[job_id] = threading.Event()
            return self._cancel_events[job_id]

    def pause_job(self, job_id: str) -> bool:
        """Signal job to pause at safe frame boundary."""
        with self._lock:
            if job_id in self._pause_events:
                self._pause_events[job_id].set()
                logger.info(f"Pause signal set for video job {job_id}")
                return True
        return False

    def cancel_job(self, job_id: str) -> bool:
        """Signal job to cancel cleanly and set event."""
        with self._lock:
            evt = self.get_cancel_event(job_id)
            evt.set()
            logger.info(f"Cancel signal set for video job {job_id}")
            try:
                self.job_mgr.transition_status(job_id, STATUS_CANCEL_REQUESTED)
            except Exception:
                pass
            return True

    def is_job_running(self, job_id: str) -> bool:
        """Check if a worker thread is actively executing this video job."""
        with self._lock:
            th = self._active_threads.get(job_id)
            return bool(th and th.is_alive())

    def wait_for_job_stopped(self, job_id: str, timeout: float = 10.0) -> bool:
        """
        Wait until active execution of job_id stops.
        Returns True if stopped, False if timed out.
        """
        with self._lock:
            th = self._active_threads.get(job_id)
        if not th or not th.is_alive():
            return True
        th.join(timeout=timeout)
        return not th.is_alive()

    def run_job(
        self,
        job_id: str,
        output_dir: Path,
        event_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None
    ) -> bool:
        """
        Execute full video pipeline for job_id.
        Can be run in a background worker thread.
        """
        with self._lock:
            if job_id in self._active_threads and self._active_threads[job_id].is_alive():
                logger.warning(f"Video job {job_id} already has an active executing worker thread: {self._active_threads[job_id].name}")
                return False
            self._active_threads[job_id] = threading.current_thread()

        pause_event = self.get_pause_event(job_id)
        pause_event.clear()
        cancel_event = self.get_cancel_event(job_id)

        job = self.job_mgr.get_video_job(job_id)
        if not job:
            logger.error(f"Video job {job_id} not found in database.")
            with self._lock:
                self._active_threads.pop(job_id, None)
            return False

        # Immediate pre-flight cancellation check
        if cancel_event.is_set() or job.get("status") == STATUS_CANCELLED:
            logger.info(f"Video job {job_id} was already cancelled prior to worker execution.")
            self.job_mgr.transition_status(job_id, STATUS_CANCELLED, force=True)
            with self._lock:
                self._active_threads.pop(job_id, None)
            return False

        def check_cancellation():
            if cancel_event.is_set():
                raise VideoJobCancelledException(f"Video job {job_id} cancelled by user request.")

        source_path = Path(job["source_path"]).resolve()
        workspace_dir = Path(job["workspace_dir"]).resolve()
        workspace_dir.mkdir(parents=True, exist_ok=True)
        out_dir = Path(output_dir).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        processing_cfg = {}
        if job.get("processing_config"):
            try:
                processing_cfg = json.loads(job["processing_config"]) if isinstance(job["processing_config"], str) else job["processing_config"]
            except Exception:
                pass

        quality_preset = processing_cfg.get("quality_preset", "balanced")
        tile_mode = processing_cfg.get("tile_mode", "auto")
        tile_size = processing_cfg.get("tile_size")

        def emit(event_type: str, data: Dict[str, Any]):
            if event_callback:
                payload = {"job_id": job_id, "filename": job["filename"], **data}
                event_callback(event_type, payload)

        temp_final: Optional[Path] = None

        try:
            start_time = time.time()
            self.job_mgr.transition_status(job_id, STATUS_PROCESSING, force=True)
            check_cancellation()

            # Stage 1: Analyze video if metadata not present
            v_meta = {}
            if job.get("video_metadata"):
                try:
                    v_meta = json.loads(job["video_metadata"]) if isinstance(job["video_metadata"], str) else job["video_metadata"]
                except Exception:
                    pass

            if not v_meta or not v_meta.get("fps"):
                self.job_mgr.update_stage(job_id, STATUS_ANALYZING)
                emit("video_stage", {"stage": STATUS_ANALYZING, "detail": "Phân tích metadata video..."})
                analysis = self.analyzer.analyze(source_path)
                v_meta = analysis["video"]
                a_meta = analysis["audio"]

            fps = float(v_meta.get("fps") or 30.0)
            total_frames = int(v_meta.get("total_frames") or 1)
            target_w = int(v_meta.get("target_width") or (int(v_meta.get("width") or 64) * 4))
            target_h = int(v_meta.get("target_height") or (int(v_meta.get("height") or 64) * 4))
            duration = float(v_meta.get("duration") or 0.0)

            # Stage 2: Extract Frames
            extracted_dir = workspace_dir / "extracted"
            existing_frames = self.extractor.get_existing_extracted_frames(workspace_dir)

            if len(existing_frames) < total_frames:
                self.job_mgr.update_stage(job_id, STATUS_EXTRACTING)
                emit("video_stage", {"stage": STATUS_EXTRACTING, "detail": "Trích xuất khung hình lossless PNG..."})

                def on_extract_progress(cur, tot, detail):
                    pct = min(100, int((cur / tot) * 100)) if tot > 0 else 0
                    emit("video_progress", {"stage": STATUS_EXTRACTING, "progress_pct": pct, "detail": detail})

                extracted_frames = self.extractor.extract_frames(
                    source_path=source_path,
                    workspace_dir=workspace_dir,
                    expected_frames=total_frames,
                    progress_callback=on_extract_progress,
                    cancel_event=cancel_event
                )
            else:
                extracted_frames = existing_frames

            check_cancellation()

            # Stage 3: Extract Audio
            audio_path = workspace_dir / "extracted_audio.m4a"
            if not audio_path.is_file():
                emit("video_stage", {"stage": "EXTRACTING_AUDIO", "detail": "Trích xuất âm thanh gốc..."})
                audio_path = self.audio_proc.extract_audio(
                    source_path=source_path,
                    workspace_dir=workspace_dir,
                    cancel_event=cancel_event
                )

            check_cancellation()

            # Stage 4: Frame Enhancement Loop
            self.job_mgr.update_stage(job_id, STATUS_PROCESSING)
            emit("video_stage", {"stage": STATUS_PROCESSING, "detail": "Nâng cấp AI từng khung hình (RealESRGAN_x4plus)..."})

            enhanced_dir = workspace_dir / "enhanced"

            def on_frame_progress(data: Dict[str, Any]):
                self.job_mgr.update_frame_progress(
                    job_id=job_id,
                    processed_frames=data["current_frame"],
                    current_frame_index=data["current_frame"],
                    tile_used=data["tile_used"],
                    elapsed_seconds=data["elapsed_seconds"]
                )
                emit("video_progress", {
                    "stage": STATUS_PROCESSING,
                    "progress_pct": data["progress_pct"],
                    "current_frame": data["current_frame"],
                    "total_frames": data["total_frames"],
                    "tile_used": data["tile_used"],
                    "fps_rate": data["fps_rate"],
                    "eta_seconds": data["eta_seconds"],
                    "detail": f"Đang nâng cấp khung hình {data['current_frame']}/{data['total_frames']} (Tile: {data['tile_used']}, ETA: {data['eta_seconds']}s)"
                })

            res = self.frame_proc.process_frames(
                extracted_frames=extracted_frames,
                enhanced_dir=enhanced_dir,
                tile_mode=tile_mode,
                requested_tile=tile_size,
                job_id=job_id,
                progress_callback=on_frame_progress,
                pause_event=pause_event,
                cancel_event=cancel_event
            )

            # Check if paused
            if res.get("status") == "paused" or pause_event.is_set():
                self.job_mgr.transition_status(job_id, STATUS_PAUSED, force=True)
                emit("video_stage", {"stage": STATUS_PAUSED, "detail": f"Đã tạm dừng ở khung hình {res.get('last_frame')}"})
                return True

            # Check if cancelled
            if res.get("status") == "cancelled" or cancel_event.is_set():
                raise VideoJobCancelledException(f"Video job {job_id} cancelled during frame processing.")

            check_cancellation()

            # Stage 5: Reconstruct Video
            self.job_mgr.update_stage(job_id, STATUS_RECONSTRUCTING)
            emit("video_stage", {"stage": STATUS_RECONSTRUCTING, "detail": "Tái tạo video H.264 từ các khung hình đã nâng cấp..."})

            temp_video_only = workspace_dir / "reconstructed_no_audio.mp4"

            def on_reconstruct_progress(cur, tot, detail):
                pct = min(100, int((cur / tot) * 100)) if tot > 0 else 0
                emit("video_progress", {"stage": STATUS_RECONSTRUCTING, "progress_pct": pct, "detail": detail})

            self.reconstructor.reconstruct_video_stream(
                enhanced_frames_dir=enhanced_dir,
                output_temp_video=temp_video_only,
                fps=fps,
                quality_preset=quality_preset,
                total_frames=total_frames,
                progress_callback=on_reconstruct_progress,
                cancel_event=cancel_event
            )

            check_cancellation()

            # Stage 6: Restore & Merge Audio
            self.job_mgr.update_stage(job_id, STATUS_RESTORING_AUDIO)
            emit("video_stage", {"stage": STATUS_RESTORING_AUDIO, "detail": "Đồng bộ và ghép âm thanh gốc vào video..."})

            final_name = source_path.stem
            final_target = get_unique_video_output_path(out_dir, final_name, ".mp4")
            temp_final = final_target.with_name(f"tmp_{final_target.name}")

            self.audio_proc.merge_audio_and_video(
                video_only_path=temp_video_only,
                audio_path=audio_path,
                output_path=temp_final,
                cancel_event=cancel_event
            )

            check_cancellation()

            # Stage 7: Verify Output
            self.job_mgr.update_stage(job_id, STATUS_VERIFYING)
            emit("video_stage", {"stage": STATUS_VERIFYING, "detail": "Kiểm tra tính toàn vẹn của video kết quả..."})

            verified_info = self.reconstructor.verify_output_video(
                video_path=temp_final,
                expected_width=target_w,
                expected_height=target_h,
                expected_duration=duration,
                expected_has_audio=bool(audio_path and audio_path.is_file())
            )

            # Atomic rename to final output
            os.replace(temp_final, final_target)

            total_elapsed = round(time.time() - start_time, 2)
            out_size = final_target.stat().st_size

            # Mark completed in DB
            self.job_mgr.transition_status(
                job_id=job_id,
                new_status=STATUS_COMPLETED,
                output_path=str(final_target),
                output_size=out_size,
                force=True
            )
            self.job_mgr.update_frame_progress(
                job_id=job_id,
                processed_frames=total_frames,
                current_frame_index=total_frames,
                tile_used=res.get("tile_used"),
                elapsed_seconds=total_elapsed
            )

            # Clean temporary raw extracted frames to save disk space
            self.storage_mgr.cleanup_workspace(job_id, keep_enhanced=True)

            logger.info(f"Video job {job_id} COMPLETED in {total_elapsed}s. Output: {final_target}")
            emit("video_complete", {
                "stage": STATUS_COMPLETED,
                "output_path": str(final_target),
                "output_filename": final_target.name,
                "output_size": out_size,
                "elapsed_seconds": total_elapsed,
                "verified": verified_info
            })
            return True

        except (VideoJobCancelledException, FFmpegCancelledException) as ce:
            logger.info(f"Video job {job_id} CANCELLED cleanly: {ce}")
            self.job_mgr.transition_status(job_id, STATUS_CANCELLED, force=True)
            emit("video_stage", {"stage": STATUS_CANCELLED, "detail": "Đã hủy tác vụ video theo yêu cầu người dùng."})
            if temp_final and temp_final.exists():
                try:
                    temp_final.unlink()
                except Exception:
                    pass
            return False

        except Exception as e:
            if cancel_event.is_set() or "cancelled" in str(e).lower():
                logger.info(f"Video job {job_id} cancelled during execution: {e}")
                self.job_mgr.transition_status(job_id, STATUS_CANCELLED, force=True)
                emit("video_stage", {"stage": STATUS_CANCELLED, "detail": "Đã hủy tác vụ video theo yêu cầu người dùng."})
                if temp_final and temp_final.exists():
                    try:
                        temp_final.unlink()
                    except Exception:
                        pass
                return False
            else:
                err_msg = str(e)
                logger.error(f"Error in video pipeline for job {job_id}: {err_msg}", exc_info=True)
                self.job_mgr.transition_status(job_id, STATUS_FAILED, error_message=err_msg, force=True)
                emit("video_error", {"stage": STATUS_FAILED, "error": err_msg})
                return False

        finally:
            with self._lock:
                self._active_threads.pop(job_id, None)
