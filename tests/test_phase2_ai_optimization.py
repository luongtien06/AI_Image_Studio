# -*- coding: utf-8 -*-
"""
Phase 2 Test & Verification Suite: AI Engine Performance Optimization
Covers Groups A, B, C, D, E, F:
- Group A: Persistent Worker Lifecycle & In-Memory Loading
- Group B: Auto-Tile Intelligence & CUDA OOM Recovery
- Group C: GPU & VRAM Performance Monitor
- Group D: Quick Preview Real AI Patch Inference
- Group E: Multi-Format Export (PNG, JPG, WEBP, RGBA white compositing) & Collision-Free Naming
- Group F: Regression & Pre-flight Baseline Integrity Verification
"""

import gc
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
from PIL import Image
import torch

from ai_worker import (
    AIWorker,
    STATE_BUSY,
    STATE_ERROR,
    STATE_READY,
    STATE_RECOVERING,
    STATE_STOPPED,
    get_ai_worker,
    get_unique_output_path,
    read_image_unicode,
)
from app import app, safe_path, validate_image_file
from gpu_monitor import get_gpu_info

BASE_DIR = Path(__file__).resolve().parent.parent


class TestGroupA_PersistentWorker(unittest.TestCase):
    """Group A: Persistent Worker Lifecycle & In-Memory Operations."""

    def setUp(self):
        self.worker = get_ai_worker(base_dir=BASE_DIR)

    def test_a1_worker_singleton_pattern(self):
        worker2 = get_ai_worker(base_dir=BASE_DIR)
        self.assertIs(self.worker, worker2, "get_ai_worker must return singleton instance")

    def test_a2_model_initialization_in_memory(self):
        ok = self.worker.initialize_model()
        self.assertTrue(ok, "Worker must initialize RealESRGANer model in memory")
        self.assertTrue(self.worker.model_loaded, "model_loaded flag must be True")
        self.assertEqual(self.worker.state, STATE_READY, "Worker state must be READY after init")
        self.assertIsNotNone(self.worker.upsampler, "upsampler must not be None")
        self.assertEqual(self.worker.model_name, "RealESRGAN_x4plus", "Must be RealESRGAN_x4plus")

    def test_a3_model_stays_in_memory_across_multiple_calls(self):
        self.worker.initialize_model()
        first_upsampler_id = id(self.worker.upsampler)

        # Create tiny test image
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            in_file = tdp / "tiny.png"
            out_dir = tdp / "out"
            Image.new("RGB", (32, 32), (100, 150, 200)).save(in_file)

            res1 = self.worker.process_image(in_file, out_dir, tile_mode="manual", requested_tile=64)
            self.assertTrue(res1["success"])
            self.assertEqual(id(self.worker.upsampler), first_upsampler_id, "Upsampler object must NOT be reloaded")

            res2 = self.worker.process_image(in_file, out_dir, tile_mode="manual", requested_tile=64)
            self.assertTrue(res2["success"])
            self.assertEqual(id(self.worker.upsampler), first_upsampler_id, "Upsampler must persist across jobs")

    def test_a4_worker_stop_and_cleanup(self):
        self.worker.initialize_model()
        self.assertEqual(self.worker.state, STATE_READY)
        self.worker.stop()
        self.assertEqual(self.worker.state, STATE_STOPPED)
        self.assertFalse(self.worker.model_loaded)
        self.assertIsNone(self.worker.upsampler)

        # Re-initialize for subsequent tests
        self.worker.initialize_model()
        self.assertEqual(self.worker.state, STATE_READY)


class TestGroupB_AutoTileAndOOM(unittest.TestCase):
    """Group B: Auto-Tile Intelligence & CUDA OOM Recovery."""

    def setUp(self):
        self.worker = get_ai_worker(base_dir=BASE_DIR)
        self.worker.initialize_model()

    def test_b1_auto_tile_selection_logic(self):
        # Small image (300x300 = 90k pixels)
        tile_small = self.worker.choose_tile(200, 200, requested_mode="auto")
        self.assertIn(tile_small, [192, 256], "Small image should get high tile (192 or 256)")

        # Medium image (1000x1000 = 1M pixels)
        tile_med = self.worker.choose_tile(1000, 1000, requested_mode="auto")
        self.assertIn(tile_med, [128, 192, 256], "Medium image should get standard or high tile")

        # Huge image (3000x3000 = 9M pixels)
        tile_huge = self.worker.choose_tile(3000, 3000, requested_mode="auto")
        self.assertIn(tile_huge, [64, 96, 128], "Huge image should get conservative tile to avoid OOM")

    def test_b2_manual_tile_override(self):
        tile_override = self.worker.choose_tile(500, 500, requested_tile=64, requested_mode="manual")
        self.assertEqual(tile_override, 64, "Manual tile mode must honor requested tile size")

        tile_override_0 = self.worker.choose_tile(100, 100, requested_tile=0, requested_mode="manual")
        self.assertEqual(tile_override_0, 0, "Tile 0 (no tile) must be accepted when requested")

    def test_b3_step_down_sequence(self):
        self.assertEqual(self.worker.get_step_down_tile(256), 192)
        self.assertEqual(self.worker.get_step_down_tile(192), 128)
        self.assertEqual(self.worker.get_step_down_tile(128), 96)
        self.assertEqual(self.worker.get_step_down_tile(96), 64)
        self.assertEqual(self.worker.get_step_down_tile(64), 32)
        self.assertIsNone(self.worker.get_step_down_tile(32), "Step-down from 32 must return None (floor reached)")

    def test_b4_simulated_oom_recovery(self):
        """Simulate an OutOfMemoryError on first attempt, verify auto step-down and success."""
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            in_file = tdp / "oom_test.png"
            out_dir = tdp / "out"
            Image.new("RGB", (64, 64), (50, 100, 150)).save(in_file)

            real_enhance = self.worker.upsampler.enhance
            calls = {"count": 0}

            def mock_enhance(img, outscale=4):
                calls["count"] += 1
                if calls["count"] == 1:
                    # Raise CUDA OOM on first call
                    raise torch.cuda.OutOfMemoryError("CUDA out of memory. Tried to allocate 2.00 GiB")
                return real_enhance(img, outscale=outscale)

            with patch.object(self.worker.upsampler, "enhance", side_effect=mock_enhance):
                res = self.worker.process_image(
                    in_file,
                    out_dir,
                    tile_mode="manual",
                    requested_tile=256
                )

            self.assertTrue(res["success"], "Job must succeed after OOM recovery")
            self.assertTrue(res["oom_recovered"], "oom_recovered flag must be True")
            self.assertEqual(res["tile_used"], 192, "Tile must have stepped down from 256 to 192")
            self.assertEqual(calls["count"], 2, "Enhance should have been called twice (1 failure + 1 retry)")
            self.assertEqual(self.worker.state, STATE_READY, "Worker state must return to READY")


class TestGroupC_GPUMonitor(unittest.TestCase):
    """Group C: GPU & VRAM Monitoring."""

    def test_c1_get_gpu_info_structure(self):
        info = get_gpu_info()
        self.assertIsInstance(info, dict)
        self.assertIn("cuda_available", info)
        self.assertIn("gpu_name", info)
        self.assertIn("total_gb", info)
        self.assertIn("used_gb", info)
        self.assertIn("free_gb", info)
        self.assertIn("used_pct", info)

        if torch.cuda.is_available():
            self.assertTrue(info["cuda_available"])
            self.assertIn("RTX 2050", info["gpu_name"])
            self.assertGreater(info["total_gb"], 3.5)
            self.assertGreater(info["free_gb"], 0.5)

    def test_c2_api_gpu_stats_endpoint(self):
        client = app.test_client()
        resp = client.get("/api/gpu-stats")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("state", data)
        self.assertIn("model_name", data)
        self.assertEqual(data["model_name"], "RealESRGAN_x4plus")
        self.assertIn("gpu", data)
        self.assertIn("total_inference_time", data)


class TestGroupD_QuickPreview(unittest.TestCase):
    """Group D: Quick Preview Real AI Patch Inference."""

    def setUp(self):
        self.worker = get_ai_worker(base_dir=BASE_DIR)
        self.worker.initialize_model()

    def test_d1_quick_preview_patch_dimensions(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            in_file = tdp / "prev_test.png"
            thumbs_dir = tdp / "thumbs"
            # 512x512 image -> 256x256 center patch -> 1024x1024 enhanced
            Image.new("RGB", (512, 512), (120, 200, 80)).save(in_file)

            prev = self.worker.generate_quick_preview(
                input_path=in_file,
                thumbnails_dir=thumbs_dir,
                patch_size=256,
                format_type="webp",
                quality=90
            )

            self.assertTrue(prev["success"])
            self.assertEqual(prev["crop_box"]["w"], 256)
            self.assertEqual(prev["crop_box"]["h"], 256)
            self.assertEqual(prev["crop_box"]["x"], 128)
            self.assertEqual(prev["crop_box"]["y"], 128)
            self.assertEqual(prev["enhanced_dimensions"], "1024x1024")
            self.assertGreater(prev["elapsed_seconds"], 0.0)

            # Check that preview files actually exist on disk
            orig_name = Path(prev["orig_url"]).name
            enh_name = Path(prev["enhanced_url"]).name
            self.assertTrue((thumbs_dir / orig_name).is_file())
            self.assertTrue((thumbs_dir / enh_name).is_file())

    def test_d2_quick_preview_small_image_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            in_file = tdp / "small.png"
            thumbs_dir = tdp / "thumbs"
            # Image is smaller than patch size (100x80)
            Image.new("RGB", (100, 80), (30, 40, 50)).save(in_file)

            prev = self.worker.generate_quick_preview(
                input_path=in_file,
                thumbnails_dir=thumbs_dir,
                patch_size=256
            )
            self.assertTrue(prev["success"])
            self.assertEqual(prev["crop_box"]["w"], 100)
            self.assertEqual(prev["crop_box"]["h"], 80)
            self.assertEqual(prev["enhanced_dimensions"], "400x320")


class TestGroupE_ExportFormats(unittest.TestCase):
    """Group E: Multi-Format Export (PNG, JPG, WEBP, RGBA white-compositing)."""

    def setUp(self):
        self.worker = get_ai_worker(base_dir=BASE_DIR)
        self.worker.initialize_model()

    def test_e1_png_export(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            in_file = tdp / "test.png"
            out_dir = tdp / "out"
            Image.new("RGB", (40, 40), (255, 128, 0)).save(in_file)

            res = self.worker.process_image(in_file, out_dir, export_format="png")
            self.assertTrue(res["success"])
            self.assertTrue(res["output_path"].endswith(".png"))
            with Image.open(res["output_path"]) as img:
                self.assertEqual(img.format, "PNG")
                self.assertEqual(img.size, (160, 160))

    def test_e2_webp_export(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            in_file = tdp / "test.png"
            out_dir = tdp / "out"
            Image.new("RGB", (40, 40), (0, 128, 255)).save(in_file)

            res = self.worker.process_image(in_file, out_dir, export_format="webp", quality=85)
            self.assertTrue(res["success"])
            self.assertTrue(res["output_path"].endswith(".webp"))
            with Image.open(res["output_path"]) as img:
                self.assertEqual(img.format, "WEBP")
                self.assertEqual(img.size, (160, 160))

    def test_e3_jpg_rgba_white_compositing(self):
        """RGBA image exported to JPEG must composite onto solid white background without error."""
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            in_file = tdp / "alpha.png"
            out_dir = tdp / "out"
            # Red box with semi-transparent alpha (alpha = 128)
            Image.new("RGBA", (40, 40), (255, 0, 0, 128)).save(in_file)

            res = self.worker.process_image(in_file, out_dir, export_format="jpg", quality=90)
            self.assertTrue(res["success"])
            self.assertTrue(res["output_path"].endswith(".jpg"))

            with Image.open(res["output_path"]) as img:
                self.assertEqual(img.format, "JPEG")
                self.assertEqual(img.mode, "RGB")
                self.assertEqual(img.size, (160, 160))

    def test_e4_collision_free_naming(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            p1 = get_unique_output_path(tdp, "sample", ".png")
            self.assertEqual(p1.name, "sample_4x.png")
            p1.touch()

            p2 = get_unique_output_path(tdp, "sample", ".png")
            self.assertEqual(p2.name, "sample_4x_1.png")
            p2.touch()

            p3 = get_unique_output_path(tdp, "sample", ".png")
            self.assertEqual(p3.name, "sample_4x_2.png")


class TestGroupF_RegressionAndIntegrity(unittest.TestCase):
    """Group F: Regression & Pre-flight Baseline Integrity Verification."""

    def test_f1_baseline_hashes_and_files_intact(self):
        baseline_file = BASE_DIR / "tests" / "preflight_baseline.json"
        self.assertTrue(baseline_file.is_file(), "preflight_baseline.json must exist")

        with open(baseline_file, "r", encoding="utf-8") as f:
            baseline = json.load(f)

        import hashlib
        def sha256_of(path):
            h = hashlib.sha256()
            with open(path, "rb") as f:
                while chunk := f.read(65536):
                    h.update(chunk)
            return h.hexdigest().upper()

        # Check weights hash
        weights_p = BASE_DIR / "weights" / "RealESRGAN_x4plus.pth"
        self.assertTrue(weights_p.is_file())
        self.assertEqual(sha256_of(weights_p), baseline["weights"]["hash"])

        # Check all baseline outputs in E:\AI\ImageStudio\output if present
        imgstudio_out = Path("E:/AI/ImageStudio/output")
        for name, meta in baseline.get("imagestudio_outputs", {}).items():
            out_p = imgstudio_out / name
            if out_p.is_file():
                self.assertEqual(sha256_of(out_p), meta["hash"], f"Corrupted output file: {out_p}")

    def test_f2_max_pixel_limit_still_enforced(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            huge_img = tdp / "huge.png"
            # 6000 x 5000 = 30M pixels > 25M limit
            img = Image.new("RGB", (6000, 5000), (255, 255, 255))
            img.save(huge_img)

            valid, reason, _, _ = validate_image_file(huge_img)
            self.assertFalse(valid)
            self.assertIn("maximum pixel limit", reason)

    def test_f3_history_persistence_and_rebasing(self):
        from app import load_history
        hist = load_history(resolve_paths=True)
        self.assertIsInstance(hist, list)
        self.assertGreater(len(hist), 0, "History entries must be preserved")


if __name__ == "__main__":
    unittest.main(verbosity=2)
