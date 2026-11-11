# -*- coding: utf-8 -*-
"""
Test Suite: JPG Upload False-Corrupt Detection & Image Reprocessing
AI Image Studio (RealESRGAN_x4plus / RTX 2050 4GB)

Covers all 14 Phase D verification scenarios:
1. Standard JPG accepted
2. ~4 MB JPG accepted
3. Real-ESRGAN output JPG (e.g. 4160x6400, 26.62 MP) accepted
4. Previously processed image allows creating a new job
5. Same content with different name allowed
6. Same name with different content not falsely treated as duplicate
7. Valid PNG accepted
8. Valid WebP accepted
9. Disguised non-image file with .jpg extension rejected properly
10. Truly corrupt JPG reported as corrupt
11. JPG with EXIF orientation handled properly
12. High-resolution JPG within resource limits accepted
13. Files exceeding limits report specific size/pixel errors, not corrupt
14. Reprocessing same image multiple times creates unique Job IDs & non-colliding outputs
"""

import io
import json
import os
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from PIL import Image, ImageOps

from app import (
    APP_CONFIG,
    BASE_DIR,
    MAX_FILE_SIZE,
    MAX_PIXELS,
    UPLOAD_MAX_PIXELS,
    ValidationResult,
    app,
    job_mgr,
    jobs,
    jobs_lock,
    validate_image_file,
)
from ai_worker import get_unique_output_path


class TestJpgUploadReprocessing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = app.test_client()
        cls.test_dir = Path(tempfile.mkdtemp(prefix="test_jpg_reprocess_"))

    @classmethod
    def tearDownClass(cls):
        if cls.test_dir.exists():
            shutil.rmtree(cls.test_dir, ignore_errors=True)

    def _create_jpeg(self, filename: str, size: tuple = (200, 200), color=(100, 150, 200), quality: int = 90) -> Path:
        """Helper to create a valid JPEG image file."""
        p = self.test_dir / filename
        im = Image.new("RGB", size, color=color)
        im.save(p, format="JPEG", quality=quality)
        return p

    # ─────────────────────────────────────────────────────────────
    # Test 1: JPG thông thường — Chấp nhận
    # ─────────────────────────────────────────────────────────────
    def test_01_normal_jpg_accepted(self):
        """1. Verify standard JPEG image is validated and accepted."""
        jpg_path = self._create_jpeg("normal.jpg", size=(800, 600))
        valid, reason, (w, h), fmt = validate_image_file(jpg_path)
        self.assertTrue(valid, f"Normal JPG should be valid: {reason}")
        self.assertEqual(fmt, "JPEG")
        self.assertEqual((w, h), (800, 600))

        with open(jpg_path, "rb") as f:
            resp = self.client.post("/api/upload", data={"files": (f, "normal.jpg")}, content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 200)
        d = resp.get_json()
        self.assertEqual(d["summary"]["valid"], 1)
        self.assertEqual(d["summary"]["corrupt"], 0)
        self.assertEqual(d["summary"]["too_large"], 0)

    # ─────────────────────────────────────────────────────────────
    # Test 2: JPG gần 4 MB — Chấp nhận nếu trong giới hạn cấu hình
    # ─────────────────────────────────────────────────────────────
    def test_02_four_mb_jpg_accepted(self):
        """2. Verify ~4 MB JPEG image is validated and uploaded cleanly."""
        import numpy as np
        p = self.test_dir / "large_4mb.jpg"
        # 1800 x 1800 RGB with high entropy noise produces ~3.6-3.8 MB JPEG file
        arr = np.random.randint(0, 256, (1800, 1800, 3), dtype=np.uint8)
        im = Image.fromarray(arr)
        im.save(p, format="JPEG", quality=95)
        file_size = p.stat().st_size
        self.assertGreater(file_size, 3 * 1024 * 1024, "Image should be ~4 MB")
        self.assertLess(file_size, 5 * 1024 * 1024, "Image should be ~4 MB")

        res = validate_image_file(p, max_pixels=UPLOAD_MAX_PIXELS)
        self.assertTrue(res.is_valid, f"4MB JPG should be valid: {res.reason}")

        with open(p, "rb") as f:
            resp = self.client.post("/api/upload", data={"files": (f, "large_4mb.jpg")}, content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 200)
        d = resp.get_json()
        self.assertEqual(d["summary"]["valid"], 1)
        self.assertEqual(d["summary"]["corrupt"], 0)

    # ─────────────────────────────────────────────────────────────
    # Test 3: JPG đầu ra từ Real-ESRGAN (4160x6400 = 26.62 MP) — Chấp nhận
    # ─────────────────────────────────────────────────────────────
    def test_03_realesrgan_output_jpg_accepted(self):
        """3. Verify Real-ESRGAN 4x output JPG (26.62 MP, matching 1.jpg) is accepted."""
        p = self.test_dir / "realesrgan_output_4x.jpg"
        # 4160 x 6400 = 26,624,000 pixels (previously crashed with max_pixels: 25M)
        im = Image.new("RGB", (4160, 6400), color=(80, 120, 160))
        im.save(p, format="JPEG", quality=90)

        # Calling with UPLOAD_MAX_PIXELS should succeed
        res = validate_image_file(p, max_pixels=UPLOAD_MAX_PIXELS)
        self.assertTrue(res.is_valid, f"RealESRGAN 4x output must be accepted: {res.reason}")
        self.assertEqual(res.category, "valid")

        with open(p, "rb") as f:
            resp = self.client.post("/api/upload", data={"files": (f, "realesrgan_output_4x.jpg")}, content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 200)
        d = resp.get_json()
        self.assertEqual(d["summary"]["valid"], 1)
        self.assertEqual(d["summary"]["corrupt"], 0)
        self.assertEqual(d["summary"]["too_large"], 0)

    # ─────────────────────────────────────────────────────────────
    # Test 4: JPG đã xử lý trước đó — Cho phép tạo Job mới
    # ─────────────────────────────────────────────────────────────
    def test_04_previously_processed_jpg_reprocessed(self):
        """4. Verify previously completed job can be reprocessed into a fresh job."""
        p = self._create_jpeg("previously_completed.jpg", size=(256, 256))
        with open(p, "rb") as f:
            r1 = self.client.post("/api/upload", data={"files": (f, "previously_completed.jpg")}, content_type="multipart/form-data")
        initial_job_id = r1.get_json()["files"][0]["job_id"]

        # Simulate job completion
        with jobs_lock:
            jobs[initial_job_id]["status"] = "completed"
            jobs[initial_job_id]["raw_status"] = "COMPLETED"

        # Request reprocessing via /api/reprocess
        r2 = self.client.post("/api/reprocess", json={"job_id": initial_job_id})
        self.assertEqual(r2.status_code, 200)
        d2 = r2.get_json()
        self.assertEqual(d2["reprocessed_count"], 1)
        new_job = d2["jobs"][0]
        self.assertNotEqual(new_job["id"], initial_job_id, "Reprocess must generate a NEW unique job ID")
        self.assertEqual(new_job["status"], "pending")
        self.assertEqual(new_job["reprocessed_from"], initial_job_id)

        # Verify old job is still in memory and preserved
        with jobs_lock:
            self.assertIn(initial_job_id, jobs)
            self.assertEqual(jobs[initial_job_id]["status"], "completed")

    # ─────────────────────────────────────────────────────────────
    # Test 5: JPG cùng nội dung, tên khác — Không tự động từ chối
    # ─────────────────────────────────────────────────────────────
    def test_05_same_content_different_name(self):
        """5. Verify images with identical bytes but different names are accepted."""
        content = self._create_jpeg("original_content.jpg", size=(300, 300)).read_bytes()
        p1 = self.test_dir / "name_a.jpg"
        p2 = self.test_dir / "name_b.jpg"
        p1.write_bytes(content)
        p2.write_bytes(content)

        with open(p1, "rb") as f1, open(p2, "rb") as f2:
            r = self.client.post("/api/upload", data={"files": [(f1, "name_a.jpg"), (f2, "name_b.jpg")]}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertEqual(d["summary"]["valid"], 2)
        self.assertEqual(len(d["files"]), 2)

    # ─────────────────────────────────────────────────────────────
    # Test 6: JPG cùng tên, nội dung khác — Không nhầm là duplicate
    # ─────────────────────────────────────────────────────────────
    def test_06_same_name_different_content(self):
        """6. Verify images with same name but different content are handled with collision counter."""
        p1 = self._create_jpeg("same_name.jpg", size=(100, 100))
        with open(p1, "rb") as f1:
            r1 = self.client.post("/api/upload", data={"files": (f1, "same_name.jpg")}, content_type="multipart/form-data")
        self.assertEqual(r1.status_code, 200)

        p2 = self._create_jpeg("same_name_v2.jpg", size=(400, 400), quality=95)
        with open(p2, "rb") as f2:
            r2 = self.client.post("/api/upload", data={"files": (f2, "same_name.jpg")}, content_type="multipart/form-data")
        self.assertEqual(r2.status_code, 200)
        d2 = r2.get_json()
        self.assertEqual(d2["summary"]["valid"], 1)
        # Should be renamed to same_name_1.jpg to prevent overwriting
        self.assertTrue(d2["files"][0]["filename"].startswith("same_name_"))

    # ─────────────────────────────────────────────────────────────
    # Test 7: PNG hợp lệ — Chấp nhận
    # ─────────────────────────────────────────────────────────────
    def test_07_valid_png_accepted(self):
        """7. Verify standard PNG image is validated and uploaded cleanly."""
        p = self.test_dir / "valid.png"
        im = Image.new("RGBA", (250, 250), color=(255, 0, 128, 200))
        im.save(p, format="PNG")

        valid, reason, (w, h), fmt = validate_image_file(p)
        self.assertTrue(valid)
        self.assertEqual(fmt, "PNG")

        with open(p, "rb") as f:
            resp = self.client.post("/api/upload", data={"files": (f, "valid.png")}, content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 200)
        d = resp.get_json()
        self.assertEqual(d["summary"]["valid"], 1)
        self.assertEqual(d["summary"]["corrupt"], 0)

    # ─────────────────────────────────────────────────────────────
    # Test 8: WebP hợp lệ — Chấp nhận
    # ─────────────────────────────────────────────────────────────
    def test_08_valid_webp_accepted(self):
        """8. Verify standard WebP image is validated and uploaded cleanly."""
        p = self.test_dir / "valid.webp"
        im = Image.new("RGB", (320, 240), color=(10, 200, 50))
        im.save(p, format="WEBP")

        valid, reason, (w, h), fmt = validate_image_file(p)
        self.assertTrue(valid)
        self.assertEqual(fmt, "WEBP")

        with open(p, "rb") as f:
            resp = self.client.post("/api/upload", data={"files": (f, "valid.webp")}, content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 200)
        d = resp.get_json()
        self.assertEqual(d["summary"]["valid"], 1)

    # ─────────────────────────────────────────────────────────────
    # Test 9: File đổi đuôi JPG nhưng không phải ảnh — Từ chối đúng loại
    # ─────────────────────────────────────────────────────────────
    def test_09_fake_disguised_jpg_rejected_properly(self):
        """9. Verify non-image text disguised as .jpg is rejected with UnidentifiedImageError."""
        fake_jpg = self.test_dir / "fake.jpg"
        fake_jpg.write_text("This is an executable or shell script disguised as JPEG.")

        res = validate_image_file(fake_jpg)
        self.assertFalse(res.is_valid)
        self.assertEqual(res.category, "corrupt")
        self.assertIn("unidentifiedimageerror", res.reason.lower())

        with open(fake_jpg, "rb") as f:
            resp = self.client.post("/api/upload", data={"files": (f, "fake.jpg")}, content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 200)
        d = resp.get_json()
        self.assertEqual(d["summary"]["corrupt"], 1)
        self.assertEqual(d["summary"]["valid"], 0)

    # ─────────────────────────────────────────────────────────────
    # Test 10: JPG thực sự bị hỏng — Báo corrupt
    # ─────────────────────────────────────────────────────────────
    def test_10_truly_corrupt_jpg_reported_as_corrupt(self):
        """10. Verify genuinely corrupted JPEG header/payload is reported as corrupt."""
        bad_jpg = self.test_dir / "corrupted_header.jpg"
        # Real JPEG starts with \xFF\xD8, write broken header
        bad_jpg.write_bytes(b"\xFF\xD8\xFF\xE0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00DEADBEEF_CORRUPTED_CHUNK")

        res = validate_image_file(bad_jpg)
        self.assertFalse(res.is_valid)
        self.assertEqual(res.category, "corrupt")

        with open(bad_jpg, "rb") as f:
            resp = self.client.post("/api/upload", data={"files": (f, "corrupted_header.jpg")}, content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 200)
        d = resp.get_json()
        self.assertEqual(d["summary"]["corrupt"], 1)
        self.assertEqual(d["summary"]["too_large"], 0)

    # ─────────────────────────────────────────────────────────────
    # Test 11: JPG có EXIF orientation — Đọc đúng
    # ─────────────────────────────────────────────────────────────
    def test_11_jpg_exif_orientation_handled(self):
        """11. Verify JPEG with EXIF orientation metadata is validated and handled."""
        p = self.test_dir / "exif_rotated.jpg"
        im = Image.new("RGB", (600, 400), color=(200, 100, 50))
        # Add EXIF orientation tag 6 (rotate 90 CW)
        exif = im.getexif()
        exif[0x0112] = 6
        im.save(p, format="JPEG", exif=exif)

        valid, reason, (w, h), fmt = validate_image_file(p)
        self.assertTrue(valid)
        self.assertEqual(fmt, "JPEG")
        # Rotated 90 deg: width and height swapped to (400, 600)
        self.assertEqual((w, h), (400, 600))

    # ─────────────────────────────────────────────────────────────
    # Test 12: JPG độ phân giải cao — Xử lý theo giới hạn tài nguyên
    # ─────────────────────────────────────────────────────────────
    def test_12_high_resolution_jpg_handled(self):
        """12. Verify high-resolution JPG (e.g. 50 MP) is accepted under UPLOAD_MAX_PIXELS."""
        p = self.test_dir / "high_res_50mp.jpg"
        # 10,000 x 5,000 = 50,000,000 pixels
        im = Image.new("RGB", (10000, 5000), color=(120, 120, 120))
        im.save(p, format="JPEG", quality=80)

        # Under 25M limit, it's rejected with category 'too_large'
        res_default = validate_image_file(p, max_pixels=25000000)
        self.assertFalse(res_default.is_valid)
        self.assertEqual(res_default.category, "too_large")
        self.assertIn("maximum pixel limit", res_default.reason)

        # Under UPLOAD_MAX_PIXELS (150M), it's accepted cleanly
        res_upload = validate_image_file(p, max_pixels=UPLOAD_MAX_PIXELS)
        self.assertTrue(res_upload.is_valid)
        self.assertEqual(res_upload.category, "valid")

    # ─────────────────────────────────────────────────────────────
    # Test 13: Upload file vượt giới hạn — Báo lỗi cụ thể, không báo corrupt
    # ─────────────────────────────────────────────────────────────
    def test_13_file_exceeding_limits_reports_specific_error(self):
        """13. Verify files exceeding pixel or size limits report too_large, NOT corrupt."""
        p = self.test_dir / "astronomical_pixels.jpg"
        # Create an image header exceeding UPLOAD_MAX_PIXELS (e.g. 16,000 x 10,000 = 160 MP > 150 MP)
        im = Image.new("1", (16000, 10000), color=1)
        im.save(p, format="PNG")  # use 1-bit PNG to keep file tiny while having 160 MP

        res = validate_image_file(p, max_pixels=UPLOAD_MAX_PIXELS)
        self.assertFalse(res.is_valid)
        self.assertEqual(res.category, "too_large")
        self.assertIn("exceeds maximum pixel limit", res.reason.lower())

        with open(p, "rb") as f:
            resp = self.client.post("/api/upload", data={"files": (f, "astronomical_pixels.png")}, content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 200)
        d = resp.get_json()
        self.assertEqual(d["summary"]["too_large"], 1)
        self.assertEqual(d["summary"]["corrupt"], 0, "Pixel limit exceeded MUST NOT be classified as corrupt!")
        self.assertEqual(len(d["errors"]), 1)
        self.assertEqual(d["errors"][0]["category"], "too_large")

    # ─────────────────────────────────────────────────────────────
    # Test 14: Xử lý lại cùng ảnh nhiều lần — Mỗi lần có Job ID riêng, không đè
    # ─────────────────────────────────────────────────────────────
    def test_14_reprocess_multiple_times_unique_job_ids_non_colliding_outputs(self):
        """14. Verify reprocessing generates unique Job IDs and non-colliding output paths."""
        out_dir = self.test_dir / "multi_outputs"
        out_dir.mkdir(parents=True, exist_ok=True)

        # 1. Test get_unique_output_path collision avoidance
        p1 = get_unique_output_path(out_dir, "test_item", ".png")
        self.assertEqual(p1.name, "test_item_4x.png")
        p1.write_text("output 1")

        p2 = get_unique_output_path(out_dir, "test_item", ".png")
        self.assertEqual(p2.name, "test_item_4x_1.png")
        p2.write_text("output 2")

        p3 = get_unique_output_path(out_dir, "test_item", ".png")
        self.assertEqual(p3.name, "test_item_4x_2.png")
        p3.write_text("output 3")

        # 2. Test multiple reprocess requests create distinct Job IDs
        src_img = self._create_jpeg("source_loop.jpg", size=(128, 128))
        with open(src_img, "rb") as f:
            r = self.client.post("/api/upload", data={"files": (f, "source_loop.jpg")}, content_type="multipart/form-data")
        job_0 = r.get_json()["files"][0]["job_id"]

        r_rep1 = self.client.post("/api/reprocess", json={"job_id": job_0})
        job_1 = r_rep1.get_json()["jobs"][0]["id"]

        r_rep2 = self.client.post("/api/reprocess", json={"job_id": job_0})
        job_2 = r_rep2.get_json()["jobs"][0]["id"]

        self.assertNotEqual(job_0, job_1)
        self.assertNotEqual(job_1, job_2)
        self.assertNotEqual(job_0, job_2)

        with jobs_lock:
            self.assertIn(job_0, jobs)
            self.assertIn(job_1, jobs)
            self.assertIn(job_2, jobs)


if __name__ == "__main__":
    unittest.main()
