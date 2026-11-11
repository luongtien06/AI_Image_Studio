import sys
import os
import ctypes
import time
import threading

user32 = ctypes.windll.user32

found_windows = []

def enforce_foreground():
    # Poll for the dialog window for up to 3 seconds
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
                        # Force foreground and topmost
                        user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002) # HWND_TOPMOST
                        user32.SetForegroundWindow(hwnd)
                        user32.BringWindowToTop(hwnd)
                        user32.SetActiveWindow(hwnd)
                        time.sleep(0.1)
                        # Check if foreground now
                        fg = user32.GetForegroundWindow()
                        found_windows.append({
                            "hwnd": hwnd,
                            "title": buff.value,
                            "is_foreground": (hwnd == fg)
                        })
                        # Close dialog to end test
                        user32.PostMessageW(hwnd, 0x0010, 0, 0)
                        return False
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        user32.EnumWindows(WNDENUMPROC(enum_cb), 0)
        if found_windows:
            break
        time.sleep(0.05)

t = threading.Thread(target=enforce_foreground, daemon=True)
t.start()

import tkinter as tk
from tkinter import filedialog

root = tk.Tk()
root.withdraw()

res = filedialog.askdirectory(parent=root, title="Select Image Folder - AI Image Studio")
root.destroy()
t.join()

print("Enforce Foreground Result:", found_windows)
