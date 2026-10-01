"""Explicit live Windows check: enable, restore, then enable the built DNS feature.

Shows UAC once. Run only when changing the active connection is intended.
Local evidence stays under build/ and is never included in a release archive.
"""

import ctypes
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import traceback

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.gemini_dns import DnsManager, atomic_json, powershell

EXE = ROOT / "dist/gemini-dns/start/start.exe"
OUTPUT = ROOT / "build/gemini-dns-live"
REPORT = OUTPUT / "report.json"


def run():
    report = {"ok": False, "stage": "preflight", "exe_sha256": hashlib.sha256(EXE.read_bytes()).hexdigest()}
    atomic_json(REPORT, report)
    manager = DnsManager()
    if manager.backup():
        raise RuntimeError("An existing managed DNS setting must be restored before the live test")
    before = manager.status()["adapter"]
    atomic_json(OUTPUT / "before.json", before)

    def helper(action):
        path = OUTPUT / (action + ".json")
        path.unlink(missing_ok=True)
        proc = subprocess.run([str(EXE), "--gemini-dns-helper", action, "--dns-result", str(path)],
                              timeout=180, creationflags=subprocess.CREATE_NO_WINDOW)
        result = json.loads(path.read_text(encoding="utf-8"))
        if proc.returncode or not result.get("ok"):
            raise RuntimeError(f"Built helper {action} failed: {result}")
        return result["result"]

    try:
        helper("enable")
        report.update(stage="enabled", enabled=manager.status())
        atomic_json(REPORT, report)
        assert report["enabled"]["state"] == "on"
        queries = []
        for ip in report["enabled"]["adapter"]["ipv4"]["servers"]:
            result = powershell(f"@(Resolve-DnsName gemini.google.com -Type A -Server '{ip}' -DnsOnly -QuickTimeout | "
                                "Where-Object IPAddress | Select-Object -ExpandProperty IPAddress) | ConvertTo-Json -Compress")
            assert result, f"No Gemini DNS answer from {ip}"
            queries.append({"resolver": ip, "answers": result})
        report["dns_queries"] = queries
        with requests.Session() as session:
            session.trust_env = False
            response = session.get("https://gemini.google.com/app", timeout=20, allow_redirects=True)
            report["gemini_http"] = {"status": response.status_code, "final_url": response.url}
            assert 200 <= response.status_code < 400, f"Gemini returned HTTP {response.status_code}"
    finally:
        helper("disable")
        restored = manager.status()["adapter"]
        report["restored"] = restored
        report["rollback_verified"] = all(restored[f] == before[f] for f in ("ipv4", "ipv6"))
        report["stage"] = "restored"
        atomic_json(REPORT, report)
        assert report["rollback_verified"], "Original DNS was not exactly restored"
    helper("enable")
    report.update(ok=True, stage="complete", final=manager.status())
    atomic_json(REPORT, report)


if __name__ == "__main__":
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if not ctypes.windll.shell32.IsUserAnAdmin():
        import win32api
        import win32event
        from win32com.shell import shell, shellcon
        child = shell.ShellExecuteEx(
            fMask=shellcon.SEE_MASK_NOCLOSEPROCESS | 0x100,  # SEE_MASK_NOASYNC
            lpVerb="runas", lpFile=str(Path(sys.executable).with_name("pythonw.exe")),
            lpParameters=subprocess.list2cmdline([str(Path(__file__).resolve())]), nShow=0,
        )
        try:
            win32event.WaitForSingleObject(child["hProcess"], win32event.INFINITE)
        finally:
            win32api.CloseHandle(child["hProcess"])
        print(REPORT.read_text(encoding="utf-8").encode("ascii", "backslashreplace").decode())
        sys.exit(0 if json.loads(REPORT.read_text(encoding="utf-8")).get("ok") else 1)
    try:
        run()
    except Exception:
        report = json.loads(REPORT.read_text(encoding="utf-8")) if REPORT.exists() else {}
        report.update(ok=False, error=traceback.format_exc())
        atomic_json(REPORT, report)
        sys.exit(1)
