"""Capture only a private offscreen STATIC window created by this test, never the real desktop."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).parents[2]
spec = importlib.util.spec_from_file_location("capture_helper", ROOT / "ComfyUI-DesktopBridge/capture.py")
capture = importlib.util.module_from_spec(spec); spec.loader.exec_module(capture)

class WindowCapture(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows-only native window capture")
    def test_hidden_test_window_and_missing_window(self):
        child = subprocess.Popen([sys.executable, __file__, "--window-fixture"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            hwnd = child.stdout.readline().strip()
            self.assertTrue(hwnd.isdigit(), child.stderr.read() if child.poll() is not None else "No window handle")
            image = capture.grab({"target": "window", "window": hwnd, "x": 4, "y": 3, "width": 24, "height": 17})
            self.assertEqual(image.mode, "RGB"); self.assertEqual(image.size, (24, 17))
            self.assertEqual(image.getpixel((10, 8)), (255, 255, 255))
            with self.assertRaises(ValueError):
                capture.grab({"target": "window", "window": "0"})
        finally:
            child.communicate("\n", timeout=10)

def fixture():
    import ctypes
    from ctypes import wintypes as w
    import threading
    import time
    capture.dpi()
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID]
    user.CreateWindowExW.restype = w.HWND
    user.DestroyWindow.argtypes = [w.HWND]
    # A tool window outside the visible desktop; no taskbar entry or visible UI.
    hwnd = user.CreateWindowExW(0x80, "STATIC", "ComfyUI private capture test", 0x90000000 | 6, -32000, -32000, 96, 64, None, None, None, None)
    print(hwnd, flush=True)
    stop = threading.Event()
    threading.Thread(target=lambda: (sys.stdin.readline(), stop.set()), daemon=True).start()
    message = w.MSG()
    while not stop.is_set():
        while user.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
            user.TranslateMessage(ctypes.byref(message)); user.DispatchMessageW(ctypes.byref(message))
        time.sleep(.01)
    user.DestroyWindow(hwnd)

if __name__ == "__main__":
    if "--window-fixture" in sys.argv:
        fixture()
    else:
        unittest.main()
