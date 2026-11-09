# -*- coding: utf-8 -*-
"""
Per-Job Logging Module for AI Image Studio (Phase 3)
Provides dedicated, structured, thread-safe logging for individual processing jobs.
Logs are stored in logs/jobs/<job_id>.log with timestamps, steps, GPU metrics,
retry attempts, OOM recoveries, and error traces.
"""

import os
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional


class JobLogger:
    """
    Manages isolated per-job log files in logs/jobs/<job_id>.log.
    Thread-safe and guarded against infinite log expansion.
    """

    MAX_LOG_SIZE_BYTES = 512 * 1024  # 512 KB per job log cap

    def __init__(self, log_dir: Optional[Path] = None):
        if log_dir is None:
            self.log_dir = Path(__file__).resolve().parent / "logs" / "jobs"
        else:
            self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _get_log_path(self, job_id: str) -> Path:
        # Sanitize job_id to prevent path traversal
        clean_id = "".join(c for c in str(job_id) if c.isalnum() or c in ("-", "_"))
        return self.log_dir / f"{clean_id}.log"

    def _append(self, job_id: str, tag: str, message: str):
        log_file = self._get_log_path(job_id)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        line = f"[{ts}] [{tag.upper()}] {message}\n"

        with self._lock:
            try:
                # Size cap check
                if log_file.is_file() and log_file.stat().st_size > self.MAX_LOG_SIZE_BYTES:
                    return

                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(line)
            except Exception:
                pass

    def start_job(
        self,
        job_id: str,
        filename: str,
        input_path: str,
        model_name: str = "RealESRGAN_x4plus",
        tile_size: int = 128,
        tile_mode: str = "auto",
        export_format: str = "auto",
        quality: int = 95
    ):
        msg = (
            f"Starting Job | File: {filename} | Input: {input_path} | "
            f"Model: {model_name} | TileMode: {tile_mode} | Tile: {tile_size} | "
            f"Format: {export_format} | Quality: {quality}%"
        )
        self._append(job_id, "START", msg)

    def log_step(self, job_id: str, step_name: str, detail: str = ""):
        msg = f"Step: {step_name}" + (f" | Detail: {detail}" if detail else "")
        self._append(job_id, "STEP", msg)

    def log_progress(self, job_id: str, pct: int, detail: str = ""):
        msg = f"Progress: {pct}%" + (f" | {detail}" if detail else "")
        self._append(job_id, "PROGRESS", msg)

    def log_gpu_info(self, job_id: str, gpu_name: str, used_vram_gb: float, total_vram_gb: float):
        msg = f"GPU: {gpu_name} | VRAM: {used_vram_gb:.2f} / {total_vram_gb:.2f} GB"
        self._append(job_id, "GPU", msg)

    def log_retry(self, job_id: str, attempt: int, max_retries: int, reason: str):
        msg = f"Retry scheduled (Attempt {attempt}/{max_retries}) | Reason: {reason}"
        self._append(job_id, "RETRY", msg)

    def log_oom_recovery(self, job_id: str, from_tile: int, to_tile: int):
        msg = f"CUDA OOM detected with tile {from_tile} -> Stepping down to tile {to_tile}"
        self._append(job_id, "OOM_RECOVERY", msg)

    def log_complete(
        self,
        job_id: str,
        elapsed_seconds: float,
        output_path: str,
        dimensions: str = "",
        size_bytes: int = 0,
        tile_used: Optional[int] = None
    ):
        msg = (
            f"Job Completed Successfully in {elapsed_seconds:.2f}s | "
            f"Output: {output_path} | Dimensions: {dimensions} | "
            f"Size: {size_bytes:,} bytes" + (f" | Tile Used: {tile_used}" if tile_used else "")
        )
        self._append(job_id, "COMPLETE", msg)

    def log_error(self, job_id: str, error_msg: str, exc: Optional[Exception] = None):
        msg = f"Error: {error_msg}"
        if exc is not None:
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            msg += f"\nStack Trace:\n{tb}"
        self._append(job_id, "ERROR", msg)

    def log_cancelled(self, job_id: str, reason: str = "User requested cancellation"):
        msg = f"Job Cancelled | Reason: {reason}"
        self._append(job_id, "CANCELLED", msg)

    def get_job_log(self, job_id: str, max_chars: int = 50000) -> str:
        """Read job log from disk safely."""
        log_file = self._get_log_path(job_id)
        if not log_file.is_file():
            return f"[No per-job log found for job {job_id}]"

        try:
            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
                if len(content) > max_chars:
                    return content[-max_chars:]
                return content
        except Exception as e:
            return f"[Error reading job log: {e}]"


# Global singleton instance
_global_job_logger: Optional[JobLogger] = None


def get_job_logger(
    base_dir: Optional[Path] = None,
    config: Optional[dict] = None,
    log_dir: Optional[Path] = None
) -> JobLogger:
    global _global_job_logger
    if _global_job_logger is None:
        target_dir = log_dir
        if target_dir is None and config and "reliability" in config and "per_job_log_dir" in config["reliability"]:
            target_dir = (base_dir or Path(".")).resolve() / config["reliability"]["per_job_log_dir"]
        _global_job_logger = JobLogger(log_dir=target_dir)
    return _global_job_logger
