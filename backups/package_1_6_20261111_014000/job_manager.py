# -*- coding: utf-8 -*-
"""
SQLite Persistent Job Queue & Crash Recovery Manager (Phase 3)
Provides ACID transaction-backed job storage, safe state transitions,
crash detection & recovery on startup, retry scheduling with backoff,
and cancellation controls.
"""

import json
import logging
import os
import shutil
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

# Job Statuses
STATUS_QUEUED = "QUEUED"
STATUS_VALIDATING = "VALIDATING"
STATUS_PROCESSING = "PROCESSING"
STATUS_RETRY_WAIT = "RETRY_WAIT"
STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = "FAILED"
STATUS_CANCEL_REQUESTED = "CANCEL_REQUESTED"
STATUS_CANCELLED = "CANCELLED"

# All recognized statuses
ALL_STATUSES = {
    STATUS_QUEUED,
    STATUS_VALIDATING,
    STATUS_PROCESSING,
    STATUS_RETRY_WAIT,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_CANCEL_REQUESTED,
    STATUS_CANCELLED,
}

# State Transition Rules
VALID_TRANSITIONS = {
    STATUS_QUEUED: {STATUS_VALIDATING, STATUS_PROCESSING, STATUS_FAILED, STATUS_CANCELLED},
    STATUS_VALIDATING: {STATUS_PROCESSING, STATUS_FAILED, STATUS_CANCELLED, STATUS_QUEUED},
    STATUS_PROCESSING: {STATUS_COMPLETED, STATUS_RETRY_WAIT, STATUS_FAILED, STATUS_CANCEL_REQUESTED, STATUS_CANCELLED},
    STATUS_CANCEL_REQUESTED: {STATUS_CANCELLED, STATUS_COMPLETED},
    STATUS_RETRY_WAIT: {STATUS_QUEUED, STATUS_PROCESSING, STATUS_FAILED, STATUS_CANCELLED},
    STATUS_FAILED: {STATUS_QUEUED},  # manual retry reset
    STATUS_COMPLETED: set(),        # terminal
    STATUS_CANCELLED: set(),        # terminal
}

# Non-retryable error keywords
NON_RETRYABLE_ERRORS = [
    "invalid image",
    "unidentifiedimageerror",
    "decompressionbomb",
    "corrupted container",
    "corrupted or truncated",
    "empty file",
    "not allowed",
    "access denied",
    "blocked system",
    "file does not exist",
    "source_file_missing",
    "source file does not exist",
    "exceeds maximum allowed size",
    "exceeds maximum pixel limit",
    "output_pixel_limit_exceeded",
    "output pixel limit exceeded",
    "insufficient_ram_for_output",
    "insufficient ram",
    "insufficient_ram",
    "insufficient disk space",
    "insufficient_disk_space",
    "generated output validation failed",
]


class JobManager:
    """
    Manages SQLite-backed persistent queue for AI Image Studio jobs.
    Uses WAL mode for concurrent reader/writer support without thread sharing.
    """

    SCHEMA_VERSION = 1

    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            base_dir = Path(__file__).resolve().parent
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
        """Initialize schema and check migration version safely."""
        with self._lock:
            with self.get_connection() as conn:
                # Schema version tracking
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS schema_version (
                        version INTEGER PRIMARY KEY,
                        applied_at TEXT NOT NULL
                    )
                """)

                cur = conn.execute("SELECT MAX(version) FROM schema_version")
                row = cur.fetchone()
                current_ver = row[0] if row and row[0] is not None else 0

                if current_ver < 1:
                    logger.info("Applying SQLite schema version 1...")
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS jobs (
                            id TEXT PRIMARY KEY,
                            batch_id TEXT,
                            filename TEXT NOT NULL,
                            input_path TEXT NOT NULL,
                            output_path TEXT,
                            status TEXT NOT NULL,
                            progress INTEGER DEFAULT 0,
                            retry_count INTEGER DEFAULT 0,
                            max_retries INTEGER DEFAULT 3,
                            error_message TEXT,
                            is_oom INTEGER DEFAULT 0,
                            processing_config TEXT,
                            dimensions TEXT,
                            output_dimensions TEXT,
                            file_size INTEGER DEFAULT 0,
                            output_size INTEGER DEFAULT 0,
                            elapsed_seconds REAL DEFAULT 0.0,
                            tile_used INTEGER,
                            created_at TEXT NOT NULL,
                            started_at TEXT,
                            updated_at TEXT NOT NULL,
                            completed_at TEXT,
                            cancelled_at TEXT
                        )
                    """)
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at)")
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_batch_id ON jobs(batch_id)")

                    conn.execute(
                        "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                        (1, datetime.now().isoformat())
                    )
                    conn.commit()
                    logger.info("SQLite schema version 1 applied successfully.")

    def backup_database(self, backup_dir: Optional[Path] = None) -> Path:
        """Create a consistent SQLite backup snapshot using the backup API."""
        if backup_dir is None:
            backup_dir = self.db_path.parent / "backups"
        backup_dir = Path(backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        target_file = backup_dir / f"jobs_backup_{ts}.db"

        with self._lock:
            src_conn = self.get_connection()
            try:
                dst_conn = sqlite3.connect(str(target_file))
                try:
                    src_conn.backup(dst_conn)
                    logger.info(f"Database backup saved to: {target_file}")
                finally:
                    dst_conn.close()
            finally:
                src_conn.close()

        return target_file

    def add_job(
        self,
        job_id: str,
        filename: str,
        input_path: str,
        output_path: Optional[str] = None,
        batch_id: Optional[str] = None,
        dimensions: str = "",
        file_size: int = 0,
        processing_config: Optional[Dict[str, Any]] = None,
        max_retries: int = 3
    ) -> Dict[str, Any]:
        """Insert a new job into SQLite with QUEUED status."""
        now = datetime.now().isoformat()
        cfg_json = json.dumps(processing_config or {}, ensure_ascii=False)

        with self.get_connection() as conn:
            conn.execute("""
                INSERT INTO jobs (
                    id, batch_id, filename, input_path, output_path,
                    status, progress, retry_count, max_retries,
                    error_message, is_oom, processing_config, dimensions,
                    output_dimensions, file_size, output_size, elapsed_seconds,
                    tile_used, created_at, started_at, updated_at, completed_at, cancelled_at
                ) VALUES (
                    ?, ?, ?, ?, ?,
                    ?, 0, 0, ?,
                    NULL, 0, ?, ?,
                    '', ?, 0, 0.0,
                    NULL, ?, NULL, ?, NULL, NULL
                )
            """, (
                job_id, batch_id, filename, str(input_path), output_path,
                STATUS_QUEUED, max_retries,
                cfg_json, dimensions, file_size,
                now, now
            ))
            conn.commit()

        return self.get_job(job_id)

    def add_jobs_batch(self, jobs_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Insert multiple jobs in a single transaction."""
        now = datetime.now().isoformat()
        results = []

        with self.get_connection() as conn:
            for j in jobs_data:
                jid = j["id"]
                cfg_json = json.dumps(j.get("processing_config") or {}, ensure_ascii=False)
                conn.execute("""
                    INSERT INTO jobs (
                        id, batch_id, filename, input_path, output_path,
                        status, progress, retry_count, max_retries,
                        error_message, is_oom, processing_config, dimensions,
                        output_dimensions, file_size, output_size, elapsed_seconds,
                        tile_used, created_at, started_at, updated_at, completed_at, cancelled_at
                    ) VALUES (
                        ?, ?, ?, ?, ?,
                        ?, 0, 0, ?,
                        NULL, 0, ?, ?,
                        '', ?, 0, 0.0,
                        NULL, ?, NULL, ?, NULL, NULL
                    )
                """, (
                    jid, j.get("batch_id"), j["filename"], str(j["input_path"]), j.get("output_path"),
                    STATUS_QUEUED, j.get("max_retries", 3),
                    cfg_json, j.get("dimensions", ""), j.get("file_size", 0),
                    now, now
                ))
            conn.commit()

        for j in jobs_data:
            results.append(self.get_job(j["id"]))
        return results

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve single job by ID."""
        with self.get_connection() as conn:
            cur = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            row = cur.fetchone()
            if not row:
                return None
            return self._row_to_dict(row)

    def get_all_jobs(self, limit: int = 500) -> List[Dict[str, Any]]:
        """Retrieve jobs sorted by creation order."""
        with self.get_connection() as conn:
            cur = conn.execute(
                "SELECT * FROM jobs ORDER BY created_at ASC LIMIT ?",
                (limit,)
            )
            return [self._row_to_dict(r) for r in cur.fetchall()]

    def query_jobs(
        self,
        status: Optional[str] = None,
        search: Optional[str] = None,
        batch_id: Optional[str] = None,
        sort_by: str = "created_at",
        order: str = "DESC",
        limit: int = 50,
        offset: int = 0
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        Query jobs with filtering, searching, sorting, and pagination.
        Returns (list_of_job_dicts, total_matching_count).
        """
        allowed_sort = {
            "created_at": "created_at",
            "completed_at": "completed_at",
            "filename": "filename",
            "file_size": "file_size",
            "elapsed_seconds": "elapsed_seconds",
            "status": "status"
        }
        col = allowed_sort.get(sort_by, "created_at")
        ord_dir = "DESC" if str(order).upper() == "DESC" else "ASC"

        where_clauses = []
        params: List[Any] = []

        if status and status.upper() != "ALL":
            st_up = status.upper()
            if st_up in ("PENDING", "QUEUED"):
                where_clauses.append("status IN (?, ?)")
                params.extend([STATUS_QUEUED, STATUS_VALIDATING])
            elif st_up in ("ERROR", "FAILED"):
                where_clauses.append("status = ?")
                params.append(STATUS_FAILED)
            elif st_up in ("RETRY", "RETRY_WAIT"):
                where_clauses.append("status = ?")
                params.append(STATUS_RETRY_WAIT)
            elif st_up in ALL_STATUSES:
                where_clauses.append("status = ?")
                params.append(st_up)

        if search:
            term = f"%{search.strip()}%"
            where_clauses.append("(filename LIKE ? OR id LIKE ?)")
            params.extend([term, term])

        if batch_id:
            where_clauses.append("batch_id = ?")
            params.append(batch_id)

        where_str = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        with self.get_connection() as conn:
            # Count matching records
            count_sql = f"SELECT COUNT(*) FROM jobs {where_str}"
            cur_count = conn.execute(count_sql, params)
            total = cur_count.fetchone()[0]

            # Query records page
            query_sql = f"""
                SELECT * FROM jobs
                {where_str}
                ORDER BY {col} {ord_dir}
                LIMIT ? OFFSET ?
            """
            cur = conn.execute(query_sql, params + [limit, offset])
            rows = [self._row_to_dict(r) for r in cur.fetchall()]

        return rows, total

    def get_jobs_by_ids(self, job_ids: List[str]) -> List[Dict[str, Any]]:
        """Retrieve specific jobs by list of IDs."""
        if not job_ids:
            return []
        placeholders = ",".join("?" for _ in job_ids)
        with self.get_connection() as conn:
            cur = conn.execute(
                f"SELECT * FROM jobs WHERE id IN ({placeholders}) ORDER BY created_at ASC",
                job_ids
            )
            return [self._row_to_dict(r) for r in cur.fetchall()]

    def get_completed_jobs(
        self,
        job_ids: Optional[List[str]] = None,
        batch_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Retrieve completed jobs with valid output paths."""
        where = ["status = ?", "output_path IS NOT NULL"]
        params: List[Any] = [STATUS_COMPLETED]

        if job_ids:
            placeholders = ",".join("?" for _ in job_ids)
            where.append(f"id IN ({placeholders})")
            params.extend(job_ids)

        if batch_id:
            where.append("batch_id = ?")
            params.append(batch_id)

        where_str = f"WHERE {' AND '.join(where)}"
        with self.get_connection() as conn:
            cur = conn.execute(
                f"SELECT * FROM jobs {where_str} ORDER BY created_at ASC",
                params
            )
            return [self._row_to_dict(r) for r in cur.fetchall()]

    def get_report_statistics(
        self,
        job_ids: Optional[List[str]] = None,
        batch_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Aggregate summary statistics for reporting."""
        where = []
        params: List[Any] = []
        if job_ids:
            placeholders = ",".join("?" for _ in job_ids)
            where.append(f"id IN ({placeholders})")
            params.extend(job_ids)
        if batch_id:
            where.append("batch_id = ?")
            params.append(batch_id)

        where_str = f"WHERE {' AND '.join(where)}" if where else ""

        with self.get_connection() as conn:
            sql = f"""
                SELECT
                    COUNT(*) as total_jobs,
                    SUM(CASE WHEN status = '{STATUS_COMPLETED}' THEN 1 ELSE 0 END) as completed_jobs,
                    SUM(CASE WHEN status = '{STATUS_FAILED}' THEN 1 ELSE 0 END) as failed_jobs,
                    SUM(CASE WHEN status = '{STATUS_CANCELLED}' THEN 1 ELSE 0 END) as cancelled_jobs,
                    SUM(CASE WHEN status IN ('{STATUS_QUEUED}', '{STATUS_VALIDATING}', '{STATUS_RETRY_WAIT}') THEN 1 ELSE 0 END) as pending_jobs,
                    SUM(CASE WHEN status = '{STATUS_PROCESSING}' THEN 1 ELSE 0 END) as processing_jobs,
                    COALESCE(SUM(elapsed_seconds), 0.0) as total_duration,
                    COALESCE(SUM(file_size), 0) as total_input_size,
                    COALESCE(SUM(output_size), 0) as total_output_size,
                    COALESCE(AVG(CASE WHEN status = '{STATUS_COMPLETED}' THEN elapsed_seconds END), 0.0) as avg_duration
                FROM jobs {where_str}
            """
            cur = conn.execute(sql, params)
            row = dict(cur.fetchone())

            total = row["total_jobs"] or 0
            completed = row["completed_jobs"] or 0
            success_rate = round((completed / total * 100), 1) if total > 0 else 0.0
            row["success_rate"] = success_rate
            row["total_duration"] = round(row["total_duration"] or 0.0, 2)
            row["avg_duration"] = round(row["avg_duration"] or 0.0, 2)
            return row

    def get_next_queued_job(self) -> Optional[Dict[str, Any]]:
        """Retrieve the oldest pending QUEUED job."""
        with self.get_connection() as conn:
            cur = conn.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY created_at ASC LIMIT 1",
                (STATUS_QUEUED,)
            )
            row = cur.fetchone()
            if not row:
                return None
            return self._row_to_dict(row)

    def transition_status(
        self,
        job_id: str,
        new_status: str,
        error_message: Optional[str] = None,
        progress: Optional[int] = None,
        output_path: Optional[str] = None,
        output_dimensions: Optional[str] = None,
        output_size: Optional[int] = None,
        elapsed_seconds: Optional[float] = None,
        tile_used: Optional[int] = None,
        is_oom: Optional[bool] = None,
        force: bool = False,
        expected_status: Optional[str] = None
    ) -> bool:
        """
        Safely transition job status following atomic state machine rules.
        Uses single conditional UPDATE transaction to guarantee atomicity and race prevention.
        """
        if new_status not in ALL_STATUSES:
            logger.warning(f"Unknown status '{new_status}' for job {job_id}")
            return False

        now = datetime.now().isoformat()
        with self.get_connection() as conn:
            updates = ["status = ?", "updated_at = ?"]
            params: List[Any] = [new_status, now]

            if error_message is not None:
                updates.append("error_message = ?")
                params.append(error_message)

            if progress is not None:
                updates.append("progress = ?")
                params.append(progress)

            if output_path is not None:
                updates.append("output_path = ?")
                params.append(output_path)

            if output_dimensions is not None:
                updates.append("output_dimensions = ?")
                params.append(output_dimensions)

            if output_size is not None:
                updates.append("output_size = ?")
                params.append(output_size)

            if elapsed_seconds is not None:
                updates.append("elapsed_seconds = ?")
                params.append(elapsed_seconds)

            if tile_used is not None:
                updates.append("tile_used = ?")
                params.append(tile_used)

            if is_oom is not None:
                updates.append("is_oom = ?")
                params.append(1 if is_oom else 0)

            if new_status == STATUS_PROCESSING:
                updates.append("started_at = COALESCE(started_at, ?)")
                params.append(now)

            if new_status == STATUS_COMPLETED:
                updates.append("completed_at = ?")
                params.append(now)
                if progress is None:
                    updates.append("progress = 100")

            if new_status == STATUS_CANCELLED:
                updates.append("cancelled_at = ?")
                params.append(now)

            # Determine WHERE conditions atomically
            where_conditions = ["id = ?"]
            where_params: List[Any] = [job_id]

            if expected_status is not None:
                where_conditions.append("status = ?")
                where_params.append(expected_status)
            elif not force:
                allowed_sources = [s for s, targets in VALID_TRANSITIONS.items() if new_status in targets]
                if not allowed_sources:
                    logger.warning(f"No valid source transitions lead to target status: {new_status}")
                    return False
                placeholders = ", ".join("?" for _ in allowed_sources)
                where_conditions.append(f"status IN ({placeholders})")
                where_params.extend(allowed_sources)
            else:
                # Force transition rules (restricted recovery/admin paths)
                # Terminal statuses (COMPLETED, CANCELLED) cannot be overwritten
                if new_status in (STATUS_PROCESSING, STATUS_RETRY_WAIT, STATUS_VALIDATING):
                    where_conditions.append("status NOT IN (?, ?, ?)")
                    where_params.extend([STATUS_CANCEL_REQUESTED, STATUS_CANCELLED, STATUS_COMPLETED])
                elif new_status == STATUS_QUEUED:
                    where_conditions.append("status NOT IN (?, ?, ?, ?)")
                    where_params.extend([STATUS_CANCEL_REQUESTED, STATUS_CANCELLED, STATUS_COMPLETED, STATUS_QUEUED])
                else:
                    where_conditions.append("status != ?")
                    where_params.append(new_status)

            sql = f"UPDATE jobs SET {', '.join(updates)} WHERE {' AND '.join(where_conditions)}"
            cur = conn.execute(sql, params + where_params)
            conn.commit()

            if cur.rowcount > 0:
                return True

            cur_chk = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,))
            row = cur_chk.fetchone()
            if not row:
                logger.warning(f"Cannot transition status: Job {job_id} not found.")
            else:
                logger.warning(
                    f"Conditional status transition rejected for job {job_id}: current='{row['status']}' -> new='{new_status}', force={force}"
                )
            return False

    def update_job_progress(self, job_id: str, progress: int, detail: Optional[str] = None):
        """Update job progress without changing status."""
        now = datetime.now().isoformat()
        with self.get_connection() as conn:
            conn.execute(
                "UPDATE jobs SET progress = ?, updated_at = ? WHERE id = ?",
                (progress, now, job_id)
            )
            conn.commit()

    def schedule_retry(
        self,
        job_id: str,
        error_message: str,
        is_oom: bool = False
    ) -> Tuple[bool, int, str]:
        """
        Evaluate and atomically schedule retry eligibility.
        Guarantees that cancellation requests are not overwritten and
        retry limits are strictly enforced.
        Returns (should_retry: bool, new_retry_count: int, reason: str).
        """
        err_lower = error_message.lower()
        for non_retry in NON_RETRYABLE_ERRORS:
            if non_retry in err_lower:
                # Mark failed only if still processing/validating (don't overwrite cancel)
                self.transition_status(
                    job_id,
                    STATUS_FAILED,
                    error_message=error_message,
                    is_oom=is_oom,
                    force=False
                )
                with self.get_connection() as conn:
                    cur = conn.execute("SELECT retry_count FROM jobs WHERE id = ?", (job_id,))
                    row = cur.fetchone()
                    rc = row["retry_count"] if row else 0
                return False, rc, f"Non-retryable error: {error_message}"

        now = datetime.now().isoformat()
        with self.get_connection() as conn:
            # Atomic conditional UPDATE for retry
            cur = conn.execute("""
                UPDATE jobs SET
                    status = ?,
                    retry_count = retry_count + 1,
                    error_message = ?,
                    is_oom = ?,
                    updated_at = ?
                WHERE id = ?
                  AND retry_count < max_retries
                  AND status IN (?, ?, ?)
            """, (STATUS_RETRY_WAIT, error_message, 1 if is_oom else 0, now, job_id, STATUS_PROCESSING, STATUS_VALIDATING, STATUS_QUEUED))
            conn.commit()

            if cur.rowcount > 0:
                cur_after = conn.execute("SELECT retry_count, max_retries FROM jobs WHERE id = ?", (job_id,))
                row_after = cur_after.fetchone()
                new_retry = row_after["retry_count"]
                max_retries = row_after["max_retries"]
                return True, new_retry, f"Retry scheduled ({new_retry}/{max_retries})"

            cur_chk = conn.execute("SELECT status, retry_count, max_retries FROM jobs WHERE id = ?", (job_id,))
            row = cur_chk.fetchone()
            if not row:
                return False, 0, "Job not found"

            current_status = row["status"]
            retry_count = row["retry_count"]
            max_retries = row["max_retries"]

            if current_status in (STATUS_CANCEL_REQUESTED, STATUS_CANCELLED):
                return False, retry_count, f"Job was cancelled ({current_status}); retry aborted"

            if retry_count >= max_retries:
                final_msg = f"{error_message} (Max retries {max_retries} exceeded)"
                self.transition_status(
                    job_id,
                    STATUS_FAILED,
                    error_message=final_msg,
                    is_oom=is_oom,
                    force=False
                )
                return False, retry_count, final_msg

            return False, retry_count, f"Job in ineligible status '{current_status}' for retry"

    def cancel_job(self, job_id: str) -> bool:
        """Request cancellation for a job atomically."""
        now = datetime.now().isoformat()
        with self.get_connection() as conn:
            # 1. Try immediate cancel if in queued or retry-wait
            cur = conn.execute("""
                UPDATE jobs SET
                    status = ?,
                    cancelled_at = ?,
                    updated_at = ?
                WHERE id = ? AND status IN (?, ?)
            """, (STATUS_CANCELLED, now, now, job_id, STATUS_QUEUED, STATUS_RETRY_WAIT))
            if cur.rowcount > 0:
                conn.commit()
                return True

            # 2. Try cooperative cancel request if in processing or validating
            cur = conn.execute("""
                UPDATE jobs SET
                    status = ?,
                    updated_at = ?
                WHERE id = ? AND status IN (?, ?)
            """, (STATUS_CANCEL_REQUESTED, now, job_id, STATUS_PROCESSING, STATUS_VALIDATING))
            if cur.rowcount > 0:
                conn.commit()
                return True

            # 3. Check if already cancelled or cancel requested
            cur = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,))
            row = cur.fetchone()
            if not row:
                return False
            if row["status"] in (STATUS_CANCEL_REQUESTED, STATUS_CANCELLED):
                return True
            return False

    def cancel_all_pending(self) -> int:
        """Cancel all pending, validating, and retry-wait jobs."""
        now = datetime.now().isoformat()
        with self.get_connection() as conn:
            cur = conn.execute("""
                UPDATE jobs SET
                    status = ?,
                    cancelled_at = ?,
                    updated_at = ?
                WHERE status IN (?, ?, ?)
            """, (STATUS_CANCELLED, now, now, STATUS_QUEUED, STATUS_VALIDATING, STATUS_RETRY_WAIT))
            cancelled_count = cur.rowcount

            # Mark running jobs as CANCEL_REQUESTED
            conn.execute("""
                UPDATE jobs SET
                    status = ?,
                    updated_at = ?
                WHERE status = ?
            """, (STATUS_CANCEL_REQUESTED, now, STATUS_PROCESSING))
            conn.commit()

        return cancelled_count

    def is_valid_source_file(self, file_path: Optional[Union[str, Path]]) -> bool:
        """
        Verify whether the source path:
        1. Is present (not None/empty)
        2. Is a regular file (not a directory)
        3. Is readable by the current process
        """
        if not file_path:
            return False
        try:
            p = Path(file_path)
            if not p.is_absolute():
                base_dir = self.db_path.parent.parent
                p = (base_dir / p).resolve()
            return p.is_file() and os.access(str(p), os.R_OK)
        except Exception:
            return False

    def recover_on_startup(self, validator_func: Optional[Any] = None) -> Dict[str, Any]:
        """
        Crash recovery executed on server boot:
        1. Identifies jobs interrupted in PROCESSING or VALIDATING.
        2. Inspects disk for completed valid output (requires full image validation).
        3. Cleans up orphaned .tmp files safely.
        4. Recovers jobs to COMPLETED or QUEUED/FAILED (rejecting missing source).
        5. Recovers RETRY_WAIT jobs according to retry policy (rejecting missing source).
        6. Finalizes CANCEL_REQUESTED jobs to CANCELLED.
        7. Returns list of eligible QUEUED job IDs for deterministic queue restoration (filtering out missing source).
        """
        logger.info("Executing Phase 3 Startup Job Recovery...")
        recovered_jobs = []

        with self.get_connection() as conn:
            # 1. Inspect uncompleted PROCESSING and VALIDATING jobs
            cur = conn.execute(
                "SELECT * FROM jobs WHERE status IN (?, ?)",
                (STATUS_PROCESSING, STATUS_VALIDATING)
            )
            interrupted_rows = cur.fetchall()

            for row in interrupted_rows:
                jid = row["id"]
                in_path_str = row["input_path"]
                out_path_str = row["output_path"]
                retries = row["retry_count"]
                max_retries = row["max_retries"]

                is_complete = False
                if out_path_str:
                    out_p = Path(out_path_str)
                    if out_p.is_file() and out_p.stat().st_size > 0:
                        if validator_func:
                            try:
                                is_valid, _, _, _ = validator_func(out_p)
                                is_complete = bool(is_valid)
                            except Exception:
                                is_complete = False
                        else:
                            # Avoid marking a job COMPLETED solely because a nonempty file exists
                            is_complete = False

                if is_complete:
                    logger.info(f"Recovery: Job {jid} found valid output on disk. Marking COMPLETED.")
                    self.transition_status(jid, STATUS_COMPLETED, force=True)
                    recovered_jobs.append({"id": jid, "action": "marked_completed"})
                else:
                    # Clean up any partial temp file
                    if out_path_str:
                        tmp_cand = Path(f"{out_path_str}.tmp")
                        if tmp_cand.is_file():
                            try:
                                tmp_cand.unlink()
                            except Exception:
                                pass

                    if not self.is_valid_source_file(in_path_str):
                        logger.warning(f"Recovery: Job {jid} source file missing or invalid ({in_path_str}). Marking FAILED.")
                        self.transition_status(
                            jid,
                            STATUS_FAILED,
                            error_message="SOURCE_FILE_MISSING: Source file is missing, not a regular file, or unreadable.",
                            force=True
                        )
                        recovered_jobs.append({"id": jid, "action": "marked_failed_missing_source"})
                    elif retries < max_retries:
                        logger.info(f"Recovery: Job {jid} interrupted mid-process. Re-queueing for retry.")
                        self.transition_status(
                            jid,
                            STATUS_QUEUED,
                            error_message="Recovered from unexpected shutdown; re-queued.",
                            force=True
                        )
                        recovered_jobs.append({"id": jid, "action": "requeued"})
                    else:
                        logger.info(f"Recovery: Job {jid} exceeded max retries. Marking FAILED.")
                        self.transition_status(
                            jid,
                            STATUS_FAILED,
                            error_message="Interrupted by unexpected shutdown; max retries exceeded.",
                            force=True
                        )
                        recovered_jobs.append({"id": jid, "action": "marked_failed"})

            # 2. Finalize any CANCEL_REQUESTED jobs
            cur_cancel = conn.execute(
                "SELECT id, output_path FROM jobs WHERE status = ?",
                (STATUS_CANCEL_REQUESTED,)
            )
            for row in cur_cancel.fetchall():
                jid = row["id"]
                out_path_str = row["output_path"]
                if out_path_str:
                    tmp_cand = Path(f"{out_path_str}.tmp")
                    if tmp_cand.is_file():
                        try:
                            tmp_cand.unlink()
                        except Exception:
                            pass
                self.transition_status(jid, STATUS_CANCELLED, force=True)
                recovered_jobs.append({"id": jid, "action": "cancelled"})

            # 3. Recover RETRY_WAIT jobs according to retry policy
            cur_retry = conn.execute(
                "SELECT id, input_path, retry_count, max_retries FROM jobs WHERE status = ?",
                (STATUS_RETRY_WAIT,)
            )
            for row in cur_retry.fetchall():
                jid = row["id"]
                in_path_str = row["input_path"]
                retries = row["retry_count"]
                max_retries = row["max_retries"]

                if not self.is_valid_source_file(in_path_str):
                    logger.warning(f"Recovery: RETRY_WAIT Job {jid} source file missing or invalid ({in_path_str}). Marking FAILED.")
                    self.transition_status(
                        jid,
                        STATUS_FAILED,
                        error_message="SOURCE_FILE_MISSING: Source file is missing, not a regular file, or unreadable.",
                        force=True
                    )
                    recovered_jobs.append({"id": jid, "action": "marked_failed_missing_source"})
                elif retries < max_retries:
                    logger.info(f"Recovery: Job {jid} in RETRY_WAIT re-queued.")
                    self.transition_status(
                        jid,
                        STATUS_QUEUED,
                        error_message="Recovered from RETRY_WAIT; re-queued.",
                        force=True
                    )
                    recovered_jobs.append({"id": jid, "action": "requeued"})
                else:
                    logger.info(f"Recovery: Job {jid} in RETRY_WAIT exceeded max retries. Marking FAILED.")
                    self.transition_status(
                        jid,
                        STATUS_FAILED,
                        error_message="Interrupted in RETRY_WAIT; max retries exceeded.",
                        force=True
                    )
                    recovered_jobs.append({"id": jid, "action": "marked_failed"})

            # 4. Collect all eligible QUEUED jobs in deterministic order, filtering out missing sources
            cur_queued = conn.execute(
                "SELECT id, input_path FROM jobs WHERE status = ? ORDER BY created_at ASC",
                (STATUS_QUEUED,)
            )
            queued_rows = cur_queued.fetchall()
            eligible_job_ids = []
            for r in queued_rows:
                jid = r["id"]
                in_path_str = r["input_path"]
                if not self.is_valid_source_file(in_path_str):
                    logger.warning(f"Recovery: QUEUED Job {jid} source file missing or invalid ({in_path_str}). Marking FAILED.")
                    self.transition_status(
                        jid,
                        STATUS_FAILED,
                        error_message="SOURCE_FILE_MISSING: Source file is missing, not a regular file, or unreadable.",
                        force=True
                    )
                    recovered_jobs.append({"id": jid, "action": "marked_failed_missing_source"})
                else:
                    eligible_job_ids.append(jid)

        logger.info(f"Startup Recovery completed: {len(recovered_jobs)} jobs processed, {len(eligible_job_ids)} queued jobs eligible.")
        return {
            "recovered_count": len(recovered_jobs),
            "details": recovered_jobs,
            "eligible_job_ids": eligible_job_ids
        }

    def is_cancellation_requested(self, job_id: str) -> bool:
        """Check if cancellation has been requested for this job."""
        with self.get_connection() as conn:
            cur = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,))
            row = cur.fetchone()
            if not row:
                return False
            return row["status"] in (STATUS_CANCEL_REQUESTED, STATUS_CANCELLED)

    def reset_for_retry(self, job_id: str) -> bool:
        """Reset a failed, cancelled, or retry-wait job to QUEUED state for a new run."""
        with self.get_connection() as conn:
            now = datetime.now().isoformat()
            cur = conn.execute("""
                UPDATE jobs SET
                    status = ?,
                    retry_count = 0,
                    error_message = NULL,
                    is_oom = 0,
                    progress = 0,
                    elapsed_seconds = 0.0,
                    started_at = NULL,
                    completed_at = NULL,
                    cancelled_at = NULL,
                    updated_at = ?
                WHERE id = ? AND status IN (?, ?, ?, ?)
            """, (STATUS_QUEUED, now, job_id, STATUS_FAILED, STATUS_CANCELLED, STATUS_RETRY_WAIT, STATUS_QUEUED))
            conn.commit()
            return cur.rowcount > 0

    def delete_job(self, job_id: str) -> bool:
        """Remove a job record from SQLite."""
        with self.get_connection() as conn:
            cur = conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            conn.commit()
            return cur.rowcount > 0

    def clear_finished_jobs(self) -> int:
        """Clear completed and cancelled jobs."""
        with self.get_connection() as conn:
            cur = conn.execute(
                "DELETE FROM jobs WHERE status IN (?, ?)",
                (STATUS_COMPLETED, STATUS_CANCELLED)
            )
            conn.commit()
            return cur.rowcount

    def get_stats(self) -> Dict[str, int]:
        """Aggregate counts by status."""
        stats = {st: 0 for st in ALL_STATUSES}
        stats["TOTAL"] = 0

        with self.get_connection() as conn:
            cur = conn.execute("SELECT status, COUNT(*) as cnt FROM jobs GROUP BY status")
            for row in cur.fetchall():
                st = row["status"]
                cnt = row["cnt"]
                if st in stats:
                    stats[st] = cnt
                stats["TOTAL"] += cnt

        return stats

    def _row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        """Convert a sqlite3.Row to standard job dictionary."""
        d = dict(row)
        # Parse processing_config JSON
        cfg_str = d.get("processing_config")
        if cfg_str:
            try:
                d["processing_config"] = json.loads(cfg_str)
            except Exception:
                d["processing_config"] = {}
        else:
            d["processing_config"] = {}

        d["is_oom"] = bool(d.get("is_oom", 0))
        d["thumbnail_url"] = f"/api/thumbnail/{d['id']}"
        return d


# Global singleton helper
_global_job_manager: Optional[JobManager] = None


def get_job_manager(
    base_dir: Optional[Path] = None,
    config: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path] = None
) -> JobManager:
    global _global_job_manager
    if _global_job_manager is None:
        target_db = db_path
        if target_db is None and config and "reliability" in config and "db_path" in config["reliability"]:
            target_db = (base_dir or Path(".")).resolve() / config["reliability"]["db_path"]
        _global_job_manager = JobManager(db_path=target_db)
    return _global_job_manager
