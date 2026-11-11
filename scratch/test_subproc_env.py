import os
import sys
import subprocess
from pathlib import Path

BASE_DIR = Path("E:/AI Image Studio/RealESRGAN-v030")
script = BASE_DIR / "folder_dialog.py"
out_file = BASE_DIR / "scratch" / "test_env_out.txt"

env = os.environ.copy()
env["AI_STUDIO_TEST_AUTO_CLOSE"] = "1"

cmd = [sys.executable, str(script), str(out_file), str(BASE_DIR), "Select Image Folder - AI Image Studio"]
print("Running subprocess with AI_STUDIO_TEST_AUTO_CLOSE=1 in env...")
proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=8)
print("Subprocess returncode:", proc.returncode)
print("Stdout:", proc.stdout.strip())
print("Stderr:", proc.stderr.strip())
