"""Global Windows hotkey registration without a visible console window."""
import ctypes
import ctypes.wintypes
import json
import logging
import os
from pathlib import Path
import threading
import time
from dataclasses import dataclass

logger = logging.getLogger("happ_suite.hotkey")

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_NOREPEAT = 0x4000
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008

VK_F8 = 0x77
VK_F9 = 0x78
VK_F10 = 0x79
VK_H = 0x48
VK_G = 0x47
VK_D = 0x44

SHORTCUT_MODIFIERS = MOD_CONTROL | MOD_ALT

DEFAULT_HOTKEYS = {
    "happ": (VK_H, SHORTCUT_MODIFIERS),
    "gemini": (VK_G, SHORTCUT_MODIFIERS),
    "dns": (VK_D, SHORTCUT_MODIFIERS),
}
HOTKEYS_PATH = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "HappSuite" / "hotkeys.json"


@dataclass(frozen=True)
class HotkeyChoice:
    vk: int
    modifiers: int = 0

    def __post_init__(self):
        if not 1 <= self.vk <= 0xFE or self.modifiers & ~0x0F:
            raise ValueError("Invalid Windows hotkey")
        if self.modifiers & MOD_WIN:
            raise ValueError("Windows-key shortcuts are reserved by the system")
        if self.modifiers == 0 and (0x30 <= self.vk <= 0x5A or self.vk in (0x08, 0x0D, 0x20)):
            raise ValueError("A plain typing key cannot be used as a global shortcut")

    @property
    def label(self) -> str:
        names = {VK_F8: "F8", VK_F9: "F9", VK_H: "H", VK_G: "G"}
        key = names.get(self.vk)
        if key is None and 0x70 <= self.vk <= 0x87:
            key = f"F{self.vk - 0x6F}"
        if key is None and 0x30 <= self.vk <= 0x5A:
            key = chr(self.vk)
        if key is None:
            key = f"VK 0x{self.vk:02X}"
        parts = []
        for flag, name in ((MOD_CONTROL, "Ctrl"), (MOD_ALT, "Alt"), (MOD_SHIFT, "Shift")):
            if self.modifiers & flag:
                parts.append(name)
        return "+".join([*parts, key])


def load_hotkey_choices(path: Path = HOTKEYS_PATH) -> dict[str, HotkeyChoice]:
    defaults = {name: HotkeyChoice(*spec) for name, spec in DEFAULT_HOTKEYS.items()}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        for name in defaults:
            if name in data:
                value = data[name]
                defaults[name] = HotkeyChoice(int(value["vk"]), int(value["modifiers"]))
        if len(set(defaults.values())) != len(defaults):
            raise ValueError("Shortcuts must differ")
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError, KeyError) as exc:
        logger.warning("Ignoring invalid hotkey settings: %s", exc)
        defaults = {name: HotkeyChoice(*spec) for name, spec in DEFAULT_HOTKEYS.items()}
    return defaults


def save_hotkey_choices(choices: dict[str, HotkeyChoice], path: Path = HOTKEYS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    data = {name: {"vk": choice.vk, "modifiers": choice.modifiers} for name, choice in choices.items()}
    temp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


class NextKeyCapture:
    """Listen for one real keyboard event for at most a few seconds, then remove the hook."""

    WH_KEYBOARD_LL = 13
    WM_KEYDOWN = 0x0100
    WM_SYSKEYDOWN = 0x0104
    LLKHF_INJECTED = 0x10

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = (
            ("vkCode", ctypes.wintypes.DWORD),
            ("scanCode", ctypes.wintypes.DWORD),
            ("flags", ctypes.wintypes.DWORD),
            ("time", ctypes.wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        )

    def capture(self, timeout: float = 10.0) -> HotkeyChoice | None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        hook_proc_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)
        user32.SetWindowsHookExW.argtypes = (ctypes.c_int, hook_proc_type, ctypes.c_void_p, ctypes.wintypes.DWORD)
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        user32.UnhookWindowsHookEx.argtypes = (ctypes.c_void_p,)
        user32.CallNextHookEx.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        kernel32.GetModuleHandleW.argtypes = (ctypes.c_wchar_p,)
        kernel32.GetModuleHandleW.restype = ctypes.c_void_p
        captured = []
        thread_id = kernel32.GetCurrentThreadId()
        deadline = time.monotonic() + timeout
        started = time.monotonic()

        def callback(code, message, data):
            if code >= 0 and message in (self.WM_KEYDOWN, self.WM_SYSKEYDOWN):
                key = ctypes.cast(data, ctypes.POINTER(self.KBDLLHOOKSTRUCT)).contents
                vk = int(key.vkCode)
                if time.monotonic() - started > 0.4 and not key.flags & self.LLKHF_INJECTED:
                    if vk not in (0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5):
                        if vk != 0x1B:
                            mods = 0
                            for flag, codes in (
                                (MOD_CONTROL, (0x11, 0xA2, 0xA3)),
                                (MOD_ALT, (0x12, 0xA4, 0xA5)),
                                (MOD_SHIFT, (0x10, 0xA0, 0xA1)),
                                (MOD_WIN, (0x5B, 0x5C)),
                            ):
                                if any(user32.GetAsyncKeyState(code) & 0x8000 for code in codes):
                                    mods |= flag
                            try:
                                captured.append(HotkeyChoice(vk, mods))
                            except ValueError as exc:
                                logger.info("Unsupported key captured: %s", exc)
                        user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
            return user32.CallNextHookEx(None, code, message, data)

        hook_proc = hook_proc_type(callback)
        hook = user32.SetWindowsHookExW(self.WH_KEYBOARD_LL, hook_proc, kernel32.GetModuleHandleW(None), 0)
        if not hook:
            raise ctypes.WinError()
        timer = threading.Timer(timeout, lambda: user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0))
        timer.daemon = True
        timer.start()
        try:
            msg = ctypes.wintypes.MSG()
            while time.monotonic() < deadline and user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                pass
        finally:
            timer.cancel()
            user32.UnhookWindowsHookEx(hook)
        return captured[0] if captured else None

_HOTKEY_BASE_ID = 0x4841  # 'HA'


class GlobalHotkey:
    """Register a single global hotkey in a dedicated message-loop thread.

    Multiple instances can coexist — each uses a unique hotkey ID derived from
    the virtual-key code so they never clash with each other.
    """

    def __init__(self, vk: int, callback, modifiers: int = 0):
        self._vk = vk
        self._modifiers = modifiers
        self._callback = callback
        self._hotkey_id = _HOTKEY_BASE_ID + vk + (modifiers << 8)
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._ready = threading.Event()
        self._registered = False

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return self._registered
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"GlobalHotkey-{self._modifiers:02X}-{self._vk:02X}",
            daemon=True,
        )
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
        self._registered = bool(
            user32.RegisterHotKey(None, self._hotkey_id, MOD_NOREPEAT | self._modifiers, self._vk)
        )
        self._ready.set()
        if not self._registered:
            logger.error(
                "Could not register global hotkey VK=0x%02X (it may already be in use)",
                self._vk,
            )
            return

        message = ctypes.wintypes.MSG()
        try:
            while True:
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result <= 0:
                    break
                if message.message == WM_HOTKEY and message.wParam == self._hotkey_id:
                    self._callback()
        finally:
            user32.UnregisterHotKey(None, self._hotkey_id)
            self._registered = False


# ── Backward-compat alias (used by existing tray.py import) ──────────────────

class GlobalF8Hotkey(GlobalHotkey):
    """Legacy wrapper — prefer GlobalHotkey(VK_F8, callback) directly."""

    def __init__(self, callback):
        super().__init__(VK_F8, callback)
