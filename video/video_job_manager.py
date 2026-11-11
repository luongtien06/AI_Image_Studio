# -*- coding: utf-8 -*-
"""
Video Job Manager Module for AI Image Studio (Phase 5)
Provides SQLite-backed persistence for video upscaling workflows (Schema v2).
Handles job creation, stage tracking, frame-level checkpoints, pause/resume states,
and safe multi-threaded transactions in SQLite WAL mode.
"""

import json
import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Video Job Statuses
STATUS_QUEUED = "QUEUED"
STATUS_ANALYZING = "ANALYZING"
STATUS_EXTRACTING = "EXTRACTING"
STATUS_PROCESSING = "PROCESSING"
STATUS_PAUSED = "PAUSED"
STATUS_RECONSTRUCTING = "RECONSTRUCTING"
STATUS_RESTORING_AUDIO = "RESTORING_AUDIO"
STATUS_VERIFYING = "VERIFYING"
STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = "FAILED"
STATUS_CANCEL_REQUESTED = "CANCEL_REQUESTED"
STATUS_CANCELLED = "CANCELLED"

ALL_VIDEO_STATUSES = {
    STATUS_QUEUED,
    STATUS_ANALYZING,
    STATUS_EXTRACTING,
    STATUS_PROCESSING,
    STATUS_PAUSED,
    STATUS_RECONSTRUCTING,
    STATUS_RESTORING_AUDIO,
    STATUS_VERIFYING,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_CANCEL_REQUESTED,
    STATUS_CANCELLED,
}

VALID_VIDEO_TRANSITIONS = {
    STATUS_QUEUED: {STATUS_ANALYZING, STATUS_EXTRACTING, STATUS_PROCESSING, STATUS_CANCELLED},
    STATUS_ANALYZING: {STATUS_EXTRACTING, STATUS_FAILED, STATUS_CANCELLED},
    STATUS_EXTRACTING: {STATUS_PROCESSING, STATUS_FAILED, STATUS_CANCEL_REQUESTED, STATUS_CANCELLED},
    STATUS_PROCESSING: {STATUS_PAUSED, STATUS_RECONSTRUCTING, STATUS_FAILED, STATUS_CANCEL_REQUESTED, STATUS_CANCELLED, STATUS_COMPLETED},
    STATUS_PAUSED: {STATUS_PROCESSING, STATUS_CANCELLED},
    STATUS_RECONSTRUCTING: {STATUS_RESTORING_AUDIO, STATUS_VERIFYING, STATUS_FAILED, STATUS_CANCEL_REQUESTED, STATUS_CANCELLED},
    STATUS_RESTORING_AUDIO: {STATUS_VERIFYING, STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCEL_REQUESTED, STATUS_CANCELLED},
    STATUS_VERIFYING: {STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED},
    STATUS_CANCEL_REQUESTED: {STATUS_CANCELLED, STATUS_COMPLETED, STATUS_FAILED},
    STATUS_FAILED: {STATUS_QUEUED, STATUS_PROCESSING},
    STATUS_COMPLETED: set(),
    STATUS_CANCELLED: set(),
}


class VideoJobManager:
    """
    Manages video jobs in the existing SQLite database (Schema Version 2).
    """

    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            base_dir = Path(__file__).resolve().parent.parent
            self.db_path = base_dir / "data" / "jobs.db"
        else:
            self.db_path = Path(db_path)

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_database()

    def get_connection(self) -> sqlite3.Connection:
        """Create a new thread-local connection with WAL mode enabled."""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init_database(self):
        """Ensure Schema Version 2 is applied."""
        with self._lock:
            with self.get_connection() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS schema_version (
                        version INTEGER PRIMARY KEY,
                        applied_at TEXT NOT NULL
                    )
                """)

                cur = conn.execute("SELECT MAX(version) FROM schema_version")
                row = cur.fetchone()
                current_ver = row[0] if row and row[0] is not None else 0

                if current_ver < 2:
                    logger.info("Applying SQLite schema version 2 (Video Enhancement)...")
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS video_jobs (
                            id TEXT PRIMARY KEY,
                            filename TEXT NOT NULL,
                            source_path TEXT NOT NULL,
                            output_path TEXT,
                            workspace_dir TEXT NOT NULL,
                            status TEXT NOT NULL,
                            current_stage TEXT NOT NULL DEFAULT 'QUEUED',
                            source_duration REAL DEFAULT 0.0,
                            source_fps REAL DEFAULT 0.0,
                            source_width INTEGER DEFAULT 0,
                            source_height INTEGER DEFAULT 0,
                            target_width INTEGER DEFAULT 0,
                            target_height INTEGER DEFAULT 0,
                            total_frames INTEGER DEFAULT 0,
                            processed_frames INTEGER DEFAULT 0,
                            failed_frames INTEGER DEFAULT 0,
                            current_frame_index INTEGER DEFAULT 0,
                            audio_metadata TEXT,
                            video_metadata TEXT,
                            processing_config TEXT,
                            retry_count INTEGER DEFAULT 0,
                            max_retries INTEGER DEFAULT 3,
                            file_size INTEGER DEFAULT 0,
                            output_size INTEGER DEFAULT 0,
                            elapsed_seconds REAL DEFAULT 0.0,
                            tile_used INTEGER,
                            created_at TEXT NOT NULL,
                            started_at TEXT,
                            updated_at TEXT NOT NULL,
                            completed_at TEXT,
                            cancelled_at TEXT,
                            error_message TEXT
                        )
                    """)
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_video_jobs_status ON video_jobs(status)")
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_video_jobs_created_at ON video_jobs(created_at)")

                    conn.execute(
                        "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                        (2, datetime.now().isoformat())
                    )
                    conn.commit()
                    logger.info("SQLite schema version 2 applied successfully.")

    def create_video_job(
        self,
        job_id: str,
        filename: str,
        source_path: str,
        workspace_dir: str,
        video_metadata: Optional[Dict[str, Any]] = None,
        audio_metadata: Optional[Dict[str, Any]] = None,
        processing_config: Optional[Dict[str, Any]] = None,
        file_size: int = 0
    ) -> Dict[str, Any]:
        """Insert a new video job."""
        now = datetime.now().isoformat()
        v_meta = video_metadata or {}
        a_meta = audio_metadata or {}
        p_cfg = processing_config or {}

        src_w = int(v_meta.get("width") or 0)
        src_h = int(v_meta.get("height") or 0)
        target_w = src_w * 4
        target_h = src_h * 4
        duration = float(v_meta.get("duration") or 0.0)
        fps = float(v_meta.get("fps") or 30.0)
        total_frames = int(v_meta.get("total_frames") or 0)

        with self.get_connection() as conn:
            conn.execute("""
                INSERT INTO video_jobs (
                    id, filename, source_path, workspace_dir, status, current_stage,
                    source_duration, source_fps, source_width, source_height,
                    target_width, target_height, total_frames, processed_frames,
                    failed_frames, current_frame_index, audio_metadata, video_metadata,
                    processing_config, file_size, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                job_id, filename, str(source_path), str(workspace_dir),
                STATUS_QUEUED, STATUS_QUEUED,
                duration, fps, src_w, src_h,
                target_w, target_h, total_frames, 0,
                0, 0, json.dumps(a_meta, ensure_ascii=False),
                json.dumps(v_meta, ensure_ascii=False),
                json.dumps(p_cfg, ensure_ascii=False),
                file_size, now, now
            ))
            conn.commit()

        return self.get_video_job(job_id)

    def get_video_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve single video job by ID."""
        with self.get_connection() as conn:
            cur = conn.execute("SELECT * FROM video_jobs WHERE id = ?", (job_id,))
            row = cur.fetchone()
            if not row:
                return None
            return dict(row)

    def query_video_jobs(
        self,
        status: Optional[str] = None,
        search: Optional[str] = None,
        sort_by: str = "created_at",
        order: str = "DESC",
        page: int = 1,
        limit: int = 50
    ) -> Dict[str, Any]:
        """Query video jobs with filtering, live search, and pagination."""
        clauses = []
        params = []

        if status and status.upper() != "ALL":
            clauses.append("status = ?")
            params.append(status.upper())

        if search and search.strip():
            term = f"%{search.strip()}%"
            clauses.append("(filename LIKE ? OR id LIKE ?)")
            params.extend([term, term])

        where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""

        allowed_sort = {
            "created_at": "created_at",
            "filename": "filename",
            "file_size": "file_size",
            "elapsed_seconds": "elapsed_seconds",
            "duration": "source_duration"
        }
        sort_col = allowed_sort.get(sort_by, "created_at")
        sort_dir = "ASC" if order.upper() == "ASC" else "DESC"

        offset = max(0, (page - 1) * limit)

        with self.get_connection() as conn:
            # Count total matching
            count_cur = conn.execute(f"SELECT COUNT(*) FROM video_jobs {where_sql}", params)
            total = count_cur.fetchone()[0]

            # Fetch rows
            sql = f"""
                SELECT * FROM video_jobs
                {where_sql}
                ORDER BY {sort_col} {sort_dir}
                LIMIT ? OFFSET ?
            """
            cur = conn.execute(sql, params + [limit, offset])
            rows = [dict(r) for r in cur.fetchall()]

            # Status counts
            cur_counts = conn.execute("SELECT status, COUNT(*) FROM video_jobs GROUP BY status")
            status_counts = {r[0]: r[1] for r in cur_counts.fetchall()}

        total_pages = max(1, (total + limit - 1) // limit) if limit > 0 else 1

        return {
            "jobs": rows,
            "total": total,
            "page": page,
            "limit": limit,
            "total_pages": total_pages,
            "status_counts": status_counts
        }

    def update_stage(self, job_id: str, stage: str, status: Optional[str] = None) -> bool:
        """Update current stage and optionally status."""
        now = datetime.now().isoformat()
        with self.get_connection() as conn:
            if status:
                conn.execute(
                    "UPDATE video_jobs SET current_stage = ?, status = ?, updated_at = ? WHERE id = ?",
                    (stage, status, now, job_id)
                )
            else:
                conn.execute(
                    "UPDATE video_jobs SET current_stage = ?, updated_at = ? WHERE id = ?",
                    (stage, now, job_id)
                )
            conn.commit()
            return True

    def update_frame_progress(
        self,
        job_id: str,
        processed_frames: int,
        current_frame_index: int,
        tile_used: Optional[int] = None,
        elapsed_seconds: Optional[float] = None,
        failed_frames: Optional[int] = None
    ) -> bool:
        """Update frame-level checkpoint."""
        now = datetime.now().isoformat()
        with self.get_connection() as conn:
            updates = [
                "processed_frames = ?",
                "current_frame_index = ?",
                "updated_at = ?"
            ]
            vals = [processed_frames, current_frame_index, now]

            if tile_used is not None:
                updates.append("tile_used = ?")
                vals.append(tile_used)
            if elapsed_seconds is not None:
                updates.append("elapsed_seconds = ?")
                vals.append(elapsed_seconds)
            if failed_frames is not None:
                updates.append("failed_frames = ?")
                vals.append(failed_frames)

            vals.append(job_id)
            sql = f"UPDATE video_jobs SET {', '.join(updates)} WHERE id = ?"
            conn.execute(sql, vals)
            conn.commit()
            return True

    def claim_job_for_execution(
        self,
        job_id: str,
        allowed_statuses: Tuple[str, ...] = (STATUS_QUEUED,)
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Atomically claim a video job for execution by transitioning it to STATUS_PROCESSING.
        Guarantees mutual exclusion and prevents duplicate background workers.
        Returns:
            (claimed: bool, message: str, job_dict: Optional[Dict])
        """
        now = datetime.now().isoformat()
        with self._lock:
            with self.get_connection() as conn:
                cur = conn.execute("SELECT * FROM video_jobs WHERE id = ?", (job_id,))
                row = cur.fetchone()
                if not row:
                    return False, f"Video job {job_id} not found", None

                job_dict = dict(row)
                curr_status = job_dict["status"]

                if curr_status == STATUS_PROCESSING:
                    return False, "Video job is already actively processing.", job_dict

                if curr_status not in allowed_statuses:
                    return False, f"Cannot start/resume job from current status '{curr_status}'. Allowed: {list(allowed_statuses)}", job_dict

                # Atomic conditional UPDATE
                placeholders = ", ".join("?" for _ in allowed_statuses)
                sql = f"""
                    UPDATE video_jobs
                    SET status = ?,
                        started_at = COALESCE(started_at, ?),
                        updated_at = ?
                    WHERE id = ? AND status IN ({placeholders})
                """
                params = [STATUS_PROCESSING, now, now, job_id] + list(allowed_statuses)
                cur_upd = conn.execute(sql, params)
                conn.commit()

                if cur_upd.rowcount == 1:
                    cur_after = conn.execute("SELECT * FROM video_jobs WHERE id = ?", (job_id,))
                    updated_dict = dict(cur_after.fetchone())
                    logger.info(f"Video job {job_id} successfully claimed for execution from '{curr_status}'")
                    return True, "Job claimed successfully", updated_dict
                else:
                    cur_after = conn.execute("SELECT * FROM video_jobs WHERE id = ?", (job_id,))
                    latest_dict = dict(cur_after.fetchone()) if cur_after else None
                    logger.warning(f"Video job {job_id} claim conflict. Current status: {latest_dict.get('status') if latest_dict else 'unknown'}")
                    return False, "Conflict: Job was claimed or modified by another worker", latest_dict

    def release_job_claim(
        self,
        job_id: str,
        target_status: str,
        error_message: Optional[str] = None
    ) -> bool:
        """
        Release execution claim on a job, transitioning it to target_status
        (e.g. PAUSED, QUEUED, CANCELLED, FAILED).
        """
        logger.info(f"Releasing execution claim for video job {job_id} -> {target_status}")
        return self.transition_status(job_id, target_status, error_message=error_message, force=True)

    def transition_status(
        self,
        job_id: str,
        new_status: str,
        error_message: Optional[str] = None,
        output_path: Optional[str] = None,
        output_size: Optional[int] = None,
        force: bool = False
    ) -> bool:
        """Transition job status safely with atomic conditional update."""
        now = datetime.now().isoformat()
        with self.get_connection() as conn:
            updates = ["status = ?", "updated_at = ?"]
            vals: List[Any] = [new_status, now]

            if new_status == STATUS_PROCESSING:
                updates.append("started_at = COALESCE(started_at, ?)")
                vals.append(now)
            elif new_status == STATUS_COMPLETED:
                updates.append("completed_at = ?")
                vals.append(now)
                if output_path:
                    updates.append("output_path = ?")
                    vals.append(str(output_path))
                if output_size is not None:
                    updates.append("output_size = ?")
                    vals.append(output_size)
            elif new_status == STATUS_CANCELLED:
                updates.append("cancelled_at = ?")
                vals.append(now)

            if error_message is not None:
                updates.append("error_message = ?")
                vals.append(error_message)

            where_clauses = ["id = ?"]
            where_params = [job_id]

            if not force:
                allowed_sources = [s for s, targets in VALID_VIDEO_TRANSITIONS.items() if new_status in targets]
                if not allowed_sources:
                    logger.warning(f"Invalid video job status transition target: {new_status}")
                    return False
                placeholders = ", ".join("?" for _ in allowed_sources)
                where_clauses.append(f"status IN ({placeholders})")
                where_params.extend(allowed_sources)
            else:
                if new_status in (STATUS_PROCESSING, STATUS_QUEUED):
                    where_clauses.append("status NOT IN (?, ?)")
                    where_params.extend([STATUS_CANCELLED, STATUS_COMPLETED])

            sql = f"UPDATE video_jobs SET {', '.join(updates)} WHERE {' AND '.join(where_clauses)}"
            cur = conn.execute(sql, vals + where_params)
            conn.commit()
            return cur.rowcount > 0

    def delete_video_job(self, job_id: str) -> bool:
        """Delete a video job from the database."""
        with self.get_connection() as conn:
            conn.execute("DELETE FROM video_jobs WHERE id = ?", (job_id,))
            conn.commit()
            return True

    def get_statistics(self) -> Dict[str, Any]:
        """Aggregate performance and summary statistics for video processing."""
        with self.get_connection() as conn:
            cur = conn.execute("""
                SELECT
                    COUNT(*) as total_jobs,
                    SUM(CASE WHEN status = 'COMPLETED' THEN 1 ELSE 0 END) as completed_jobs,
                    SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) as failed_jobs,
                    SUM(CASE WHEN status = 'PROCESSING' THEN 1 ELSE 0 END) as processing_jobs,
                    SUM(CASE WHEN status = 'PAUSED' THEN 1 ELSE 0 END) as paused_jobs,
                    SUM(CASE WHEN status = 'QUEUED' THEN 1 ELSE 0 END) as queued_jobs,
                    SUM(total_frames) as total_frames_sum,
                    SUM(processed_frames) as processed_frames_sum,
                    SUM(elapsed_seconds) as total_seconds,
                    AVG(CASE WHEN status = 'COMPLETED' AND elapsed_seconds > 0 THEN elapsed_seconds ELSE NULL END) as avg_duration,
                    SUM(file_size) as total_input_bytes,
                    SUM(output_size) as total_output_bytes
                FROM video_jobs
            """)
            r = dict(cur.fetchone())
            return {
                "total_jobs": r["total_jobs"] or 0,
                "completed_jobs": r["completed_jobs"] or 0,
                "failed_jobs": r["failed_jobs"] or 0,
                "processing_jobs": r["processing_jobs"] or 0,
                "paused_jobs": r["paused_jobs"] or 0,
                "queued_jobs": r["queued_jobs"] or 0,
                "total_frames_sum": r["total_frames_sum"] or 0,
                "processed_frames_sum": r["processed_frames_sum"] or 0,
                "total_seconds": round(r["total_seconds"] or 0.0, 2),
                "avg_duration": round(r["avg_duration"] or 0.0, 2),
                "total_input_bytes": r["total_input_bytes"] or 0,
                "total_output_bytes": r["total_output_bytes"] or 0,
            }


_global_video_job_manager: Optional[VideoJobManager] = None


def get_video_job_manager(db_path: Optional[Path] = None) -> VideoJobManager:
    """Get or create singleton VideoJobManager."""
    global _global_video_job_manager
    if _global_video_job_manager is None:
        _global_video_job_manager = VideoJobManager(db_path=db_path)
    return _global_video_job_manager
