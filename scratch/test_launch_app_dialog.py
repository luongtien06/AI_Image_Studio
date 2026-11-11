import os
import sys
import ctypes
from pathlib import Path

BASE_DIR = Path("E:/AI Image Studio/RealESRGAN-v030")
sys.path.insert(0, str(BASE_DIR))

# Set both python os.environ and Win32 process environment
os.environ["AI_STUDIO_TEST_AUTO_CLOSE"] = "1"
if sys.platform == 'win32':
    ctypes.windll.kernel32.SetEnvironmentVariableW("AI_STUDIO_TEST_AUTO_CLOSE", "1")

import app

print("Testing launch_native_folder_dialog with AUTO_CLOSE=1...", flush=True)
result = app.launch_native_folder_dialog(initial_dir=str(BASE_DIR), timeout=10)
print("Result:", result, flush=True)
assert result.get('status') == 'cancelled', f"Expected cancelled, got {result}"
print("[PASS] Full launch_native_folder_dialog subprocess lifecycle verified!", flush=True)
