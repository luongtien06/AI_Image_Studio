# -*- coding: utf-8 -*-
"""
Native Windows Folder Browser Dialog for AI Image Studio.
Executes tkinter.filedialog.askdirectory() with a topmost foreground owner
in an isolated subprocess to avoid GUI/threading conflicts with Flask & PyTorch.
"""
import os
import sys

def main():
    # Force UTF-8 on stdout and stderr to handle Vietnamese & Unicode paths
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')

    initial_dir = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].strip() else None
    title = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2].strip() else "Chọn thư mục ảnh - AI Image Studio"

    try:
        import tkinter as tk
        from tkinter import filedialog
        import ctypes

        # Grant Win32 permission to set foreground window
        try:
            ctypes.windll.user32.AllowSetForegroundWindow(-1)
        except Exception:
            pass

        root = tk.Tk()
        # Invisible, non-interactive owner window that holds topmost z-order
        root.attributes('-alpha', 0.0)
        root.attributes('-topmost', True)
        root.geometry("1x1+0+0")
        root.overrideredirect(True)
        root.lift()
        root.focus_force()

        try:
            hwnd = root.winfo_id()
            ctypes.windll.user32.SetForegroundWindow(hwnd)
            ctypes.windll.user32.BringWindowToTop(hwnd)
        except Exception:
            pass

        kwargs = {
            "parent": root,
            "title": title
        }
        if initial_dir and os.path.isdir(initial_dir):
            kwargs["initialdir"] = initial_dir

        selected = filedialog.askdirectory(**kwargs)
        root.destroy()

        if selected:
            # Normalize path slashes for Windows
            norm_path = os.path.normpath(selected)
            sys.stdout.write(norm_path + "\n")
            sys.stdout.flush()
        sys.exit(0)
    except Exception as e:
        sys.stderr.write(f"ERROR: {str(e)}\n")
        sys.stderr.flush()
        sys.exit(1)

if __name__ == '__main__':
    main()
