# -*- coding: utf-8 -*-
"""
Frame Processor Module for AI Image Studio (Phase 5)
Sequentially upscales extracted video frames using the persistent RealESRGAN_x4plus
AI Worker. Integrates Auto-Tile, CUDA OOM recovery, Pause/Resume, and Cancellation.
"""

import gc
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image

import torch
from ai_worker import (
    AIWorker,
    STATE_BUSY,
    STATE_READY,
    STATE_RECOVERING,
    get_ai_worker,
    read_image_unicode,
)

logger = logging.getLogger(__name__)


class FrameProcessor:
    """
    Coordinates frame-by-frame enhancement through the singleton AIWorker.
    Enforces strict sequential execution and non-concurrency on the GPU.
    """

    def __init__(self, ai_worker: Optional[AIWorker] = None, base_dir: Optional[Path] = None):
        self.worker = ai_worker or get_ai_worker(base_dir=base_dir)

    def process_frames(
        self,
        extracted_frames: List[Path],
        enhanced_dir: Path,
        tile_mode: str = "auto",
        requested_tile: Optional[int] = None,
        job_id: Optional[str] = None,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        pause_event: Optional[threading.Event] = None,
        cancel_event: Optional[threading.Event] = None
    ) -> Dict[str, Any]:
        """
        Process frame sequence sequentially with Auto-Tile, OOM recovery,
        Pause/Resume checkpoints, and cancellation support.
        """
        enhanced_dir = Path(enhanced_dir).resolve()
        enhanced_dir.mkdir(parents=True, exist_ok=True)

        total_frames = len(extracted_frames)
        if total_frames == 0:
            return {"success": True, "processed": 0, "total": 0, "status": "completed"}

        # Ensure model is initialized once in memory
        with self.worker._lock:
            if not self.worker.model_loaded:
                if not self.worker.initialize_model():
                    raise RuntimeError(f"Failed to initialize AI Worker for video: {self.worker.error_message}")

        start_time = time.time()
        processed_in_session = 0
        failed_count = 0
        last_tile_used = self.worker.default_tile

        # Acquire AI Worker active task ownership
        task_token = self.worker.acquire_busy(task_type="video", task_id=job_id)

        try:
            # Find first frame to process (skips already enhanced frames for instant resume)
            for idx, frame_path in enumerate(extracted_frames, 1):
                # 1. Cancellation Checkpoint
                if cancel_event and cancel_event.is_set():
                    logger.info(f"Video job {job_id} cancelled before frame {idx}")
                    return {
                        "success": False,
                        "status": "cancelled",
                        "processed": processed_in_session,
                        "total": total_frames,
                        "last_frame": idx - 1
                    }

                # 2. Pause Checkpoint (safe frame boundary)
                if pause_event and pause_event.is_set():
                    logger.info(f"Video job {job_id} paused safely at frame boundary {idx}")
                    return {
                        "success": True,
                        "status": "paused",
                        "processed": processed_in_session,
                        "total": total_frames,
                        "last_frame": idx - 1
                    }

                target_frame_name = f"frame_{idx:06d}.png"
                target_frame_path = enhanced_dir / target_frame_name

                # Skip if already enhanced and valid on disk (supports resume)
                if target_frame_path.is_file() and target_frame_path.stat().st_size > 0:
                    continue

                frame_start_ts = time.time()

                try:
                    # Read input frame
                    img_cv = read_image_unicode(frame_path)
                    h, w = img_cv.shape[:2]

                    # Determine tile size
                    chosen_tile = self.worker.choose_tile(
                        width=w,
                        height=h,
                        requested_tile=requested_tile,
                        requested_mode=tile_mode
                    )

                    # Acquire GPU inference lock
                    with self.worker._lock:
                        self.worker.state = STATE_BUSY
                        current_tile_to_try = chosen_tile
                        enhanced_cv = None
                        oom_recovered = False

                        while True:
                            try:
                                self.worker.upsampler.tile_size = current_tile_to_try
                                enhanced_cv, _ = self.worker.upsampler.enhance(img_cv, outscale=self.worker.scale)
                                last_tile_used = current_tile_to_try
                                break
                            except (torch.cuda.OutOfMemoryError, RuntimeError) as e:
                                err_str = str(e).lower()
                                if "out of memory" in err_str or "cuda out of memory" in err_str:
                                    oom_recovered = True
                                    self.worker.state = STATE_RECOVERING
                                    logger.warning(
                                        f"Video frame {idx} CUDA OOM with tile {current_tile_to_try}. Stepping down..."
                                    )
                                    torch.cuda.empty_cache()
                                    gc.collect()

                                    next_tile = self.worker.get_step_down_tile(current_tile_to_try)
                                    if next_tile is None:
                                        raise RuntimeError(f"OOM on frame {idx}: cannot step down from tile {current_tile_to_try}")

                                    current_tile_to_try = next_tile
                                    self.worker.current_tile = next_tile
                                    self.worker.state = STATE_BUSY
                                else:
                                    raise e

                    # Save enhanced frame atomically to temporary file, then replace
                    temp_frame_path = target_frame_path.with_name(f"tmp_{target_frame_name}")
                    cv2.imwrite(str(temp_frame_path), enhanced_cv)

                    if not temp_frame_path.is_file() or temp_frame_path.stat().st_size == 0:
                        raise IOError(f"Failed to write enhanced frame {idx}")

                    os.replace(temp_frame_path, target_frame_path)

                    processed_in_session += 1
                    frame_elapsed = time.time() - frame_start_ts

                    # Clean up memory buffers
                    del img_cv, enhanced_cv
                    if processed_in_session % 20 == 0:
                        gc.collect()

                    # Calculate measured ETA
                    total_elapsed = time.time() - start_time
                    fps_rate = processed_in_session / total_elapsed if total_elapsed > 0 else 0.0
                    remaining_frames = total_frames - idx
                    eta_seconds = (remaining_frames / fps_rate) if fps_rate > 0 else 0.0

                    pct = min(100, int((idx / total_frames) * 100))

                    if progress_callback:
                        progress_callback({
                            "job_id": job_id,
                            "current_frame": idx,
                            "total_frames": total_frames,
                            "progress_pct": pct,
                            "tile_used": last_tile_used,
                            "oom_recovered": oom_recovered,
                            "fps_rate": round(fps_rate, 2),
                            "eta_seconds": round(eta_seconds, 1),
                            "elapsed_seconds": round(total_elapsed, 1),
                            "frame_time": round(frame_elapsed, 2)
                        })

                except Exception as ex:
                    logger.error(f"Error processing video frame {idx}: {ex}")
                    failed_count += 1
                    # If frame is severely corrupted, write a blank or scaled fallback so video continues
                    if not target_frame_path.exists():
                        try:
                            # Fallback simple resize to preserve frame count
                            src_raw = cv2.imread(str(frame_path))
                            if src_raw is not None:
                                fb = cv2.resize(src_raw, (w * 4, h * 4), interpolation=cv2.INTER_LANCZOS4)
                                cv2.imwrite(str(target_frame_path), fb)
                        except Exception:
                            pass

            # Final state check
            return {
                "success": True,
                "status": "completed",
                "processed": processed_in_session,
                "total": total_frames,
                "failed": failed_count,
                "tile_used": last_tile_used,
                "elapsed_seconds": round(time.time() - start_time, 2)
            }
        finally:
            self.worker.release_busy(task_token)
