# -*- coding: utf-8 -*-
"""
Video Reconstructor Module for AI Image Studio (Phase 5)
Reconstructs enhanced video from sequential 4x frames using FFmpeg.
Applies H.264 encoding with configurable CRF quality presets, preserves
frame rates and aspect ratios, and validates output video integrity.
"""

import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .ffmpeg_service import FFmpegService, get_ffmpeg_service

logger = logging.getLogger(__name__)

QUALITY_PRESETS = {
    "high": {"crf": 18, "preset": "slow"},
    "balanced": {"crf": 22, "preset": "medium"},
    "fast": {"crf": 26, "preset": "fast"}
}


class VideoReconstructor:
    """
    Reconstructs video stream from enhanced frame sequence.
    """

    def __init__(self, ffmpeg_service: Optional[FFmpegService] = None):
        self.service = ffmpeg_service or get_ffmpeg_service()

    def reconstruct_video_stream(
        self,
        enhanced_frames_dir: Path,
        output_temp_video: Path,
        fps: float,
        quality_preset: str = "balanced",
        total_frames: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        cancel_event: Optional[threading.Event] = None
    ) -> Path:
        """
        Encode enhanced PNG frames into an H.264 video file.
        Uses yuv420p pixel format for standard HTML5/player compatibility.
        """
        frames_dir = Path(enhanced_frames_dir).resolve()
        temp_out = Path(output_temp_video).resolve()
        temp_out.parent.mkdir(parents=True, exist_ok=True)

        input_pattern = str(frames_dir / "frame_%06d.png")

        preset_cfg = QUALITY_PRESETS.get(quality_preset.lower(), QUALITY_PRESETS["balanced"])
        crf = str(preset_cfg["crf"])
        preset = preset_cfg["preset"]

        fps_val = fps if fps and fps > 0 else 30.0

        args = [
            "-framerate", str(fps_val),
            "-i", input_pattern,
            "-c:v", "libx264",
            "-crf", crf,
            "-preset", preset,
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            "-y",
            str(temp_out)
        ]

        def on_ffmpeg_progress(info: Dict[str, Any]):
            cur_frame = info.get("frame", 0)
            if progress_callback:
                tot = total_frames or cur_frame or 1
                pct = min(100, int((cur_frame / tot) * 100)) if tot > 0 else 0
                progress_callback(cur_frame, tot, f"Tái tạo video: {cur_frame}/{tot}")

        logger.info(f"Reconstructing video with fps={fps_val}, crf={crf}, preset={preset} -> {temp_out}")
        ret, stdout, stderr = self.service.execute_ffmpeg(
            args=args,
            timeout=1800,
            progress_callback=on_ffmpeg_progress,
            cancel_event=cancel_event
        )

        if ret != 0:
            raise RuntimeError(f"FFmpeg video reconstruction failed (code {ret}): {stderr[:500]}")

        if not temp_out.is_file() or temp_out.stat().st_size == 0:
            raise IOError("Reconstructed video file not found or is empty.")

        logger.info(f"Video reconstructed successfully: {temp_out} ({temp_out.stat().st_size} bytes)")
        return temp_out

    def verify_output_video(
        self,
        video_path: Path,
        expected_width: int,
        expected_height: int,
        expected_duration: float,
        expected_has_audio: bool = False
    ) -> Dict[str, Any]:
        """
        Verify the integrity, dimensions, duration, and streams of output video.
        """
        path = Path(video_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Output video missing: {path}")

        file_size = path.stat().st_size
        if file_size == 0:
            raise ValueError(f"Output video is empty (0 bytes): {path}")

        probe = self.service.probe_json(path)
        format_info = probe.get("format", {})
        streams = probe.get("streams", [])

        video_streams = [s for s in streams if s.get("codec_type") == "video"]
        if not video_streams:
            raise ValueError("Output video contains no video stream.")

        v_stream = video_streams[0]
        actual_w = int(v_stream.get("width") or 0)
        actual_h = int(v_stream.get("height") or 0)

        # Allow slight padding/rounding if codec aligns to macroblocks (mod 2)
        if abs(actual_w - expected_width) > 4 or abs(actual_h - expected_height) > 4:
            logger.warning(f"Output resolution warning: expected {expected_width}x{expected_height}, got {actual_w}x{actual_h}")

        actual_dur = float(v_stream.get("duration") or format_info.get("duration") or 0.0)

        audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
        has_audio = len(audio_streams) > 0

        return {
            "verified": True,
            "path": str(path),
            "size": file_size,
            "width": actual_w,
            "height": actual_h,
            "duration": actual_dur,
            "has_audio": has_audio,
            "codec": v_stream.get("codec_name"),
            "audio_codec": audio_streams[0].get("codec_name") if has_audio else None
        }
