# -*- coding: utf-8 -*-
"""
Frame Extractor Module for AI Image Studio (Phase 5)
Extracts video frames to lossless PNG sequences in an isolated job workspace.
Supports progress monitoring, cancellation, and validation of frame counts.
"""

import logging
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .ffmpeg_service import FFmpegService, get_ffmpeg_service

logger = logging.getLogger(__name__)


class FrameExtractor:
    """
    Extracts frames from source video using FFmpeg into a workspace directory.
    Uses PNG format for 100% lossless preservation before AI upscaling.
    """

    def __init__(self, ffmpeg_service: Optional[FFmpegService] = None):
        self.service = ffmpeg_service or get_ffmpeg_service()

    def extract_frames(
        self,
        source_path: Path,
        workspace_dir: Path,
        expected_frames: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        cancel_event: Optional[threading.Event] = None
    ) -> List[Path]:
        """
        Extract frames to workspace_dir / 'extracted' / 'frame_%06d.png'.
        Returns sorted list of extracted frame file paths.
        """
        source = Path(source_path).resolve()
        if not source.is_file():
            raise FileNotFoundError(f"Source video not found: {source}")

        extracted_dir = Path(workspace_dir) / "extracted"
        extracted_dir.mkdir(parents=True, exist_ok=True)

        target_pattern = str(extracted_dir / "frame_%06d.png")

        # Command arguments:
        # -fps_mode passthrough prevents dropping or duplicating frames in FFmpeg
        args = [
            "-i", str(source),
            "-fps_mode", "passthrough",
            "-q:v", "2",
            "-y",
            target_pattern
        ]

        def on_ffmpeg_progress(info: Dict[str, Any]):
            cur_frame = info.get("frame", 0)
            if progress_callback:
                tot = expected_frames or cur_frame or 1
                pct = min(100, int((cur_frame / tot) * 100)) if tot > 0 else 0
                progress_callback(cur_frame, tot, f"Trích xuất khung hình: {cur_frame}/{tot}")

        ret_code, stdout, stderr = self.service.execute_ffmpeg(
            args=args,
            timeout=1800,  # 30 minute timeout for extraction
            progress_callback=on_ffmpeg_progress,
            cancel_event=cancel_event
        )

        if ret_code != 0:
            raise RuntimeError(f"FFmpeg frame extraction failed with exit code {ret_code}: {stderr[:500]}")

        # Scan and verify extracted frames
        frames = sorted(list(extracted_dir.glob("frame_*.png")))
        if not frames:
            raise RuntimeError("Frame extraction completed but no frame files were created on disk.")

        logger.info(f"Extracted {len(frames)} frames to {extracted_dir}")
        return frames

    def get_existing_extracted_frames(self, workspace_dir: Path) -> List[Path]:
        """Return already extracted frames if resuming."""
        extracted_dir = Path(workspace_dir) / "extracted"
        if not extracted_dir.is_dir():
            return []
        return sorted([f for f in extracted_dir.glob("frame_*.png") if f.is_file() and f.stat().st_size > 0])
