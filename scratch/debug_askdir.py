import sys
import os
import time
import ctypes
import threading
import tkinter as tk
from tkinter import filedialog

user32 = ctypes.windll.user32

def closer():
    time.sleep(1.0)
    print("Closer thread looking for window...", flush=True)
    def enum_cb(hwnd, extra):
        class_name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, class_name, 256)
        if class_name.value == "#32770":
            length = user32.GetWindowTextLengthW(hwnd)
            buff = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buff, length + 1)
            print("Found #32770 window title:", buff.value, flush=True)
            user32.PostMessageW(hwnd, 0x0010, 0, 0) # WM_CLOSE
            return False
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    user32.EnumWindows(WNDENUMPROC(enum_cb), 0)

root = tk.Tk()
root.geometry("1x1+-2000+-2000")
root.attributes("-topmost", True)
root.attributes("-alpha", 0.0)
root.update()

t = threading.Thread(target=closer, daemon=True)
t.start()

print("Calling askdirectory...", flush=True)
res = filedialog.askdirectory(parent=root, title="Select Image Folder - AI Image Studio")
print("askdirectory returned:", repr(res), flush=True)
root.destroy()
print("Done", flush=True)
