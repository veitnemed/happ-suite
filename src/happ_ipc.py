"""Send HAPP deep links to its already running GUI through SingleApplication IPC.

This is the private SingleApplication protocol used by the installed HAPP 4.3.0.
Every operation verifies the pipe's owning PID and executable before writing.
The acknowledgement proves delivery to the GUI, not VPN connection success.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import os
import struct
import sys
import time
from ctypes import wintypes

import psutil


_CRC_TABLE = (
    0x0000, 0x1081, 0x2102, 0x3183, 0x4204, 0x5285, 0x6306, 0x7387,
    0x8408, 0x9489, 0xA50A, 0xB58B, 0xC60C, 0xD68D, 0xE70E, 0xF78F,
)


class HappIpcError(RuntimeError):
    """HAPP's existing-instance IPC is absent, untrusted, or unresponsive."""


def _qt_checksum(payload: bytes) -> int:
    crc = 0xFFFF
    for byte in payload:
        crc = ((crc >> 4) & 0x0FFF) ^ _CRC_TABLE[(crc ^ byte) & 15]
        byte >>= 4
        crc = ((crc >> 4) & 0x0FFF) ^ _CRC_TABLE[(crc ^ byte) & 15]
    return (~crc) & 0xFFFF


def _server_name() -> str:
    digest = hashlib.sha256(b"SingleApplicationHapp").digest()
    return base64.b64encode(digest).decode("ascii").replace("/", "_")


def _init_message() -> bytes:
    name = _server_name().encode("ascii")
    body = struct.pack(">I", len(name)) + name + struct.pack(">BI", 3, 0)
    return body + struct.pack(">H", _qt_checksum(body))


class HappIpcClient:
    """Talk to one *existing* Happ.exe without launching or activating it."""

    def __init__(self, happ_exe: str, timeout_sec: float = 3.0):
        self.happ_exe = os.path.normcase(os.path.abspath(happ_exe))
        self.timeout_sec = timeout_sec
        self.pipe_path = rf"\\.\pipe\{_server_name()}"

    def _connect(self) -> tuple[int, int]:
        if sys.platform != "win32":
            raise HappIpcError("HAPP named pipe is available only on Windows")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.WaitNamedPipeW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD)
        kernel32.WaitNamedPipeW.restype = wintypes.BOOL
        kernel32.CreateFileW.argtypes = (
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        )
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.GetNamedPipeServerProcessId.argtypes = (
            wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG),
        )
        kernel32.GetNamedPipeServerProcessId.restype = wintypes.BOOL

        if not kernel32.WaitNamedPipeW(self.pipe_path, int(self.timeout_sec * 1000)):
            raise HappIpcError(f"HAPP GUI IPC not available: {ctypes.get_last_error()}")
        handle = kernel32.CreateFileW(self.pipe_path, 0xC0000000, 0, None, 3, 0, None)
        if handle == wintypes.HANDLE(-1).value:
            raise HappIpcError(f"Cannot open HAPP GUI IPC: {ctypes.get_last_error()}")

        try:
            pid = wintypes.ULONG()
            if not kernel32.GetNamedPipeServerProcessId(handle, ctypes.byref(pid)):
                raise HappIpcError("Cannot verify HAPP GUI IPC owner")
            process = psutil.Process(pid.value)
            actual_exe = os.path.normcase(os.path.abspath(process.exe()))
            if process.name().casefold() != "happ.exe" or actual_exe != self.happ_exe:
                raise HappIpcError("HAPP GUI IPC is owned by another executable")
            return handle, pid.value
        except Exception:
            kernel32.CloseHandle(handle)
            raise

    def _write_confirmed(self, handle: int, payload: bytes) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.WriteFile.argtypes = (
            wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
        )
        kernel32.WriteFile.restype = wintypes.BOOL
        kernel32.ReadFile.argtypes = (
            wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
        )
        kernel32.ReadFile.restype = wintypes.BOOL
        kernel32.PeekNamedPipe.argtypes = (
            wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
            ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
        )
        kernel32.PeekNamedPipe.restype = wintypes.BOOL

        for frame in (struct.pack(">Q", len(payload)), payload):
            sent = wintypes.DWORD()
            buffer = ctypes.create_string_buffer(frame)
            if not kernel32.WriteFile(handle, buffer, len(frame), ctypes.byref(sent), None) or sent.value != len(frame):
                raise HappIpcError(f"HAPP GUI IPC write failed: {ctypes.get_last_error()}")
            deadline = time.monotonic() + self.timeout_sec
            while time.monotonic() < deadline:
                available = wintypes.DWORD()
                if not kernel32.PeekNamedPipe(handle, None, 0, None, ctypes.byref(available), None):
                    raise HappIpcError(f"HAPP GUI IPC closed before acknowledgement: {ctypes.get_last_error()}")
                if available.value:
                    ack = ctypes.create_string_buffer(1)
                    read = wintypes.DWORD()
                    if not kernel32.ReadFile(handle, ack, 1, ctypes.byref(read), None) or read.value != 1 or ack.raw != b"\n":
                        raise HappIpcError("Invalid HAPP GUI IPC acknowledgement")
                    break
                time.sleep(0.01)
            else:
                raise HappIpcError("HAPP GUI IPC acknowledgement timed out")

    def _send(self, payload: bytes | None) -> int:
        handle, pid = self._connect()
        try:
            self._write_confirmed(handle, _init_message())
            if payload is not None:
                self._write_confirmed(handle, payload)
            return pid
        finally:
            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(handle)

    def probe(self) -> int:
        """Inert handshake; does not request a VPN or UI action."""
        return self._send(None)

    def send_deeplink(self, action: str) -> int:
        """Deliver only a validated connect/disconnect action to the HAPP GUI."""
        if action not in {"connect", "disconnect"}:
            raise ValueError("Unsupported HAPP deep link action")
        return self._send(f"Happ.exe,happ://{action}".encode("utf-8"))
