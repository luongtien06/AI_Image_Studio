import sys
import os
import ctypes
from pathlib import Path

BASE_DIR = Path("E:/AI Image Studio/RealESRGAN-v030")
script_path = BASE_DIR / "folder_dialog.py"
out_file = str(BASE_DIR / "scratch" / "test_out2.txt")
cmd = f'"{sys.executable}" "{str(script_path)}" "{out_file}" "{str(BASE_DIR)}" "Select Image Folder - AI Image Studio"'

kernel32 = ctypes.windll.kernel32

class _STARTUPINFO(ctypes.Structure):
    _fields_ = [
        ('cb', ctypes.c_ulong),
        ('lpReserved', ctypes.c_wchar_p),
        ('lpDesktop', ctypes.c_wchar_p),
        ('lpTitle', ctypes.c_wchar_p),
        ('dwX', ctypes.c_ulong),
        ('dwY', ctypes.c_ulong),
        ('dwXSize', ctypes.c_ulong),
        ('dwYSize', ctypes.c_ulong),
        ('dwXCountChars', ctypes.c_ulong),
        ('dwYCountChars', ctypes.c_ulong),
        ('dwFillAttribute', ctypes.c_ulong),
        ('dwFlags', ctypes.c_ulong),
        ('wShowWindow', ctypes.c_ushort),
        ('cbReserved2', ctypes.c_ushort),
        ('lpReserved2', ctypes.c_void_p),
        ('hStdInput', ctypes.c_void_p),
        ('hStdOutput', ctypes.c_void_p),
        ('hStdError', ctypes.c_void_p),
    ]

class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ('hProcess', ctypes.c_void_p),
        ('hThread', ctypes.c_void_p),
        ('dwProcessId', ctypes.c_ulong),
        ('dwThreadId', ctypes.c_ulong),
    ]

si = _STARTUPINFO()
si.cb = ctypes.sizeof(_STARTUPINFO)
si.lpDesktop = "WinSta0\\Default"
si.dwFlags = 1
si.wShowWindow = 1

pi = _PROCESS_INFORMATION()

print("Calling CreateProcessW...")
created = kernel32.CreateProcessW(
    None, cmd, None, None, False, 0, None, None, ctypes.byref(si), ctypes.byref(pi)
)
err = kernel32.GetLastError()
print("created:", created, "err:", err, "pid:", pi.dwProcessId)

if created:
    print("Waiting for process...")
    res = kernel32.WaitForSingleObject(pi.hProcess, 5000)
    print("WaitForSingleObject result:", hex(res))
    kernel32.CloseHandle(pi.hProcess)
    kernel32.CloseHandle(pi.hThread)
