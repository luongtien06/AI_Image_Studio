import sys
import os
import ctypes
import time
import threading

user32 = ctypes.windll.user32

found_windows = []

def find_dialog():
    time.sleep(0.5)
    def enum_cb(hwnd, extra):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            buff = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buff, length + 1)
            title = buff.value
            class_name = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_name, 256)
            if "select" in title.lower() or "folder" in title.lower() or "#32770" in class_name.value:
                # Get window rect and foreground status
                fg_hwnd = user32.GetForegroundWindow()
                is_fg = (hwnd == fg_hwnd)
                found_windows.append({
                    "hwnd": hwnd,
                    "title": title,
                    "class": class_name.value,
                    "is_foreground": is_fg
                })
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    user32.EnumWindows(WNDENUMPROC(enum_cb), 0)

    # Automatically close dialog after inspecting to not block
    time.sleep(0.5)
    for w in found_windows:
        user32.PostMessageW(w["hwnd"], 0x0010, 0, 0) # WM_CLOSE

t = threading.Thread(target=find_dialog, daemon=True)
t.start()

import tkinter as tk
from tkinter import filedialog

root = tk.Tk()
root.withdraw()
root.wm_attributes('-topmost', 1)

try:
    hwnd = root.winfo_id()
    ctypes.windll.user32.AllowSetForegroundWindow(-1)
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    ctypes.windll.user32.BringWindowToTop(hwnd)
except Exception:
    pass

res = filedialog.askdirectory(parent=root, title="Select Image Folder - AI Image Studio")
root.destroy()
t.join()

print("Found Windows during askdirectory:", found_windows)
