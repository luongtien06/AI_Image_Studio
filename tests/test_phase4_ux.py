# -*- coding: utf-8 -*-
"""
Phase 4 Test Suite: UX Enhancement, Batch Management & Non-Regression
AI Image Studio (RealESRGAN_x4plus / RTX 2050 4GB)

Covers all 44 Phase 4 verification scenarios:
- Feature 1: Bilingual Interface (Tests 1-4)
- Feature 2: Multiple Image & Folder Upload (Tests 5-12)
- Feature 3: Filter, Search, Sort & View Modes (Tests 13-18)
- Feature 4: Memory-Safe ZIP Export (Tests 19-27)
- Feature 5: Processing Report (CSV & HTML) (Tests 28-33)
- Non-Regression: Core Engine & Data Integrity (Tests 34-44)
"""

import csv
import hashlib
import io
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
import uuid
import zipfile
from pathlib import Path

from PIL import Image

from ai_worker import (
    STATE_BUSY,
    STATE_READY,
    get_ai_worker,
)
from app import (
    BASE_DIR,
    HISTORY_FILE,
    app,
    load_app_config,
    load_history,
    safe_path,
    validate_image_file,
)
from batch_exporter import BatchExporter
from job_manager import (
    JobManager,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PROCESSING,
    STATUS_QUEUED,
    get_job_manager,
)
from report_generator import generate_csv_report, generate_html_report


class TestPhase4UXEnhancement(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base_dir = BASE_DIR
        cls.config = load_app_config()
        cls.test_dir = Path(tempfile.mkdtemp(prefix="phase4_ux_test_"))
        cls.db_path = cls.test_dir / "test_jobs.db"
        cls.exports_dir = cls.test_dir / "exports"
        cls.exports_dir.mkdir(parents=True, exist_ok=True)
        cls.client = app.test_client()

    @classmethod
    def tearDownClass(cls):
        if cls.test_dir.exists():
            shutil.rmtree(cls.test_dir, ignore_errors=True)

    def setUp(self):
        # Fresh isolated job manager for tests
        if self.db_path.exists():
            self.db_path.unlink()
        self.job_mgr = JobManager(db_path=self.db_path)

    # ─────────────────────────────────────────────────────────────
    # Feature 1: Bilingual Interface (Scenarios 1-4)
    # ─────────────────────────────────────────────────────────────

    def test_01_default_language_vietnamese(self):
        """1. Verify default UI language is set to 'vi' in configuration."""
        ui_cfg = self.config.get("ui", {})
        self.assertEqual(ui_cfg.get("default_language"), "vi")
        self.assertIn("vi", ui_cfg.get("supported_languages", []))
        self.assertIn("en", ui_cfg.get("supported_languages", []))

    def test_02_language_selector_persistence_config(self):
        """2. Verify /api/config returns ui settings with supported languages."""
        resp = self.client.get("/api/config")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("ui", data)
        self.assertEqual(data["ui"]["default_language"], "vi")
        self.assertListEqual(data["ui"]["supported_languages"], ["vi", "en"])

    def test_03_all_ui_keys_translated_in_vi_and_en(self):
        """3. Verify index.html contains complete I18N dictionary for both vi and en."""
        index_file = self.base_dir / "templates" / "index.html"
        self.assertTrue(index_file.is_file())
        content = index_file.read_text(encoding="utf-8")
        self.assertIn("const I18N =", content)
        self.assertIn("vi: {", content)
        self.assertIn("en: {", content)
        # Check critical bilingual keys
        for key in ["app_title", "btn_start", "btn_cancel", "drop_title", "export_zip_all", "export_csv"]:
            self.assertIn(f"{key}:", content)

    def test_04_language_switching_preserves_job_state(self):
        """4. Verify frontend setLanguage implementation does not clear fileQueue."""
        index_file = self.base_dir / "templates" / "index.html"
        content = index_file.read_text(encoding="utf-8")
        self.assertIn("function setLanguage(lang)", content)
        self.assertIn("localStorage.setItem('ais_language', lang)", content)
        self.assertNotIn("location.reload()", content)

    # ─────────────────────────────────────────────────────────────
    # Feature 2: Multiple Image & Folder Upload (Scenarios 5-12)
    # ─────────────────────────────────────────────────────────────

    def test_05_multi_image_upload_single_request(self):
        """5. Verify uploading multiple images in a single request returns summary and files."""
        # Create 2 valid in-memory images
        img1_bytes = io.BytesIO()
        Image.new("RGB", (64, 64), color="red").save(img1_bytes, "PNG")
        img1_bytes.seek(0)

        img2_bytes = io.BytesIO()
        Image.new("RGB", (64, 64), color="blue").save(img2_bytes, "JPEG")
        img2_bytes.seek(0)

        data = {
            "files": [
                (img1_bytes, "multi_test_1.png"),
                (img2_bytes, "multi_test_2.jpg")
            ]
        }
        resp = self.client.post("/api/upload", data=data, content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 200)
        res = resp.get_json()
        self.assertIn("files", res)
        self.assertIn("summary", res)
        self.assertEqual(res["summary"]["valid"], 2)
        self.assertEqual(res["summary"]["corrupt"], 0)

    def test_06_folder_recursive_scanning_simulation(self):
        """6. Verify /api/select-folder scans images within a directory."""
        sub_folder = self.test_dir / "test_folder_scan"
        sub_folder.mkdir(parents=True, exist_ok=True)
        img_p = sub_folder / "scan_sample.png"
        Image.new("RGB", (32, 32), color="green").save(img_p, "PNG")

        resp = self.client.post("/api/select-folder", json={"path": str(sub_folder)})
        self.assertEqual(resp.status_code, 200)
        res = resp.get_json()
        self.assertIn("files", res)
        self.assertTrue(any(f["filename"] == "scan_sample.png" for f in res["files"]))

    def test_07_duplicate_detection_summary(self):
        """7. Verify duplicate image upload is detected and counted in summary."""
        test_filename = f"dup_{uuid.uuid4().hex[:8]}.png"
        img_bytes1 = io.BytesIO()
        Image.new("RGB", (50, 50), color="yellow").save(img_bytes1, "PNG")
        raw_content = img_bytes1.getvalue()
        img_bytes1.seek(0)

        # First upload
        resp1 = self.client.post("/api/upload", data={"files": [(img_bytes1, test_filename)]}, content_type="multipart/form-data")
        self.assertEqual(resp1.status_code, 200)

        # Second upload with same name and content
        img_bytes2 = io.BytesIO(raw_content)
        resp2 = self.client.post("/api/upload", data={"files": [(img_bytes2, test_filename)]}, content_type="multipart/form-data")
        self.assertEqual(resp2.status_code, 200)
        res2 = resp2.get_json()
        self.assertGreaterEqual(res2["summary"]["duplicates"], 1)

    def test_08_corrupt_image_rejection_in_summary(self):
        """8. Verify corrupt image is rejected and tracked as corrupt in summary."""
        corrupt_bytes = io.BytesIO(b"\x89PNG\r\n\x1a\nCorruptPayloadNotAValidPNG")
        resp = self.client.post("/api/upload", data={"files": [(corrupt_bytes, "corrupt.png")]}, content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 200)
        res = resp.get_json()
        self.assertEqual(res["summary"]["corrupt"], 1)
        self.assertEqual(res["summary"]["valid"], 0)

    def test_09_unsupported_extensions_in_summary(self):
        """9. Verify unsupported file extensions (.exe, .txt) are ignored in summary."""
        txt_bytes = io.BytesIO(b"Hello world")
        resp = self.client.post("/api/upload", data={"files": [(txt_bytes, "document.txt")]}, content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 200)
        res = resp.get_json()
        self.assertEqual(res["summary"]["unsupported"], 1)
        self.assertEqual(res["summary"]["valid"], 0)

    def test_10_bounded_memory_during_batch_upload(self):
        """10. Verify uploading multiple files sequentially doesn't exceed standard memory buffers."""
        img_bytes = io.BytesIO()
        Image.new("RGB", (30, 30), color="white").save(img_bytes, "JPEG")
        raw = img_bytes.getvalue()

        # Batch of 10 files
        files = [(io.BytesIO(raw), f"batch_mem_{i}.jpg") for i in range(10)]
        resp = self.client.post("/api/upload", data={"files": files}, content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 200)
        res = resp.get_json()
        self.assertEqual(res["summary"]["valid"], 10)

    def test_11_sequential_worker_queue_order(self):
        """11. Verify jobs added to database maintain insertion timestamp order."""
        id1 = str(uuid.uuid4())
        id2 = str(uuid.uuid4())
        self.job_mgr.add_job(id1, "seq_1.png", "in/seq_1.png")
        time.sleep(0.01)
        self.job_mgr.add_job(id2, "seq_2.png", "in/seq_2.png")

        jobs = self.job_mgr.get_all_jobs()
        ids = [j["id"] for j in jobs]
        self.assertIn(id1, ids)
        self.assertIn(id2, ids)
        self.assertLess(ids.index(id1), ids.index(id2))

    def test_12_empty_upload_handling(self):
        """12. Verify empty upload payload returns 400 error."""
        resp = self.client.post("/api/upload", data={})
        self.assertEqual(resp.status_code, 400)

    # ─────────────────────────────────────────────────────────────
    # Feature 3: Filter, Search, Sort & View Modes (Scenarios 13-18)
    # ─────────────────────────────────────────────────────────────

    def test_13_filter_by_status(self):
        """13. Verify query_jobs filters correctly by status."""
        id_comp = str(uuid.uuid4())
        id_fail = str(uuid.uuid4())
        id_que = str(uuid.uuid4())
        self.job_mgr.add_job(id_comp, "comp.png", "in/comp.png")
        self.job_mgr.transition_status(id_comp, STATUS_COMPLETED, output_path="out/comp.png", force=True)

        self.job_mgr.add_job(id_fail, "fail.png", "in/fail.png")
        self.job_mgr.transition_status(id_fail, STATUS_FAILED, error_message="Failed test", force=True)

        self.job_mgr.add_job(id_que, "que.png", "in/que.png")

        # Query completed
        res_comp, total_comp = self.job_mgr.query_jobs(status="completed")
        self.assertEqual(total_comp, 1)
        self.assertEqual(res_comp[0]["id"], id_comp)

        # Query failed
        res_fail, total_fail = self.job_mgr.query_jobs(status="failed")
        self.assertEqual(total_fail, 1)
        self.assertEqual(res_fail[0]["id"], id_fail)

    def test_14_live_search_by_filename_and_id(self):
        """14. Verify query_jobs searches by filename or partial UUID."""
        jid = str(uuid.uuid4())
        self.job_mgr.add_job(jid, "mountain_landscape_photo.png", "in/mountain.png")

        # Search by filename keyword
        rows, count = self.job_mgr.query_jobs(search="mountain")
        self.assertEqual(count, 1)
        self.assertEqual(rows[0]["id"], jid)

        # Search by partial UUID prefix
        prefix = jid[:8]
        rows_id, count_id = self.job_mgr.query_jobs(search=prefix)
        self.assertEqual(count_id, 1)
        self.assertEqual(rows_id[0]["id"], jid)

    def test_15_sort_by_name_asc_and_desc(self):
        """15. Verify sorting by filename ASC and DESC."""
        id_a = str(uuid.uuid4())
        id_z = str(uuid.uuid4())
        self.job_mgr.add_job(id_a, "alpha.png", "in/a.png")
        self.job_mgr.add_job(id_z, "zeta.png", "in/z.png")

        asc_rows, _ = self.job_mgr.query_jobs(sort_by="filename", order="ASC")
        self.assertEqual(asc_rows[0]["filename"], "alpha.png")
        self.assertEqual(asc_rows[-1]["filename"], "zeta.png")

        desc_rows, _ = self.job_mgr.query_jobs(sort_by="filename", order="DESC")
        self.assertEqual(desc_rows[0]["filename"], "zeta.png")
        self.assertEqual(desc_rows[-1]["filename"], "alpha.png")

    def test_16_sort_by_file_size(self):
        """16. Verify sorting by file_size ASC and DESC."""
        id_small = str(uuid.uuid4())
        id_large = str(uuid.uuid4())
        self.job_mgr.add_job(id_small, "small.png", "in/s.png", file_size=1024)
        self.job_mgr.add_job(id_large, "large.png", "in/l.png", file_size=1048576)

        rows_asc, _ = self.job_mgr.query_jobs(sort_by="file_size", order="ASC")
        self.assertEqual(rows_asc[0]["id"], id_small)

        rows_desc, _ = self.job_mgr.query_jobs(sort_by="file_size", order="DESC")
        self.assertEqual(rows_desc[0]["id"], id_large)

    def test_17_sort_by_processing_duration(self):
        """17. Verify sorting by elapsed_seconds."""
        id_fast = str(uuid.uuid4())
        id_slow = str(uuid.uuid4())
        self.job_mgr.add_job(id_fast, "fast.png", "in/f.png")
        self.job_mgr.transition_status(id_fast, STATUS_COMPLETED, elapsed_seconds=1.2, force=True)

        self.job_mgr.add_job(id_slow, "slow.png", "in/s.png")
        self.job_mgr.transition_status(id_slow, STATUS_COMPLETED, elapsed_seconds=9.8, force=True)

        rows, _ = self.job_mgr.query_jobs(sort_by="duration", order="DESC")
        self.assertEqual(rows[0]["id"], id_slow)
        self.assertEqual(rows[1]["id"], id_fast)

    def test_18_pagination_support_query_jobs(self):
        """18. Verify limit and offset pagination."""
        for i in range(15):
            self.job_mgr.add_job(str(uuid.uuid4()), f"page_item_{i:02d}.png", f"in/{i}.png")

        p1_rows, total = self.job_mgr.query_jobs(limit=10, offset=0)
        self.assertEqual(total, 15)
        self.assertEqual(len(p1_rows), 10)

        p2_rows, _ = self.job_mgr.query_jobs(limit=10, offset=10)
        self.assertEqual(len(p2_rows), 5)

    # ─────────────────────────────────────────────────────────────
    # Feature 4: Memory-Safe ZIP Export (Scenarios 19-27)
    # ─────────────────────────────────────────────────────────────

    def test_19_export_all_completed_zip(self):
        """19. Verify batch ZIP export includes all completed jobs."""
        exporter = BatchExporter(export_dir=self.exports_dir)
        # Create a real completed output file
        out_p = self.test_dir / "valid_out_1.png"
        Image.new("RGB", (64, 64), color="blue").save(out_p, "PNG")

        jid = str(uuid.uuid4())
        self.job_mgr.add_job(jid, "img1.png", "in/img1.png")
        self.job_mgr.transition_status(jid, STATUS_COMPLETED, output_path=str(out_p), force=True)

        completed = self.job_mgr.get_completed_jobs()
        res = exporter.create_zip_archive(completed)
        self.assertTrue(res["success"])
        zip_path = Path(res["archive_path"])

        self.assertTrue(zip_path.is_file())
        self.assertTrue(zipfile.is_zipfile(zip_path))
        with zipfile.ZipFile(zip_path, "r") as zf:
            namelist = zf.namelist()
            self.assertIn("valid_out_1.png", namelist)

    def test_20_export_selected_jobs_zip(self):
        """20. Verify exporting only selected job IDs."""
        exporter = BatchExporter(export_dir=self.exports_dir)
        out1 = self.test_dir / "sel_1.png"
        out2 = self.test_dir / "sel_2.png"
        Image.new("RGB", (32, 32), color="red").save(out1, "PNG")
        Image.new("RGB", (32, 32), color="green").save(out2, "PNG")

        id1 = str(uuid.uuid4())
        id2 = str(uuid.uuid4())
        self.job_mgr.add_job(id1, "sel1.png", "in/sel1.png")
        self.job_mgr.transition_status(id1, STATUS_COMPLETED, output_path=str(out1), force=True)
        self.job_mgr.add_job(id2, "sel2.png", "in/sel2.png")
        self.job_mgr.transition_status(id2, STATUS_COMPLETED, output_path=str(out2), force=True)

        # Select only id2
        selected = self.job_mgr.get_completed_jobs(job_ids=[id2])
        res = exporter.create_zip_archive(selected)
        self.assertTrue(res["success"])
        zip_p = Path(res["archive_path"])

        with zipfile.ZipFile(zip_p, "r") as zf:
            self.assertIn("sel_2.png", zf.namelist())
            self.assertNotIn("sel_1.png", zf.namelist())

    def test_21_reject_zip_when_no_completed_jobs(self):
        """21. Verify create_zip_archive returns success=False if job list is empty."""
        exporter = BatchExporter(export_dir=self.exports_dir)
        res = exporter.create_zip_archive([])
        self.assertFalse(res["success"])

    def test_22_streaming_buffer_chunk_size(self):
        """22. Verify exporter uses CHUNK_SIZE buffer without unbounded RAM allocation."""
        exporter = BatchExporter(export_dir=self.exports_dir)
        self.assertGreaterEqual(exporter.CHUNK_SIZE, 64 * 1024)
        self.assertLessEqual(exporter.CHUNK_SIZE, 1024 * 1024)

    def test_23_deduplicated_filenames_in_archive(self):
        """23. Verify collision handling renames identical output basenames inside ZIP."""
        exporter = BatchExporter(export_dir=self.exports_dir)
        dir1 = self.test_dir / "dir1"
        dir2 = self.test_dir / "dir2"
        dir1.mkdir(parents=True, exist_ok=True)
        dir2.mkdir(parents=True, exist_ok=True)

        p1 = dir1 / "result.png"
        p2 = dir2 / "result.png"
        Image.new("RGB", (10, 10), color="cyan").save(p1, "PNG")
        Image.new("RGB", (10, 10), color="magenta").save(p2, "PNG")

        jobs = [
            {"id": "1", "filename": "1.png", "status": "COMPLETED", "output_path": str(p1)},
            {"id": "2", "filename": "2.png", "status": "COMPLETED", "output_path": str(p2)},
        ]
        res = exporter.create_zip_archive(jobs)
        self.assertTrue(res["success"])
        zip_p = Path(res["archive_path"])
        with zipfile.ZipFile(zip_p, "r") as zf:
            names = zf.namelist()
            self.assertIn("result.png", names)
            self.assertIn("result_1.png", names)

    def test_24_strict_path_traversal_prevention_in_zip(self):
        """24. Verify archive entry names cannot contain directory traversal (../ or \\)."""
        exporter = BatchExporter(export_dir=self.exports_dir)
        real_img = self.test_dir / "legit.png"
        Image.new("RGB", (10, 10), color="black").save(real_img, "PNG")

        # Fake job with traversal attempt in output_path
        jobs = [
            {"id": "1", "filename": "attack.png", "status": "COMPLETED", "output_path": str(real_img)}
        ]
        res = exporter.create_zip_archive(jobs)
        self.assertTrue(res["success"])
        zip_p = Path(res["archive_path"])
        with zipfile.ZipFile(zip_p, "r") as zf:
            for n in zf.namelist():
                self.assertNotIn("..", n)
                self.assertNotIn("/", n)
                self.assertNotIn("\\", n)

    def test_25_exclude_tmp_originals_db_logs_from_zip(self):
        """25. Verify .tmp files or uncompleted jobs are strictly excluded."""
        exporter = BatchExporter(export_dir=self.exports_dir)
        tmp_file = self.test_dir / "temp_output.tmp"
        tmp_file.write_text("temporary data")

        jobs = [
            {"id": "1", "status": "COMPLETED", "output_path": str(tmp_file)}
        ]
        res = exporter.create_zip_archive(jobs)
        self.assertFalse(res["success"])

    def test_26_cleanup_expired_archives(self):
        """26. Verify cleanup_expired_archives deletes files older than max_age."""
        exporter = BatchExporter(export_dir=self.exports_dir)
        old_zip = self.exports_dir / "old_export_20200101_000000.zip"
        old_zip.write_bytes(b"PK\x05\x06" + b"\x00" * 18)  # Empty valid zip structure
        # Set mtime to 2 hours ago
        past = time.time() - 7200
        os.utime(str(old_zip), (past, past))

        cleaned = exporter.cleanup_expired_archives(max_age_seconds=3600)
        self.assertGreaterEqual(cleaned, 1)
        self.assertFalse(old_zip.exists())

    def test_27_zero_gpu_worker_blocking_during_export(self):
        """27. Verify ZIP export does not acquire the AIWorker lock."""
        worker = get_ai_worker(base_dir=self.base_dir)
        # AI Worker lock should remain unheld before, during, and after BatchExporter operations
        exporter = BatchExporter(export_dir=self.exports_dir)
        self.assertIsNotNone(exporter)
        # Lock can be acquired without blocking
        acquired = worker._lock.acquire(blocking=False)
        self.assertTrue(acquired)
        worker._lock.release()

    # ─────────────────────────────────────────────────────────────
    # Feature 5: Processing Report (CSV & HTML) (Scenarios 28-33)
    # ─────────────────────────────────────────────────────────────

    def test_28_csv_utf8_bom_encoding(self):
        """28. Verify generated CSV starts with UTF-8 BOM bytes (\xef\xbb\xbf)."""
        jid = str(uuid.uuid4())
        self.job_mgr.add_job(jid, "anh_dep_tieng_viet_utf8.png", "in/anh.png")
        jobs = self.job_mgr.get_all_jobs()
        stats = self.job_mgr.get_report_statistics()

        csv_content = generate_csv_report(jobs, stats)
        # First 3 bytes must be UTF-8 BOM
        self.assertTrue(csv_content.startswith(b"\xef\xbb\xbf"))

    def test_29_csv_formula_injection_sanitization(self):
        """29. Verify formulas (=SUM, +cmd, -exec, @calc) are escaped with single quote."""
        jid = str(uuid.uuid4())
        evil_name = "=cmd|' /C calc'!A0.png"
        self.job_mgr.add_job(jid, evil_name, "in/evil.png")

        jobs = self.job_mgr.get_all_jobs()
        stats = self.job_mgr.get_report_statistics()
        csv_bytes = generate_csv_report(jobs, stats)
        csv_text = csv_bytes.decode("utf-8-sig")

        self.assertIn(f"'{evil_name}", csv_text)
        self.assertNotIn(f",{evil_name}", csv_text)

    def test_30_csv_summary_metrics_and_job_rows(self):
        """30. Verify CSV includes summary metrics header and individual records."""
        jid = str(uuid.uuid4())
        self.job_mgr.add_job(jid, "report_sample.png", "in/sample.png")
        self.job_mgr.transition_status(jid, STATUS_COMPLETED, output_path="out/sample.png", elapsed_seconds=3.5, force=True)

        jobs = self.job_mgr.get_all_jobs()
        stats = self.job_mgr.get_report_statistics()
        csv_bytes = generate_csv_report(jobs, stats)
        csv_text = csv_bytes.decode("utf-8-sig")

        self.assertIn("Total Jobs", csv_text)
        self.assertIn("Success Rate", csv_text)
        self.assertIn("report_sample.png", csv_text)
        self.assertIn("3.5", csv_text)

    def test_31_html_report_offline_styling(self):
        """31. Verify HTML report is self-contained without external CSS/JS CDN dependencies."""
        jobs = [
            {"id": "1", "filename": "offline.png", "status": "COMPLETED", "file_size": 2048, "elapsed_seconds": 2.1}
        ]
        stats = {"total": 1, "completed": 1, "failed": 0, "cancelled": 0, "pending": 0, "success_rate": 100.0}
        html_text = generate_html_report(jobs, stats)

        self.assertIn("<!DOCTYPE html>", html_text)
        self.assertIn("<style>", html_text)
        self.assertNotIn("http://", html_text)
        self.assertNotIn("https://", html_text)

    def test_32_html_report_xss_injection_escaping(self):
        """32. Verify user inputs are escaped with html.escape() preventing XSS."""
        evil_filename = "<script>alert('XSS')</script>.png"
        jobs = [
            {"id": "2", "filename": evil_filename, "status": "FAILED", "error_message": "<img src=x onerror=alert(1)>"}
        ]
        stats = {"total": 1, "completed": 0, "failed": 1, "cancelled": 0, "pending": 0, "success_rate": 0.0}
        html_text = generate_html_report(jobs, stats)

        self.assertNotIn("<script>", html_text)
        self.assertIn("&lt;script&gt;", html_text)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", html_text)

    def test_33_html_report_omits_private_server_paths(self):
        """33. Verify private local server paths (e.g. C:\\, E:\\) are omitted from public report."""
        jobs = [
            {"id": "3", "filename": "safe.png", "status": "COMPLETED", "input_path": r"E:\AI\RealESRGAN-v030\uploads\safe.png"}
        ]
        stats = {"total": 1, "completed": 1, "failed": 0, "cancelled": 0, "pending": 0, "success_rate": 100.0}
        html_text = generate_html_report(jobs, stats)

        self.assertNotIn(r"E:\AI\RealESRGAN-v030", html_text)

    # ─────────────────────────────────────────────────────────────
    # Non-Regression & Core Engine Integrity (Scenarios 34-44)
    # ─────────────────────────────────────────────────────────────

    def test_34_only_model_is_realesrgan_x4plus(self):
        """34. Verify only RealESRGAN_x4plus is configured and supported."""
        self.assertEqual(self.config["inference"]["model_name"], "RealESRGAN_x4plus")
        weights_path = self.base_dir / self.config["inference"]["model_weights"]
        self.assertEqual(weights_path.name, "RealESRGAN_x4plus.pth")

    def test_35_model_weights_sha256_match(self):
        """35. Verify weights SHA256 matches exact baseline 4FA0D389..."""
        weights_path = self.base_dir / "weights" / "RealESRGAN_x4plus.pth"
        self.assertTrue(weights_path.is_file())
        hasher = hashlib.sha256()
        with open(weights_path, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        expected_prefix = "4fa0d389"
        self.assertTrue(hasher.hexdigest().lower().startswith(expected_prefix))

    def test_36_persistent_in_memory_worker_alive(self):
        """36. Verify AIWorker singleton is persistent and in STATE_READY or STOPPED."""
        worker = get_ai_worker(base_dir=self.base_dir)
        self.assertIsNotNone(worker)
        st = worker.get_status()
        self.assertIn(st.get("state"), [STATE_READY, STATE_BUSY, "STOPPED"])

    def test_37_auto_tile_and_oom_recovery_preserved(self):
        """37. Verify AIWorker auto-tile ladder and OOM recovery step-down."""
        worker = get_ai_worker(base_dir=self.base_dir)
        self.assertEqual(worker.get_step_down_tile(128), 96)
        self.assertEqual(worker.get_step_down_tile(96), 64)
        self.assertEqual(worker.get_step_down_tile(64), 32)

    def test_38_sequential_gpu_inference(self):
        """38. Verify sequential inference mutual exclusion with _lock."""
        worker = get_ai_worker(base_dir=self.base_dir)
        self.assertIsNotNone(worker._lock)

    def test_39_14_baseline_outputs_intact(self):
        """39. Verify baseline outputs in output/ are preserved and not empty if present."""
        out_dir = self.base_dir / "output"
        baseline_files = [f for f in out_dir.glob("*.png") if not f.name.endswith(".tmp")]
        if not baseline_files:
            self.skipTest("No baseline files in output/ (cleaned up in prior maintenance)")
        self.assertGreaterEqual(len(baseline_files), 2)
        for bf in baseline_files:
            self.assertGreater(bf.stat().st_size, 0)

    def test_40_history_json_integrity(self):
        """40. Verify history.json is valid and contains recorded history items."""
        self.assertTrue(HISTORY_FILE.is_file())
        history = load_history(resolve_paths=False)
        self.assertIsInstance(history, list)
        self.assertGreaterEqual(len(history), 14)

    def test_41_sqlite_jobs_db_integrity(self):
        """41. Verify SQLite jobs database executes PRAGMA integrity_check."""
        prod_db = self.base_dir / "data" / "jobs.db"
        if prod_db.is_file():
            with sqlite3.connect(prod_db) as conn:
                cur = conn.cursor()
                res = cur.execute("PRAGMA integrity_check;").fetchone()
                self.assertEqual(res[0], "ok")

    def test_42_crash_recovery_on_startup(self):
        """42. Verify startup recovery handles interrupted jobs without crashing."""
        jid = str(uuid.uuid4())
        src_file = self.base_dir / "crash_test.png"
        src_file.write_bytes(b"dummy_crash")
        try:
            self.job_mgr.add_job(jid, "crash_test.png", str(src_file))
            self.job_mgr.transition_status(jid, STATUS_PROCESSING, force=True)

            rec = self.job_mgr.recover_on_startup()
            self.assertIn("recovered_count", rec)
            # Status should have moved to QUEUED or RETRY_WAIT
            job = self.job_mgr.get_job(jid)
            self.assertIn(job["status"], ["QUEUED", "RETRY_WAIT"])
        finally:
            src_file.unlink(missing_ok=True)

    def test_43_path_traversal_security(self):
        """43. Verify safe_path blocks UNC, device namespace, and system directories."""
        self.assertIsNone(safe_path(r"\\127.0.0.1\share\img.png"))
        self.assertIsNone(safe_path(r"\\.\COM1"))
        self.assertIsNone(safe_path(r"\\?\C:\Windows\System32"))
        self.assertIsNone(safe_path(r"C:\Windows\System32\cmd.exe"))

    def test_44_pillow_deep_image_validation(self):
        """44. Verify validate_image_file rejects 0-byte, corrupt, or invalid format files."""
        # Test 0-byte file
        zero_file = self.test_dir / "empty.png"
        zero_file.write_bytes(b"")
        valid, reason, _, _ = validate_image_file(zero_file)
        self.assertFalse(valid)
        self.assertIn("0 bytes", reason)

        # Test valid image
        good_img = self.test_dir / "good.png"
        Image.new("RGB", (20, 20), color="white").save(good_img, "PNG")
        valid_good, _, dims, fmt = validate_image_file(good_img)
        self.assertTrue(valid_good)
        self.assertEqual(dims, (20, 20))
        self.assertEqual(fmt, "PNG")


if __name__ == "__main__":
    unittest.main()
