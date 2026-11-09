# -*- coding: utf-8 -*-
"""
Video Recovery Module for AI Image Studio (Phase 5)
Recovers interrupted video processing jobs upon application startup.
Inspects workspace state, verifies frame sequences, and sets safe recovery states.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from .video_job_manager import (
    STATUS_ANALYZING,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_EXTRACTING,
    STATUS_FAILED,
    STATUS_PAUSED,
    STATUS_PROCESSING,
    STATUS_QUEUED,
    STATUS_RECONSTRUCTING,
    STATUS_RESTORING_AUDIO,
    STATUS_VERIFYING,
    VideoJobManager,
    get_video_job_manager,
)

logger = logging.getLogger(__name__)


class VideoRecoveryManager:
    """
    Manages crash detection and recovery for video jobs.
    """

    def __init__(self, job_manager: Optional[VideoJobManager] = None):
        self.mgr = job_manager or get_video_job_manager()

    def recover_interrupted_jobs(self) -> Dict[str, Any]:
        """
        Scan and recover video jobs interrupted during server shutdown.
        Returns recovery summary report.
        """
        logger.info("Executing Phase 5 Video Startup Recovery...")
        active_statuses = [
            STATUS_ANALYZING,
            STATUS_EXTRACTING,
            STATUS_PROCESSING,
            STATUS_RECONSTRUCTING,
            STATUS_RESTORING_AUDIO,
            STATUS_VERIFYING,
        ]

        recovered_jobs = []

        with self.mgr.get_connection() as conn:
            cur = conn.execute(
                f"SELECT * FROM video_jobs WHERE status IN ({','.join(['?']*len(active_statuses))})",
                active_statuses
            )
            interrupted = [dict(r) for r in cur.fetchall()]

        for job in interrupted:
            jid = job["id"]
            status = job["status"]
            ws = Path(job["workspace_dir"])
            ext_dir = ws / "extracted"
            enh_dir = ws / "enhanced"
            out_path = Path(job["output_path"]) if job.get("output_path") else None

            # Case 1: Video output already finalized and valid
            if out_path and out_path.is_file() and out_path.stat().st_size > 0:
                logger.info(f"Recovery: Job {jid} already has complete output on disk. Marking COMPLETED.")
                self.mgr.transition_status(
                    jid,
                    STATUS_COMPLETED,
                    output_size=out_path.stat().st_size,
                    force=True
                )
                recovered_jobs.append({"id": jid, "action": "completed", "reason": "output_verified"})
                continue

            # Case 2: PROCESSING was interrupted
            if status == STATUS_PROCESSING:
                enhanced_frames = list(enh_dir.glob("frame_*.png")) if enh_dir.is_dir() else []
                total_frames = job.get("total_frames") or 1
                completed_count = len(enhanced_frames)

                logger.info(
                    f"Recovery: Job {jid} interrupted during PROCESSING. "
                    f"Enhanced frames present: {completed_count}/{total_frames}. Setting PAUSED for user resume."
                )
                self.mgr.update_frame_progress(
                    job_id=jid,
                    processed_frames=completed_count,
                    current_frame_index=completed_count
                )
                self.mgr.transition_status(
                    jid,
                    STATUS_PAUSED,
                    error_message="Recovered from unexpected shutdown. Ready to resume.",
                    force=True
                )
                recovered_jobs.append({"id": jid, "action": "paused_for_resume", "frames": completed_count})
                continue

            # Case 3: EXTRACTING or ANALYZING was interrupted
            if status in [STATUS_EXTRACTING, STATUS_ANALYZING]:
                logger.info(f"Recovery: Job {jid} was interrupted during {status}. Re-queueing.")
                self.mgr.transition_status(
                    jid,
                    STATUS_QUEUED,
                    error_message=f"Recovered from shutdown during {status}. Re-queued.",
                    force=True
                )
                recovered_jobs.append({"id": jid, "action": "requeued", "previous_status": status})
                continue

            # Case 4: RECONSTRUCTING / RESTORING_AUDIO / VERIFYING interrupted
            if status in [STATUS_RECONSTRUCTING, STATUS_RESTORING_AUDIO, STATUS_VERIFYING]:
                # Check if enhanced frames are fully available
                enhanced_frames = list(enh_dir.glob("frame_*.png")) if enh_dir.is_dir() else []
                total_frames = job.get("total_frames") or 1
                if len(enhanced_frames) >= total_frames:
                    logger.info(f"Recovery: Job {jid} frames complete. Ready to resume reconstruction.")
                    self.mgr.transition_status(
                        jid,
                        STATUS_PAUSED,
                        error_message="All frames enhanced. Ready to resume reconstruction.",
                        force=True
                    )
                    recovered_jobs.append({"id": jid, "action": "ready_to_reconstruct"})
                else:
                    self.mgr.transition_status(
                        jid,
                        STATUS_PAUSED,
                        error_message="Interrupted before completion. Ready to resume.",
                        force=True
                    )
                    recovered_jobs.append({"id": jid, "action": "paused_for_resume"})

        logger.info(f"Video Startup Recovery completed: {len(recovered_jobs)} jobs processed.")
        return {
            "recovered_count": len(recovered_jobs),
            "jobs": recovered_jobs
        }
