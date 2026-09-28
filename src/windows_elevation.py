"""Small Windows helpers for an on-demand elevated VPN session."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
from pathlib import Path
import subprocess
import sys


def is_admin() -> bool:
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def relaunch_vpn_elevated(*, connect: bool = True) -> tuple[bool, int]:
    """Request UAC elevation for the same app and apply one VPN action."""
    if os.name != "nt":
        return False, 0
    executable = sys.executable
    action = "--elevated-vpn" if connect else "--elevated-vpn-stop"
    if getattr(sys, "frozen", False):
        parameters = subprocess.list2cmdline([action])
        working_dir = str(Path(executable).resolve().parent)
    else:
        arguments = [sys.argv[0], *sys.argv[1:], action]
        parameters = subprocess.list2cmdline(arguments)
        working_dir = str(Path(sys.argv[0]).resolve().parent)
    try:
        shell = ctypes.windll.shell32
        shell.ShellExecuteW.argtypes = (
            ctypes.c_void_p, ctypes.wintypes.LPCWSTR, ctypes.wintypes.LPCWSTR,
            ctypes.wintypes.LPCWSTR, ctypes.wintypes.LPCWSTR, ctypes.c_int,
        )
        shell.ShellExecuteW.restype = ctypes.c_void_p
        result = shell.ShellExecuteW(
            None, "runas", executable, parameters, working_dir, 1
        )
        result = int(result or 0)
    except OSError as exc:
        return False, int(getattr(exc, "winerror", 0) or 0)
    return result > 32, result
