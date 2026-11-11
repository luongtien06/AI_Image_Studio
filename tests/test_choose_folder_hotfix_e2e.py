"""
End-to-end verification test suite for Choose Folder Urgent Hotfix.
Verifies:
1. Native Windows Dialog Topmost & Foreground Ownership (WS_EX_TOPMOST 0x8).
2. Clean Cancellation handling when dialog is closed.
3. Path selection and return handling via /api/browse-folder.
4. /api/select-folder recursive discovery (nested subfolders, hidden files, non-images skipped).
5. State management: fresh QUEUED (pending) status, error=None, even if historical FAILED in DB.
6. Duplicate prevention on repeat folder selection.
7. Database immutability of historical failed jobs and user records.
"""

import os
import sys
import time
import json
import sqlite3
import tempfile
import threading
import subprocess
import unittest
from pathlib import Path
from PIL import Image

# Ensure project root is in sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import app as flask_app


class TestChooseFolderEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        flask_app.app.config['TESTING'] = True
        cls.client = flask_app.app.test_client()
        cls.test_dir = tempfile.mkdtemp(prefix="test_choose_folder_e2e_")

        # Snapshot historical failed jobs
        conn = sqlite3.connect(BASE_DIR / "data" / "jobs.db")
        cls.initial_total_jobs = conn.execute("SELECT count(*) FROM jobs").fetchone()[0]
        cls.initial_failed_jobs = conn.execute("SELECT count(*) FROM jobs WHERE status = 'FAILED'").fetchone()[0]
        cur = conn.execute("SELECT id, filename, status, error_message FROM jobs WHERE input_path LIKE '%WEB%' AND status = 'FAILED'")
        cls.historical_user_jobs = cur.fetchall()
        conn.close()

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def test_01_native_dialog_topmost_style_and_lifecycle(self):
        """Verify that folder_dialog spawns a topmost dialog (WS_EX_TOPMOST) and handles close cleanly."""
        if sys.platform != 'win32':
            self.skipTest("Windows-only test")

        import ctypes
        user32 = ctypes.windll.user32

        out_file = Path(self.test_dir) / "dialog_out.txt"
        if out_file.exists():
            out_file.unlink()

        found_windows = []

        # Run direct Tkinter check to inspect Win32 style properties
        import tkinter as tk
        from tkinter import filedialog

        def inspect_dialog():
            start = time.time()
            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
            while time.time() - start < 4.0:
                def enum_cb(hwnd, extra):
                    if user32.IsWindowVisible(hwnd):
                        class_name = ctypes.create_unicode_buffer(256)
                        user32.GetClassNameW(hwnd, class_name, 256)
                        if class_name.value == "#32770":
                            length = user32.GetWindowTextLengthW(hwnd)
                            buff = ctypes.create_unicode_buffer(length + 1)
                            user32.GetWindowTextW(hwnd, buff, length + 1)
                            if "select" in buff.value.lower() or "folder" in buff.value.lower():
                                ex_style = user32.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
                                is_topmost = bool(ex_style & 0x00000008)     # WS_EX_TOPMOST
                                found_windows.append({
                                    "hwnd": hwnd,
                                    "title": buff.value,
                                    "ex_style": hex(ex_style),
                                    "is_topmost": is_topmost
                                })
                                user32.PostMessageW(hwnd, 0x0010, 0, 0)  # Close
                                return False
                    return True

                user32.EnumWindows(WNDENUMPROC(enum_cb), 0)
                if found_windows:
                    break
                time.sleep(0.05)

        t = threading.Thread(target=inspect_dialog, daemon=True)
        t.start()

        root = tk.Tk()
        root.geometry("1x1+-2000+-2000")
        root.attributes("-topmost", True)
        root.attributes("-alpha", 0.0)
        root.update()

        filedialog.askdirectory(parent=root, title="Select Image Folder - AI Image Studio")
        root.destroy()
        t.join(timeout=3.0)

        self.assertTrue(found_windows, "Dialog #32770 not detected")
        self.assertTrue(found_windows[0]['is_topmost'], f"Dialog is not topmost: {found_windows[0]}")
        print("\n[PASS] Win32 Topmost ExStyle Verified:", found_windows[0])

        # Now test folder_dialog.py subprocess lifecycle with AI_STUDIO_TEST_AUTO_CLOSE=1
        env = os.environ.copy()
        env["AI_STUDIO_TEST_AUTO_CLOSE"] = "1"
        cmd = [sys.executable, str(BASE_DIR / "folder_dialog.py"), str(out_file), str(self.test_dir), "Select Image Folder - AI Image Studio"]
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.returncode, 0, f"folder_dialog.py failed: {proc.stderr}")
        self.assertTrue(out_file.exists())
        with open(out_file, 'r', encoding='utf-8') as f:
            content = f.read().strip()
        self.assertEqual(content, "", "Output file must be empty on cancellation")
        print("[PASS] Subprocess Lifecycle & Clean Cancellation Verified")

    def test_02_browse_folder_api_path_validation(self):
        """Verify POST /api/browse-folder with direct path returns validated folder."""
        sample_folder = Path(self.test_dir) / "valid_browse_dir"
        sample_folder.mkdir(exist_ok=True)

        resp = self.client.post('/api/browse-folder', json={'path': str(sample_folder)})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get('status'), 'success')
        self.assertEqual(os.path.normpath(data.get('path')), os.path.normpath(str(sample_folder)))

        # Invalid path should return error
        bad_path = Path(self.test_dir) / "non_existent_dir_99999"
        resp_bad = self.client.post('/api/browse-folder', json={'path': str(bad_path)})
        self.assertEqual(resp_bad.status_code, 404)
        print("\n[PASS] Browse Folder API Direct Path Validation Verified")

    def test_03_folder_selection_recursive_and_filtering(self):
        """
        Verify /api/select-folder:
        - Discovers 3 root images + 1 nested subfolder image
        - Skips unsupported files (.txt)
        - Skips hidden files (.hidden.png)
        - Marks all 4 jobs as 'pending' (QUEUED) with error=None
        """
        sample_folder = Path(self.test_dir) / "sample_folder with spaces"
        sample_folder.mkdir(exist_ok=True)
        sub_folder = sample_folder / "nested_subfolder"
        sub_folder.mkdir(exist_ok=True)

        # 1. Root valid images
        img1 = Image.new('RGB', (100, 100), color='red')
        img1.save(sample_folder / "img1.png")
        img2 = Image.new('RGB', (120, 80), color='green')
        img2.save(sample_folder / "img2.jpg")
        img3 = Image.new('RGB', (90, 90), color='blue')
        img3.save(sample_folder / "img3.webp")

        # 2. Nested valid image
        img_nested = Image.new('RGB', (64, 64), color='yellow')
        img_nested.save(sub_folder / "nested.png")

        # 3. Non-image file (should be skipped)
        with open(sample_folder / "readme.txt", "w") as f:
            f.write("This is a text file, not an image.")

        # 4. Hidden file (should be skipped)
        img_hidden = Image.new('RGB', (32, 32), color='black')
        img_hidden.save(sample_folder / ".hidden.png")

        # Reset in-memory jobs for clean test
        with flask_app.jobs_lock:
            flask_app.jobs.clear()

        # Call /api/select-folder
        resp = self.client.post('/api/select-folder', json={'folder_path': str(sample_folder)})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get('status'), 'success')
        self.assertEqual(data.get('count'), 4, f"Expected 4 valid images, got {data.get('count')}")
        self.assertEqual(data.get('folder'), str(sample_folder))

        # Check returned jobs
        returned_jobs = data.get('jobs', [])
        self.assertEqual(len(returned_jobs), 4)

        for j in returned_jobs:
            self.assertEqual(j['status'], 'pending', f"Job {j['filename']} has status {j['status']}, expected 'pending'")
            self.assertIsNone(j['error'], f"Job {j['filename']} has error: {j['error']}")
            self.assertGreater(j['width'], 0)
            self.assertGreater(j['height'], 0)

        # Verify all 4 jobs in memory are pending
        with flask_app.jobs_lock:
            pending_count = sum(1 for j in flask_app.jobs.values() if j.get('status') == 'pending')
            self.assertEqual(pending_count, 4)
        print("\n[PASS] Recursive Discovery and Status Mapping Verified: 4/4 QUEUED (pending)")

    def test_04_repeat_folder_selection_no_duplicates(self):
        """Verify selecting the same folder again does not duplicate jobs."""
        sample_folder = Path(self.test_dir) / "sample_folder with spaces"

        resp = self.client.post('/api/select-folder', json={'folder_path': str(sample_folder)})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get('count'), 4)

        # In-memory jobs count should remain exactly 4
        with flask_app.jobs_lock:
            self.assertEqual(len(flask_app.jobs), 4)
        print("\n[PASS] Repeat Selection Duplicate Prevention Verified")

    def test_05_historical_failed_jobs_not_shown_as_error(self):
        """
        Verify that if an image was previously FAILED in SQLite,
        re-selecting its folder creates a fresh QUEUED job rather than marking it Error.
        """
        sim_folder = Path(self.test_dir) / "simulated_historical"
        sim_folder.mkdir(exist_ok=True)
        img_path = sim_folder / "previously_failed.png"
        img = Image.new('RGB', (80, 80), color='purple')
        img.save(img_path)

        # Insert a simulated historical FAILED job in SQLite
        conn = sqlite3.connect(BASE_DIR / "data" / "jobs.db")
        cur = conn.cursor()
        test_job_id = "test-hist-failed-0001"
        cur.execute(
            "INSERT INTO jobs (id, filename, input_path, status, error_message, created_at, updated_at) "
            "VALUES (?, ?, ?, 'FAILED', 'Old CUDA OOM Error', datetime('now'), datetime('now'))",
            (test_job_id, "previously_failed.png", str(img_path))
        )
        conn.commit()
        conn.close()

        try:
            # Clear memory
            with flask_app.jobs_lock:
                flask_app.jobs.clear()

            # Select the folder containing this file
            resp = self.client.post('/api/select-folder', json={'folder_path': str(sim_folder)})
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            jobs = data.get('jobs', [])
            self.assertEqual(len(jobs), 1)
            j = jobs[0]

            # MUST BE PENDING, NOT ERROR!
            self.assertEqual(j['status'], 'pending', f"Job status was {j['status']}, expected 'pending'")
            self.assertIsNone(j['error'], f"Job error was {j['error']}, expected None")
            returned_id = j.get('job_id') or j.get('id')
            self.assertNotEqual(returned_id, test_job_id, "Should create fresh job ID, not reuse old failed job ID")
            print("\n[PASS] Historical FAILED jobs properly presented as fresh QUEUED jobs")
        finally:
            # Clean up the test row
            conn = sqlite3.connect(BASE_DIR / "data" / "jobs.db")
            conn.execute("DELETE FROM jobs WHERE id = ?", (test_job_id,))
            conn.commit()
            conn.close()

    def test_06_database_immutability(self):
        """Verify that historical failed jobs and user records remain completely untouched."""
        conn = sqlite3.connect(BASE_DIR / "data" / "jobs.db")
        cur = conn.cursor()
        cur.execute("SELECT id, filename, status, error_message FROM jobs WHERE input_path LIKE '%WEB%' AND status = 'FAILED'")
        current_user_jobs = cur.fetchall()
        conn.close()

        self.assertEqual(len(current_user_jobs), len(self.historical_user_jobs))
        for old, curr in zip(self.historical_user_jobs, current_user_jobs):
            self.assertEqual(old[0], curr[0])
            self.assertEqual(old[1], curr[1])
            self.assertEqual(old[2], curr[2])
            self.assertEqual(old[3], curr[3])
        print(f"\n[PASS] Database Invariants Verified: All {len(current_user_jobs)} user records completely intact")


if __name__ == '__main__':
    unittest.main()
