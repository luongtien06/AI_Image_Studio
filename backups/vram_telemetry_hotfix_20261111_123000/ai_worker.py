# -*- coding: utf-8 -*-
"""
AI Worker Module for AI Image Studio
Provides a persistent in-memory RealESRGAN_x4plus worker, auto-tile intelligence,
CUDA OOM recovery, multi-format export, and quick preview inference.
"""

import gc
import logging
import multiprocessing
import os
import queue
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import cv2
import numpy as np
from PIL import Image

import torch
from basicsr.archs.rrdbnet_arch import RRDBNet
from realesrgan import RealESRGANer

from gpu_monitor import get_gpu_info

logger = logging.getLogger(__name__)

# Worker States
STATE_STOPPED = "STOPPED"
STATE_STARTING = "STARTING"
STATE_READY = "READY"
STATE_BUSY = "BUSY"
STATE_ERROR = "ERROR"
STATE_RECOVERING = "RECOVERING"

# Tile Candidates for RTX 2050 (4GB) and step-down sequence
TILE_CANDIDATES = [256, 192, 128, 96, 64, 32]
TILE_STEP_DOWN = {
    256: 192,
    192: 128,
    128: 96,
    96: 64,
    64: 32,
    32: None,
    0: 128
}


def read_image_unicode(file_path: Path) -> np.ndarray:
    """
    Read an image from a path (including Unicode and long paths on Windows)
    into an OpenCV BGR/BGRA numpy array.
    """
    path_obj = Path(file_path)
    if not path_obj.is_file():
        raise FileNotFoundError(f"Image file does not exist: {file_path}")

    with open(path_obj, "rb") as f:
        buf = np.frombuffer(f.read(), dtype=np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError(f"Failed to decode image data from {file_path}")
    return img


def get_unique_output_path(output_dir: Path, basename: str, ext: str) -> Path:
    """
    Generate a non-colliding output path:
    <basename>_4x.<ext>, <basename>_4x_1.<ext>, <basename>_4x_2.<ext>, etc.
    """
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


class AIWorker:
    """
    Persistent In-Memory AI Worker for Real-ESRGAN.
    Maintains a single model instance in GPU VRAM across requests.
    """

    _instance = None
    _init_lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._init_lock:
            if cls._instance is None:
                cls._instance = super(AIWorker, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(
        self,
        base_dir: Optional[Path] = None,
        model_name: str = "RealESRGAN_x4plus",
        weights_rel_path: str = "weights/RealESRGAN_x4plus.pth",
        scale: int = 4,
        default_tile: int = 128,
        default_tile_pad: int = 20,
        gpu_id: int = 0,
        fp32: bool = False
    ):
        if getattr(self, "_initialized", False):
            return

        self.base_dir = Path(base_dir) if base_dir else Path(__file__).resolve().parent
        self.model_name = model_name
        self.weights_path = (self.base_dir / weights_rel_path).resolve()
        self.scale = scale
        self.default_tile = default_tile
        self.default_tile_pad = default_tile_pad
        self.gpu_id = gpu_id
        self.fp32 = fp32

        self.state = STATE_STOPPED
        self.model_loaded = False
        self.upsampler: Optional[RealESRGANer] = None
        self.current_tile = default_tile
        self.current_job: Optional[Dict[str, Any]] = None
        self.last_oom_recovered = False
        self.last_oom_tile: Optional[int] = None
        self.last_inference_time = 0.0
        self.total_inference_time = 0.0
        self.jobs_completed = 0
        self.error_message: Optional[str] = None

        self._lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._active_tokens: Dict[str, Dict[str, Any]] = {}
        self._initialized = True

    def acquire_busy(self, task_type: str = "generic", task_id: Optional[str] = None) -> str:
        """
        Register an active task requiring AIWorker inference/processing.
        Sets state to STATE_BUSY and returns an ownership token.
        Thread-safe and counter-safe (avoids negative counter and double release).
        """
        token = f"{task_type}:{task_id or 'anon'}:{uuid.uuid4().hex[:6]}"
        with self._state_lock:
            self._active_tokens[token] = {
                "type": task_type,
                "id": task_id,
                "time": time.time()
            }
            if self.state not in [STATE_ERROR, STATE_STOPPED, STATE_RECOVERING]:
                self.state = STATE_BUSY
        return token

    def release_busy(self, token: str) -> None:
        """
        Release task ownership token. If no other active tasks remain,
        restores state to STATE_READY safely.
        """
        with self._state_lock:
            self._active_tokens.pop(token, None)
            if len(self._active_tokens) == 0:
                if self.state not in [STATE_ERROR, STATE_STOPPED]:
                    self.state = STATE_READY
            else:
                if self.state not in [STATE_ERROR, STATE_STOPPED, STATE_RECOVERING]:
                    self.state = STATE_BUSY

    @contextmanager
    def busy_scope(self, task_type: str = "generic", task_id: Optional[str] = None):
        """Context manager to guarantee safe acquisition and release of worker state."""
        token = self.acquire_busy(task_type, task_id)
        try:
            yield token
        finally:
            self.release_busy(token)

    def initialize_model(self) -> bool:
        """
        Load model weights into GPU VRAM once.
        Thread-safe; sets state to READY on success.
        """
        with self._lock:
            if self.model_loaded and self.upsampler is not None:
                self.state = STATE_READY
                return True

            self.state = STATE_STARTING
            self.error_message = None
            logger.info(f"Initializing Persistent RealESRGANer ({self.model_name})...")

            if not self.weights_path.is_file():
                self.state = STATE_ERROR
                self.error_message = f"Weights file not found: {self.weights_path}"
                logger.error(self.error_message)
                return False

            try:
                # RRDBNet architecture strictly matches RealESRGAN_x4plus
                model = RRDBNet(
                    num_in_ch=3,
                    num_out_ch=3,
                    num_feat=64,
                    num_block=23,
                    num_grow_ch=32,
                    scale=self.scale
                )

                use_cuda = torch.cuda.is_available()
                half = (not self.fp32) and use_cuda

                self.upsampler = RealESRGANer(
                    scale=self.scale,
                    model_path=str(self.weights_path),
                    dni_weight=None,
                    model=model,
                    tile=self.default_tile,
                    tile_pad=self.default_tile_pad,
                    pre_pad=0,
                    half=half,
                    gpu_id=self.gpu_id if use_cuda else None
                )

                self.model_loaded = True
                self.state = STATE_READY
                self.current_tile = self.default_tile
                logger.info(
                    f"RealESRGANer successfully loaded in memory. "
                    f"CUDA: {use_cuda}, Half: {half}, Device: {self.upsampler.device}"
                )
                return True

            except Exception as e:
                self.state = STATE_ERROR
                self.error_message = f"Failed to initialize RealESRGANer: {e}"
                logger.exception(self.error_message)
                return False

    def choose_tile(
        self,
        width: int,
        height: int,
        requested_tile: Optional[Any] = None,
        requested_mode: str = "auto"
    ) -> int:
        """
        Select tile size automatically or honor requested tile.
        Auto mode selects optimal tile based on image size and available VRAM.
        """
        if requested_mode != "auto" and requested_tile is not None:
            try:
                val = int(requested_tile)
                if val in [0, 32, 64, 96, 128, 192, 256]:
                    return val
            except (ValueError, TypeError):
                pass

        # Auto tile selection
        gpu_info = get_gpu_info()
        free_gb = gpu_info.get("free_gb", 3.0)
        total_pixels = width * height

        # RTX 2050 4GB baseline logic
        if free_gb >= 2.5:
            if total_pixels <= 300 * 300:
                # Small icons / thumbnails
                return 256
            elif total_pixels <= 2_000_000:
                # Normal photos up to ~1400x1400: tile 256 gives maximum throughput
                return 256
            elif total_pixels <= 6_000_000:
                return 192
            else:
                return 128
        elif free_gb >= 1.5:
            if total_pixels <= 1_500_000:
                return 192
            else:
                return 128
        elif free_gb >= 0.8:
            return 96
        else:
            return 64

    def get_step_down_tile(self, current_tile: int) -> Optional[int]:
        """Return next smaller tile size for OOM recovery."""
        if current_tile in TILE_STEP_DOWN:
            return TILE_STEP_DOWN[current_tile]
        # If custom tile, find closest smaller standard candidate
        for cand in TILE_CANDIDATES:
            if cand < current_tile:
                return cand
        return None

    def export_image(
        self,
        output_cv: np.ndarray,
        target_path: Path,
        export_format: str = "auto",
        quality: int = 95
    ) -> Path:
        """
        Save output image with proper format conversion and quality control.
        Uses atomic writing (saves to .tmp file first, then os.replace)
        to prevent corrupted or partial output files on disk.
        """
        target_path = Path(target_path)
        ext = target_path.suffix.lower()

        is_rgba = (len(output_cv.shape) == 3 and output_cv.shape[2] == 4)

        # Temporary atomic destination
        temp_path = target_path.with_name(f"{target_path.stem}_{uuid.uuid4().hex[:6]}.tmp")

        try:
            if ext in [".jpg", ".jpeg"]:
                if is_rgba:
                    # Convert BGRA -> RGBA PIL
                    rgba_img = Image.fromarray(cv2.cvtColor(output_cv, cv2.COLOR_BGRA2RGBA))
                    # Create solid white background
                    bg = Image.new("RGB", rgba_img.size, (255, 255, 255))
                    bg.paste(rgba_img, mask=rgba_img.split()[3])
                    bg.save(str(temp_path), "JPEG", quality=quality, optimize=True)
                else:
                    rgb_img = Image.fromarray(cv2.cvtColor(output_cv, cv2.COLOR_BGR2RGB))
                    rgb_img.save(str(temp_path), "JPEG", quality=quality, optimize=True)

            elif ext == ".webp":
                if is_rgba:
                    rgba_img = Image.fromarray(cv2.cvtColor(output_cv, cv2.COLOR_BGRA2RGBA))
                    rgba_img.save(str(temp_path), "WEBP", quality=quality)
                else:
                    rgb_img = Image.fromarray(cv2.cvtColor(output_cv, cv2.COLOR_BGR2RGB))
                    rgb_img.save(str(temp_path), "WEBP", quality=quality)

            elif ext == ".png":
                if is_rgba:
                    rgba_img = Image.fromarray(cv2.cvtColor(output_cv, cv2.COLOR_BGRA2RGBA))
                    rgba_img.save(str(temp_path), "PNG", optimize=True)
                else:
                    rgb_img = Image.fromarray(cv2.cvtColor(output_cv, cv2.COLOR_BGR2RGB))
                    rgb_img.save(str(temp_path), "PNG", optimize=True)

            else:
                # Fallback to OpenCV write (BMP, etc.)
                is_success, buf = cv2.imencode(ext, output_cv)
                if is_success:
                    with open(temp_path, "wb") as f:
                        f.write(buf)
                else:
                    cv2.imwrite(str(temp_path), output_cv)

            if not temp_path.is_file() or temp_path.stat().st_size == 0:
                raise IOError(f"Failed to generate valid temporary output: {temp_path}")

            # Atomic rename / replace
            os.replace(temp_path, target_path)
            return target_path

        except Exception as e:
            if temp_path.is_file():
                try:
                    temp_path.unlink()
                except Exception:
                    pass
            raise e

    def process_image(
        self,
        input_path: Path,
        output_dir: Path,
        tile_mode: str = "auto",
        requested_tile: Optional[int] = None,
        export_format: str = "auto",
        quality: int = 95,
        job_id: Optional[str] = None,
        progress_callback: Optional[Any] = None,
        is_cancelled_func: Optional[Any] = None
    ) -> Dict[str, Any]:
        """
        Process an image in-memory using persistent RealESRGANer with Auto-Tile,
        automatic OOM recovery, and safe cancellation checkpoints.
        """
        with self._lock, self.busy_scope(task_type="image", task_id=job_id):
            if not self.model_loaded:
                if not self.initialize_model():
                    raise RuntimeError(f"AI Worker model initialization failed: {self.error_message}")

            self.current_job = {
                "id": job_id,
                "input_path": str(input_path),
                "start_time": time.time()
            }
            self.last_oom_recovered = False

            input_path = Path(input_path)
            output_dir = Path(output_dir)
            start_ts = time.time()

            try:
                # Cancellation Checkpoint 1: Before Image Load & Inference
                if is_cancelled_func and is_cancelled_func():
                    raise RuntimeError("Job cancelled before inference")

                # Unicode-safe image load
                img_cv = read_image_unicode(input_path)
                h, w = img_cv.shape[:2]

                # Determine tile size
                chosen_tile = self.choose_tile(
                    width=w,
                    height=h,
                    requested_tile=requested_tile,
                    requested_mode=tile_mode
                )
                self.current_tile = chosen_tile

                # Determine target extension
                inp_ext = input_path.suffix.lower()
                if export_format == "auto" or not export_format:
                    target_ext = inp_ext if inp_ext in [".png", ".jpg", ".jpeg", ".webp", ".bmp"] else ".png"
                elif export_format in ["jpg", "jpeg"]:
                    target_ext = ".jpg"
                elif export_format == "webp":
                    target_ext = ".webp"
                elif export_format == "png":
                    target_ext = ".png"
                else:
                    target_ext = f".{export_format.lower()}"

                # Generate unique non-colliding output path
                basename = input_path.stem
                out_path = get_unique_output_path(output_dir, basename, target_ext)

                # Cancellation Checkpoint 2: Right before GPU inference loop
                if is_cancelled_func and is_cancelled_func():
                    raise RuntimeError("Job cancelled before inference loop")

                # Inference with OOM Recovery Loop
                current_tile_to_try = chosen_tile
                output_cv = None
                oom_occurred = False

                while True:
                    try:
                        self.upsampler.tile_size = current_tile_to_try
                        if progress_callback:
                            progress_callback(10, f"Inference with tile {current_tile_to_try}...")

                        # Run enhancement
                        output_cv, _ = self.upsampler.enhance(img_cv, outscale=self.scale)
                        break

                    except (torch.cuda.OutOfMemoryError, RuntimeError) as e:
                        err_str = str(e).lower()
                        if "out of memory" in err_str or "cuda out of memory" in err_str:
                            oom_occurred = True
                            self.state = STATE_RECOVERING
                            self.last_oom_tile = current_tile_to_try
                            logger.warning(
                                f"CUDA OOM encountered on {input_path.name} with tile {current_tile_to_try}. "
                                f"Initiating recovery..."
                            )

                            # Purge VRAM
                            torch.cuda.empty_cache()
                            gc.collect()

                            # Step down tile size
                            next_tile = self.get_step_down_tile(current_tile_to_try)
                            if next_tile is None:
                                raise RuntimeError(
                                    f"CUDA Out Of Memory: unable to recover even with minimum tile size ({current_tile_to_try})."
                                )

                            logger.info(f"OOM Recovery: retrying with reduced tile {next_tile}")
                            if progress_callback:
                                progress_callback(15, f"OOM recovery: stepping down to tile {next_tile}...")

                            current_tile_to_try = next_tile
                            self.current_tile = next_tile
                            self.state = STATE_BUSY
                        else:
                            raise e

                # Cancellation Checkpoint 3: After inference, before disk export
                if is_cancelled_func and is_cancelled_func():
                    raise RuntimeError("Job cancelled after inference")

                # Export result atomically
                self.export_image(
                    output_cv=output_cv,
                    target_path=out_path,
                    export_format=export_format,
                    quality=quality
                )

                elapsed = round(time.time() - start_ts, 2)
                self.last_inference_time = elapsed
                self.total_inference_time += elapsed
                self.jobs_completed += 1
                self.last_oom_recovered = oom_occurred
                self.state = STATE_READY

                out_h, out_w = output_cv.shape[:2]
                out_size = out_path.stat().st_size if out_path.exists() else 0

                return {
                    "success": True,
                    "output_path": str(out_path),
                    "output_filename": out_path.name,
                    "input_dimensions": f"{w}x{h}",
                    "output_dimensions": f"{out_w}x{out_h}",
                    "output_size": out_size,
                    "elapsed_seconds": elapsed,
                    "tile_used": current_tile_to_try,
                    "oom_recovered": oom_occurred,
                    "format": target_ext.lstrip(".")
                }

            except Exception as ex:
                self.state = STATE_ERROR
                self.error_message = str(ex)
                logger.error(f"Error processing image {input_path.name}: {ex}")
                raise ex

            finally:
                self.current_job = None
                if self.state not in [STATE_ERROR, STATE_STOPPED]:
                    self.state = STATE_READY

    def generate_quick_preview(
        self,
        input_path: Path,
        thumbnails_dir: Path,
        patch_size: int = 256,
        format_type: str = "webp",
        quality: int = 90
    ) -> Dict[str, Any]:
        """
        Generate a quick preview by center-cropping a patch and running
        REAL RealESRGAN_x4plus inference.
        """
        with self._lock:
            if not self.model_loaded:
                if not self.initialize_model():
                    raise RuntimeError(f"AI Worker model initialization failed: {self.error_message}")

            # Check if currently busy with an active batch job or video
            with self._state_lock:
                if len(self._active_tokens) > 0 or self.state == STATE_BUSY:
                    return {
                        "success": False,
                        "error": "AI Worker đang bận xử lý hàng đợi. Vui lòng chờ một lát rồi thử lại preview."
                    }

            start_ts = time.time()
            preview_id = uuid.uuid4().hex[:8]

            with self.busy_scope(task_type="preview", task_id=preview_id):
                try:
                    input_path = Path(input_path)
                    thumbnails_dir = Path(thumbnails_dir)
                    thumbnails_dir.mkdir(parents=True, exist_ok=True)

                    img_cv = read_image_unicode(input_path)
                    h, w = img_cv.shape[:2]

                    # Determine crop window (center crop)
                    pw = min(patch_size, w)
                    ph = min(patch_size, h)
                    sx = max(0, (w - pw) // 2)
                    sy = max(0, (h - ph) // 2)

                    patch_orig = img_cv[sy:sy + ph, sx:sx + pw]

                    # Save original patch
                    orig_preview_name = f"preview_orig_{preview_id}.jpg"
                    orig_preview_path = thumbnails_dir / orig_preview_name
                    # Save as high quality JPG
                    if len(patch_orig.shape) == 3 and patch_orig.shape[2] == 4:
                        rgba = Image.fromarray(cv2.cvtColor(patch_orig, cv2.COLOR_BGRA2RGBA))
                        bg = Image.new("RGB", rgba.size, (255, 255, 255))
                        bg.paste(rgba, mask=rgba.split()[3])
                        bg.save(str(orig_preview_path), "JPEG", quality=90)
                    else:
                        rgb = Image.fromarray(cv2.cvtColor(patch_orig, cv2.COLOR_BGR2RGB))
                        rgb.save(str(orig_preview_path), "JPEG", quality=90)

                    # Inference on patch (safe tile = 256 or 0)
                    self.upsampler.tile_size = 256
                    patch_enhanced, _ = self.upsampler.enhance(patch_orig, outscale=self.scale)

                    # Save enhanced patch
                    ext = ".webp" if format_type == "webp" else ".jpg"
                    enh_preview_name = f"preview_4x_{preview_id}{ext}"
                    enh_preview_path = thumbnails_dir / enh_preview_name

                    self.export_image(
                        output_cv=patch_enhanced,
                        target_path=enh_preview_path,
                        export_format=format_type,
                        quality=quality
                    )

                    elapsed = round(time.time() - start_ts, 2)
                    enh_h, enh_w = patch_enhanced.shape[:2]

                    return {
                        "success": True,
                        "preview_id": preview_id,
                        "orig_url": f"/thumbnails/{orig_preview_name}",
                        "enhanced_url": f"/thumbnails/{enh_preview_name}",
                        "crop_box": {"x": sx, "y": sy, "w": pw, "h": ph},
                        "enhanced_dimensions": f"{enh_w}x{enh_h}",
                        "elapsed_seconds": elapsed,
                        "patch_size": patch_size
                    }

                except Exception as e:
                    self.state = STATE_ERROR
                    self.error_message = str(e)
                    logger.error(f"Quick preview error: {e}")
                    return {
                        "success": False,
                        "error": f"Lỗi tạo Quick Preview: {e}"
                    }

    def stop(self):
        """Release GPU resources and set state to STOPPED."""
        with self._lock:
            logger.info("Stopping AI Worker and releasing GPU memory...")
            self.state = STATE_STOPPED
            self.upsampler = None
            self.model_loaded = False
            torch.cuda.empty_cache()
            gc.collect()
            logger.info("AI Worker stopped successfully.")

    def get_status(self) -> Dict[str, Any]:
        """Return current status of AI Worker and GPU VRAM."""
        gpu_info = get_gpu_info()
        with self._state_lock:
            active_count = len(self._active_tokens)
        return {
            "state": self.state,
            "active_tasks": active_count,
            "model_name": self.model_name,
            "model_loaded": self.model_loaded,
            "scale": self.scale,
            "current_tile": self.current_tile,
            "current_job": self.current_job,
            "last_oom_recovered": self.last_oom_recovered,
            "last_oom_tile": self.last_oom_tile,
            "last_inference_time": self.last_inference_time,
            "total_inference_time": round(self.total_inference_time, 2),
            "jobs_completed": self.jobs_completed,
            "error_message": self.error_message,
            "gpu": gpu_info
        }


# Global singleton helper
_global_worker: Optional[AIWorker] = None
_global_supervisor: Optional["SubprocessWorkerSupervisor"] = None


def run_inference_worker(task_queue, result_queue, cancel_event, base_dir=None):
    """
    Subprocess worker entry point (PID B).
    Exclusively initializes PyTorch and CUDA in an isolated process.
    Reads tasks from task_queue, reports progress/results via result_queue,
    and checks cancel_event.
    """
    try:
        worker = AIWorker(base_dir=Path(base_dir) if base_dir else None)
        if not worker.initialize_model():
            result_queue.put({"type": "init_error", "error": worker.error_message})
            return

        result_queue.put({"type": "ready", "pid": os.getpid()})

        while True:
            try:
                task = task_queue.get()
            except Exception:
                break

            if task is None or task.get("type") == "shutdown":
                worker.stop()
                break

            task_type = task.get("type", "image")

            if task_type == "image":
                job_id = task.get("job_id")
                input_path = Path(task["input_path"])
                output_dir = Path(task["output_dir"])
                tile_mode = task.get("tile_mode", "auto")
                requested_tile = task.get("requested_tile")
                export_format = task.get("export_format", "auto")
                quality = task.get("quality", 95)

                def progress_cb(pct, detail):
                    result_queue.put({
                        "type": "progress",
                        "job_id": job_id,
                        "progress_pct": pct,
                        "detail": detail
                    })

                def is_cancelled():
                    return cancel_event.is_set()

                try:
                    res = worker.process_image(
                        input_path=input_path,
                        output_dir=output_dir,
                        tile_mode=tile_mode,
                        requested_tile=requested_tile,
                        export_format=export_format,
                        quality=quality,
                        job_id=job_id,
                        progress_callback=progress_cb,
                        is_cancelled_func=is_cancelled
                    )
                    result_queue.put({
                        "type": "complete",
                        "job_id": job_id,
                        "result": res
                    })
                except Exception as e:
                    is_oom = "out of memory" in str(e).lower()
                    result_queue.put({
                        "type": "error",
                        "job_id": job_id,
                        "error": str(e),
                        "is_oom": is_oom
                    })

            elif task_type == "preview":
                preview_id = task.get("preview_id")
                input_path = Path(task["input_path"])
                thumbnails_dir = Path(task["thumbnails_dir"])
                patch_size = task.get("patch_size", 256)
                format_type = task.get("format_type", "webp")
                quality = task.get("quality", 90)

                try:
                    res = worker.generate_quick_preview(
                        input_path=input_path,
                        thumbnails_dir=thumbnails_dir,
                        patch_size=patch_size,
                        format_type=format_type,
                        quality=quality
                    )
                    result_queue.put({
                        "type": "preview_complete",
                        "preview_id": preview_id,
                        "result": res
                    })
                except Exception as e:
                    result_queue.put({
                        "type": "preview_error",
                        "preview_id": preview_id,
                        "error": str(e)
                    })

            elif task_type == "status":
                try:
                    st = worker.get_status()
                    result_queue.put({
                        "type": "status_reply",
                        "status": st
                    })
                except Exception as e:
                    result_queue.put({
                        "type": "status_reply",
                        "status": {"state": "ERROR", "error": str(e)}
                    })

    except Exception as fatal_e:
        logger.error(f"Fatal unhandled exception in inference worker process: {fatal_e}", exc_info=True)
        try:
            result_queue.put({"type": "fatal_crash", "error": str(fatal_e)})
        except Exception:
            pass


class SubprocessWorkerSupervisor:
    """
    Supervises an isolated child worker process for RealESRGAN inference.
    - Spawns child using multiprocessing with spawn context.
    - Child process owns CUDA and PyTorch exclusively.
    - Communicates via task_queue and result_queue.
    - Detects unrecoverable CUDA faults / non-zero exits.
    - Cleans up dangling .tmp files on crash or cancellation.
    - Automatically respawns a clean child process without server restart.
    """
    _instance = None
    _init_lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._init_lock:
            if cls._instance is None:
                cls._instance = super(SubprocessWorkerSupervisor, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, base_dir: Optional[Path] = None):
        if getattr(self, "_initialized", False):
            return
        self.base_dir = Path(base_dir) if base_dir else Path(__file__).resolve().parent
        self._lock = threading.RLock()
        self._mp_ctx = multiprocessing.get_context("spawn")
        self._task_queue = None
        self._result_queue = None
        self._cancel_event = None
        self._process = None
        self.worker_pid = None
        self.state = STATE_STOPPED
        self._initialized = True
        self._start_child_process()

    def _start_child_process(self):
        with self._lock:
            self._task_queue = self._mp_ctx.Queue()
            self._result_queue = self._mp_ctx.Queue()
            self._cancel_event = self._mp_ctx.Event()
            self.state = STATE_STARTING
            self._process = self._mp_ctx.Process(
                target=run_inference_worker,
                args=(self._task_queue, self._result_queue, self._cancel_event, str(self.base_dir)),
                daemon=True
            )
            self._process.start()
            self.worker_pid = self._process.pid
            logger.info(f"Spawned inference worker subprocess (PID {self.worker_pid})")

            # Wait for ready signal (timeout 45s for model loading)
            try:
                msg = self._result_queue.get(timeout=45.0)
                if msg.get("type") == "ready":
                    self.state = STATE_READY
                    logger.info(f"Inference worker subprocess (PID {self.worker_pid}) is READY.")
                else:
                    self.state = STATE_ERROR
                    logger.error(f"Worker subprocess initialization error: {msg}")
            except Exception as e:
                self.state = STATE_ERROR
                logger.error(f"Timeout or failure waiting for worker subprocess: {e}")

    def _ensure_alive(self):
        with self._lock:
            if self._process is None or not self._process.is_alive():
                logger.warning("Worker subprocess not alive; restarting fresh child...")
                self._terminate_child()
                self._start_child_process()

    def _terminate_child(self):
        with self._lock:
            if self._process:
                try:
                    if self._process.is_alive():
                        self._process.terminate()
                        self._process.join(timeout=2.0)
                        if self._process.is_alive():
                            self._process.kill()
                except Exception as e:
                    logger.warning(f"Error terminating child process: {e}")
                self._process = None
                self.worker_pid = None
                self.state = STATE_STOPPED

    def _clean_orphan_tmp(self, output_dir: Path, basename: str):
        try:
            for f in Path(output_dir).glob(f"{basename}*.tmp"):
                try:
                    f.unlink(missing_ok=True)
                except Exception:
                    pass
        except Exception:
            pass

    def process_image(
        self,
        input_path: Path,
        output_dir: Path,
        tile_mode: str = "auto",
        requested_tile: Optional[int] = None,
        export_format: str = "auto",
        quality: int = 95,
        job_id: Optional[str] = None,
        progress_callback: Optional[Any] = None,
        is_cancelled_func: Optional[Any] = None
    ) -> Dict[str, Any]:
        with self._lock:
            self._ensure_alive()
            self.state = STATE_BUSY
            self._cancel_event.clear()

            input_path = Path(input_path)
            output_dir = Path(output_dir)

            task = {
                "type": "image",
                "job_id": job_id,
                "input_path": str(input_path),
                "output_dir": str(output_dir),
                "tile_mode": tile_mode,
                "requested_tile": requested_tile,
                "export_format": export_format,
                "quality": quality,
            }
            self._task_queue.put(task)

            try:
                while True:
                    # Check cancellation
                    if is_cancelled_func and is_cancelled_func():
                        self._cancel_event.set()
                        cancel_start = time.time()
                        cancelled_cleanly = False
                        while time.time() - cancel_start < 3.0:
                            if not self._process.is_alive():
                                break
                            try:
                                msg = self._result_queue.get(timeout=0.2)
                                if msg.get("type") == "error" and "cancel" in str(msg.get("error", "")).lower():
                                    cancelled_cleanly = True
                                    break
                            except queue.Empty:
                                pass
                        if not cancelled_cleanly and self._process.is_alive():
                            logger.warning("Worker did not exit within cancellation deadline; forcefully terminating.")
                            self._terminate_child()
                            self._clean_orphan_tmp(output_dir, input_path.stem)
                            self._start_child_process()
                        self._clean_orphan_tmp(output_dir, input_path.stem)
                        raise RuntimeError("Job cancelled by user request")

                    # Check process survival (detect Class 2/3 unrecoverable CUDA panic)
                    if self._process and not self._process.is_alive():
                        exitcode = self._process.exitcode
                        logger.error(f"Inference child process crashed with exitcode {exitcode}!")
                        self._clean_orphan_tmp(output_dir, input_path.stem)
                        self._terminate_child()
                        self._start_child_process()
                        raise RuntimeError(
                            f"Inference worker process crashed unexpectedly (exitcode {exitcode}). CUDA context reset."
                        )

                    # Poll result queue
                    try:
                        msg = self._result_queue.get(timeout=0.2)
                    except queue.Empty:
                        continue

                    msg_type = msg.get("type")
                    if msg_type == "progress":
                        if progress_callback:
                            progress_callback(msg.get("progress_pct", 0), msg.get("detail", ""))
                    elif msg_type == "complete":
                        self.state = STATE_READY
                        return msg["result"]
                    elif msg_type == "error":
                        self.state = STATE_READY
                        err = msg.get("error", "Unknown inference error")
                        raise RuntimeError(err)

            finally:
                if self.state != STATE_ERROR:
                    self.state = STATE_READY

    def generate_quick_preview(
        self,
        input_path: Path,
        thumbnails_dir: Path,
        patch_size: int = 256,
        format_type: str = "webp",
        quality: int = 90
    ) -> Dict[str, Any]:
        with self._lock:
            self._ensure_alive()
            preview_id = uuid.uuid4().hex[:8]
            task = {
                "type": "preview",
                "preview_id": preview_id,
                "input_path": str(input_path),
                "thumbnails_dir": str(thumbnails_dir),
                "patch_size": patch_size,
                "format_type": format_type,
                "quality": quality
            }
            self._task_queue.put(task)

            while True:
                if self._process and not self._process.is_alive():
                    exitcode = self._process.exitcode
                    self._terminate_child()
                    self._start_child_process()
                    return {"success": False, "error": f"Worker crashed during preview (exitcode {exitcode})"}

                try:
                    msg = self._result_queue.get(timeout=0.2)
                except queue.Empty:
                    continue

                if msg.get("type") == "preview_complete":
                    return msg["result"]
                elif msg.get("type") == "preview_error":
                    return {"success": False, "error": msg.get("error")}

    def stop(self):
        with self._lock:
            if self._task_queue and self._process and self._process.is_alive():
                try:
                    self._task_queue.put({"type": "shutdown"})
                    self._process.join(timeout=3.0)
                except Exception:
                    pass
            self._terminate_child()

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            alive = bool(self._process and self._process.is_alive())
            return {
                "state": self.state if alive else STATE_STOPPED,
                "worker_pid": self.worker_pid if alive else None,
                "is_alive": alive,
                "supervisor": True
            }


def get_supervisor_worker(base_dir: Optional[Path] = None) -> SubprocessWorkerSupervisor:
    """Get or initialize singleton SubprocessWorkerSupervisor instance."""
    global _global_supervisor
    if _global_supervisor is None:
        _global_supervisor = SubprocessWorkerSupervisor(base_dir=base_dir)
    return _global_supervisor


def get_ai_worker(base_dir: Optional[Path] = None, use_subprocess: bool = False, **kwargs) -> Any:
    """Get or initialize AIWorker instance (or SubprocessWorkerSupervisor if use_subprocess=True)."""
    if use_subprocess:
        return get_supervisor_worker(base_dir=base_dir)
    global _global_worker
    if _global_worker is None:
        _global_worker = AIWorker(base_dir=base_dir, **kwargs)
    return _global_worker
