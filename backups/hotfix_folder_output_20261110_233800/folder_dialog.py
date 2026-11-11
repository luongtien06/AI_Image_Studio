# -*- coding: utf-8 -*-
"""
Native Windows Folder Browser Dialog for AI Image Studio.
Executes tkinter.filedialog.askdirectory() in an isolated subprocess
to avoid GUI/threading conflicts with Flask and PyTorch runtimes.
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

        root = tk.Tk()
        root.withdraw()  # Hide root window
        root.wm_attributes('-topmost', 1)  # Bring native dialog to front
        root.lift()
        root.focus_force()

        kwargs = {"title": title}
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
