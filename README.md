# Happ Suite

Windows tray controls for Mihomo VPN, Antigravity and Gemini DNS.

## Quick start

1. Run `start.exe` and open **Настройки и установка**.
2. Select **Install Mihomo**. The pinned Windows compatible release is checked by SHA-256 before installation.
3. Paste your HTTPS subscription URL and select **Save**. Suite requests it as `mihomo/HappSuite-<version>`, sends a stable random installation HWID, validates the returned Mihomo YAML, and shows the discovered nodes. A browser page response triggers the documented `/mihomo` format endpoint before Suite reports an error.
4. Select a node if needed, then use the VPN card to connect. Suite builds a local full-tunnel configuration from the fetched profile and confirms the authenticated local API, TUN route, and HTTPS connectivity.
5. Use **Update nodes** to fetch the latest profile and **Select** to choose a node for the next or current run.

Suite accepts a Mihomo YAML profile and does not convert Xray or other subscription formats. There is no `DIRECT` fallback. It never starts a second TUN when another Mihomo or HAPP tunnel is detected, and it stops Mihomo only when the saved process identity still matches the Suite-launched process.

## HAPP compatibility

The default backend is Mihomo. To use an existing HAPP installation, set `vpn_backend` to `happ` in `%LOCALAPPDATA%\HappSuite\local.json`. Suite runs only the selected backend. HAPP installation remains optional and is available from Settings in the setup package.

## Runtime data

Mihomo, the validated subscription profile, generated configuration, subscription URL (DPAPI protected), random installation HWID, and process ownership records are stored under `%LOCALAPPDATA%\HappSuite\vpn\mihomo`. The subscription URL is fetched by Suite and is never embedded in the Mihomo configuration or written to logs.

## Validation

Unit tests mock provider responses and do not change routes or start a live TUN. A real connection still requires an available provider response, an administrator-approved TUN start, and no already-running external tunnel.

The desktop build uses Windows per-monitor DPI awareness and a scalable Happ Suite mark for the window and executable icon.

See [docs/MIHOMO.md](docs/MIHOMO.md) for implementation details and current limits.
