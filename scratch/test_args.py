import sys, os, ctypes
with open('scratch/log.txt', 'w', encoding='utf-8') as out:
    out.write(f'argv: {sys.argv}\n')
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    h = user32.GetThreadDesktop(kernel32.GetCurrentThreadId())
    out.write(f'desktop: {h}\n')
