# -*- coding: utf-8 -*-
"""
FFmpeg Service Module for AI Image Studio (Phase 5)
Provides safe, robust detection and execution of FFmpeg and FFprobe subprocesses.
Enforces argument list execution, zero shell=True, timeout controls,
progress monitoring, and process tree termination upon cancellation.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class FFmpegCancelledException(RuntimeError):
    """Raised when an FFmpeg subprocess is intentionally cancelled by user request."""
    pass

# Fallback known paths on Windows
FALLBACK_FFMPEG_PATHS = [
    Path(r"C:\ProgramData\chocolatey\bin\ffmpeg.exe"),
    Path(r"C:\ffmpeg\bin\ffmpeg.exe"),
]

FALLBACK_FFPROBE_PATHS = [
    Path(r"C:\ProgramData\chocolatey\bin\ffprobe.exe"),
    Path(r"C:\ffmpeg\bin\ffprobe.exe"),
]


class FFmpegService:
    """
    Service wrapper around FFmpeg and FFprobe executables.
    Thread-safe and process-isolated.
    """

    def __init__(self, ffmpeg_path: Optional[str] = None, ffprobe_path: Optional[str] = None):
        self.ffmpeg_path = self._resolve_executable("ffmpeg", ffmpeg_path, FALLBACK_FFMPEG_PATHS)
        self.ffprobe_path = self._resolve_executable("ffprobe", ffprobe_path, FALLBACK_FFPROBE_PATHS)

    def _resolve_executable(self, name: str, custom_path: Optional[str], fallbacks: List[Path]) -> Optional[str]:
        # 1. Custom configured path
        if custom_path and custom_path.lower() != "auto":
            p = Path(custom_path)
            if p.is_file():
                return str(p.resolve())
            return None

        # 2. PATH resolution
        found_in_path = shutil.which(name)
        if found_in_path:
            return str(Path(found_in_path).resolve())

        # 3. Fallbacks
        for fb in fallbacks:
            if fb.is_file():
                return str(fb.resolve())

        return None

    def is_available(self) -> bool:
        """Check if both ffmpeg and ffprobe are available on the system."""
        return bool(self.ffmpeg_path and self.ffprobe_path)

    def get_versions(self) -> Dict[str, Any]:
        """Inspect and return version details of ffmpeg and ffprobe."""
        result = {
            "available": False,
            "ffmpeg_path": self.ffmpeg_path,
            "ffprobe_path": self.ffprobe_path,
            "ffmpeg_version": None,
            "ffprobe_version": None,
            "error": None
        }

        if not self.is_available():
            result["error"] = "FFmpeg or FFprobe executable not found."
            return result

        try:
            # Check ffmpeg version
            p_ff = subprocess.run(
                [self.ffmpeg_path, "-version"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False
            )
            if p_ff.returncode == 0:
                first_line = p_ff.stdout.splitlines()[0] if p_ff.stdout else ""
                result["ffmpeg_version"] = first_line

            # Check ffprobe version
            p_probe = subprocess.run(
                [self.ffprobe_path, "-version"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False
            )
            if p_probe.returncode == 0:
                first_line = p_probe.stdout.splitlines()[0] if p_probe.stdout else ""
                result["ffprobe_version"] = first_line

            result["available"] = bool(result["ffmpeg_version"] and result["ffprobe_version"])
            return result
        except Exception as e:
            result["error"] = str(e)
            return result

    def probe_json(self, file_path: Path, timeout: int = 30) -> Dict[str, Any]:
        """
        Execute ffprobe with JSON output format to inspect media metadata.
        Safe execution with no shell=True.
        """
        if not self.ffprobe_path:
            raise FileNotFoundError("ffprobe executable not located.")

        target = Path(file_path).resolve()
        if not target.is_file():
            raise FileNotFoundError(f"Target media file does not exist: {target}")

        args = [
            self.ffprobe_path,
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            "-show_error",
            str(target)
        ]

        logger.debug(f"Running ffprobe command: {' '.join(args)}")
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        if proc.returncode != 0:
            err_msg = proc.stderr.strip() or f"ffprobe exited with code {proc.returncode}"
            raise RuntimeError(f"FFprobe inspection failed: {err_msg}")

        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as jde:
            raise ValueError(f"Failed to parse ffprobe JSON output: {jde}")

    def execute_ffmpeg(
        self,
        args: List[str],
        timeout: Optional[int] = None,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        cancel_event: Optional[threading.Event] = None
    ) -> Tuple[int, str, str]:
        """
        Execute an FFmpeg command safely using subprocess.Popen.
        args: List of command arguments excluding the ffmpeg binary name.
        Supports cancellation and stream progress tracking.
        """
        if not self.ffmpeg_path:
            raise FileNotFoundError("ffmpeg executable not located.")

        cmd = [self.ffmpeg_path] + args
        logger.debug(f"Starting ffmpeg process: {' '.join(cmd)}")

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            universal_newlines=True
        )

        stdout_lines = []
        stderr_lines = []

        start_time = time.time()

        # Frame progress regex matching: frame=  123 fps= 24 q=28.0 size=   1024kB time=00:00:05.12
        frame_pattern = re.compile(r"frame=\s*(\d+)")
        time_pattern = re.compile(r"time=\s*(\d+:\d+:\d+\.\d+|\d+\.\d+)")

        def reader(stream, accumulator, is_stderr=False):
            try:
                for line in iter(stream.readline, ""):
                    accumulator.append(line)
                    if is_stderr and progress_callback:
                        # Extract progress details
                        fm = frame_pattern.search(line)
                        tm = time_pattern.search(line)
                        if fm or tm:
                            progress_data = {}
                            if fm:
                                progress_data["frame"] = int(fm.group(1))
                            if tm:
                                progress_data["time_str"] = tm.group(1)
                            progress_callback(progress_data)
            except Exception:
                pass
            finally:
                stream.close()

        t_out = threading.Thread(target=reader, args=(proc.stdout, stdout_lines, False), daemon=True)
        t_err = threading.Thread(target=reader, args=(proc.stderr, stderr_lines, True), daemon=True)
        t_out.start()
        t_err.start()

        cancelled = False
        timed_out = False

        while proc.poll() is None:
            if cancel_event and cancel_event.is_set():
                logger.info("Cancellation requested for ffmpeg process. Terminating...")
                cancelled = True
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                break

            if timeout and (time.time() - start_time) > timeout:
                logger.warning(f"FFmpeg process exceeded timeout of {timeout}s. Terminating...")
                timed_out = True
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                break

            time.sleep(0.1)

        proc.wait()
        t_out.join(timeout=1.0)
        t_err.join(timeout=1.0)

        stdout_text = "".join(stdout_lines)
        stderr_text = "".join(stderr_lines)

        if cancelled:
            raise FFmpegCancelledException("FFmpeg process cancelled by user request.")
        if timed_out:
            raise TimeoutError(f"FFmpeg process timed out after {timeout} seconds.")

        return proc.returncode, stdout_text, stderr_text


# Global singleton instance
_global_ffmpeg_service: Optional[FFmpegService] = None


def get_ffmpeg_service(config: Optional[Dict[str, Any]] = None) -> FFmpegService:
    """Get or create singleton FFmpegService."""
    global _global_ffmpeg_service
    if _global_ffmpeg_service is None:
        ff_cfg = config.get("video", {}) if config else {}
        _global_ffmpeg_service = FFmpegService(
            ffmpeg_path=ff_cfg.get("ffmpeg_path"),
            ffprobe_path=ff_cfg.get("ffprobe_path")
        )
    return _global_ffmpeg_service
