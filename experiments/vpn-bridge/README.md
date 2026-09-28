# VPN Recovery Bridge: dry-run

The launcher saves a **read-only snapshot** and starts separate current-user Task Scheduler jobs for the worker and watchdog. The jobs run through `wscript.exe` with a hidden PowerShell child and continue after the launching shell exits. No command here disconnects HAPP or changes adapters, routes, DNS, or services.

```powershell
.\run-dry-run.ps1 -Scenario normal
.\run-dry-run.ps1 -Scenario hang
```

Each command returns a run ID immediately. The normal worker writes 12 heartbeats and finishes. In the hang scenario the worker stops heartbeats; the watchdog calls the separate `restore.ps1` script. That script writes `restore-receipt.json` with `action: mock_restore_previous_happ_profile`. This is a real dispatch of a **mock** action, not a VPN reconnect.

```powershell
.\status.ps1 -RunId <run_id>
.\remove-dry-run.ps1 -RunId <run_id>
```

`remove-dry-run.ps1` removes only the two completed tasks for that run and keeps the journal. The local journal is under `%LOCALAPPDATA%\HappSuite\vpn-bridge\<run_id>\`. It contains `snapshot.json`, `run.json`, worker and watchdog state, `recovery.json` on timeout, and JSONL events. The snapshot includes HAPP process/service observations, a remembered server preference, adapters, IPv4/IPv6 routes, DNS, and an external connectivity probe. It excludes process command lines, subscription data, and keys. A remembered server preference is **not** proof of the active profile.

## Live test gate

The snapshot also records the HAPP `lastServer`, `lastSubscription`, and `lastServerGuid` preference values used by its reconnect path. These are remembered identifiers, not confirmation of the active tunnel; the bridge therefore still has no live restore command.

The bridge has no live HAPP disconnect/reconnect backend yet. The installed HAPP IPC must be understood and its restoration path checked before adding live mode. Then a separate confirmation is required before the first real VPN disconnect. The independent worker cannot preserve a Codex chat session while the network is down; it can only finish locally and leave a journal for the resumed session.

For a manual emergency recovery, use the existing HAPP user interface to reconnect the previously working profile. If the old profile is unknown, inspect HAPP itself; `last_server_preference` in the snapshot may be stale. Do not treat the mock receipt as evidence that the VPN was restored.
