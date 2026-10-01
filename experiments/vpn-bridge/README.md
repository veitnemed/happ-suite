# VPN Recovery Bridge

`run-cycle.ps1` saves a read-only snapshot and starts two separate current-user Task Scheduler jobs via `pythonw.exe`: worker and watchdog. They continue after the launcher exits and write JSON/JSONL under `%LOCALAPPDATA%\HappSuite\vpn-bridge\<run_id>\`. The default mode is a mock-only dry-run.

```powershell
.\run-cycle.ps1 -Scenario normal
.\run-cycle.ps1 -Scenario hang
.\status.ps1 -RunId <run_id>
.\remove-cycle.ps1 -RunId <run_id>
```

The normal dry-run changes only `mock-network.json`: connected → disconnected → wait 5 seconds → connected. The hang run exits the worker after mock-disconnect; the independent watchdog observes that the worker process is dead and restores the mock connection. Both roles record PID and Windows session ID. The `remove-cycle.ps1` script deletes only the completed scheduled tasks and keeps the journal.

The earlier `run-dry-run.ps1` and its worker/watchdog scripts remain as a separate read-only scheduler check:

```powershell
.\run-dry-run.ps1 -Scenario normal
.\run-dry-run.ps1 -Scenario hang
```

Each older command returns a run ID immediately. Its normal worker writes 12 heartbeats. Its hang scenario calls `restore.ps1`, which writes a mock receipt. None of these dry-run commands changes HAPP, adapters, routes, DNS, or services.

```powershell
.\status.ps1 -RunId <run_id>
.\remove-dry-run.ps1 -RunId <run_id>
```

`remove-dry-run.ps1` removes only the two completed tasks for that run and keeps the journal. The local journal is under `%LOCALAPPDATA%\HappSuite\vpn-bridge\<run_id>\`. It contains `snapshot.json`, `run.json`, worker and watchdog state, `recovery.json` on timeout, and JSONL events. The snapshot includes HAPP process/service observations, a remembered server preference, adapters, IPv4/IPv6 routes, DNS, and an external connectivity probe. It excludes process command lines, subscription data, and keys. A remembered server preference is **not** proof of the active profile.

## Live test gate

The live worker uses the same existing-GUI IPC controller as F8. It sends `happ://disconnect`, waits until Windows no longer routes through `happ-xray`, holds that state for 5 seconds, then sends `happ://connect` and requires the HAPP route plus direct external HTTPS. The watchdog starts first. If the worker dies or stalls, it independently repeats the connect path, up to three attempts, and records the result. Neither task closes HAPP or changes DNS/adapter settings directly.

`happ://connect` uses HAPP's remembered `lastServer`/`lastSubscription` selection. Those preferences do not prove which profile is currently active, so live mode requires the operator to confirm the profile name visible in HAPP before launch. It also checks the selected IDs, GUI PID, session, working route, external HTTPS, and watchdog readiness. **No live test has been run yet.** The first real disconnect requires separate user approval.

After that approval, the single live test uses:

```powershell
.\run-cycle.ps1 -Live -ConfirmNetworkInterruption -ConfirmedProfileName 'AUTO [Beta]'
```

The independent job cannot keep a remote chat connection alive during the outage. It can finish locally and leave a journal to inspect after reconnection. If the HAPP GUI process itself exits, this controller cannot relaunch it invisibly; use HAPP's own interface for manual recovery in that case.
