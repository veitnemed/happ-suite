"""Global Windows hotkey registration without a visible console window."""
import ctypes
import ctypes.wintypes
import logging
import threading

logger = logging.getLogger("happ_suite.hotkey")

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
VK_F8 = 0x77
MOD_NOREPEAT = 0x4000
HOTKEY_ID = 0x4841


class GlobalF8Hotkey:
    """Register F8 in a message-loop thread and call a Python callback."""

    def __init__(self, callback):
        self._callback = callback
        self._thread = None
        self._thread_id = None
        self._ready = threading.Event()
        self._registered = False

    def start(self):
        if self._thread and self._thread.is_alive():
            return self._registered
        self._thread = threading.Thread(target=self._run, name="GlobalF8Hotkey", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=3)
        return self._registered

    def stop(self):
        if self._thread_id is not None:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self):
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = kernel32.GetCurrentThreadId()
        self._registered = bool(user32.RegisterHotKey(None, HOTKEY_ID, MOD_NOREPEAT, VK_F8))
        self._ready.set()
        if not self._registered:
            logger.error("Could not register global F8 hotkey (it may already be in use)")
            return

        message = ctypes.wintypes.MSG()
        try:
            while True:
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result <= 0:
                    break
                if message.message == WM_HOTKEY and message.wParam == HOTKEY_ID:
                    self._callback()
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID)
            self._registered = False

