import hashlib
import os
import shutil
from pathlib import Path
from datetime import datetime

root = Path("E:/AI Image Studio/RealESRGAN-v030")
ts = datetime.now().strftime("%Y%m%d_%H%M%S")
backup_dir = root / "backups" / f"choose_folder_hotfix_{ts}"
backup_dir.mkdir(parents=True, exist_ok=True)

files = ["folder_dialog.py", "app.py"]
for f in files:
    src = root / f
    dst = backup_dir / f
    shutil.copy2(src, dst)
    sha = hashlib.sha256(src.read_bytes()).hexdigest()
    print(f"BACKUP: {f} -> {dst}")
    print(f"SHA-256: {sha}")
