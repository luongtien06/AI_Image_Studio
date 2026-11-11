import sys
import os
import ctypes
import time
import threading

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

found_windows = []

def force_foreground_window(hwnd):
    fg_hwnd = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(fg_hwnd, None)
    my_thread = kernel32.GetCurrentThreadId()

    # 1. Bring to top with HWND_TOPMOST
    user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040) # HWND_TOPMOST | SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW

    # 2. Attach thread input trick
    if fg_thread != my_thread:
        user32.AttachThreadInput(my_thread, fg_thread, True)
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
        user32.SetFocus(hwnd)
        user32.AttachThreadInput(my_thread, fg_thread, False)
    else:
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)

    # 3. Fallback Alt key simulation if needed
    if user32.GetForegroundWindow() != hwnd:
        user32.keybd_event(0x12, 0, 0, 0) # Alt down
        user32.SetForegroundWindow(hwnd)
        user32.keybd_event(0x12, 0, 2, 0) # Alt up

def poll_and_force():
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
                        force_foreground_window(hwnd)
                        time.sleep(0.1)
                        fg = user32.GetForegroundWindow()
                        found_windows.append({
                            "hwnd": hwnd,
                            "title": buff.value,
                            "is_foreground": (hwnd == fg)
                        })
                        user32.PostMessageW(hwnd, 0x0010, 0, 0)
                        return False
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        user32.EnumWindows(WNDENUMPROC(enum_cb), 0)
        if found_windows:
            break
        time.sleep(0.05)

t = threading.Thread(target=poll_and_force, daemon=True)
t.start()

import tkinter as tk
from tkinter import filedialog

root = tk.Tk()
root.withdraw()
res = filedialog.askdirectory(parent=root, title="Select Image Folder - AI Image Studio")
root.destroy()
t.join()

print("Force Foreground Result:", found_windows)
