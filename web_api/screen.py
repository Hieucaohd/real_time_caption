"""Capture behind the web window when Chat's automatic screenshot is enabled."""
import ctypes
import time
from PIL import ImageGrab
from caption.screenshot import save


def capture(folder):
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    user32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    hwnd = user32.GetForegroundWindow()
    title, class_name = ctypes.create_unicode_buffer(512), ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, title, len(title))
    user32.GetClassNameW(hwnd, class_name, len(class_name))
    hide = class_name.value == 'Chrome_WidgetWin_1' and 'Real-time Caption' in title.value
    try:
        if hide:
            user32.ShowWindow(hwnd, 6)
            time.sleep(.25)
        return save(ImageGrab.grab(), folder)
    finally:
        if hide:
            user32.ShowWindow(hwnd, 9)
            user32.SetForegroundWindow(hwnd)
