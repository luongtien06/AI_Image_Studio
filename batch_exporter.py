# -*- coding: utf-8 -*-
"""
Batch Exporter Module for AI Image Studio (Phase 4)
Handles streaming ZIP archive creation for completed upscale results with
strict security controls: path traversal rejection, duplicate filename handling,
file integrity verification, memory-bounded streaming, and temporary file cleanup.
"""

import logging
import os
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from job_manager import STATUS_COMPLETED

logger = logging.getLogger(__name__)


class BatchExporter:
    """
    Manages generation of ZIP archives containing only completed upscale outputs.
    Guarantees thread safety and zero interference with the GPU AI Worker.
    """

    CHUNK_SIZE = 128 * 1024  # 128 KB buffer for streaming file reads
    MAX_ARCHIVE_FILES = 5000
    MAX_TOTAL_BYTES = 4 * 1024 * 1024 * 1024  # 4 GB limit

    def __init__(self, export_dir: Path):
        self.export_dir = Path(export_dir)
        self.export_dir.mkdir(parents=True, exist_ok=True)

    def cleanup_expired_archives(self, max_age_seconds: int = 3600) -> int:
        """Clean up temporary ZIP exports older than max_age_seconds. Returns removed count."""
        now = time.time()
        cleaned_count = 0
        try:
            for item in self.export_dir.glob("*.zip"):
                if item.is_file():
                    mtime = item.stat().st_mtime
                    if now - mtime > max_age_seconds:
                        try:
                            item.unlink()
                            cleaned_count += 1
                            logger.info(f"Cleaned up expired export archive: {item.name}")
                        except Exception as e:
                            logger.warning(f"Failed to remove expired archive {item.name}: {e}")
        except Exception as e:
            logger.error(f"Error during archive cleanup: {e}")
        return cleaned_count

    def create_zip_archive(
        self,
        jobs: List[Dict[str, Any]],
        archive_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Creates a ZIP archive containing ONLY completed outputs from jobs.
        Returns a dictionary with status, archive_path, file_count, and total_bytes.
        """
        self.cleanup_expired_archives()

        if not jobs:
            return {
                "success": False,
                "error": "No jobs provided for ZIP export.",
                "total_files": 0,
                "total_bytes": 0,
                "archive_path": None
            }

        if len(jobs) > self.MAX_ARCHIVE_FILES:
            return {
                "success": False,
                "error": f"Batch size exceeds maximum limit of {self.MAX_ARCHIVE_FILES} files.",
                "total_files": 0,
                "total_bytes": 0,
                "archive_path": None
            }

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        uid = uuid.uuid4().hex[:6]
        base_name = archive_name or f"ImageStudio_Export_{ts}_{uid}.zip"
        if not base_name.lower().endswith(".zip"):
            base_name += ".zip"

        # Sanitize archive filename
        safe_archive_name = "".join(c for c in base_name if c.isalnum() or c in ("-", "_", "."))
        archive_path = self.export_dir / safe_archive_name
        temp_zip_path = self.export_dir / f"{safe_archive_name}.tmp"

        added_files = 0
        total_uncompressed_bytes = 0
        used_archive_names: Set[str] = set()

        try:
            with zipfile.ZipFile(str(temp_zip_path), mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                for job in jobs:
                    # 1. Enforce completed status check
                    raw_status = str(job.get("status", "")).upper()
                    if raw_status != STATUS_COMPLETED and raw_status != "COMPLETED":
                        continue

                    # 2. Enforce output_path validity
                    out_path_str = job.get("output_path")
                    if not out_path_str:
                        continue

                    out_path = Path(out_path_str)
                    if not out_path.is_file():
                        logger.warning(f"Export skipping missing output: {out_path}")
                        continue

                    # Strict leak protection: reject .tmp files or non-image extensions
                    if out_path.suffix.lower() == ".tmp" or out_path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
                        logger.warning(f"Export skipping invalid file extension: {out_path.name}")
                        continue

                    file_size = out_path.stat().st_size
                    if file_size <= 0:
                        continue

                    if total_uncompressed_bytes + file_size > self.MAX_TOTAL_BYTES:
                        logger.warning("Export reached maximum total size limit; truncating remaining.")
                        break

                    # 3. Path Traversal Protection: strictly use filename only
                    base_filename = out_path.name

                    # 4. Deduplicate archive filenames
                    stem = out_path.stem
                    suffix = out_path.suffix
                    target_name = base_filename
                    collision_idx = 1
                    while target_name.lower() in used_archive_names:
                        target_name = f"{stem}_{collision_idx}{suffix}"
                        collision_idx += 1

                    used_archive_names.add(target_name.lower())

                    # 5. Stream file into ZIP archive using buffer
                    with open(out_path, "rb") as src_f, zf.open(target_name, "w") as dst_f:
                        while True:
                            chunk = src_f.read(self.CHUNK_SIZE)
                            if not chunk:
                                break
                            dst_f.write(chunk)

                    added_files += 1
                    total_uncompressed_bytes += file_size

            if added_files == 0:
                if temp_zip_path.exists():
                    temp_zip_path.unlink()
                return {
                    "success": False,
                    "error": "No valid completed output files were found to export.",
                    "total_files": 0,
                    "total_bytes": 0,
                    "archive_path": None
                }

            # Atomic rename to final archive
            temp_zip_path.replace(archive_path)

            archive_size = archive_path.stat().st_size
            logger.info(
                f"Export ZIP created: {archive_path.name} | "
                f"Files: {added_files} | Size: {archive_size:,} bytes"
            )

            return {
                "success": True,
                "error": None,
                "archive_path": str(archive_path),
                "archive_filename": archive_path.name,
                "total_files": added_files,
                "uncompressed_bytes": total_uncompressed_bytes,
                "archive_size_bytes": archive_size
            }

        except Exception as e:
            logger.error(f"Failed to generate ZIP archive: {e}")
            if temp_zip_path.exists():
                try:
                    temp_zip_path.unlink()
                except Exception:
                    pass
            return {
                "success": False,
                "error": str(e),
                "total_files": added_files,
                "total_bytes": total_uncompressed_bytes,
                "archive_path": None
            }
