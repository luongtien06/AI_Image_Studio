# -*- coding: utf-8 -*-
"""
Video Enhancement Package for AI Image Studio (Phase 5)
Provides FFmpeg integration, video stream analysis, frame extraction,
sequential AI frame upscaling using the existing RealESRGAN_x4plus model,
video reconstruction, audio preservation, pause/resume, and crash recovery.
"""

from .ffmpeg_service import FFmpegService, get_ffmpeg_service
from .video_analyzer import VideoAnalyzer
from .frame_extractor import FrameExtractor
from .audio_processor import AudioProcessor
from .video_reconstructor import VideoReconstructor
from .storage_manager import VideoStorageManager
from .video_job_manager import VideoJobManager, get_video_job_manager
from .frame_processor import FrameProcessor
from .video_recovery import VideoRecoveryManager
from .video_report import generate_video_csv_report, generate_video_html_report
from .video_pipeline import VideoPipeline, get_unique_video_output_path

__all__ = [
    "FFmpegService",
    "get_ffmpeg_service",
    "VideoAnalyzer",
    "FrameExtractor",
    "AudioProcessor",
    "VideoReconstructor",
    "VideoStorageManager",
    "VideoJobManager",
    "get_video_job_manager",
    "FrameProcessor",
    "VideoRecoveryManager",
    "generate_video_csv_report",
    "generate_video_html_report",
    "VideoPipeline",
    "get_unique_video_output_path",
]
