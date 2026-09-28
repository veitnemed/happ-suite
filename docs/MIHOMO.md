# Mihomo VPN backend

Happ Suite now selects Mihomo by default. Set `vpn_backend` to `happ` in the
per-user `%LOCALAPPDATA%\HappSuite\local.json` file to use the retained HAPP
adapter. Changing backend is explicit; Suite never starts both tunnel engines.

The pinned Windows compatible Mihomo archive is downloaded on first connection
or from the dashboard installer button. Suite verifies the archive SHA-256,
stores the executable under `%LOCALAPPDATA%\HappSuite\vpn\mihomo`, verifies
its installed hash and version, and asks Mihomo to validate the generated
configuration before replacing the active config.

Paste the HTTPS subscription URL in the dashboard. `SubscriptionClient`
requests it with a Mihomo user agent, YAML Accept header, persistent random
`x-hwid`, and Windows device headers. It checks Remnawave HWID response flags,
status, content type, expiry metadata, and the returned YAML. If the base URL
returns browser HTML, the client retries the documented `/mihomo` format
endpoint. It reports a controlled error if that response is still not Mihomo
YAML. The URL is DPAPI protected; the validated profile is cached in per-user
application data. The subscription URL is never sent to Mihomo or logged.

The generated configuration binds the authenticated REST controller to
`127.0.0.1`, routes through the `VPN` group, enables Mihomo TUN, and has no
`DIRECT` fallback. Nodes are fetched and validated by Suite, then selected
from the dashboard. Automatic selection measures the group, avoids small
latency changes, and remembers the last selected good node.

Suite records the PID, process creation time, executable path and hash, and
config hash when it launches Mihomo. It only stops a process while that record
still matches. A pre-existing or unverifiable Mihomo process is external or
unknown and is never terminated. A per-user process lock prevents two Suite
instances from launching competing Mihomo TUNs.

Unit tests mock provider responses and do not create a TUN, alter routes, or
access Task Scheduler. A live connection still requires an available provider
response, an administrator-approved TUN start, and no already-running external
tunnel.
