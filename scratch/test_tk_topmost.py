import sys
import os
import ctypes
import time
import threading

user32 = ctypes.windll.user32

found_windows = []

def inspect_dialog():
    start = time.time()
    while time.time() - start < 3.0:
        def enum_cb(hwnd, extra):
            if user32.IsWindowVisible(hwnd):
                class_name = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, class_name, 256)
                if class_name.value == "#32770":
                    length = user32.GetWindowTextLengthW(hwnd)
                    buff = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buff, length + 1)
                    if "select" in buff.value.lower() or "folder" in buff.value.lower():
                        # Check window extended styles for WS_EX_TOPMOST (0x00000008)
                        ex_style = user32.GetWindowLongW(hwnd, -20) # GWL_EXSTYLE
                        is_topmost = bool(ex_style & 0x00000008)
                        found_windows.append({
                            "hwnd": hwnd,
                            "title": buff.value,
                            "ex_style": hex(ex_style),
                            "is_topmost": is_topmost
                        })
                        user32.PostMessageW(hwnd, 0x0010, 0, 0) # Close
                        return False
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        user32.EnumWindows(WNDENUMPROC(enum_cb), 0)
        if found_windows:
            break
        time.sleep(0.05)

t = threading.Thread(target=inspect_dialog, daemon=True)
t.start()

import tkinter as tk
from tkinter import filedialog

root = tk.Tk()
# Use 1x1 window positioned offscreen with topmost set
root.geometry("1x1+-2000+-2000")
root.attributes("-topmost", True)
root.attributes("-alpha", 0.0) # completely invisible
root.update()

# Also start thread to ensure foreground
def poll_force(hwnd_root):
    time.sleep(0.1)
    user32.SetForegroundWindow(hwnd_root)

threading.Thread(target=poll_force, args=(root.winfo_id(),), daemon=True).start()

res = filedialog.askdirectory(parent=root, title="Select Image Folder - AI Image Studio")
root.destroy()
t.join()

print("Tk Topmost Result:", found_windows)
