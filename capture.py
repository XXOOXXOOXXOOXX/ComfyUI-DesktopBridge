"""CPU-only capture helpers; shared verbatim with the desktop companion."""
import base64
import ctypes
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from PIL import Image

MAX_PIXELS = 40_000_000
MAX_BATCH_PIXELS = 64_000_000


def dpi():
    if os.name == "nt":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (OSError, AttributeError):
            pass


def windows():
    if os.name != "nt":
        return []
    from ctypes import wintypes as w
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetWindowTextLengthW.argtypes = [w.HWND]
    user.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
    user.IsWindowVisible.argtypes = user.IsIconic.argtypes = [w.HWND]
    result = []
    callback_type = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    @callback_type
    def callback(hwnd, _):
        if user.IsWindowVisible(hwnd):
            length = user.GetWindowTextLengthW(hwnd)
            if length:
                title = ctypes.create_unicode_buffer(length + 1)
                user.GetWindowTextW(hwnd, title, length + 1)
                result.append({"id": str(hwnd), "title": title.value, "minimized": bool(user.IsIconic(hwnd))})
        return True
    user.EnumWindows(callback, 0)
    return result


def sources():
    dpi()
    import mss
    with mss.mss() as screen:
        monitors = [{"id": index, **dict(monitor)} for index, monitor in enumerate(screen.monitors)]
    cursor = None
    if os.name == "nt":
        from ctypes import wintypes as w
        point = w.POINT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            cursor = {"x": point.x, "y": point.y}
    return {"monitors": monitors, "windows": windows(), "cursor": cursor}


def _window(hwnd):
    """Run in a disposable subprocess: a hung application's WM_PRINT cannot hang ComfyUI."""
    if os.name != "nt":
        raise ValueError("Python 窗口捕获目前支持 Windows；其他系统请选择浏览器窗口共享。")
    from ctypes import wintypes as w
    dpi()
    user, gdi = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("gdi32", use_last_error=True)
    user.IsWindow.argtypes = user.IsIconic.argtypes = [w.HWND]
    user.GetClientRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
    user.GetDC.argtypes, user.GetDC.restype = [w.HWND], w.HDC
    user.ReleaseDC.argtypes = [w.HWND, w.HDC]
    user.PrintWindow.argtypes = [w.HWND, w.HDC, w.UINT]
    gdi.CreateCompatibleDC.argtypes, gdi.CreateCompatibleDC.restype = [w.HDC], w.HDC
    gdi.CreateCompatibleBitmap.argtypes, gdi.CreateCompatibleBitmap.restype = [w.HDC, ctypes.c_int, ctypes.c_int], w.HBITMAP
    gdi.SelectObject.argtypes, gdi.SelectObject.restype = [w.HDC, w.HANDLE], w.HANDLE
    gdi.DeleteObject.argtypes, gdi.DeleteDC.argtypes = [w.HANDLE], [w.HDC]
    gdi.GetDIBits.argtypes = [w.HDC, w.HBITMAP, w.UINT, w.UINT, ctypes.c_void_p, ctypes.c_void_p, w.UINT]
    hwnd = int(hwnd)
    if not user.IsWindow(hwnd) or user.IsIconic(hwnd):
        raise ValueError("窗口已关闭或最小化，请恢复窗口后重试。")
    rect = w.RECT()
    if not user.GetClientRect(hwnd, ctypes.byref(rect)):
        raise ValueError("读取窗口大小失败。")
    width, height = rect.right, rect.bottom
    if width < 1 or height < 1 or width * height > MAX_PIXELS:
        raise ValueError("窗口尺寸超出捕获限制。")
    dc = user.GetDC(hwnd)
    memory, bitmap, previous = None, None, None
    try:
        memory = gdi.CreateCompatibleDC(dc)
        bitmap = gdi.CreateCompatibleBitmap(dc, width, height)
        if not memory or not bitmap:
            raise ValueError("无法分配截图缓冲。")
        previous = gdi.SelectObject(memory, bitmap)
        if not user.PrintWindow(hwnd, memory, 1) and not user.PrintWindow(hwnd, memory, 3):
            raise ValueError("应用不支持后台窗口捕获，请使用浏览器共享。")
        gdi.SelectObject(memory, previous)
        previous = None
        # BITMAPINFOHEADER with a top-down, 32-bit RGB bitmap.
        import struct
        info = ctypes.create_string_buffer(struct.pack("<IiiHHIIiiII", 40, width, -height, 1, 32, 0, width * height * 4, 0, 0, 0, 0))
        pixels = ctypes.create_string_buffer(width * height * 4)
        if not gdi.GetDIBits(memory, bitmap, 0, height, pixels, info, 0):
            raise ValueError("读取窗口像素失败。")
        image = Image.frombytes("RGB", (width, height), pixels.raw, "raw", "BGRX")
        if image.getbbox() is None:
            # Chromium/composited windows may require PW_RENDERFULLCONTENT.
            previous = gdi.SelectObject(memory, bitmap)
            rendered = user.PrintWindow(hwnd, memory, 3)
            gdi.SelectObject(memory, previous)
            previous = None
            if rendered and gdi.GetDIBits(memory, bitmap, 0, height, pixels, info, 0):
                image = Image.frombytes("RGB", (width, height), pixels.raw, "raw", "BGRX")
        return image
    finally:
        if previous:
            gdi.SelectObject(memory, previous)
        if bitmap:
            gdi.DeleteObject(bitmap)
        if memory:
            gdi.DeleteDC(memory)
        if dc:
            user.ReleaseDC(hwnd, dc)


def crop(image, config):
    x, y = int(config.get("x", 0)), int(config.get("y", 0))
    width, height = int(config.get("width", 0)), int(config.get("height", 0))
    # Zero width/height means the remaining source area.
    width = width or image.width - x
    height = height or image.height - y
    if x < 0 or y < 0 or width < 1 or height < 1 or x + width > image.width or y + height > image.height:
        raise ValueError("裁剪区域超出画面；X/Y 是相对所选屏幕或窗口的像素坐标。")
    return image.crop((x, y, x + width, y + height))


def grab(config):
    dpi()
    if config.get("target", "monitor") == "window":
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--window", str(int(config.get("window", 0)))], capture_output=True, timeout=6)
        if result.returncode:
            raise ValueError(result.stderr.decode("utf-8", errors="replace")[-300:] or "窗口捕获失败")
        with Image.open(io.BytesIO(result.stdout)) as image:
            return crop(image.convert("RGB"), config)
    import mss
    with mss.mss() as screen:
        index = int(config.get("monitor", 1))
        if not 0 <= index < len(screen.monitors):
            raise ValueError("所选显示器已断开，请刷新显示器列表。")
        monitor = screen.monitors[index]
        x, y = int(config.get("x", 0)), int(config.get("y", 0))
        width, height = int(config.get("width", 0)) or monitor["width"] - x, int(config.get("height", 0)) or monitor["height"] - y
        if min(x, y) < 0 or min(width, height) < 1 or x + width > monitor["width"] or y + height > monitor["height"] or width * height > MAX_PIXELS:
            raise ValueError("屏幕捕获区域无效或超过 4000 万像素限制。")
        pixels = screen.grab({"left": monitor["left"] + x, "top": monitor["top"] + y, "width": width, "height": height})
        return Image.frombytes("RGB", pixels.size, pixels.rgb)


def encode(image):
    output = io.BytesIO()
    image.save(output, format="PNG", compress_level=2)
    return base64.b64encode(output.getvalue()).decode("ascii")


def capture(config, cancel=None):
    count = int(config.get("frames", 1))
    interval, fps = float(config.get("interval", .1)), float(config.get("fps", 10))
    if not 1 <= count <= 64 or not .2 <= fps <= 60 or not 0 <= interval <= 10:
        raise ValueError("帧数应为 1–64，FPS 为 0.2–60，帧间隔为 0–10 秒。")
    delay = max(interval, 1 / fps)
    frames, first_size, encoded_size, source_size = [], None, 0, None
    for index in range(count):
        if cancel:
            cancel()
        started = time.monotonic()
        image = grab(config)
        source_size = image.size
        if config.get("preview"):
            image.thumbnail((1280, 800), Image.Resampling.LANCZOS)
        if image.width * image.height * count > MAX_BATCH_PIXELS:
            raise ValueError("批量截图超过 6400 万像素，请缩小裁剪区域或减少帧数。")
        first_size = first_size or image.size
        if image.size != first_size:
            image = image.resize(first_size, Image.Resampling.LANCZOS)
        encoded = encode(image)
        encoded_size += len(encoded)
        if encoded_size > 160 * 1024 * 1024:
            raise ValueError("截图数据超过传输上限，请减少帧数或缩小捕获区域。")
        frames.append(encoded)
        if index + 1 < count:
            remaining = delay - (time.monotonic() - started)
            while remaining > 0:
                time.sleep(min(.2, remaining))
                if cancel:
                    cancel()
                remaining = delay - (time.monotonic() - started)
    return {"frames": frames, **({"width": source_size[0], "height": source_size[1]} if config.get("preview") else {})}


if __name__ == "__main__":
    try:
        _window(sys.argv[2]).save(sys.stdout.buffer, format="PNG")
    except Exception as exc:
        sys.stderr.buffer.write(str(exc).encode("utf-8"))
        sys.exit(1)
