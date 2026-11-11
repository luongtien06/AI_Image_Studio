# -*- coding: utf-8 -*-
"""
Native Windows Folder Browser Dialog for AI Image Studio.
Executes tkinter.filedialog.askdirectory() in an isolated subprocess.
Guarantees foreground visibility and HWND_TOPMOST z-order on the active Windows desktop.
"""
import ctypes
import os
import sys
import threading
import time

def enforce_dialog_foreground(title):
    """
    Background monitor thread that detects the native Win32 dialog (#32770)
    and asserts HWND_TOPMOST and foreground focus so it never opens behind other windows.
    """
    if sys.platform != 'win32':
        return

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    start_time = time.time()

    while time.time() - start_time < 10.0:
        found = False

        def enum_cb(hwnd, extra):
            nonlocal found
            if user32.IsWindowVisible(hwnd):
                class_name = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, class_name, 256)
                if class_name.value == "#32770":
                    length = user32.GetWindowTextLengthW(hwnd)
                    buff = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buff, length + 1)
                    win_title = buff.value
                    if not title or title.lower() in win_title.lower() or "select" in win_title.lower() or "folder" in win_title.lower():
                        user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)  # HWND_TOPMOST | SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW
                        user32.AllowSetForegroundWindow(-1)
                        user32.SetForegroundWindow(hwnd)
                        user32.BringWindowToTop(hwnd)
                        user32.SetActiveWindow(hwnd)
                        user32.SetFocus(hwnd)
                        found = True
                        if os.environ.get("AI_STUDIO_TEST_AUTO_CLOSE") == "1":
                            time.sleep(0.2)
                            user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
                        return False
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        user32.EnumWindows(WNDENUMPROC(enum_cb), 0)
        if found:
            break
        time.sleep(0.05)


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
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        # Keep window mapped with topmost property, positioned offscreen
        root.geometry("1x1+-2000+-2000")
        root.attributes("-topmost", True)
        root.attributes("-alpha", 0.0)
        root.update()

        # Start background monitor thread to guarantee dialog is topmost and in foreground
        monitor_thread = threading.Thread(target=enforce_dialog_foreground, args=(title,), daemon=True)
        monitor_thread.start()

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
