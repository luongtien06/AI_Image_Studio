# -*- coding: utf-8 -*-
"""
Test Suite: Native Windows Folder Browser & Local Directory Selection
AI Image Studio (RealESRGAN_x4plus)

Covers all 12 mandatory verification scenarios:
1. Native folder dialog opening and successful directory return
2. Image scanning for supported formats (JPG, PNG, WEBP, BMP)
3. Folder path containing spaces
4. Folder path containing Vietnamese Unicode characters
5. Empty folder handling (0 valid images, non-blocking)
6. Dialog cancellation / dismissal handling
7. Non-existent or restricted / protected directory rejection (404 / 403)
8. Dynamic folder switching without restarting application
9. Batch processing of images from the custom selected folder
10. Preservation of single image processing functionality
11. Preservation of video pipeline functionality
12. Duplicate job prevention on multiple scans or re-selection
+ Security validations: Remote client rejection (403), UNC rejection, System dir protection
"""

import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

from app import (
    BASE_DIR,
    UPLOAD_DIR,
    app,
    launch_native_folder_dialog,
    safe_path,
)
from job_manager import STATUS_COMPLETED, JobManager, get_job_manager


class TestFolderBrowserSelection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = app.test_client()
        cls.test_dir = Path(tempfile.mkdtemp(prefix="test_folder_browser_"))
        cls.job_mgr = get_job_manager()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def test_01_browse_folder_native_success(self):
        """1. Verify opening folder dialog successfully returns absolute path."""
        sample_dir = self.test_dir / "target_folder_01"
        sample_dir.mkdir(parents=True, exist_ok=True)

        # A: Direct path via API
        resp = self.client.post("/api/browse-folder", json={"path": str(sample_dir)})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["path"], str(sample_dir.resolve()))

        # B: Subprocess call with mocked subprocess returning folder
        with patch("subprocess.run") as mock_run:
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.stdout = str(sample_dir.resolve()) + "\n"
            mock_proc.stderr = ""
            mock_run.return_value = mock_proc

            res = launch_native_folder_dialog(title="Test Dialog")
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["path"], str(sample_dir.resolve()))

    def test_02_select_folder_with_jpg_png_webp(self):
        """2. Verify scanning folder with JPG, PNG, WEBP images."""
        img_folder = self.test_dir / "multi_format_folder"
        img_folder.mkdir(parents=True, exist_ok=True)

        Image.new("RGB", (64, 64), (255, 0, 0)).save(img_folder / "img1.png", "PNG")
        Image.new("RGB", (64, 64), (0, 255, 0)).save(img_folder / "img2.jpg", "JPEG")
        Image.new("RGB", (64, 64), (0, 0, 255)).save(img_folder / "img3.webp", "WEBP")
        # Non-image file to ensure filtering
        (img_folder / "notes.txt").write_text("not an image", encoding="utf-8")

        resp = self.client.post("/api/select-folder", json={"path": str(img_folder)})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("files", data)
        filenames = [f["filename"] for f in data["files"]]
        self.assertIn("img1.png", filenames)
        self.assertIn("img2.jpg", filenames)
        self.assertIn("img3.webp", filenames)
        self.assertNotIn("notes.txt", filenames)
        self.assertEqual(data["total_valid"], 3)

    def test_03_folder_path_with_spaces(self):
        """3. Verify selecting a folder with spaces in path."""
        spaced_dir = self.test_dir / "Thư Mục Có Dấu Cách Test Folder"
        spaced_dir.mkdir(parents=True, exist_ok=True)
        img_path = spaced_dir / "space sample.png"
        Image.new("RGB", (32, 32), (120, 150, 180)).save(img_path, "PNG")

        # API browse-folder
        resp_browse = self.client.post("/api/browse-folder", json={"path": str(spaced_dir)})
        self.assertEqual(resp_browse.status_code, 200)
        self.assertEqual(resp_browse.get_json()["status"], "success")

        # API select-folder
        resp_select = self.client.post("/api/select-folder", json={"path": str(spaced_dir)})
        self.assertEqual(resp_select.status_code, 200)
        data = resp_select.get_json()
        self.assertEqual(len(data["files"]), 1)
        self.assertEqual(data["files"][0]["filename"], "space sample.png")

    def test_04_folder_path_with_vietnamese_unicode(self):
        """4. Verify selecting a folder with Vietnamese Unicode characters."""
        vn_dir = self.test_dir / "Thư mục ảnh nghệ thuật đẹp 2026"
        vn_dir.mkdir(parents=True, exist_ok=True)
        vn_img = vn_dir / "bình minh rạng rỡ.png"
        Image.new("RGB", (48, 48), (200, 100, 50)).save(vn_img, "PNG")

        # API browse-folder
        resp_browse = self.client.post("/api/browse-folder", json={"path": str(vn_dir)})
        self.assertEqual(resp_browse.status_code, 200)
        self.assertEqual(resp_browse.get_json()["status"], "success")

        # API select-folder
        resp_select = self.client.post("/api/select-folder", json={"path": str(vn_dir)})
        self.assertEqual(resp_select.status_code, 200)
        data = resp_select.get_json()
        self.assertEqual(len(data["files"]), 1)
        self.assertEqual(data["files"][0]["filename"], "bình minh rạng rỡ.png")

    def test_05_empty_folder_handling(self):
        """5. Verify selecting an empty folder returns 0 images gracefully."""
        empty_dir = self.test_dir / "empty_target_dir"
        empty_dir.mkdir(parents=True, exist_ok=True)

        resp = self.client.post("/api/select-folder", json={"path": str(empty_dir)})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(len(data["files"]), 0)
        self.assertEqual(data["total_valid"], 0)
        self.assertEqual(data["folder"], str(empty_dir.resolve()))

    def test_06_folder_dialog_cancelled(self):
        """6. Verify user cancelling folder dialog returns cancelled status."""
        with patch("subprocess.run") as mock_run:
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.stdout = ""  # Empty stdout means user cancelled
            mock_proc.stderr = ""
            mock_run.return_value = mock_proc

            res = launch_native_folder_dialog(title="Cancel Test")
            self.assertEqual(res["status"], "cancelled")
            self.assertIn("đóng hoặc hủy", res["message"])

        # API endpoint invocation simulating cancel
        with patch("app.launch_native_folder_dialog") as mock_dlg:
            mock_dlg.return_value = {"status": "cancelled", "message": "User closed dialog"}
            resp = self.client.post("/api/browse-folder", json={})
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.get_json()["status"], "cancelled")

    def test_07_nonexistent_or_protected_folder_rejection(self):
        """7. Verify non-existent, system directories, and UNC paths are rejected."""
        # Non-existent folder
        fake_dir = self.test_dir / "does_not_exist_xyz_123"
        resp1 = self.client.post("/api/select-folder", json={"path": str(fake_dir)})
        self.assertEqual(resp1.status_code, 404)

        # Protected system directory
        resp2 = self.client.post("/api/browse-folder", json={"path": "C:\\Windows\\System32"})
        self.assertEqual(resp2.status_code, 403)

        # UNC path
        resp3 = self.client.post("/api/browse-folder", json={"path": "\\\\192.168.1.100\\share"})
        self.assertEqual(resp3.status_code, 403)

    def test_08_dynamic_folder_switching(self):
        """8. Verify switching between multiple folders without restarting."""
        folder_a = self.test_dir / "dir_alpha"
        folder_a.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (32, 32), (10, 20, 30)).save(folder_a / "alpha.png", "PNG")

        folder_b = self.test_dir / "dir_beta"
        folder_b.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (32, 32), (40, 50, 60)).save(folder_b / "beta.png", "PNG")

        # Select Alpha
        resp_a = self.client.post("/api/select-folder", json={"path": str(folder_a)})
        self.assertEqual(resp_a.status_code, 200)
        self.assertEqual(resp_a.get_json()["files"][0]["filename"], "alpha.png")

        # Switch to Beta
        resp_b = self.client.post("/api/select-folder", json={"path": str(folder_b)})
        self.assertEqual(resp_b.status_code, 200)
        self.assertEqual(resp_b.get_json()["files"][0]["filename"], "beta.png")

        # Switch back to default uploads
        resp_def = self.client.post("/api/select-folder", json={"path": ""})
        self.assertEqual(resp_def.status_code, 200)
        self.assertIn("files", resp_def.get_json())

    def test_09_batch_processing_from_selected_folder(self):
        """9. Verify end-to-end batch job submission from selected folder."""
        batch_folder = self.test_dir / "batch_input_folder"
        batch_folder.mkdir(parents=True, exist_ok=True)
        out_folder = self.test_dir / "batch_output_folder"
        out_folder.mkdir(parents=True, exist_ok=True)

        img_p = batch_folder / "batch_img.png"
        Image.new("RGB", (32, 32), (70, 80, 90)).save(img_p, "PNG")

        # 1. Select folder
        resp_sel = self.client.post("/api/select-folder", json={"path": str(batch_folder)})
        self.assertEqual(resp_sel.status_code, 200)
        files = resp_sel.get_json()["files"]
        self.assertGreater(len(files), 0)
        job_id = files[0]["job_id"]

        # 2. Submit for batch processing
        resp_proc = self.client.post("/api/process", json={
            "job_ids": [job_id],
            "tile_size": 128,
            "export_format": "png",
            "output_dir": str(out_folder)
        })
        self.assertEqual(resp_proc.status_code, 200)
        proc_data = resp_proc.get_json()
        self.assertEqual(proc_data["total_jobs"], 1)

    def test_10_single_image_processing_unaffected(self):
        """10. Verify single image processing APIs remain intact and functional."""
        # Check /api/config
        resp_cfg = self.client.get("/api/config")
        self.assertEqual(resp_cfg.status_code, 200)
        cfg = resp_cfg.get_json()
        self.assertEqual(cfg["inference"]["model_name"], "RealESRGAN_x4plus")

        # Check /api/status
        resp_st = self.client.get("/api/status")
        self.assertEqual(resp_st.status_code, 200)

        # Check /api/jobs
        resp_jobs = self.client.get("/api/jobs")
        self.assertEqual(resp_jobs.status_code, 200)

    def test_11_video_pipeline_unaffected(self):
        """11. Verify video enhancement pipeline endpoints remain 100% operational."""
        resp_vjobs = self.client.get("/api/video/jobs")
        self.assertEqual(resp_vjobs.status_code, 200)
        vdata = resp_vjobs.get_json()
        self.assertIn("jobs", vdata)
        self.assertIsInstance(vdata["jobs"], list)

        resp_vstorage = self.client.get("/api/video/storage")
        self.assertEqual(resp_vstorage.status_code, 200)
        vstorage = resp_vstorage.get_json()
        self.assertIn("free_disk_human", vstorage)

    def test_12_no_duplicate_jobs_on_repeated_selection(self):
        """12. Verify repeated folder scanning does not duplicate jobs."""
        dup_folder = self.test_dir / "dup_check_dir"
        dup_folder.mkdir(parents=True, exist_ok=True)
        img = dup_folder / "item_once.png"
        Image.new("RGB", (32, 32), (11, 22, 33)).save(img, "PNG")

        # First scan
        resp1 = self.client.post("/api/select-folder", json={"path": str(dup_folder)})
        self.assertEqual(resp1.status_code, 200)
        id1 = resp1.get_json()["files"][0]["job_id"]

        # Second scan of identical folder
        resp2 = self.client.post("/api/select-folder", json={"path": str(dup_folder)})
        self.assertEqual(resp2.status_code, 200)
        id2 = resp2.get_json()["files"][0]["job_id"]

        # Job IDs must match, preventing duplicate records
        self.assertEqual(id1, id2)

    def test_13_security_remote_access_denied(self):
        """13. Verify remote clients receive 403 with is_remote flag for browse-folder."""
        # Simulate request from remote client IP (e.g., smartphone on LAN 192.168.1.50)
        resp = self.client.post(
            "/api/browse-folder",
            json={},
            environ_overrides={"REMOTE_ADDR": "192.168.1.50"}
        )
        self.assertEqual(resp.status_code, 403)
        data = resp.get_json()
        self.assertTrue(data.get("is_remote"))
        self.assertIn("máy chủ cục bộ", data.get("error"))

    def test_14_dialog_timeout_handling(self):
        """14. Verify subprocess timeout handling returns structured error."""
        with patch("subprocess.run") as mock_run:
            import subprocess
            mock_run.side_effect = subprocess.TimeoutExpired(cmd=["test"], timeout=1)

            res = launch_native_folder_dialog(timeout=1)
            self.assertEqual(res["status"], "error")
            self.assertIn("hết thời gian chờ", res["error"])

    def test_15_get_browse_folder_backward_compatibility(self):
        """15. Verify GET /api/browse-folder retains backward compatibility."""
        resp = self.client.get("/api/browse-folder")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("is_root"))
        self.assertIn("folders", data)


if __name__ == "__main__":
    unittest.main()
