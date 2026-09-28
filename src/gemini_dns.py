"""Reversible Xbox DNS on the active physical Windows connection.

Only the short-lived helper is elevated. The dashboard stays unprivileged.
The journal is written before any change and survives application restarts.
"""

from __future__ import annotations

import base64
import ctypes
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import uuid

# Published at https://xbox-dns.ru/ (checked 2026-09-28).
XBOX_DNS = {"ipv4": ["111.88.96.54", "111.88.96.55"],
            "ipv6": ["2a00:ab00:1233:26::50", "2a00:ab00:1233:26::51"]}
_LOCK = threading.Lock()


def state_dir() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "HappSuite" / "gemini-dns"


def atomic_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def powershell(script: str):
    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    prefix = "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new(); "
    encoded = base64.b64encode((prefix + script).encode("utf-16-le")).decode("ascii")
    result = subprocess.run(
        [str(executable), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True, encoding="utf-8", errors="replace", timeout=45,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Windows не выполнила настройку DNS")
    return json.loads(result.stdout.lstrip("\ufeff")) if result.stdout.strip() else None


_SNAPSHOT = r"""
$adapters = @(Get-NetAdapter -Physical)
$routes = @(Get-NetRoute -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue)
$interfaces = @(Get-NetIPInterface -AddressFamily IPv4)
$profiles = @(Get-NetConnectionProfile -ErrorAction SilentlyContinue)
$all = @(foreach ($adapter in $adapters) {
    $idx = $adapter.ifIndex
    $route = $routes | Where-Object { $_.InterfaceIndex -eq $idx -and $_.NextHop -ne '0.0.0.0' } |
        Sort-Object RouteMetric | Select-Object -First 1
    $iface = $interfaces | Where-Object InterfaceIndex -eq $idx | Select-Object -First 1
    $profile = $profiles | Where-Object InterfaceIndex -eq $idx | Select-Object -First 1
    $guid = ([guid]$adapter.InterfaceGuid).ToString()
    $entry = [ordered]@{guid=$guid; index=[int]$idx; name=$adapter.Name; network=[string]$profile.Name;
        active=($adapter.Status -eq 'Up' -and $null -ne $route);
        metric=([int]$route.RouteMetric + [int]$iface.InterfaceMetric)}
    foreach ($family in @('ipv4','ipv6')) {
        $protocol = if ($family -eq 'ipv4') {'Tcpip'} else {'Tcpip6'}
        $reg = "HKLM:\SYSTEM\CurrentControlSet\Services\$protocol\Parameters\Interfaces\{$guid}"
        $manual = [string](Get-ItemProperty -LiteralPath $reg -Name NameServer -ErrorAction SilentlyContinue).NameServer
        $servers = @((Get-DnsClientServerAddress -InterfaceIndex $idx -AddressFamily $family).ServerAddresses)
        $entry[$family] = @{auto=[string]::IsNullOrWhiteSpace($manual); servers=$servers;
            configured=@($manual -split '[,;\s]+' | Where-Object {$_})}
    }
    $entry.ipv6_online = @(
        Get-NetRoute -InterfaceIndex $idx -AddressFamily IPv6 -DestinationPrefix '::/0' -ErrorAction SilentlyContinue
    ).Count -gt 0
    [pscustomobject]$entry
})
$physicalIds = @($adapters.ifIndex)
$vpn = @($routes | Where-Object {$_.InterfaceIndex -notin $physicalIds -and $_.InterfaceAlias -notlike '*Loopback*'}).Count -gt 0
# Some TUNs install two /1 routes instead of a default route.
$vpn = $vpn -or @(
    Get-NetRoute -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object {$_.DestinationPrefix -in @('0.0.0.0/1','128.0.0.0/1') -and $_.InterfaceIndex -notin $physicalIds}
).Count -gt 0
@{adapters=$all; vpn_active=$vpn} | ConvertTo-Json -Depth 6 -Compress
"""


class WindowsDns:
    def snapshot(self):
        return powershell(_SNAPSHOT)

    def set_servers(self, guid: str, family: str, servers: list[str] | None):
        # Only validated literals reach PowerShell. Identify by GUID, not a
        # recycled interface index or a network name containing shell syntax.
        guid = str(uuid.UUID(guid))
        if family not in XBOX_DNS:
            raise ValueError("Unknown address family")
        version = 4 if family == "ipv4" else 6
        if servers is not None:
            if not servers or any(ipaddress.ip_address(ip).version != version for ip in servers):
                raise ValueError("Invalid DNS addresses")
            args = "-ServerAddresses @(" + ",".join("'" + str(ipaddress.ip_address(ip)) + "'" for ip in servers) + ")"
        else:
            args = "-ResetServerAddresses"
        powershell(
            "$adapter = @(Get-NetAdapter -Physical | Where-Object { "
            f"([guid]$_.InterfaceGuid).ToString() -eq '{guid}' "
            "}); if ($adapter.Count -ne 1) {throw 'Network adapter is unavailable'}; "
            f"Get-DnsClientServerAddress -InterfaceIndex $adapter[0].ifIndex -AddressFamily {family} | "
            f"Set-DnsClientServerAddress {args}; Clear-DnsClientCache"
        )


def choose_adapter(snapshot):
    candidates = [a for a in snapshot["adapters"] if a["active"]]
    if not candidates:
        raise RuntimeError("Нет активного подключения Wi-Fi или Ethernet с выходом в интернет")
    return min(candidates, key=lambda a: (a["metric"], a["index"]))


def original_matches(current, original):
    if original["auto"]:
        return current["auto"]
    return not current["auto"] and current["configured"] == original["configured"]


class DnsManager:
    def __init__(self, backend=None, journal=None):
        self.backend = backend or WindowsDns()
        self.journal = Path(journal) if journal else state_dir() / "backup.json"

    def backup(self):
        if not self.journal.exists():
            return None
        data = json.loads(self.journal.read_text(encoding="utf-8"))
        if data.get("schema") != 1 or not data.get("targets"):
            raise RuntimeError("Копия прежних DNS повреждена; автоматическая замена остановлена")
        uuid.UUID(data["adapter"]["guid"])
        for family, servers in data["targets"].items():
            if family not in XBOX_DNS or not servers:
                raise ValueError("Invalid DNS backup")
            for ip in servers + data["adapter"][family]["configured"]:
                if ipaddress.ip_address(ip).version != (4 if family == "ipv4" else 6):
                    raise ValueError("Invalid DNS backup address")
        return data

    @staticmethod
    def saved_adapter(snapshot, backup):
        matches = [a for a in snapshot["adapters"] if a["guid"] == backup["adapter"]["guid"]]
        if len(matches) != 1:
            raise RuntimeError("Подключите прежний сетевой адаптер, чтобы восстановить его DNS")
        return matches[0]

    def status(self):
        snapshot = self.backend.snapshot()
        backup = self.backup()
        if backup:
            adapter = self.saved_adapter(snapshot, backup)
            configured = all(adapter[f]["servers"] == ips for f, ips in backup["targets"].items())
            active = choose_adapter(snapshot) if any(a["active"] for a in snapshot["adapters"]) else None
            same_network = active is not None and active["guid"] == adapter["guid"] and active["network"] == backup["adapter"]["network"]
            state = "on" if configured and same_network else "changed"
            message = (f"Xbox DNS включён: {adapter['name']}" if state == "on" else
                       "Сеть или DNS изменились — доступно восстановление")
        else:
            adapter = choose_adapter(snapshot)
            state = "off"
            message = f"Xbox DNS для сети: {adapter['network'] or adapter['name']}"
        return {"state": state, "message": message, "managed": backup is not None,
                "adapter": adapter, "vpn_active": snapshot["vpn_active"]}

    def enable(self):
        if self.backup():
            status = self.status()
            if status["state"] == "on":
                return status
            raise RuntimeError("Сначала восстановите DNS предыдущего подключения")
        adapter = choose_adapter(self.backend.snapshot())
        targets = {"ipv4": XBOX_DNS["ipv4"][:]}
        if adapter["ipv6_online"]:
            targets["ipv6"] = XBOX_DNS["ipv6"][:]
        backup = {"schema": 1, "adapter": adapter, "targets": targets}
        atomic_json(self.journal, backup)
        try:
            for family, servers in targets.items():
                self.backend.set_servers(adapter["guid"], family, servers)
            status = self.status()
            if status["state"] != "on":
                raise RuntimeError("Windows не подтвердила новые DNS или сеть успела измениться")
            return status
        except Exception as exc:
            try:
                self.disable()
            except Exception as rollback:
                raise RuntimeError(f"{exc}. Восстановление не завершено: {rollback}. Копия DNS сохранена.") from exc
            raise RuntimeError(f"{exc}. Прежние DNS восстановлены.") from exc

    def disable(self):
        backup = self.backup()
        if not backup:
            return {"message": "Нет DNS, изменённых Relay Studio", "state": "off", "managed": False}
        adapter = self.saved_adapter(self.backend.snapshot(), backup)
        # A user or another app may have changed DNS since activation. Never
        # overwrite that change as a side effect of clicking our off button.
        for family, servers in backup["targets"].items():
            if adapter[family]["servers"] != servers and not original_matches(adapter[family], backup["adapter"][family]):
                raise RuntimeError("DNS изменены другой программой. Копия сохранена; автоматический откат остановлен")
        for family in backup["targets"]:
            original = backup["adapter"][family]
            if not original_matches(adapter[family], original):
                self.backend.set_servers(adapter["guid"], family, None if original["auto"] else original["configured"])
        restored = self.saved_adapter(self.backend.snapshot(), backup)
        if not all(original_matches(restored[f], backup["adapter"][f]) for f in backup["targets"]):
            raise RuntimeError("Windows не подтвердила восстановление DNS; копия сохранена")
        self.journal.unlink()
        return {"message": f"Прежние DNS восстановлены: {adapter['name']}", "state": "off", "managed": False}


def helper_main(action: str, result_path: str) -> int:
    try:
        if not ctypes.windll.shell32.IsUserAnAdmin():
            raise RuntimeError("Для изменения DNS нужны права администратора")
        # Serializes helpers from several Suite builds without changing the
        # existing tray mutex. Abandoned locks remain recoverable via journal.
        import win32event
        import win32api
        handle = win32event.CreateMutex(None, False, r"Local\HappSuiteGeminiDnsMutation")
        acquired = False
        try:
            acquired = win32event.WaitForSingleObject(handle, 0) in (0, 128)
            if not acquired:
                raise RuntimeError("Другая операция DNS ещё выполняется")
            manager = DnsManager()
            if action not in {"enable", "disable"}:
                raise ValueError("Unknown action")
            value = getattr(manager, action)()
            atomic_json(Path(result_path), {"ok": True, "result": value})
            return 0
        finally:
            if acquired:
                win32event.ReleaseMutex(handle)
            win32api.CloseHandle(handle)
    except Exception as exc:
        atomic_json(Path(result_path), {"ok": False, "error": str(exc)})
        return 1


def apply_action(action: str):
    """Called in a worker thread; UAC is shown only for the DNS operation."""
    if action not in {"enable", "disable"}:
        raise ValueError("Unknown action")
    with _LOCK:
        result_path = state_dir() / (uuid.uuid4().hex + ".json")
        result_path.parent.mkdir(parents=True, exist_ok=True)
        args = [] if getattr(sys, "frozen", False) else [str(Path(__file__).with_name("start.py"))]
        args += ["--gemini-dns-helper", action, "--dns-result", str(result_path)]
        try:
            import win32api
            import win32event
            from win32com.shell import shell, shellcon
            process = shell.ShellExecuteEx(
                fMask=shellcon.SEE_MASK_NOCLOSEPROCESS | 0x100,  # SEE_MASK_NOASYNC
                lpVerb="runas", lpFile=sys.executable,
                lpParameters=subprocess.list2cmdline(args), nShow=0,
            )
            try:
                win32event.WaitForSingleObject(process["hProcess"], win32event.INFINITE)
            finally:
                win32api.CloseHandle(process["hProcess"])
            if not result_path.is_file():
                raise RuntimeError("Помощник DNS не вернул результат. Запустите Suite под своей учётной записью")
            value = json.loads(result_path.read_text(encoding="utf-8"))
            if not value["ok"]:
                raise RuntimeError(value["error"])
            return value["result"]
        except Exception as exc:
            if getattr(exc, "winerror", None) == 1223:
                raise RuntimeError("Изменение DNS отменено в окне Windows") from exc
            raise
        finally:
            result_path.unlink(missing_ok=True)
