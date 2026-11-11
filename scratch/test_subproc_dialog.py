import sys
import os
import time
import ctypes
import subprocess
import threading
from pathlib import Path

BASE_DIR = Path("E:/AI Image Studio/RealESRGAN-v030")
out_file = BASE_DIR / "scratch" / "test_out.txt"
if out_file.exists():
    out_file.unlink()

user32 = ctypes.windll.user32
found_props = {}

def inspector():
    time.sleep(0.5)
    start = time.time()
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    while time.time() - start < 8.0:
        windows = []
        def enum_cb(hwnd, extra):
            class_name = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_name, 256)
            if class_name.value == "#32770":
                length = user32.GetWindowTextLengthW(hwnd)
                buff = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buff, length + 1)
                title = buff.value
                visible = bool(user32.IsWindowVisible(hwnd))
                windows.append((hwnd, title, visible))
                if "select" in title.lower() or "folder" in title.lower():
                    ex_style = user32.GetWindowLongW(hwnd, -20)
                    is_topmost = bool(ex_style & 0x00000008)
                    fg_hwnd = user32.GetForegroundWindow()
                    found_props.update({
                        "hwnd": hwnd,
                        "title": title,
                        "ex_style": hex(ex_style),
                        "is_topmost": is_topmost,
                        "is_foreground": (hwnd == fg_hwnd)
                    })
                    print("Found in inspector:", found_props, flush=True)
                    user32.PostMessageW(hwnd, 0x0010, 0, 0) # Close
                    return False
            return True

        user32.EnumWindows(WNDENUMPROC(enum_cb), 0)
        if found_props:
            break
        time.sleep(0.1)

t = threading.Thread(target=inspector, daemon=True)
t.start()

cmd = [
    sys.executable,
    str(BASE_DIR / "folder_dialog.py"),
    str(out_file),
    str(BASE_DIR),
    "Select Image Folder - AI Image Studio"
]
print("Launching subprocess:", cmd, flush=True)
proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
t.join()
print("Subprocess finished. Returncode:", proc.returncode)
print("Stdout:", proc.stdout.strip())
print("Stderr:", proc.stderr.strip())
print("Properties found:", found_props)
