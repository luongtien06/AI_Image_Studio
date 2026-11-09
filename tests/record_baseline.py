import hashlib
import json
from pathlib import Path

def hash_file(path):
    p = Path(path)
    if not p.is_file():
        return None
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest().upper()

w_path = Path('E:/AI/RealESRGAN-v030/weights/RealESRGAN_x4plus.pth')
im_hist = Path('E:/AI/ImageStudio/logs/history.json')
re_hist = Path('E:/AI/RealESRGAN-v030/logs/history.json')

baseline = {
    'weights': {
        'path': str(w_path),
        'size': w_path.stat().st_size,
        'hash': hash_file(w_path)
    },
    'imagestudio_history': {
        'path': str(im_hist),
        'size': im_hist.stat().st_size,
        'hash': hash_file(im_hist)
    },
    'realesrgan_history': {
        'path': str(re_hist),
        'size': re_hist.stat().st_size if re_hist.exists() else 0,
        'hash': hash_file(re_hist)
    },
    'imagestudio_outputs': {},
    'phase1_backups': {}
}

# ImageStudio outputs (14 files)
im_out = Path('E:/AI/ImageStudio/output')
for f in sorted(im_out.glob('*')):
    if f.is_file() and f.name != 'desktop.ini':
        baseline['imagestudio_outputs'][f.name] = {
            'size': f.stat().st_size,
            'hash': hash_file(f)
        }

# Phase 1 backups
bdir = Path('E:/AI/ImageStudio/backup_duplicates_phase1')
if bdir.is_dir():
    for f in sorted(bdir.glob('*')):
        if f.is_file():
            baseline['phase1_backups'][f.name] = {
                'size': f.stat().st_size,
                'hash': hash_file(f)
            }

out_file = Path('E:/AI/RealESRGAN-v030/tests/preflight_baseline.json')
out_file.parent.mkdir(parents=True, exist_ok=True)
out_file.write_text(json.dumps(baseline, indent=2), encoding='utf-8')

print("Model Weight Hash:", baseline['weights']['hash'])
print("ImageStudio History Hash:", baseline['imagestudio_history']['hash'])
print("ImageStudio Outputs count:", len(baseline['imagestudio_outputs']))
print("Phase 1 Backups count:", len(baseline['phase1_backups']))
print("Baseline saved to:", out_file)
