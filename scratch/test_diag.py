import sys
import os
import ctypes
import tempfile

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

test_script = os.path.abspath("scratch/test_args.py")
os.makedirs("scratch", exist_ok=True)
with open(test_script, "w", encoding="utf-8") as f:
    f.write("""import sys, os, ctypes
with open('scratch/log.txt', 'w', encoding='utf-8') as out:
    out.write(f'argv: {sys.argv}\\n')
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    h = user32.GetThreadDesktop(kernel32.GetCurrentThreadId())
    out.write(f'desktop: {h}\\n')
""")

out_file = os.path.abspath("scratch/out.txt")
init_dir_arg = ""
title_arg = "Select Image Folder - AI Image Studio"
cmd = f'"{sys.executable}" "{test_script}" "{out_file}" "{init_dir_arg}" "{title_arg}"'

print("Testing cmd:", cmd)
res = kernel32.CreateProcessW(None, cmd, None, None, False, 0, None, None, ctypes.byref(si), ctypes.byref(pi))
print("CreateProcessW returned:", res)
if res:
    kernel32.WaitForSingleObject(pi.hProcess, 5000)
    kernel32.CloseHandle(pi.hProcess)
    kernel32.CloseHandle(pi.hThread)
    with open("scratch/log.txt", "r", encoding="utf-8") as f:
        print("Child log:\n" + f.read())
