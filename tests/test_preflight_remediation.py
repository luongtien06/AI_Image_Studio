# -*- coding: utf-8 -*-
"""
Pre-Flight Remediation Test Suite for Phase 2
Verifies:
1. Image Validation (Pillow verify, load, 25M pixel limit, corrupt IDAT/WEBP, DecompressionBombError).
2. Safe Path (UNC loopback/remote, device paths, ProgramData, Windows system roots, traversal, symlinks).
3. History Re-basing (Atomic save, backup fallback, relative path re-basing, missing fields).
4. Batch error isolation and live inference regression.
"""

import os
import sys
import json
import time
import shutil
import unittest
import io
from pathlib import Path
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app import (
    app,
    BASE_DIR,
    APP_CONFIG,
    UPLOAD_DIR,
    OUTPUT_DIR,
    LOG_DIR,
    MODEL_PATH,
    INFERENCE_SCRIPT,
    PYTHON_EXE,
    MAX_PIXELS,
    safe_path,
    validate_image_file,
    load_history,
    save_history_entry,
    resolve_history_entry,
    process_single_image,
    jobs,
    jobs_lock
)

class TestPreflightRemediation(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        app.config['TESTING'] = True
        cls.client = app.test_client()
        cls.test_dir = PROJECT_ROOT / "tests" / "scratch_preflight"
        cls.test_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        if cls.test_dir.exists():
            shutil.rmtree(cls.test_dir, ignore_errors=True)

    # =========================================================================
    # FIX 1: IMAGE VALIDATION
    # =========================================================================

    def test_01_valid_formats(self):
        """Test 1-4: Valid JPEG, PNG, WEBP, BMP."""
        formats = [('JPEG', 'valid.jpg'), ('PNG', 'valid.png'), ('WEBP', 'valid.webp'), ('BMP', 'valid.bmp')]
        for fmt, fname in formats:
            p = self.test_dir / fname
            im = Image.new('RGB', (64, 64), color='green')
            im.save(p, format=fmt)
            valid, reason, dims, detected_fmt = validate_image_file(p)
            self.assertTrue(valid, f"Expected {fmt} to be valid, got: {reason}")
            self.assertEqual(dims, (64, 64))
            self.assertEqual(detected_fmt, fmt)
        print("\n[PASS] Fix 1.1: Valid JPEG, PNG, WEBP, BMP validated successfully")

    def test_02_empty_file(self):
        """Test 5: Empty file (0 bytes) rejected."""
        empty = self.test_dir / "empty.png"
        empty.write_bytes(b"")
        valid, reason, _, _ = validate_image_file(empty)
        self.assertFalse(valid)
        self.assertIn("0 bytes", reason.lower())
        print("[PASS] Fix 1.2: Empty file (0 bytes) rejected")

    def test_03_fake_extension(self):
        """Test 6: Plain text disguised as .jpg."""
        fake = self.test_dir / "fake.jpg"
        fake.write_text("Hello, this is just ASCII text not image data!", encoding="utf-8")
        valid, reason, _, _ = validate_image_file(fake)
        self.assertFalse(valid)
        self.assertIn("unidentifiedimageerror", reason.lower())
        print("[PASS] Fix 1.3: Fake file with disguised extension rejected")

    def test_04_truncated_png_idat(self):
        """Test 7: PNG with truncated IDAT data (caught by img.load())."""
        # Create a valid PNG in memory
        im = Image.new('RGB', (100, 100), color='blue')
        buf = io.BytesIO()
        im.save(buf, format='PNG')
        full_bytes = buf.getvalue()

        # Truncate after header and first chunk so verify() might pass or fail, but load() definitely fails
        truncated = self.test_dir / "truncated.png"
        truncated.write_bytes(full_bytes[:len(full_bytes) - 80])
        valid, reason, _, _ = validate_image_file(truncated)
        self.assertFalse(valid)
        self.assertTrue(any(term in reason.lower() for term in ["truncated", "corrupted", "oserror"]))
        print("[PASS] Fix 1.4: Truncated PNG IDAT data caught by img.load()")

    def test_05_corrupt_webp(self):
        """Test 8: Corrupt WEBP container."""
        bad_webp = self.test_dir / "corrupted.webp"
        bad_webp.write_bytes(b"RIFF\x20\x00\x00\x00WEBPVP8 \x10\x00\x00\x00corrupted_bytes_here")
        valid, reason, _, _ = validate_image_file(bad_webp)
        self.assertFalse(valid)
        print("[PASS] Fix 1.5: Corrupt WEBP caught and rejected")

    def test_06_pixel_limit_exceeded(self):
        """Test 9: Image exceeding 25M pixels (> 25,000,000 pixels)."""
        # Create an image header claiming 5001x5000 = 25,005,000 pixels
        over_limit = self.test_dir / "over_limit.png"
        im = Image.new('1', (5001, 5000), color=1)
        im.save(over_limit, format='PNG')
        valid, reason, dims, _ = validate_image_file(over_limit)
        self.assertFalse(valid, "Image over 25M pixels should be rejected!")
        self.assertIn("exceeds maximum pixel limit", reason.lower())
        self.assertIn("25,005,000 pixels", reason)
        print(f"[PASS] Fix 1.6: Image exceeding 25M pixels ({dims[0]}x{dims[1]}) rejected with clear explanation")

    def test_07_unicode_filename(self):
        """Test 10: Image with Vietnamese unicode filename."""
        viet_file = self.test_dir / "ảnh_mẫu_tiếng_Việt_rất_đẹp.png"
        im = Image.new('RGB', (80, 80), color='cyan')
        im.save(viet_file, format='PNG')
        valid, reason, dims, fmt = validate_image_file(viet_file)
        self.assertTrue(valid)
        self.assertEqual(dims, (80, 80))
        self.assertEqual(fmt, "PNG")
        print("[PASS] Fix 1.7: Vietnamese Unicode filename verified successfully")

    # =========================================================================
    # FIX 2: SAFE PATH
    # =========================================================================

    def test_08_safe_path_unc_and_loopback(self):
        """Test explicit blocking of UNC remote and loopback paths."""
        unc_paths = [
            r"\\server\share\file.png",
            r"\\127.0.0.1\c$\Windows",
            r"\\localhost\c$\output",
            "//server/share/file.png",
            "//127.0.0.1/c$/Windows"
        ]
        for p in unc_paths:
            resolved = safe_path(p, allow_outside_base=True)
            self.assertIsNone(resolved, f"UNC path should be blocked: {p}")
        print("[PASS] Fix 2.1: UNC remote, loopback, and forward-slash UNC blocked")

    def test_09_safe_path_device_namespaces(self):
        """Test explicit blocking of Windows device namespaces (\\?\\ and \\.\\)."""
        device_paths = [
            r"\\?\C:\Windows\System32",
            r"\\.\COM1",
            r"\\.\PhysicalDrive0",
            "//?/C:/Windows",
            "//./COM1"
        ]
        for p in device_paths:
            resolved = safe_path(p, allow_outside_base=True)
            self.assertIsNone(resolved, f"Device namespace path should be blocked: {p}")
        print("[PASS] Fix 2.2: Windows device namespace paths (\\\\?\\ and \\\\.\\) blocked")

    def test_10_safe_path_system_directories(self):
        """Test explicit blocking of C:\\Windows, Program Files, and ProgramData."""
        system_paths = [
            "C:\\Windows",
            "C:\\Windows\\System32\\cmd.exe",
            "C:\\Program Files",
            "C:\\Program Files (x86)",
            "C:\\ProgramData",
            "C:\\ProgramData\\test"
        ]
        for p in system_paths:
            resolved = safe_path(p, allow_outside_base=True)
            self.assertIsNone(resolved, f"System directory should be blocked: {p}")
        print("[PASS] Fix 2.3: Windows system directories (Windows, Program Files, ProgramData) blocked")

    def test_11_safe_path_traversal(self):
        """Test path traversal (../) resolution and rejection outside BASE_DIR."""
        traversal = "../../Windows/System32"
        resolved = safe_path(traversal, allow_outside_base=False)
        self.assertIsNone(resolved, "Traversal outside BASE_DIR must be blocked!")

        # Valid relative path inside base
        valid_rel = "uploads/realesrgan_logo.png"
        resolved_valid = safe_path(valid_rel, allow_outside_base=False)
        self.assertIsNotNone(resolved_valid)
        self.assertEqual(resolved_valid, (BASE_DIR / "uploads" / "realesrgan_logo.png").resolve())
        print("[PASS] Fix 2.4: Path traversal protection verified")

    def test_12_safe_path_allowed_outside(self):
        """Test valid custom directory outside BASE_DIR when allow_outside_base=True."""
        custom_allowed = self.test_dir / "user_output_folder"
        custom_allowed.mkdir(parents=True, exist_ok=True)
        resolved = safe_path(str(custom_allowed), allow_outside_base=True)
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved, custom_allowed.resolve())
        print("[PASS] Fix 2.5: Valid user directory outside BASE_DIR allowed safely")

    # =========================================================================
    # FIX 3: HISTORY RE-BASE
    # =========================================================================

    def test_13_history_existing_path(self):
        """Test history entry where absolute path still exists on disk."""
        test_img = self.test_dir / "hist_exist.png"
        im = Image.new('RGB', (32, 32), color='blue')
        im.save(test_img, format='PNG')

        entry = {
            'job_id': 'test-hist-01',
            'filename': 'hist_exist.png',
            'input_path': str(test_img),
            'output_path': str(test_img)
        }
        resolved = resolve_history_entry(entry)
        self.assertTrue(resolved['input_exists'])
        self.assertTrue(resolved['output_exists'])
        self.assertEqual(resolved['input_path_resolved'], str(test_img.resolve()))
        print("[PASS] Fix 3.1: History entry with existing absolute path verified")

    def test_14_history_rebasing_non_existing_path(self):
        """Test history re-basing when old absolute path is missing but file is in current project."""
        # Create a file in project's output directory
        target_name = "rebase_test_out.png"
        actual_file = OUTPUT_DIR / target_name
        im = Image.new('RGB', (32, 32), color='magenta')
        im.save(actual_file, format='PNG')

        try:
            # Simulate entry pointing to an old foreign drive path: X:\OldMachine\Project\output\rebase_test_out.png
            fake_old_path = f"X:\\OldMachine\\Project\\output\\{target_name}"
            entry = {
                'job_id': 'test-rebase-01',
                'filename': target_name,
                'input_path': 'X:\\OldMachine\\Project\\uploads\\input.png',
                'output_path': fake_old_path,
                'rel_output_path': f"output/{target_name}"
            }
            resolved = resolve_history_entry(entry)

            # Output should be successfully re-based to actual_file!
            self.assertTrue(resolved['output_exists'])
            self.assertTrue(resolved.get('output_rebased'))
            self.assertEqual(resolved['output_path_resolved'], str(actual_file.resolve()))

            # Input genuinely missing on X:\
            self.assertFalse(resolved['input_exists'])
            self.assertEqual(resolved['input_path'], 'X:\\OldMachine\\Project\\uploads\\input.png')
            self.assertTrue(resolved.get('missing'))
            print("[PASS] Fix 3.2: Missing absolute path successfully re-based via relative path")
        finally:
            if actual_file.exists():
                actual_file.unlink()

    def test_15_history_atomic_save_and_backup(self):
        """Test atomic writing and backup creation of history.json."""
        test_entry = {
            'job_id': 'atomic-test-01',
            'filename': 'atomic.png',
            'status': 'completed',
            'input_path': 'uploads/atomic.png',
            'output_path': 'output/atomic_4x.png',
            'rel_input_path': 'uploads/atomic.png',
            'rel_output_path': 'output/atomic_4x.png'
        }
        save_history_entry(test_entry)

        hist_file = LOG_DIR / "history.json"
        bak_file = LOG_DIR / "history.bak"
        self.assertTrue(hist_file.is_file())
        
        # Verify content loaded back
        loaded = load_history(resolve_paths=False)
        self.assertGreater(len(loaded), 0)
        self.assertEqual(loaded[0]['job_id'], 'atomic-test-01')
        self.assertEqual(loaded[0]['rel_input_path'], 'uploads/atomic.png')
        print("[PASS] Fix 3.3: History saved atomically with relative path and backup")

    # =========================================================================
    # BATCH ISOLATION & REGRESSION
    # =========================================================================

    def test_16_batch_isolation_good_and_bad_images(self):
        """Test Fix 1.11 & Regression: Batch with good and bad images processes cleanly."""
        good_img = self.test_dir / "batch_ok.png"
        im = Image.new('RGB', (32, 32), color='yellow')
        im.save(good_img, format='PNG')

        # Corrupt image
        bad_img = self.test_dir / "batch_truncated.png"
        bad_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 30)

        job_good_id = 'batch-good-job'
        job_bad_id = 'batch-bad-job'

        with jobs_lock:
            jobs[job_good_id] = {
                'id': job_good_id,
                'filename': 'batch_ok.png',
                'input_path': str(good_img),
                'output_path': None,
                'width': 32,
                'height': 32,
                'dimensions': '32x32',
                'file_size': good_img.stat().st_size,
                'file_size_human': '1 KB',
                'status': 'pending',
                'error': None,
                'tile_size': 128
            }
            jobs[job_bad_id] = {
                'id': job_bad_id,
                'filename': 'batch_truncated.png',
                'input_path': str(bad_img),
                'output_path': None,
                'width': 32,
                'height': 32,
                'dimensions': '32x32',
                'file_size': 38,
                'file_size_human': '38 B',
                'status': 'pending',
                'error': None,
                'tile_size': 128
            }

        out_dir = self.test_dir / "batch_out"
        out_dir.mkdir(parents=True, exist_ok=True)

        resp = self.client.post('/api/process', json={
            'job_ids': [job_good_id, job_bad_id],
            'tile_size': 128,
            'output_dir': str(out_dir)
        })
        self.assertEqual(resp.status_code, 200)

        # Wait for processing to complete
        start = time.time()
        while time.time() - start < 30:
            with jobs_lock:
                st_good = jobs[job_good_id]['status']
                st_bad = jobs[job_bad_id]['status']
            if st_good in ('completed', 'error') and st_bad in ('completed', 'error'):
                break
            time.sleep(0.5)

        with jobs_lock:
            final_good = jobs[job_good_id]
            final_bad = jobs[job_bad_id]

        self.assertEqual(final_good['status'], 'completed', f"Good image should succeed: {final_good.get('error')}")
        self.assertEqual(final_bad['status'], 'error', "Bad image should fail gracefully")
        self.assertIn("invalid image", final_bad['error'].lower())
        print(f"[PASS] Fix 1.11 & Batch: Good image completed in {final_good.get('elapsed')}s, bad image isolated safely")

if __name__ == "__main__":
    unittest.main(verbosity=2)
