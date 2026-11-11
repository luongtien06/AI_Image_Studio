# -*- coding: utf-8 -*-
"""
Native Windows Folder Browser Dialog for AI Image Studio.
Executes tkinter.filedialog.askdirectory() attached to WinSta0\Default
in an isolated subprocess to guarantee GUI visibility and avoid threading conflicts.
"""
import os
import sys
import ctypes

def main():
    # Force UTF-8 on stdout and stderr to handle Unicode paths properly
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')

    out_file = None
    initial_dir = None
    title = "Select Image Folder - AI Image Studio"

    # Support signature with optional output file:
    # Option A: python folder_dialog.py "<out_file.txt>" "<initial_dir>" "<title>"
    # Option B: python folder_dialog.py "<initial_dir>" "<title>"
    if len(sys.argv) > 1:
        arg1 = sys.argv[1].strip()
        if arg1.endswith('.txt') or arg1.endswith('.tmp'):
            out_file = arg1
            if len(sys.argv) > 2 and sys.argv[2].strip():
                initial_dir = sys.argv[2].strip()
            if len(sys.argv) > 3 and sys.argv[3].strip():
                title = sys.argv[3].strip()
        else:
            initial_dir = arg1 if arg1 else None
            if len(sys.argv) > 2 and sys.argv[2].strip():
                title = sys.argv[2].strip()

    try:
        # 1. Force attachment to interactive desktop (WinSta0\Default)
        if sys.platform == 'win32':
            try:
                user32 = ctypes.windll.user32
                h_default = user32.OpenDesktopW('Default', 0, False, 0x01FF)
                if h_default:
                    user32.SetThreadDesktop(h_default)
            except Exception:
                pass

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

        kwargs = {
            "parent": root,
            "title": title
        }
        if initial_dir and os.path.isdir(initial_dir):
            kwargs["initialdir"] = initial_dir

        selected = filedialog.askdirectory(**kwargs)
        try:
            root.destroy()
        except Exception:
            pass

        norm_path = ""
        if selected:
            norm_path = os.path.normpath(selected)
            sys.stdout.write(norm_path + "\n")
            sys.stdout.flush()

        if out_file:
            try:
                with open(out_file, 'w', encoding='utf-8') as f:
                    if norm_path:
                        f.write(norm_path)
            except Exception as fe:
                sys.stderr.write(f"ERROR_WRITE_FILE: {fe}\n")

        sys.exit(0)
    except Exception as e:
        sys.stderr.write(f"ERROR: {str(e)}\n")
        sys.stderr.flush()
        if out_file:
            try:
                with open(out_file, 'w', encoding='utf-8') as f:
                    f.write(f"ERROR: {str(e)}")
            except Exception:
                pass
        sys.exit(1)

if __name__ == '__main__':
    main()
