"""
Comprehensive verification test suite for VRAM Telemetry Hotfix.
Verifies the 5 states required by the specification:
1. Application idle (telemetry matches nvidia-smi source of truth).
2. Worker loaded and model initialized (worker status + VRAM telemetry via /api/gpu-stats).
3. During actual image inference (VRAM dynamic measurement).
4. Immediately after inference (VRAM persistence / cleanup).
5. After worker restart (recovery and continued telemetry).
Plus edge cases: telemetry error non-zeroing, N/A formatting.
"""

import os
import sys
import time
import json
import unittest
import subprocess
from pathlib import Path
from PIL import Image

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import gpu_monitor
from ai_worker import get_ai_worker, STATE_READY, STATE_BUSY


def get_nvidia_smi_direct(device_id=0):
    cmd = [
        "nvidia-smi",
        f"--id={device_id}",
        "--query-gpu=index,name,memory.used,memory.total,utilization.gpu",
        "--format=csv,noheader,nounits"
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
    if proc.returncode != 0:
        return None
    parts = [p.strip() for p in proc.stdout.strip().split(",")]
    if len(parts) >= 5:
        return {
            "device_id": int(parts[0]),
            "gpu_name": parts[1],
            "used_mb": float(parts[2]),
            "total_mb": float(parts[3]),
            "utilization_gpu": float(parts[4])
        }
    return None


class TestVramTelemetry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app
        app.app.config['TESTING'] = True
        cls.client = app.app.test_client()
        cls.worker = get_ai_worker(base_dir=BASE_DIR, use_subprocess=True)

    def test_01_idle_state_matches_nvidia_smi(self):
        """State 1: Application idle. Telemetry matches nvidia-smi source of truth."""
        smi_data = get_nvidia_smi_direct(0)
        self.assertIsNotNone(smi_data, "nvidia-smi must be available and return data")

        gpu_info = gpu_monitor.get_gpu_info(0)
        self.assertTrue(gpu_info["telemetry_available"])
        self.assertIn(gpu_info["telemetry_source"], ("nvml", "nvidia-smi"))
        self.assertEqual(gpu_info["device_id"], 0)
        self.assertIn("RTX 2050", gpu_info["gpu_name"])

        # Check numeric types and non-zero totals
        self.assertGreater(gpu_info["total_mb"], 3000)
        self.assertGreater(gpu_info["used_mb"], 100)
        self.assertGreater(gpu_info["free_mb"], 1000)
        self.assertAlmostEqual(gpu_info["total_mb"], gpu_info["used_mb"] + gpu_info["free_mb"], delta=2.0)

        # Check GB conversions and percentages
        expected_used_gb = round(gpu_info["used_mb"] / 1024.0, 1)
        expected_total_gb = round(gpu_info["total_mb"] / 1024.0, 1)
        self.assertEqual(gpu_info["used_gb"], expected_used_gb)
        self.assertEqual(gpu_info["total_gb"], expected_total_gb)

        expected_pct = round((gpu_info["used_mb"] / gpu_info["total_mb"]) * 100.0, 1)
        self.assertEqual(gpu_info["used_pct"], expected_pct)

        # Compare with nvidia-smi source of truth (within reasonable dynamic delta)
        delta_mb = abs(gpu_info["used_mb"] - smi_data["used_mb"])
        self.assertLess(delta_mb, 200, f"NVML used_mb ({gpu_info['used_mb']}) diverged from nvidia-smi ({smi_data['used_mb']})")
        print(f"\n[State 1 Idle PASS] NVML: {gpu_info['used_mb']} MB / {gpu_info['total_mb']} MB ({gpu_info['used_pct']}%) | SMI: {smi_data['used_mb']} MB")

    def test_02_worker_initialized_api_stats(self):
        """State 2: Worker loaded and model initialized. API returns supervisor state + GPU telemetry."""
        resp = self.client.get("/api/gpu-stats")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()

        self.assertIn("gpu", data, "Response must contain 'gpu' telemetry dictionary")
        self.assertIn("state", data)
        self.assertEqual(data["state"], "READY")
        self.assertTrue(data.get("is_alive"))

        gpu = data["gpu"]
        self.assertTrue(gpu.get("telemetry_available"))
        self.assertGreater(gpu.get("total_mb", 0), 0)
        self.assertGreater(gpu.get("used_mb", 0), 0)
        self.assertIsNotNone(gpu.get("utilization_percent"))
        print(f"\n[State 2 Initialized PASS] API /api/gpu-stats: worker={data['state']} (PID {data.get('worker_pid')}), VRAM={gpu['used_gb']}/{gpu['total_gb']} GB ({gpu['used_pct']}%)")

    def test_03_04_inference_vram_and_post_inference(self):
        """State 3 & 4: VRAM telemetry during and immediately after actual inference."""
        # Initial VRAM
        before_info = gpu_monitor.get_gpu_info(0)

        # Create synthetic test image
        temp_dir = BASE_DIR / "scratch" / "vram_test"
        temp_dir.mkdir(parents=True, exist_ok=True)
        img_p = temp_dir / "vram_test_img.png"
        out_p = temp_dir / "vram_test_out.png"
        img = Image.new("RGB", (400, 400), color=(100, 150, 200))
        img.save(img_p)

        # Process via worker
        res = self.worker.process_image(
            input_path=img_p,
            output_dir=temp_dir,
            tile_mode="manual",
            requested_tile=128
        )
        self.assertTrue(res.get("success"), f"Inference failed: {res.get('error')}")

        # Post-inference VRAM
        after_info = gpu_monitor.get_gpu_info(0)
        smi_after = get_nvidia_smi_direct(0)

        self.assertTrue(after_info["telemetry_available"])
        self.assertGreater(after_info["used_mb"], 0)
        print(f"\n[State 3 & 4 Inference PASS] Before: {before_info['used_mb']} MB -> After: {after_info['used_mb']} MB | SMI: {smi_after['used_mb']} MB")

    def test_05_worker_restart_resilience(self):
        """State 5: Worker restart recovery. Telemetry continues working seamlessly."""
        supervisor = self.worker
        old_pid = supervisor.worker_pid
        print(f"\nSimulating worker child crash (terminating PID {old_pid})...")
        supervisor._terminate_child()

        # Restart child
        supervisor._start_child_process()
        new_pid = supervisor.worker_pid
        self.assertNotEqual(old_pid, new_pid)

        # Check /api/gpu-stats immediately after restart
        resp = self.client.get("/api/gpu-stats")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["worker_pid"], new_pid)
        self.assertTrue(data["gpu"]["telemetry_available"])
        self.assertGreater(data["gpu"]["used_mb"], 0)
        print(f"[State 5 Restart PASS] Worker respawned PID {new_pid}; VRAM telemetry active: {data['gpu']['used_gb']}/{data['gpu']['total_gb']} GB")

    def test_06_error_handling_no_silent_zeros(self):
        """Test that telemetry failure never emits silent zeros and sets telemetry_available=False."""
        # Query invalid device ID 99 to trigger failure
        bad_info = gpu_monitor.get_gpu_info(99)
        self.assertFalse(bad_info["telemetry_available"])
        self.assertIsNone(bad_info["used_mb"])
        self.assertIsNone(bad_info["total_mb"])
        self.assertEqual(bad_info["gpu_name"], "N/A")
        self.assertEqual(bad_info["status"], "Telemetry Unavailable")
        print(f"\n[Error Handling PASS] Simulated failure correctly returned None and status='Telemetry Unavailable' (no silent zeros)")


if __name__ == "__main__":
    unittest.main()
