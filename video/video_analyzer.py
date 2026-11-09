# -*- coding: utf-8 -*-
"""
Video Analyzer Module for AI Image Studio (Phase 5)
Extracts and validates comprehensive video stream metadata via FFprobe.
Detects CFR/VFR, color characteristics, multi-audio streams, and estimates
temporary storage requirements for frame extraction and 4x upscaling.
"""

import logging
import math
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .ffmpeg_service import FFmpegService, get_ffmpeg_service

logger = logging.getLogger(__name__)


def _parse_fps(fps_str: Optional[str]) -> float:
    """Parse fractional frame rate string like '30/1' or '24000/1001' to float."""
    if not fps_str or fps_str == "0/0":
        return 0.0
    try:
        if "/" in fps_str:
            num, den = fps_str.split("/", 1)
            den_val = float(den)
            return float(num) / den_val if den_val != 0 else 0.0
        return float(fps_str)
    except Exception:
        return 0.0


class VideoAnalyzer:
    """
    Analyzes video files, validates technical limits, and calculates storage budgets.
    """

    SUPPORTED_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm"}

    def __init__(self, ffmpeg_service: Optional[FFmpegService] = None, config: Optional[Dict[str, Any]] = None):
        self.service = ffmpeg_service or get_ffmpeg_service(config)
        cfg = config or {}
        v_cfg = cfg.get("video", {})
        sec_cfg = cfg.get("security", {})

        self.max_size_mb = float(v_cfg.get("max_video_size_mb", 2048))
        self.max_duration = float(v_cfg.get("max_duration_seconds", 3600))
        res_limit = v_cfg.get("max_source_resolution", [3840, 2160])
        self.max_width = int(res_limit[0]) if len(res_limit) > 0 else 3840
        self.max_height = int(res_limit[1]) if len(res_limit) > 1 else 2160

    def analyze(self, file_path: Path) -> Dict[str, Any]:
        """
        Deep technical inspection of media file.
        Returns dictionary containing video metadata, audio metadata, validation status,
        and temporary storage requirements.
        """
        path = Path(file_path).resolve()

        if not path.is_file():
            raise FileNotFoundError(f"Video file not found: {path}")

        file_size = path.stat().st_size
        if file_size == 0:
            raise ValueError("Video file is empty (0 bytes).")

        ext = path.suffix.lower()
        if ext not in self.SUPPORTED_EXTENSIONS:
            raise ValueError(f"Unsupported container extension '{ext}'. Allowed: {sorted(list(self.SUPPORTED_EXTENSIONS))}")

        # Run ffprobe
        probe_data = self.service.probe_json(path)
        format_info = probe_data.get("format", {})
        streams = probe_data.get("streams", [])

        video_streams = [s for s in streams if s.get("codec_type") == "video"]
        audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

        if not video_streams:
            raise ValueError("No video stream found in the uploaded file.")

        v_stream = video_streams[0]

        # Resolution
        width = int(v_stream.get("width") or 0)
        height = int(v_stream.get("height") or 0)
        if width <= 0 or height <= 0:
            raise ValueError(f"Invalid video dimensions: {width}x{height}")

        # Frame rates & CFR / VFR
        r_fps = _parse_fps(v_stream.get("r_frame_rate"))
        avg_fps = _parse_fps(v_stream.get("avg_frame_rate"))
        fps = r_fps if r_fps > 0 else (avg_fps if avg_fps > 0 else 30.0)

        # Duration
        dur_val = v_stream.get("duration") or format_info.get("duration")
        try:
            duration = float(dur_val) if dur_val else 0.0
        except (ValueError, TypeError):
            duration = 0.0

        # Total frames
        nb_frames_str = v_stream.get("nb_frames")
        try:
            total_frames = int(nb_frames_str) if nb_frames_str and nb_frames_str.isdigit() else 0
        except Exception:
            total_frames = 0

        if total_frames == 0 and duration > 0 and fps > 0:
            total_frames = int(math.ceil(duration * fps))

        # Detect Variable Frame Rate (VFR) vs Constant Frame Rate (CFR)
        is_vfr = False
        if r_fps > 0 and avg_fps > 0 and abs(r_fps - avg_fps) > 0.05:
            is_vfr = True

        # Rotation metadata
        rotation = 0
        side_data = v_stream.get("side_data_list", [])
        for sd in side_data:
            if "rotation" in sd:
                try:
                    rotation = int(sd["rotation"])
                except Exception:
                    pass
        tags = v_stream.get("tags", {})
        if "rotate" in tags:
            try:
                rotation = int(tags["rotate"])
            except Exception:
                pass

        # Audio streams analysis
        audio_list = []
        for a in audio_streams:
            audio_list.append({
                "index": a.get("index"),
                "codec_name": a.get("codec_name"),
                "sample_rate": int(a.get("sample_rate") or 0),
                "channels": int(a.get("channels") or 0),
                "channel_layout": a.get("channel_layout", "stereo"),
                "bit_rate": int(a.get("bit_rate") or 0)
            })

        has_audio = len(audio_list) > 0

        # Validation checks
        size_mb = file_size / (1024 * 1024)
        if size_mb > self.max_size_mb:
            raise ValueError(f"Video file size ({size_mb:.1f} MB) exceeds maximum limit ({self.max_size_mb:.0f} MB).")

        if duration > self.max_duration:
            raise ValueError(f"Video duration ({duration:.1f}s) exceeds maximum allowed limit ({self.max_duration:.0f}s).")

        if width > self.max_width or height > self.max_height:
            raise ValueError(
                f"Video resolution ({width}x{height}) exceeds maximum supported resolution ({self.max_width}x{self.max_height})."
            )

        # Target dimensions (4x upscale)
        target_width = width * 4
        target_height = height * 4

        # Estimate temporary storage requirements:
        # 1. Extracted frames (PNG): approx (width * height * 3 * 0.5) bytes per frame
        # 2. Enhanced frames (PNG 4x): approx (target_width * target_height * 3 * 0.4) bytes per frame
        # 3. Intermediate video + final output: approx 3 * original file size
        bytes_per_extracted_frame = max(50_000, int(width * height * 1.5))
        bytes_per_enhanced_frame = max(400_000, int(target_width * target_height * 0.8))

        est_extracted_bytes = total_frames * bytes_per_extracted_frame
        est_enhanced_bytes = total_frames * bytes_per_enhanced_frame
        est_output_bytes = file_size * 4
        est_total_temp_bytes = est_extracted_bytes + est_enhanced_bytes + est_output_bytes

        # Available drive space check
        drive_path = path.anchor or str(path.parent)
        try:
            total_disk, used_disk, free_disk = shutil.disk_usage(drive_path)
        except Exception:
            free_disk = 10 * 1024 * 1024 * 1024

        is_space_sufficient = free_disk >= (est_total_temp_bytes + (2 * 1024 * 1024 * 1024))  # +2GB headroom

        return {
            "valid": True,
            "filename": path.name,
            "file_size": file_size,
            "file_size_human": f"{size_mb:.1f} MB",
            "format_name": format_info.get("format_name"),
            "duration": round(duration, 2),
            "bit_rate": int(format_info.get("bit_rate") or 0),
            "video": {
                "codec": v_stream.get("codec_name"),
                "width": width,
                "height": height,
                "target_width": target_width,
                "target_height": target_height,
                "fps": round(fps, 3),
                "total_frames": total_frames,
                "is_vfr": is_vfr,
                "pix_fmt": v_stream.get("pix_fmt"),
                "color_space": v_stream.get("color_space"),
                "color_primaries": v_stream.get("color_primaries"),
                "rotation": rotation,
                "aspect_ratio": v_stream.get("display_aspect_ratio") or f"{width}:{height}"
            },
            "audio": {
                "has_audio": has_audio,
                "stream_count": len(audio_list),
                "streams": audio_list,
                "primary_codec": audio_list[0]["codec_name"] if has_audio else None
            },
            "storage": {
                "estimated_temp_bytes": est_total_temp_bytes,
                "estimated_temp_human": f"{est_total_temp_bytes / (1024 * 1024 * 1024):.2f} GB",
                "free_disk_bytes": free_disk,
                "free_disk_human": f"{free_disk / (1024 * 1024 * 1024):.2f} GB",
                "is_space_sufficient": is_space_sufficient
            }
        }
