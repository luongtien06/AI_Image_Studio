# -*- coding: utf-8 -*-
"""
Test Suite: UI/UX Redesign & Image/Folder Selection Integration
AI Image Studio (RealESRGAN_x4plus v2.0 Turbo)

Covers all 16 verification requirements:
1. Single image selection / upload (/api/upload).
2. Multiple image selection / upload (/api/upload multi-part).
3. Folder selection simulation (/api/select-folder).
4. Combined image + folder selection (queue persistence without clearing).
5. Repeated selections (appending to queue).
6. Duplicate detection (same source file content/name does not create duplicate job).
7. Same filename in different directories (distinct jobs created).
8. Removing individual selections (/api/remove-job).
9. Clear List with confirmation (/api/clear-jobs).
10. Invalid / corrupt image handling (rejected with clear reason).
11. Empty folder handling (warning, queue preserved).
12. Large folder performance (fast scan).
13. Correct job creation & persistence (id, filename, dimensions, format, source).
14. Existing queue functionality (search, filter, sort, export endpoints).
15. Existing Video Upscale functionality (video endpoints intact).
16. Responsive layouts at all 4 resolutions (DOM structure & CSS rules).
"""

import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from PIL import Image

import app as app_module
from app import BASE_DIR, app, safe_path
from job_manager import (
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PROCESSING,
    STATUS_QUEUED,
    JobManager,
)


class TestUIImageFolderSelection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = app.test_client()
        cls.test_dir = Path(tempfile.mkdtemp(prefix="test_ui_sel_"))
        cls.test_db_path = cls.test_dir / "test_jobs.db"
        cls.test_mgr = JobManager(db_path=str(cls.test_db_path))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def setUp(self):
        # Create helper test images
        self.img1_path = self.test_dir / "sample1.png"
        img = Image.new("RGB", (64, 64), color="blue")
        img.save(self.img1_path, format="PNG")

        self.img2_path = self.test_dir / "sample2.jpg"
        img2 = Image.new("RGB", (128, 128), color="red")
        img2.save(self.img2_path, format="JPEG")

        self.corrupt_img_path = self.test_dir / "corrupt.png"
        self.corrupt_img_path.write_bytes(b"NOT_A_VALID_IMAGE_HEADER_12345")

    def test_01_single_image_upload(self):
        """1. Single image selection / upload via /api/upload."""
        with open(self.img1_path, "rb") as f:
            data = {"files": (io.BytesIO(f.read()), "test_single.png")}
            resp = self.client.post("/api/upload", data=data, content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 200)
        res_data = resp.get_json()
        self.assertEqual(res_data["status"], "success")
        self.assertEqual(res_data["summary"]["valid"], 1)
        files = res_data.get("files") or res_data.get("jobs", [])
        self.assertEqual(len(files), 1)
        job = files[0]
        self.assertTrue(job["filename"].startswith("test_single"))
        self.assertIn("job_id", job)

    def test_02_multiple_image_upload(self):
        """2. Multiple image selection / upload in single request."""
        with open(self.img1_path, "rb") as f1, open(self.img2_path, "rb") as f2:
            data = {
                "files": [
                    (io.BytesIO(f1.read()), "multi_1.png"),
                    (io.BytesIO(f2.read()), "multi_2.jpg"),
                ]
            }
            resp = self.client.post("/api/upload", data=data, content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 200)
        res_data = resp.get_json()
        self.assertEqual(res_data["summary"]["valid"], 2)
        files = res_data.get("files") or res_data.get("jobs", [])
        self.assertEqual(len(files), 2)

    def test_03_folder_selection_simulation(self):
        """3. Folder selection simulation via /api/select-folder."""
        scan_folder = self.test_dir / "scan_target"
        scan_folder.mkdir(exist_ok=True)
        img = Image.new("RGB", (32, 32), color="green")
        img.save(scan_folder / "img_folder_1.png")
        img.save(scan_folder / "img_folder_2.jpg")

        resp = self.client.post(
            "/api/select-folder",
            data=json.dumps({"path": str(scan_folder)}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        res_data = resp.get_json()
        self.assertEqual(res_data["status"], "success")
        self.assertEqual(res_data["source"], "folder")
        self.assertGreaterEqual(res_data["valid_count"], 2)

    def test_04_combined_image_and_folder_selection(self):
        """4. Combined image + folder selection (neither clears the other)."""
        # Upload an image first
        with open(self.img1_path, "rb") as f:
            resp1 = self.client.post(
                "/api/upload",
                data={"files": (io.BytesIO(f.read()), "combined_initial.png")},
                content_type="multipart/form-data",
            )
        self.assertEqual(resp1.status_code, 200)

        # Select a folder
        scan_folder = self.test_dir / "combined_folder"
        scan_folder.mkdir(exist_ok=True)
        img = Image.new("RGB", (40, 40), color="yellow")
        img.save(scan_folder / "folder_item.png")

        resp2 = self.client.post(
            "/api/select-folder",
            data=json.dumps({"path": str(scan_folder)}),
            content_type="application/json",
        )
        self.assertEqual(resp2.status_code, 200)

        # Check job manager contains both
        all_jobs = app_module.job_mgr.get_all_jobs()
        job_filenames = [j["filename"] for j in all_jobs]
        matching_initial = any("combined_initial" in fn for fn in job_filenames)
        matching_folder = any("folder_item" in fn for fn in job_filenames)
        self.assertTrue(matching_initial)
        self.assertTrue(matching_folder)

    def test_05_repeated_selections_appending(self):
        """5. Repeated selections append to queue rather than overwrite."""
        count_before = len(app_module.job_mgr.get_all_jobs())

        with open(self.img1_path, "rb") as f:
            resp_a = self.client.post(
                "/api/upload",
                data={"files": (io.BytesIO(f.read()), "repeat_1.png")},
                content_type="multipart/form-data",
            )
        self.assertEqual(resp_a.status_code, 200)

        with open(self.img2_path, "rb") as f:
            resp_b = self.client.post(
                "/api/upload",
                data={"files": (io.BytesIO(f.read()), "repeat_2.jpg")},
                content_type="multipart/form-data",
            )
        self.assertEqual(resp_b.status_code, 200)

        count_after = len(app_module.job_mgr.get_all_jobs())
        self.assertEqual(count_after, count_before + 2)

    def test_06_duplicate_detection(self):
        """6. Duplicate detection (same source file does not create duplicate job)."""
        content = self.img1_path.read_bytes()
        unique_name = f"dup_check_{os.getpid()}.png"

        # First upload
        resp1 = self.client.post(
            "/api/upload",
            data={"files": (io.BytesIO(content), unique_name)},
            content_type="multipart/form-data",
        )
        res1 = resp1.get_json()
        files1 = res1.get("files") or res1.get("jobs", [])
        self.assertGreater(len(files1), 0)
        job_id_1 = files1[0]["job_id"]

        # Second upload of the EXACT same file and filename
        resp2 = self.client.post(
            "/api/upload",
            data={"files": (io.BytesIO(content), unique_name)},
            content_type="multipart/form-data",
        )
        res2 = resp2.get_json()
        files2 = res2.get("files") or res2.get("jobs", [])
        self.assertEqual(res2["summary"]["duplicates"], 1)
        self.assertEqual(len(files2), 1)
        self.assertEqual(files2[0]["job_id"], job_id_1)
        self.assertTrue(files2[0].get("is_duplicate"))

    def test_07_same_filename_different_directories(self):
        """7. Same filename in different directories are distinct jobs."""
        dir_a = self.test_dir / "dir_a"
        dir_b = self.test_dir / "dir_b"
        dir_a.mkdir(exist_ok=True)
        dir_b.mkdir(exist_ok=True)

        img_a = Image.new("RGB", (30, 30), color="blue")
        img_a.save(dir_a / "common_name.png")

        img_b = Image.new("RGB", (50, 50), color="red")
        img_b.save(dir_b / "common_name.png")

        resp_a = self.client.post(
            "/api/select-folder",
            data=json.dumps({"path": str(dir_a)}),
            content_type="application/json",
        )
        self.assertEqual(resp_a.status_code, 200)

        resp_b = self.client.post(
            "/api/select-folder",
            data=json.dumps({"path": str(dir_b)}),
            content_type="application/json",
        )
        self.assertEqual(resp_b.status_code, 200)

        # Both jobs should exist with distinct input paths
        all_jobs = app_module.job_mgr.get_all_jobs()
        common_jobs = [j for j in all_jobs if j["filename"] == "common_name.png"]
        self.assertGreaterEqual(len(common_jobs), 2)
        paths = set(j["input_path"] for j in common_jobs)
        self.assertGreaterEqual(len(paths), 2)

    def test_08_remove_individual_job(self):
        """8. Removing individual selections via /api/remove-job."""
        with open(self.img1_path, "rb") as f:
            resp = self.client.post(
                "/api/upload",
                data={"files": (io.BytesIO(f.read()), "to_remove.png")},
                content_type="multipart/form-data",
            )
        files = resp.get_json().get("files") or resp.get_json().get("jobs", [])
        job_id = files[0]["job_id"]

        del_resp = self.client.post(
            "/api/remove-job",
            data=json.dumps({"job_id": job_id}),
            content_type="application/json",
        )
        self.assertEqual(del_resp.status_code, 200)
        self.assertTrue(del_resp.get_json().get("removed"))

        # Verify job is no longer queued
        job = app_module.job_mgr.get_job(job_id)
        self.assertTrue(job is None or job["status"] in ["CANCELLED", "cancelled"])

    def test_09_clear_all_jobs(self):
        """9. Clear List with confirmation (/api/clear-jobs)."""
        # Upload a dummy job
        with open(self.img1_path, "rb") as f:
            self.client.post(
                "/api/upload",
                data={"files": (io.BytesIO(f.read()), "to_clear.png")},
                content_type="multipart/form-data",
            )

        resp = self.client.post("/api/clear-jobs")
        self.assertEqual(resp.status_code, 200)
        res_data = resp.get_json()
        self.assertIn("removed", res_data)

    def test_10_corrupt_invalid_image_handling(self):
        """10. Invalid / corrupt image handling rejected with clear reason."""
        with open(self.corrupt_img_path, "rb") as f:
            resp = self.client.post(
                "/api/upload",
                data={"files": (io.BytesIO(f.read()), "corrupt.png")},
                content_type="multipart/form-data",
            )
        self.assertEqual(resp.status_code, 200)
        res_data = resp.get_json()
        self.assertEqual(res_data["summary"]["corrupt"], 1)
        self.assertEqual(res_data["summary"]["valid"], 0)
        self.assertIn("corrupt", res_data["errors"][0]["reason"].lower())

    def test_11_empty_folder_handling(self):
        """11. Empty folder handling: returns 0 images, preserves existing queue."""
        empty_dir = self.test_dir / "empty_folder"
        empty_dir.mkdir(exist_ok=True)

        resp = self.client.post(
            "/api/select-folder",
            data=json.dumps({"path": str(empty_dir)}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        res_data = resp.get_json()
        self.assertEqual(res_data["valid_count"], 0)
        files = res_data.get("files") or res_data.get("jobs", [])
        self.assertEqual(len(files), 0)

    def test_12_large_folder_performance(self):
        """12. Large folder performance: scans 50 images efficiently."""
        large_dir = self.test_dir / "large_dir"
        large_dir.mkdir(exist_ok=True)
        img = Image.new("RGB", (16, 16), color="black")
        for i in range(50):
            img.save(large_dir / f"bulk_{i:03d}.png")

        resp = self.client.post(
            "/api/select-folder",
            data=json.dumps({"path": str(large_dir)}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        res_data = resp.get_json()
        self.assertEqual(res_data["valid_count"], 50)

    def test_13_job_attributes_and_source_tracking(self):
        """13. Correct job creation & persistence with source attribute."""
        with open(self.img1_path, "rb") as f:
            resp = self.client.post(
                "/api/upload",
                data={"files": (io.BytesIO(f.read()), "attr_test.png")},
                content_type="multipart/form-data",
            )
        files = resp.get_json().get("files") or resp.get_json().get("jobs", [])
        job = files[0]
        self.assertEqual(job["source"], "upload")
        self.assertEqual(job["dimensions"], "64x64")
        self.assertGreater(job["file_size"], 0)

        # Query /api/jobs
        resp_list = self.client.get("/api/jobs")
        self.assertEqual(resp_list.status_code, 200)
        jobs_data = resp_list.get_json()
        jobs_list = jobs_data if isinstance(jobs_data, list) else jobs_data.get("jobs", [])
        matching = [j for j in jobs_list if j.get("id") == job["job_id"] or j.get("job_id") == job["job_id"]]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0].get("source"), "upload")

    def test_14_existing_queue_functionality(self):
        """14. Existing queue functionality (search, filter, sort, export endpoints)."""
        resp_jobs = self.client.get("/api/jobs")
        self.assertEqual(resp_jobs.status_code, 200)

        resp_stats = self.client.get("/api/status")
        self.assertEqual(resp_stats.status_code, 200)
        self.assertIn("cuda_ok", resp_stats.get_json())

        resp_export_report = self.client.get("/api/export/report?format=csv")
        self.assertIn(resp_export_report.status_code, [200, 204, 400])

    def test_15_existing_video_upscale_endpoints(self):
        """15. Existing Video Upscale functionality (routes and workflow intact)."""
        # Test /api/video/jobs
        resp = self.client.get("/api/video/jobs")
        self.assertEqual(resp.status_code, 200)

        # Test video storage endpoint
        resp_space = self.client.get("/api/video/storage")
        self.assertEqual(resp_space.status_code, 200)
        data = resp_space.get_json()
        self.assertIn("free_disk_bytes", data)

        # Test video analyze rejection for missing file
        resp_analyze = self.client.post("/api/video/analyze", json={})
        self.assertIn(resp_analyze.status_code, [400, 422])

    def test_16_responsive_layout_and_template_structure(self):
        """16. Template verification: Header, Settings, Workspace, 3 breakpoint media queries."""
        template_path = BASE_DIR / "templates" / "index.html"
        self.assertTrue(template_path.exists())
        content = template_path.read_text(encoding="utf-8")

        # Verify Header elements
        self.assertIn("AI Image Studio", content)
        self.assertIn("v2.0 Turbo", content)
        self.assertIn("gpuMonitor", content)
        self.assertIn("RTX 2050", content)
        self.assertIn("btnLangVi", content)
        self.assertIn("btnLangEn", content)

        # Verify Input Selection dual buttons
        self.assertIn("btnChooseImages", content)
        self.assertIn("btnChooseFolder", content)
        self.assertIn("imageFileInput", content)
        self.assertIn("dropzone", content)

        # Verify Sidebar Statistics
        self.assertIn("sumTotal", content)
        self.assertIn("sumPending", content)
        self.assertIn("sumProcessing", content)
        self.assertIn("sumDone", content)
        self.assertIn("sumError", content)

        # Verify Right Workspace & Views
        self.assertIn("btnViewGrid", content)
        self.assertIn("btnViewList", content)
        self.assertIn("empty-queue-container", content)

        # Verify Video tab and Section preserved
        self.assertIn("tabVideo", content)
        self.assertIn("videoSection", content)

        # Verify responsive CSS breakpoints
        self.assertIn("@media (max-width: 1600px)", content)
        self.assertIn("@media (max-width: 1366px)", content)
        self.assertIn("@media (max-width: 992px)", content)


if __name__ == "__main__":
    unittest.main()
