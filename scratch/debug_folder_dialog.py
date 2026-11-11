import sys
import os

print("Step 1: start", flush=True)
import ctypes
user32 = ctypes.windll.user32
print("Step 2: ctypes loaded", flush=True)

try:
    h_default = user32.OpenDesktopW('Default', 0, False, 0x01FF)
    print("Step 3: OpenDesktopW:", h_default, flush=True)
    if h_default:
        res = user32.SetThreadDesktop(h_default)
        print("Step 4: SetThreadDesktop:", res, flush=True)
except Exception as e:
    print("Step 3/4 error:", e, flush=True)

import tkinter as tk
print("Step 5: tkinter imported", flush=True)

root = tk.Tk()
print("Step 6: tk.Tk() created", flush=True)
root.geometry("1x1+-2000+-2000")
root.attributes("-topmost", True)
root.attributes("-alpha", 0.0)
root.update()
print("Step 7: root configured", flush=True)

root.destroy()
print("Step 8: root destroyed", flush=True)
