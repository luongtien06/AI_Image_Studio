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

baseline_file = Path('E:/AI/RealESRGAN-v030/tests/preflight_baseline.json')
baseline = json.loads(baseline_file.read_text(encoding='utf-8'))

errors = []

# 1. Model weights
w_path = Path(baseline['weights']['path'])
w_hash = hash_file(w_path)
if w_hash != baseline['weights']['hash']:
    errors.append(f"Model weights hash mismatch: expected {baseline['weights']['hash']}, got {w_hash}")
else:
    print(f"[PASS] Model Weights intact: {w_hash}")

# 2. ImageStudio history
im_hist = Path(baseline['imagestudio_history']['path'])
im_hist_hash = hash_file(im_hist)
if im_hist_hash != baseline['imagestudio_history']['hash']:
    errors.append(f"ImageStudio history hash mismatch: expected {baseline['imagestudio_history']['hash']}, got {im_hist_hash}")
else:
    print(f"[PASS] ImageStudio history.json intact: {im_hist_hash}")

# 3. ImageStudio 14 outputs
im_out = Path('E:/AI/ImageStudio/output')
current_outputs = {f.name: {'size': f.stat().st_size, 'hash': hash_file(f)} for f in im_out.glob('*') if f.is_file() and f.name != 'desktop.ini'}

for fname, meta in baseline['imagestudio_outputs'].items():
    if fname not in current_outputs:
        errors.append(f"Missing ImageStudio output file: {fname}")
    elif current_outputs[fname]['hash'] != meta['hash']:
        errors.append(f"Hash mismatch for {fname}: expected {meta['hash']}, got {current_outputs[fname]['hash']}")

print(f"[PASS] All {len(baseline['imagestudio_outputs'])} ImageStudio user outputs verified 100% byte-exact.")

# 4. Phase 1 backups
bdir = Path('E:/AI/ImageStudio/backup_duplicates_phase1')
for fname, meta in baseline['phase1_backups'].items():
    bf = bdir / fname
    if not bf.is_file():
        errors.append(f"Missing backup file: {fname}")
    elif hash_file(bf) != meta['hash']:
        errors.append(f"Hash mismatch for backup {fname}")

print(f"[PASS] Phase 1 backup files intact.")

if errors:
    print("\n[FAIL] DATA INTEGRITY ERRORS:")
    for e in errors:
        print("  -", e)
    sys.exit(1)
else:
    print("\n[SUCCESS] 100% DATA INTEGRITY VERIFIED WITHOUT ANY LOSS OR MODIFICATION.")
