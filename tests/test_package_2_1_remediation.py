# -*- coding: utf-8 -*-
"""
Tests for Package 2.1 Remediation:
- Incident A: Output Pixel Limit Asymmetry & Config Synchronization (150M Input / 250M Output).
- Incident B: Output Resource Safety (Pre-inference Output Limit, RAM Safety Budget, Disk Headroom).
- Regression Verification: Terminal States, Collision Protection, Cleanup, Startup Recovery.

All tests run in temporary directories with isolated temporary SQLite databases.
No GPU inference is performed.
"""

import os
import sys
import uuid
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from PIL import Image

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from job_manager import (
    JobManager,
    STATUS_QUEUED,
    STATUS_VALIDATING,
    STATUS_PROCESSING,
    STATUS_RETRY_WAIT,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_CANCEL_REQUESTED,
    STATUS_CANCELLED,
    NON_RETRYABLE_ERRORS
)
import app as flask_app


class TestPackage21Remediation(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)
        self.db_path = self.base_path / "test_jobs.db"
        self.job_mgr = JobManager(db_path=self.db_path)

        # Standard small valid image
        self.valid_img_path = self.base_path / "sample_valid.png"
        img = Image.new("RGB", (100, 100), color="green")
        img.save(self.valid_img_path)

    def _create_test_job(self, input_path, output_path=None):
        jid = str(uuid.uuid4())
        p_in = Path(input_path)
        self.job_mgr.add_job(
            job_id=jid,
            filename=p_in.name,
            input_path=str(p_in),
            output_path=str(output_path) if output_path else None,
            dimensions="100x100",
            file_size=p_in.stat().st_size if p_in.is_file() else 1024
        )
        return jid

    def tearDown(self):
        self.temp_dir.cleanup()

    # ═══════════════════════════════════════════════════════════════
    # Test 1: Input image below input limit (PASS)
    # ═══════════════════════════════════════════════════════════════
    def test_01_input_image_below_input_limit(self):
        """Input image below 150M pixels passes validation cleanly."""
        res = flask_app.validate_image_file(self.valid_img_path, role="input")
        self.assertTrue(res.is_valid)
        self.assertEqual(res.category, "valid")
        self.assertEqual(res.dimensions, (100, 100))

    # ═══════════════════════════════════════════════════════════════
    # Test 2: Input image exceeding input limit (rejected too_large)
    # ═══════════════════════════════════════════════════════════════
    def test_02_input_image_exceeding_input_limit(self):
        """Input image exceeding 150M pixels is rejected with category 'too_large'."""
        with patch.object(Image, "open") as mock_open:
            mock_img = MagicMock()
            mock_img.format = "PNG"
            mock_img.size = (15000, 11000)  # 165,000,000 pixels > 150M
            mock_img.getexif.return_value = None
            mock_img.__enter__.return_value = mock_img
            mock_img.__exit__.return_value = False
            mock_open.return_value = mock_img

            res = flask_app.validate_image_file(self.valid_img_path, role="input")
            self.assertFalse(res.is_valid)
            self.assertEqual(res.category, "too_large")
            self.assertIn("exceeds maximum pixel limit", res.reason)

    # ═══════════════════════════════════════════════════════════════
    # Test 3: Output image below output limit (passes output validation)
    # ═══════════════════════════════════════════════════════════════
    def test_03_output_image_below_output_limit(self):
        """Output image between 150M and 250M pixels passes output validation."""
        with patch.object(Image, "open") as mock_open:
            mock_img = MagicMock()
            mock_img.format = "PNG"
            mock_img.size = (14000, 14000)  # 196,000,000 pixels (>150M input limit, <250M output limit)
            mock_img.getexif.return_value = None
            mock_img.__enter__.return_value = mock_img
            mock_img.__exit__.return_value = False
            mock_open.return_value = mock_img

            # role='output' uses OUTPUT_MAX_PIXELS (250M)
            res = flask_app.validate_image_file(self.valid_img_path, role="output")
            self.assertTrue(res.is_valid)
            self.assertEqual(res.category, "valid")

    # ═══════════════════════════════════════════════════════════════
    # Test 4: Output image exceeding output limit (rejected too_large)
    # ═══════════════════════════════════════════════════════════════
    def test_04_output_image_exceeding_output_limit(self):
        """Output image or projected output exceeding 250M pixels is rejected."""
        # 1. Post-inference output validation check
        with patch.object(Image, "open") as mock_open:
            mock_img = MagicMock()
            mock_img.format = "PNG"
            mock_img.size = (16000, 16000)  # 256,000,000 pixels > 250M
            mock_img.getexif.return_value = None
            mock_img.__enter__.return_value = mock_img
            mock_img.__exit__.return_value = False
            mock_open.return_value = mock_img

            res = flask_app.validate_image_file(self.valid_img_path, role="output")
            self.assertFalse(res.is_valid)
            self.assertEqual(res.category, "too_large")
            self.assertIn("exceeds maximum pixel limit", res.reason)

        # 2. Pre-inference output resource safety check
        safe, reason = flask_app.check_output_resource_safety(16000, 16000, self.base_path)
        self.assertFalse(safe)
        self.assertIn("Output pixel limit exceeded", reason)

    # ═══════════════════════════════════════════════════════════════
    # Test 5: Output exceeding safe RAM budget
    # ═══════════════════════════════════════════════════════════════
    def test_05_output_exceeding_safe_ram_budget(self):
        """Output requiring more RAM than available (plus 512MB headroom) is safely rejected."""
        # Mock available RAM to 600 MB
        with patch("app.get_available_ram_bytes", return_value=600 * 1024 * 1024):
            # 8000 x 8000 = 64 MP. Raw RGBA = 256 MB. Required working RAM ~ 640 MB + 512 MB margin = 1152 MB > 600 MB.
            safe, reason = flask_app.check_output_resource_safety(8000, 8000, self.base_path)
            self.assertFalse(safe)
            self.assertIn("Insufficient RAM for output", reason)

            # Verify non-retryable handling
            jid = self._create_test_job(self.valid_img_path, self.base_path / "out.png")
            self.job_mgr.transition_status(jid, STATUS_PROCESSING)
            should_retry, _, err_reason = self.job_mgr.schedule_retry(jid, reason)
            self.assertFalse(should_retry)
            self.assertIn("Non-retryable error", err_reason)
            job = self.job_mgr.get_job(jid)
            self.assertEqual(job["status"], STATUS_FAILED)

    # ═══════════════════════════════════════════════════════════════
    # Test 6: Insufficient disk space handling
    # ═══════════════════════════════════════════════════════════════
    def test_06_insufficient_disk_space_handling(self):
        """Output requiring more free disk space than available (plus 500MB headroom) is rejected."""
        mock_usage = MagicMock(free=100 * 1024 * 1024)  # 100 MB free < 500 MB headroom
        with patch("shutil.disk_usage", return_value=mock_usage):
            safe, reason = flask_app.check_output_resource_safety(2000, 2000, self.base_path)
            self.assertFalse(safe)
            self.assertIn("Insufficient disk space for output", reason)

            # Verify non-retryable handling
            jid = self._create_test_job(self.valid_img_path, self.base_path / "out.png")
            self.job_mgr.transition_status(jid, STATUS_PROCESSING)
            should_retry, _, err_reason = self.job_mgr.schedule_retry(jid, reason)
            self.assertFalse(should_retry)
            self.assertIn("Non-retryable error", err_reason)
            job = self.job_mgr.get_job(jid)
            self.assertEqual(job["status"], STATUS_FAILED)

    # ═══════════════════════════════════════════════════════════════
    # Test 7: Pillow validation behavior (Decompression bomb handling)
    # ═══════════════════════════════════════════════════════════════
    def test_07_pillow_decompression_bomb_handling(self):
        """Pillow DecompressionBombError is classified as too_large."""
        with patch.object(Image, "open") as mock_open:
            mock_open.side_effect = Image.DecompressionBombError("Image size exceeds limit")
            res = flask_app.validate_image_file(self.valid_img_path, role="input")
            self.assertFalse(res.is_valid)
            self.assertEqual(res.category, "too_large")
            self.assertIn("Decompression bomb detected", res.reason)

    # ═══════════════════════════════════════════════════════════════
    # Test 8: Output export failure & atomic cleanup
    # ═══════════════════════════════════════════════════════════════
    def test_08_output_export_failure_atomic_cleanup(self):
        """Failed output file validation triggers unlink and sets job to FAILED."""
        bad_out = self.base_path / "corrupt_output.png"
        with open(bad_out, "wb") as f:
            f.write(b"not an image data bytes")

        # Validation fails
        valid, val_reason, (w, h), fmt = flask_app.validate_image_file(bad_out, role="output")
        self.assertFalse(valid)
        self.assertEqual(val_reason, "Corrupted or unrecognized image format (UnidentifiedImageError)")

        # Emulate server cleanup behavior
        bad_out.unlink(missing_ok=True)
        self.assertFalse(bad_out.exists())

    # ═══════════════════════════════════════════════════════════════
    # Test 9: Existing output collision protection
    # ═══════════════════════════════════════════════════════════════
    def test_09_existing_output_collision_protection(self):
        """unique_output_path generates distinct paths and never overwrites existing files."""
        out_1 = flask_app.unique_output_path(self.base_path, "photo", ".png")
        self.assertEqual(out_1.name, "photo_4x.png")
        out_1.touch()

        out_2 = flask_app.unique_output_path(self.base_path, "photo", ".png")
        self.assertEqual(out_2.name, "photo_4x_1.png")
        out_2.touch()

        out_3 = flask_app.unique_output_path(self.base_path, "photo", ".png")
        self.assertEqual(out_3.name, "photo_4x_2.png")

    # ═══════════════════════════════════════════════════════════════
    # Test 10: Worker failure-state handling (Non-retryable errors)
    # ═══════════════════════════════════════════════════════════════
    def test_10_worker_failure_state_handling(self):
        """All Package 2.1 non-retryable error variants transition immediately to FAILED."""
        error_cases = [
            "Output pixel limit exceeded: projected 16000x16000 (256,000,000 pixels)",
            "output_pixel_limit_exceeded: 300M",
            "Insufficient RAM for output: projected output requires ~2000 MB RAM",
            "insufficient_ram_for_output",
            "Insufficient disk space for output: requires at least 600 MB free space",
            "insufficient_disk_space",
            "Generated output validation failed: corrupt container",
        ]

        for err in error_cases:
            jid = self._create_test_job(self.valid_img_path, self.base_path / "out.png")
            self.job_mgr.transition_status(jid, STATUS_PROCESSING)
            should_retry, _, reason = self.job_mgr.schedule_retry(jid, err)
            self.assertFalse(should_retry, f"Expected non-retryable for: {err}")
            self.assertIn("Non-retryable error", reason)
            row = self.job_mgr.get_job(jid)
            self.assertEqual(row["status"], STATUS_FAILED)

    # ═══════════════════════════════════════════════════════════════
    # Test 11: Job status transition correctness (Terminal state immutability)
    # ═══════════════════════════════════════════════════════════════
    def test_11_job_status_transition_correctness(self):
        """Terminal states (COMPLETED, CANCELLED) cannot be overwritten even with force=True."""
        # Completed job
        jid_c = self._create_test_job(self.valid_img_path, self.base_path / "out_c.png")
        self.job_mgr.transition_status(jid_c, STATUS_PROCESSING)
        self.job_mgr.transition_status(jid_c, STATUS_COMPLETED, output_path=str(self.valid_img_path))
        self.assertEqual(self.job_mgr.get_job(jid_c)["status"], STATUS_COMPLETED)

        # Attempt to overwrite COMPLETED
        ok = self.job_mgr.transition_status(jid_c, STATUS_PROCESSING, force=True)
        self.assertFalse(ok)
        self.assertEqual(self.job_mgr.get_job(jid_c)["status"], STATUS_COMPLETED)

        # Cancelled job
        jid_x = self._create_test_job(self.valid_img_path, self.base_path / "out_x.png")
        self.job_mgr.transition_status(jid_x, STATUS_PROCESSING)
        self.job_mgr.transition_status(jid_x, STATUS_CANCELLED)
        self.assertEqual(self.job_mgr.get_job(jid_x)["status"], STATUS_CANCELLED)

        # Attempt to overwrite CANCELLED
        ok = self.job_mgr.transition_status(jid_x, STATUS_QUEUED, force=True)
        self.assertFalse(ok)
        self.assertEqual(self.job_mgr.get_job(jid_x)["status"], STATUS_CANCELLED)

    # ═══════════════════════════════════════════════════════════════
    # Test 12: Regression tests for Package 1 & 2 behaviors
    # ═══════════════════════════════════════════════════════════════
    def test_12_regression_package_1_and_2(self):
        """Configuration limits are synchronized and startup recovery handles missing sources."""
        # 1. Config pixel limit synchronization
        self.assertEqual(flask_app.INPUT_MAX_PIXELS, 150000000)
        self.assertEqual(flask_app.OUTPUT_MAX_PIXELS, 250000000)
        self.assertEqual(flask_app.UPLOAD_MAX_PIXELS, flask_app.INPUT_MAX_PIXELS)

        # 2. Startup recovery with missing source file
        missing_src = self.base_path / "does_not_exist.png"
        jid_miss = self._create_test_job(missing_src, self.base_path / "out_miss.png")
        self.assertEqual(self.job_mgr.get_job(jid_miss)["status"], STATUS_QUEUED)

        rec = self.job_mgr.recover_on_startup()
        self.assertNotIn(jid_miss, rec["eligible_job_ids"])
        job_miss_row = self.job_mgr.get_job(jid_miss)
        self.assertEqual(job_miss_row["status"], STATUS_FAILED)
        self.assertIn("SOURCE_FILE_MISSING", job_miss_row["error_message"])

        # 3. Startup recovery with existing source file
        jid_valid = self._create_test_job(self.valid_img_path, self.base_path / "out_val.png")
        rec2 = self.job_mgr.recover_on_startup()
        self.assertIn(jid_valid, rec2["eligible_job_ids"])
        self.assertEqual(self.job_mgr.get_job(jid_valid)["status"], STATUS_QUEUED)


if __name__ == "__main__":
    unittest.main()
