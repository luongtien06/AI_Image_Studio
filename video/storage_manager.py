# -*- coding: utf-8 -*-
"""
Storage Manager Module for Video Enhancement (Phase 5)
Manages isolated workspaces per video job, tracks disk usage,
enforces safety boundaries, and cleans temporary files responsibly.
"""

import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class VideoStorageManager:
    """
    Manages job workspaces under data/video_workspaces/<job_id>.
    """

    def __init__(self, base_workspaces_dir: Path):
        self.base_dir = Path(base_workspaces_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def get_workspace(self, job_id: str) -> Path:
        """Get or create workspace directory for a specific job."""
        ws = self.base_dir / str(job_id)
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "extracted").mkdir(parents=True, exist_ok=True)
        (ws / "enhanced").mkdir(parents=True, exist_ok=True)
        return ws

    def get_workspace_info(self, job_id: str) -> Dict[str, Any]:
        """Compute disk usage and file counts for a job workspace."""
        ws = self.base_dir / str(job_id)
        if not ws.is_dir():
            return {
                "exists": False,
                "total_bytes": 0,
                "extracted_frames": 0,
                "enhanced_frames": 0
            }

        total_bytes = 0
        for p in ws.rglob("*"):
            if p.is_file():
                try:
                    total_bytes += p.stat().st_size
                except Exception:
                    pass

        extracted_dir = ws / "extracted"
        enhanced_dir = ws / "enhanced"

        extracted_count = len(list(extracted_dir.glob("frame_*.png"))) if extracted_dir.is_dir() else 0
        enhanced_count = len(list(enhanced_dir.glob("frame_*.png"))) if enhanced_dir.is_dir() else 0

        return {
            "exists": True,
            "path": str(ws),
            "total_bytes": total_bytes,
            "total_human": f"{total_bytes / (1024 * 1024):.1f} MB",
            "extracted_frames": extracted_count,
            "enhanced_frames": enhanced_count
        }

    def cleanup_workspace(self, job_id: str, keep_enhanced: bool = False) -> bool:
        """
        Safely remove temporary workspace files.
        If keep_enhanced is True, only deletes extracted frames and temporary intermediates.
        """
        ws = self.base_dir / str(job_id)
        if not ws.is_dir():
            return False

        try:
            if keep_enhanced:
                # Remove only raw extracted frames to save disk space while keeping enhanced
                ext_dir = ws / "extracted"
                if ext_dir.is_dir():
                    shutil.rmtree(ext_dir, ignore_errors=True)
                for f in ws.glob("*.tmp*"):
                    try:
                        f.unlink()
                    except Exception:
                        pass
                logger.info(f"Cleaned extracted frames for workspace {job_id}")
            else:
                shutil.rmtree(ws, ignore_errors=True)
                logger.info(f"Cleaned entire workspace for {job_id}")
            return True
        except Exception as e:
            logger.error(f"Error cleaning workspace {job_id}: {e}")
            return False

    def list_abandoned_workspaces(self, active_job_ids: List[str]) -> List[str]:
        """List workspace directory names that do not correspond to any active job."""
        abandoned = []
        try:
            for item in self.base_dir.iterdir():
                if item.is_dir() and item.name not in active_job_ids:
                    abandoned.append(item.name)
        except Exception as e:
            logger.error(f"Error listing workspaces: {e}")
        return abandoned
