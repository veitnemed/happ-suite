"""Read fixed PE product-version metadata without executing a Windows binary."""

from __future__ import annotations

import os
from pathlib import Path


def read_product_version(path: Path) -> str | None:
    if os.name != "nt":
        return None
    import ctypes
    import ctypes.wintypes

    class FixedFileInfo(ctypes.Structure):
        _fields_ = [(name, ctypes.wintypes.DWORD) for name in (
            "dwSignature", "dwStrucVersion", "dwFileVersionMS", "dwFileVersionLS",
            "dwProductVersionMS", "dwProductVersionLS", "dwFileFlagsMask", "dwFileFlags",
            "dwFileOS", "dwFileType", "dwFileSubtype", "dwFileDateMS", "dwFileDateLS",
        )]

    version = ctypes.windll.version
    version.GetFileVersionInfoSizeW.argtypes = [ctypes.wintypes.LPCWSTR, ctypes.POINTER(ctypes.wintypes.DWORD)]
    version.GetFileVersionInfoSizeW.restype = ctypes.wintypes.DWORD
    size = version.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        return None
    buffer = ctypes.create_string_buffer(size)
    version.GetFileVersionInfoW.argtypes = [ctypes.wintypes.LPCWSTR, ctypes.wintypes.DWORD,
                                            ctypes.wintypes.DWORD, ctypes.c_void_p]
    version.GetFileVersionInfoW.restype = ctypes.wintypes.BOOL
    if not version.GetFileVersionInfoW(str(path), 0, size, buffer):
        return None
    pointer, length = ctypes.c_void_p(), ctypes.wintypes.UINT()
    version.VerQueryValueW.argtypes = [ctypes.c_void_p, ctypes.wintypes.LPCWSTR,
                                       ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.wintypes.UINT)]
    version.VerQueryValueW.restype = ctypes.wintypes.BOOL
    if not version.VerQueryValueW(buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)):
        return None
    info = ctypes.cast(pointer, ctypes.POINTER(FixedFileInfo)).contents
    major, minor = info.dwProductVersionMS >> 16, info.dwProductVersionMS & 0xFFFF
    build, revision = info.dwProductVersionLS >> 16, info.dwProductVersionLS & 0xFFFF
    return f"{major}.{minor}.{build}.{revision}"
