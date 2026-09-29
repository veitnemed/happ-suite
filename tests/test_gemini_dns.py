"""The DNS switch must survive restarts and never lose prior network settings."""

import copy
import json
from unittest.mock import Mock

import pytest

from src.gemini_dns import DnsManager, WindowsDns, XBOX_DNS, choose_adapter


def adapter(guid="11111111-1111-1111-1111-111111111111", index=21, auto=True, ipv6=False):
    return {"guid": guid, "index": index, "name": "Wi-Fi", "network": "Home",
            "active": True, "metric": 25, "ipv6_online": ipv6,
            "ipv4": {"auto": auto, "servers": ["192.168.1.1"],
                     "configured": [] if auto else ["192.168.1.1"]},
            "ipv6": {"auto": auto, "servers": ["2001:4860:4860::8888"] if ipv6 else [],
                     "configured": ["2001:4860:4860::8888"] if ipv6 and not auto else []}}


class Network:
    def __init__(self, original, journal):
        self.adapters = [copy.deepcopy(original)]
        self.original = copy.deepcopy(original)
        self.journal = journal
        self.calls = []
        self.fail_family = None

    def snapshot(self):
        return {"adapters": copy.deepcopy(self.adapters), "vpn_active": True}

    def set_servers(self, guid, family, servers):
        assert self.journal.exists(), "Recovery journal must precede ANY write"
        self.calls.append((guid, family, servers))
        if self.fail_family == family:
            self.fail_family = None
            raise RuntimeError("simulated Windows failure")
        current = next(a for a in self.adapters if a["guid"] == guid)
        current[family] = {"auto": servers is None,
                           "servers": list(servers) if servers else self.original[family]["servers"],
                           "configured": list(servers) if servers else []}


def manager(tmp_path, **options):
    journal = tmp_path / "backup.json"
    backend = Network(adapter(**options), journal)
    return DnsManager(backend, journal), backend


@pytest.mark.parametrize("auto", [True, False])
@pytest.mark.parametrize("ipv6", [True, False])
def test_apply_restart_restore_preserves_static_or_dhcp(tmp_path, auto, ipv6):
    dns, net = manager(tmp_path, auto=auto, ipv6=ipv6)
    before = net.snapshot()
    assert dns.enable()["state"] == "on"
    assert net.adapters[0]["ipv4"]["servers"] == XBOX_DNS["ipv4"]
    assert (len(net.calls) == 2) == ipv6
    # Simulates closing and reopening Suite, with the journal on disk.
    reopened = DnsManager(net, dns.journal)
    assert reopened.status()["managed"]
    assert reopened.disable()["state"] == "off"
    assert net.snapshot() == before
    assert not dns.journal.exists()
    assert net.calls[-1][2] == (None if auto else before["adapters"][0]["ipv6" if ipv6 else "ipv4"]["configured"])


def test_partial_failure_rolls_back_completed_family(tmp_path):
    dns, net = manager(tmp_path, auto=False, ipv6=True)
    before = net.snapshot()
    net.fail_family = "ipv6"
    with pytest.raises(RuntimeError, match="Прежние DNS восстановлены"):
        dns.enable()
    assert net.snapshot() == before
    assert not dns.journal.exists()


def test_do_not_clobber_changes_made_by_user(tmp_path):
    dns, net = manager(tmp_path)
    dns.enable()
    net.adapters[0]["ipv4"] = {"auto": False, "servers": ["9.9.9.9"], "configured": ["9.9.9.9"]}
    before = len(net.calls)
    with pytest.raises(RuntimeError, match="другой программой"):
        dns.disable()
    assert len(net.calls) == before
    assert dns.journal.exists()
    assert dns.status()["state"] == "changed"


def test_changed_network_cannot_overwrite_original_backup(tmp_path):
    dns, net = manager(tmp_path)
    dns.enable()
    saved = dns.journal.read_bytes()
    net.adapters[0]["network"] = "New network"
    with pytest.raises(RuntimeError, match="предыдущего"):
        dns.enable()
    assert dns.journal.read_bytes() == saved
    dns.disable()


def test_restore_uses_guid_after_windows_reassigns_interface_index(tmp_path):
    dns, net = manager(tmp_path)
    dns.enable()
    net.adapters[0]["index"] = 42
    net.adapters.append(adapter("22222222-2222-2222-2222-222222222222", index=21))
    dns.disable()
    assert net.calls[-1][0] == net.original["guid"]
    assert net.adapters[1]["ipv4"]["auto"]


def test_failed_restoration_keeps_journal_for_retry(tmp_path):
    dns, net = manager(tmp_path)
    dns.enable()
    net.fail_family = "ipv4"
    with pytest.raises(RuntimeError):
        dns.disable()
    assert dns.journal.exists()
    dns.disable()
    assert not dns.journal.exists()


def test_repeated_enable_keeps_first_backup(tmp_path):
    dns, net = manager(tmp_path)
    dns.enable()
    saved = dns.journal.read_bytes()
    dns.enable()
    assert dns.journal.read_bytes() == saved
    assert len(net.calls) == 1


def test_choose_connected_physical_route_with_lowest_metric():
    slow, fast, disconnected = adapter(index=10), adapter(index=11), adapter(index=12)
    slow["metric"] = 100
    fast["metric"] = 20
    disconnected.update(metric=1, active=False)
    assert choose_adapter({"adapters": [slow, disconnected, fast]})["index"] == 11
    with pytest.raises(RuntimeError, match="Нет активного"):
        choose_adapter({"adapters": [disconnected]})


def test_invalid_backup_is_not_replaced(tmp_path):
    dns, net = manager(tmp_path)
    dns.journal.write_text('{"schema": 9}', encoding="utf-8")
    with pytest.raises(RuntimeError, match="повреждена"):
        dns.enable()
    assert not net.calls
    assert json.loads(dns.journal.read_text())["schema"] == 9


def test_powershell_mutation_is_bound_to_guid_and_family(monkeypatch):
    runner = Mock()
    monkeypatch.setattr("src.gemini_dns.powershell", runner)
    backend = WindowsDns()
    backend.set_servers(adapter()["guid"], "ipv4", None)
    script = runner.call_args.args[0]
    assert "Get-NetAdapter -Physical" in script
    assert "-AddressFamily ipv4" in script
    assert "-ResetServerAddresses" in script
    with pytest.raises(ValueError):
        backend.set_servers(adapter()["guid"], "ipv4", ["1.1.1.1'; exit"])


def test_http_forbidden_only_marks_site_reachable_not_gemini_account():
    # A 403 proves the endpoint answered, not that a Google account can use Gemini.
    from src.dashboard import Dashboard
    from unittest.mock import patch
    from src.gemini_availability import GeminiNetworkStatus, RegionSupport
    fake = Mock(_gemini_probe_running=False)
    fake.gemini_availability_client.check.return_value = GeminiNetworkStatus(
        network_available=True, website_reachable=True, website_http_status=403,
        account_status="UNKNOWN", region_supported=RegionSupport.UNKNOWN,
    )
    fake._format_gemini_availability = Dashboard._format_gemini_availability
    with patch("src.dashboard.threading.Thread") as thread:
        Dashboard._check_gemini_now(fake)
        thread.call_args.kwargs["target"]()
    assert "сайт: ✓ (HTTP 403)" in fake.gemini_site_status
    assert "Google Account: ? не проверен" in fake.gemini_site_status


def test_elevation_launcher_collects_helper_result(tmp_path, monkeypatch):
    from src import gemini_dns
    from win32com.shell import shell
    import win32event
    import win32api
    monkeypatch.setattr(gemini_dns, "state_dir", lambda: tmp_path)

    def elevated(**kwargs):
        assert kwargs["lpVerb"] == "runas"
        assert "--gemini-dns-helper enable" in kwargs["lpParameters"]
        assert kwargs["fMask"] & 0x100
        # The result filename is a generated, per-operation UUID.
        import re
        filename = re.search(r"[0-9a-f]{32}\.json", kwargs["lpParameters"]).group()
        (tmp_path / filename).write_text(json.dumps({"ok": True, "result": {"state": "on"}}))
        return {"hProcess": 42}

    monkeypatch.setattr(shell, "ShellExecuteEx", elevated)
    monkeypatch.setattr(win32event, "WaitForSingleObject", Mock(return_value=0))
    monkeypatch.setattr(win32api, "CloseHandle", Mock())
    assert gemini_dns.apply_action("enable") == {"state": "on"}
    assert not list(tmp_path.glob("*.json"))
