# -*- coding: utf-8 -*-
"""
Audio Processor Module for AI Image Studio (Phase 5)
Extracts audio from the source video, preserves original codecs and streams,
and synchronizes audio into the reconstructed video. Handles videos without audio.
"""

import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from .ffmpeg_service import FFmpegService, get_ffmpeg_service

logger = logging.getLogger(__name__)


class AudioProcessor:
    """
    Manages audio extraction, re-encoding fallback, and final audio/video multiplexing.
    """

    def __init__(self, ffmpeg_service: Optional[FFmpegService] = None):
        self.service = ffmpeg_service or get_ffmpeg_service()

    def extract_audio(
        self,
        source_path: Path,
        workspace_dir: Path,
        audio_info: Optional[Dict[str, Any]] = None,
        cancel_event: Optional[threading.Event] = None
    ) -> Optional[Path]:
        """
        Extract audio stream to workspace.
        Returns Path to extracted audio file, or None if video has no audio.
        """
        source = Path(source_path).resolve()
        ws = Path(workspace_dir).resolve()
        ws.mkdir(parents=True, exist_ok=True)

        if audio_info and not audio_info.get("has_audio", True):
            logger.info("Source video has no audio stream. Skipping audio extraction.")
            return None

        # Check with ffprobe if audio exists
        probe = self.service.probe_json(source)
        audio_streams = [s for s in probe.get("streams", []) if s.get("codec_type") == "audio"]
        if not audio_streams:
            logger.info("No audio stream found in source. Skipping extraction.")
            return None

        primary_codec = audio_streams[0].get("codec_name", "aac").lower()

        # Preferred container for stream copy
        if primary_codec in ["aac", "alac"]:
            audio_target = ws / "extracted_audio.m4a"
            copy_args = ["-i", str(source), "-vn", "-c:a", "copy", "-y", str(audio_target)]
        elif primary_codec in ["mp3"]:
            audio_target = ws / "extracted_audio.mp3"
            copy_args = ["-i", str(source), "-vn", "-c:a", "copy", "-y", str(audio_target)]
        elif primary_codec in ["flac"]:
            audio_target = ws / "extracted_audio.flac"
            copy_args = ["-i", str(source), "-vn", "-c:a", "copy", "-y", str(audio_target)]
        elif primary_codec in ["opus"]:
            audio_target = ws / "extracted_audio.opus"
            copy_args = ["-i", str(source), "-vn", "-c:a", "copy", "-y", str(audio_target)]
        else:
            # Re-encode to high quality AAC if unknown or incompatible
            audio_target = ws / "extracted_audio.m4a"
            copy_args = ["-i", str(source), "-vn", "-c:a", "aac", "-b:a", "192k", "-y", str(audio_target)]

        try:
            ret, out, err = self.service.execute_ffmpeg(copy_args, timeout=600, cancel_event=cancel_event)
            if ret == 0 and audio_target.is_file() and audio_target.stat().st_size > 0:
                logger.info(f"Audio extracted successfully via stream copy to {audio_target}")
                return audio_target
        except Exception as e:
            logger.warning(f"Direct stream copy failed ({e}), attempting fallback high-quality AAC re-encode...")

        # Fallback: re-encode to high-quality AAC
        fallback_target = ws / "extracted_audio.m4a"
        reencode_args = [
            "-i", str(source),
            "-vn",
            "-c:a", "aac",
            "-b:a", "192k",
            "-y", str(fallback_target)
        ]
        ret, out, err = self.service.execute_ffmpeg(reencode_args, timeout=600, cancel_event=cancel_event)
        if ret == 0 and fallback_target.is_file() and fallback_target.stat().st_size > 0:
            logger.info(f"Audio extracted via high-quality AAC fallback to {fallback_target}")
            return fallback_target

        logger.warning(f"Audio extraction failed completely: {err}")
        return None

    def merge_audio_and_video(
        self,
        video_only_path: Path,
        audio_path: Optional[Path],
        output_path: Path,
        cancel_event: Optional[threading.Event] = None
    ) -> Path:
        """
        Merge video and extracted audio into the final output video.
        If audio_path is None, finalizes video directly.
        """
        v_path = Path(video_only_path).resolve()
        out_path = Path(output_path).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)

        if not v_path.is_file():
            raise FileNotFoundError(f"Video file missing for muxing: {v_path}")

        # If no audio to merge, simply copy video stream into target
        if not audio_path or not Path(audio_path).is_file():
            logger.info("No audio path provided. Copying video to final target.")
            args = [
                "-i", str(v_path),
                "-c:v", "copy",
                "-y", str(out_path)
            ]
            ret, out, err = self.service.execute_ffmpeg(args, timeout=600, cancel_event=cancel_event)
            if ret != 0:
                raise RuntimeError(f"Video finalization without audio failed: {err}")
            return out_path

        a_path = Path(audio_path).resolve()

        # Merge video and audio with stream copy
        # -map 0:v:0 maps first video stream from input 0
        # -map 1:a? maps audio streams from input 1 if present
        # -c:v copy preserves enhanced H.264 video
        # -c:a aac -b:a 192k ensures output MP4 compatibility
        args = [
            "-i", str(v_path),
            "-i", str(a_path),
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            "-y", str(out_path)
        ]

        logger.info(f"Merging video and audio into {out_path}...")
        ret, out, err = self.service.execute_ffmpeg(args, timeout=600, cancel_event=cancel_event)
        if ret != 0:
            raise RuntimeError(f"Failed to merge audio and video: {err}")

        if not out_path.is_file() or out_path.stat().st_size == 0:
            raise IOError("Final video file not created or 0 bytes after audio merge.")

        logger.info(f"Final video with audio created successfully: {out_path}")
        return out_path
