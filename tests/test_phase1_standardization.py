# -*- coding: utf-8 -*-
"""
Test Suite for Phase 1 - AI Image Studio Project Standardization
Validates environment, config, path safety, image validation, GPU inference, and batch handling.
"""

import sys
import os
import json
import hashlib
import time
import shutil
import unittest
import importlib.metadata
from pathlib import Path
from PIL import Image

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app import (
    app,
    BASE_DIR,
    APP_CONFIG,
    MODEL_PATH,
    INFERENCE_SCRIPT,
    PYTHON_EXE,
    safe_path,
    validate_image_file,
    jobs,
    jobs_lock
)

class TestPhase1Standardization(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        app.config['TESTING'] = True
        cls.client = app.test_client()
        cls.test_dir = PROJECT_ROOT / "tests" / "scratch_phase1"
        cls.test_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        if cls.test_dir.exists():
            shutil.rmtree(cls.test_dir, ignore_errors=True)

    # =========================================================================
    # 1. Environment & Config Integrity
    # =========================================================================
    def test_01_environment_integrity(self):
        """Verify Python, PyTorch, CUDA, BasicSR, and Real-ESRGAN versions."""
        import torch
        import torchvision
        import basicsr

        self.assertGreaterEqual(sys.version_info, (3, 10))
        self.assertEqual(torch.__version__, "2.6.0+cu126")
        self.assertEqual(torchvision.__version__, "0.21.0+cu126")
        self.assertEqual(basicsr.__version__, "1.4.2")
        
        realesrgan_ver = importlib.metadata.version('realesrgan')
        self.assertEqual(realesrgan_ver, "0.3.0")
        
        self.assertTrue(torch.cuda.is_available(), "CUDA is not available!")
        gpu_name = torch.cuda.get_device_name(0)
        self.assertIn("RTX 2050", gpu_name)
        print(f"\n[PASS] Environment: PyTorch {torch.__version__}, CUDA {torch.version.cuda}, realesrgan {realesrgan_ver}, GPU: {gpu_name}")

    def test_02_model_weights_integrity(self):
        """Verify RealESRGAN_x4plus weights file existence and SHA-256."""
        self.assertTrue(MODEL_PATH.exists(), f"Model weights not found at {MODEL_PATH}")
        self.assertEqual(MODEL_PATH.stat().st_size, 67040989)
        
        hasher = hashlib.sha256()
        with open(MODEL_PATH, 'rb') as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        calc_sha = hasher.hexdigest().upper()
        expected_sha = "4FA0D38905F75AC06EB49A7951B426670021BE3018265FD191D2125DF9D682F1"
        self.assertEqual(calc_sha, expected_sha, "Model weights SHA-256 mismatch!")
        print(f"[PASS] Model Weights: RealESRGAN_x4plus.pth (SHA-256 verified)")

    def test_03_config_json_structure(self):
        """Verify config.json loads properly and contains all required sections."""
        self.assertIn("server", APP_CONFIG)
        self.assertIn("directories", APP_CONFIG)
        self.assertIn("inference", APP_CONFIG)
        self.assertIn("security", APP_CONFIG)

        self.assertEqual(APP_CONFIG["server"]["host"], "127.0.0.1")
        self.assertEqual(APP_CONFIG["server"]["port"], 5678)
        self.assertEqual(APP_CONFIG["inference"]["model_name"], "RealESRGAN_x4plus")
        self.assertEqual(APP_CONFIG["inference"]["scale"], 4)
        self.assertEqual(APP_CONFIG["inference"]["default_tile"], 128)
        self.assertEqual(APP_CONFIG["inference"]["default_gpu_id"], 0)
        print(f"[PASS] Config: Validated 127.0.0.1:5678, model=RealESRGAN_x4plus, scale=4")

    # =========================================================================
    # 2. Path Safety & Vietnamese / Unicode Handling
    # =========================================================================
    def test_04_path_traversal_protection(self):
        """Verify safe_path blocks traversal outside allowed base and system directories."""
        # 1. Traversal outside base
        traversal_path = "../../Windows/System32"
        resolved = safe_path(traversal_path, allow_outside_base=False)
        self.assertIsNone(resolved, "Traversal path outside BASE_DIR should be blocked!")

        # 2. Blocked system root (even if allow_outside_base=True)
        win_path = "C:\\Windows\\System32\\cmd.exe"
        resolved_win = safe_path(win_path, allow_outside_base=True)
        self.assertIsNone(resolved_win, "Windows system directory should be blocked!")

        prog_path = "C:\\Program Files\\app"
        resolved_prog = safe_path(prog_path, allow_outside_base=True)
        self.assertIsNone(resolved_prog, "Program Files directory should be blocked!")

        # 3. Normal allowed path inside base
        valid_rel = "uploads/test.png"
        resolved_valid = safe_path(valid_rel, allow_outside_base=False)
        self.assertIsNotNone(resolved_valid)
        self.assertEqual(resolved_valid, (BASE_DIR / "uploads" / "test.png").resolve())
        print(f"[PASS] Path Safety: Traversal and system directory blocks verified")

    def test_05_unicode_and_spaces_path_handling(self):
        """Verify safe_path and file creation handles spaces and Vietnamese characters."""
        viet_dir = self.test_dir / "thư mục thử nghiệm tiếng Việt"
        viet_dir.mkdir(parents=True, exist_ok=True)
        viet_file = viet_dir / "ảnh mẫu thử nghiệm.png"

        # Create a small valid test PNG
        img = Image.new('RGB', (100, 100), color='blue')
        img.save(viet_file, format='PNG')

        resolved = safe_path(str(viet_file), allow_outside_base=True)
        self.assertIsNotNone(resolved)
        self.assertTrue(resolved.exists())

        valid, err_val, dims, fmt = validate_image_file(resolved)
        self.assertTrue(valid)
        self.assertEqual(dims, (100, 100))
        self.assertEqual(fmt, "PNG")
        print(f"[PASS] Unicode Paths: Vietnamese characters and spaces handled correctly")

    # =========================================================================
    # 3. File Validation & Security
    # =========================================================================
    def test_06_image_validation_valid_formats(self):
        """Verify PNG, JPEG, WEBP, BMP images pass validation."""
        formats = [('PNG', 'test.png'), ('JPEG', 'test.jpg'), ('WEBP', 'test.webp'), ('BMP', 'test.bmp')]
        for fmt, fname in formats:
            fpath = self.test_dir / fname
            img = Image.new('RGB', (64, 64), color='red')
            img.save(fpath, format=fmt)
            valid, err, dims, detected_fmt = validate_image_file(fpath)
            self.assertTrue(valid, f"Failed for {fmt}: {err}")
            self.assertEqual(detected_fmt, fmt)
            self.assertEqual(dims, (64, 64))
        print(f"[PASS] Image Formats: Validated PNG, JPEG, WEBP, BMP")

    def test_07_image_validation_corrupted_and_fake(self):
        """Verify corrupted, fake, and 0-byte files are rejected."""
        # 1. 0-byte file
        zero_file = self.test_dir / "empty.jpg"
        zero_file.write_bytes(b"")
        valid, err, _, _ = validate_image_file(zero_file)
        self.assertFalse(valid)
        self.assertIn("empty", err.lower())

        # 2. Text file disguised as .jpg
        fake_file = self.test_dir / "fake.jpg"
        fake_file.write_text("This is NOT an image, it is plain text.", encoding="utf-8")
        valid, err, _, _ = validate_image_file(fake_file)
        self.assertFalse(valid)
        self.assertTrue(any(term in err.lower() for term in ["unidentified", "cannot identify", "invalid"]))

        # 3. Corrupted header
        bad_header = self.test_dir / "corrupted.png"
        bad_header.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\xff" * 20)
        valid, err, _, _ = validate_image_file(bad_header)
        self.assertFalse(valid)

        # 4. desktop.ini / hidden / system files
        system_file = self.test_dir / "desktop.ini"
        system_file.write_text("[.ShellClassInfo]\nIconResource=...", encoding="utf-8")
        valid, err, _, _ = validate_image_file(system_file)
        self.assertFalse(valid)
        self.assertTrue(any(term in err.lower() for term in ["blocked", "disallowed", "system"]))
        print(f"[PASS] Image Security: 0-byte, fake, corrupted, and system files rejected")

    # =========================================================================
    # 4. REST API Endpoints
    # =========================================================================
    def test_08_api_config(self):
        """Test GET /api/config returns active configuration."""
        resp = self.client.get('/api/config')
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(data['server']['port'], 5678)
        self.assertEqual(data['server']['host'], "127.0.0.1")
        self.assertEqual(data['inference']['model_name'], "RealESRGAN_x4plus")
        print(f"[PASS] API: GET /api/config returned valid JSON")

    def test_09_api_storage_info(self):
        """Test GET /api/storage-info returns valid directory statistics."""
        resp = self.client.get('/api/storage-info')
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIn('uploads', data)
        self.assertIn('output', data)
        self.assertIn('weights', data)
        self.assertIn('logs', data)
        print(f"[PASS] API: GET /api/storage-info reported uploads: {data['uploads']['count']} files, output: {data['output']['count']} files")

    def test_10_api_directory_security(self):
        """Test POST /api/set-output-dir verifies directory safety and rejects system folders."""
        # 1. Valid custom directory
        target_dir = self.test_dir / "custom_output"
        resp = self.client.post('/api/set-output-dir', json={'path': str(target_dir)})
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIn('output_dir', data)

        # 2. Blocked system directory
        resp_sys = self.client.post('/api/set-output-dir', json={'path': "C:\\Windows\\System32"})
        self.assertEqual(resp_sys.status_code, 403)
        print(f"[PASS] API: POST /api/set-output-dir successfully enforced 403 Forbidden on system paths")

    # =========================================================================
    # 5. Live Inference on NVIDIA RTX 2050 (4 GB VRAM)
    # =========================================================================
    def test_11_real_gpu_inference(self):
        """Test real Real-ESRGAN inference on GPU 0 with RealESRGAN_x4plus."""
        test_in = self.test_dir / "logo_test_in.png"
        sample_src = PROJECT_ROOT / "assets" / "realesrgan_logo.png"
        if not sample_src.exists():
            self.skipTest("Sample source asset realesrgan_logo.png not present")
        
        # Read and create a 64x64 sub-crop
        with Image.open(sample_src) as src_img:
            crop_img = src_img.crop((0, 0, 64, 64))
            crop_img.save(test_in, format="PNG")

        orig_stat = test_in.stat()
        orig_size = orig_stat.st_size
        orig_mtime = orig_stat.st_mtime

        out_test_dir = self.test_dir / "out"
        out_test_dir.mkdir(parents=True, exist_ok=True)

        import subprocess
        cmd = [
            PYTHON_EXE,
            str(INFERENCE_SCRIPT),
            "-i", str(test_in),
            "-o", str(out_test_dir),
            "-n", "RealESRGAN_x4plus",
            "--model_path", str(MODEL_PATH),
            "-s", "4",
            "-g", "0",
            "-t", "128",
            "--tile_pad", "20",
            "--suffix", "x4_standardized",
            "--ext", "auto"
        ]

        t0 = time.time()
        proc = subprocess.run(cmd, cwd=str(BASE_DIR), capture_output=True, text=True)
        elapsed = time.time() - t0

        self.assertEqual(proc.returncode, 0, f"Inference failed! Stderr: {proc.stderr}")

        expected_out = out_test_dir / "logo_test_in_x4_standardized.png"
        self.assertTrue(expected_out.exists(), f"Output file was not created at {expected_out}")

        # Check dimension: 64x64 must be upscale 4x to 256x256
        with Image.open(expected_out) as out_img:
            out_w, out_h = out_img.size
            self.assertEqual((out_w, out_h), (256, 256), f"Upscale size expected (256, 256), got ({out_w}, {out_h})")

        # Verify original file untouched
        after_stat = test_in.stat()
        self.assertEqual(after_stat.st_size, orig_size, "Original input file size changed!")
        self.assertEqual(after_stat.st_mtime, orig_mtime, "Original input file mtime changed!")

        print(f"[PASS] Inference: Upscaled 64x64 -> 256x256 in {elapsed:.2f}s on RTX 2050 (tile=128, tile_pad=20)")

    # =========================================================================
    # 6. Batch Processing & Error Isolation Simulation
    # =========================================================================
    def test_12_batch_simulation_and_recovery(self):
        """Verify batch queue isolates errors: valid job succeeds, corrupt job fails gracefully."""
        import uuid

        # Job 1: Valid image
        good_img_path = self.test_dir / "batch_good.png"
        img = Image.new('RGB', (32, 32), color='green')
        img.save(good_img_path, format="PNG")

        # Job 2: Corrupt image file
        bad_img_path = self.test_dir / "batch_corrupt.png"
        bad_img_path.write_bytes(b"corrupted_png_header_and_data")

        job_id_good = str(uuid.uuid4())
        job_id_bad = str(uuid.uuid4())

        with jobs_lock:
            jobs[job_id_good] = {
                'id': job_id_good,
                'filename': "batch_good.png",
                'input_path': str(good_img_path),
                'output_path': None,
                'width': 32,
                'height': 32,
                'dimensions': "32x32",
                'file_size': good_img_path.stat().st_size,
                'file_size_human': "1 KB",
                'status': 'pending',
                'error': None,
                'tile_size': 128
            }
            jobs[job_id_bad] = {
                'id': job_id_bad,
                'filename': "batch_corrupt.png",
                'input_path': str(bad_img_path),
                'output_path': None,
                'width': 32,
                'height': 32,
                'dimensions': "32x32",
                'file_size': 28,
                'file_size_human': "28 B",
                'status': 'pending',
                'error': None,
                'tile_size': 128
            }

        out_batch_dir = self.test_dir / "batch_out"
        out_batch_dir.mkdir(parents=True, exist_ok=True)

        resp = self.client.post('/api/process', json={
            'job_ids': [job_id_good, job_id_bad],
            'tile_size': 128,
            'output_dir': str(out_batch_dir)
        })
        self.assertEqual(resp.status_code, 200)

        # Wait for both jobs to finish processing (polling with timeout 30s)
        t_start = time.time()
        while time.time() - t_start < 30:
            with jobs_lock:
                status_good = jobs[job_id_good]['status']
                status_bad = jobs[job_id_bad]['status']
            if status_good in ('completed', 'error') and status_bad in ('completed', 'error'):
                break
            time.sleep(0.5)

        with jobs_lock:
            final_good = jobs[job_id_good]
            final_bad = jobs[job_id_bad]

        self.assertEqual(final_good['status'], 'completed', f"Good job failed: {final_good.get('error')}")
        self.assertIsNotNone(final_good['output_path'])
        self.assertTrue(Path(final_good['output_path']).exists())

        self.assertEqual(final_bad['status'], 'error', "Corrupt job should have error status!")
        self.assertIsNotNone(final_bad['error'])

        # Test retry endpoint on failed job
        retry_resp = self.client.post('/api/retry', json={'job_ids': [job_id_bad], 'tile_size': 96})
        self.assertEqual(retry_resp.status_code, 200)

        print(f"[PASS] Batch Processing: Valid job succeeded, corrupt job gracefully isolated to 'error' status, retry queued")

if __name__ == "__main__":
    unittest.main(verbosity=2)
