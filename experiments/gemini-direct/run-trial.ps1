param(
    [switch]$Activate,
    [switch]$ConfirmNetworkInterruption
)

$ErrorActionPreference = 'Stop'
$profilePath = Join-Path $PSScriptRoot 'profile.json'
$profile = Get-Content -LiteralPath $profilePath -Raw | ConvertFrom-Json
$routingPath = Join-Path $env:LOCALAPPDATA 'Happ\routing.json'
$routing = Get-Content -LiteralPath $routingPath -Raw | ConvertFrom-Json
$dnsInterfaces = @(Get-DnsClientServerAddress -AddressFamily IPv4 | Where-Object {
    $_.InterfaceAlias -ne 'happ-xray' -and $_.ServerAddresses.Count -gt 0 -and
    $_.ServerAddresses[0] -eq $profile.DomesticDNSIP
})
$happ = @(Get-Process -Name 'Happ' -ErrorAction Stop)
$route = Find-NetRoute -RemoteIPAddress 1.1.1.1 | Select-Object -First 1

if ($profile.Name -ne 'HappSuite Gemini Direct' -or
    @($profile.DirectSites).Count -ne 1 -or
    $profile.DirectSites[0] -ne 'domain:gemini.google.com' -or
    $dnsInterfaces.Count -ne 1) {
    throw 'The trial profile no longer matches the current Xbox DNS on Wi-Fi.'
}
if ($happ.Count -ne 1 -or $route.InterfaceAlias -ne 'happ-xray') {
    throw 'Exactly one running HAPP and its working TUN route are required.'
}
if ($routing.activeRoutingName) {
    throw 'A global HAPP routing profile is already active; this rollback would not preserve it.'
}

if (-not $Activate) {
    [ordered]@{
        mode = 'PREVIEW_ONLY'
        profile_name = $profile.Name
        direct_sites = $profile.DirectSites
        xbox_dns = $profile.DomesticDNSIP
        original_global_routing = $routing.activeRoutingName
        happ_route = $route.InterfaceAlias
        network_changed = $false
    } | ConvertTo-Json
    return
}
if (-not $ConfirmNetworkInterruption) {
    throw 'Live activation requires separate confirmation of a possible HAPP interruption.'
}

$probe = & curl.exe -sS --max-time 5 --output NUL --write-out '%{http_code}' https://www.google.com/generate_204 2>$null
if ($LASTEXITCODE -ne 0 -or $probe -ne '204') {
    throw 'Normal HTTPS through HAPP failed before the routing trial.'
}
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$pythonExe = (& py -3 -c 'import sys; print(sys.executable)').Trim()
$pythonwExe = Join-Path (Split-Path -Parent $pythonExe) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonwExe)) { throw 'pythonw.exe is unavailable.' }

$runId = [guid]::NewGuid().ToString('N')
$runDir = Join-Path (Join-Path $env:LOCALAPPDATA 'HappSuite\gemini-route') $runId
New-Item -ItemType Directory -Path $runDir -Force | Out-Null
Copy-Item -LiteralPath $routingPath -Destination (Join-Path $runDir 'routing-before.json')
$workerName = "HappSuite-GeminiRoute-Worker-$runId"
$watchdogName = "HappSuite-GeminiRoute-Watchdog-$runId"
$runner = Join-Path $PSScriptRoot 'route_trial.py'
$manifest = [ordered]@{
    run_id = $runId
    authorized = $true
    profile_name = $profile.Name
    xbox_dns = $profile.DomesticDNSIP
    happ_pid = $happ[0].Id
    happ_exe = $happ[0].Path
    worker_task = $workerName
    watchdog_task = $watchdogName
    started_utc = [datetime]::UtcNow.ToString('o')
}
$manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runDir 'run.json') -Encoding utf8

$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 3) -MultipleInstances IgnoreNew
$trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddHours(6))
$workerAction = New-ScheduledTaskAction -Execute $pythonwExe -Argument "`"$runner`" worker $runId" -WorkingDirectory $root
$watchdogAction = New-ScheduledTaskAction -Execute $pythonwExe -Argument "`"$runner`" watchdog $runId" -WorkingDirectory $root

Register-ScheduledTask -TaskName $watchdogName -Action $watchdogAction -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
Register-ScheduledTask -TaskName $workerName -Action $workerAction -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
Start-ScheduledTask -TaskName $watchdogName
$ready = $false
for ($i = 0; $i -lt 40; $i++) {
    $path = Join-Path $runDir 'watchdog.json'
    if (Test-Path -LiteralPath $path) {
        $state = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
        if ($state.status -eq 'ready' -and (Get-ScheduledTask -TaskName $watchdogName).State -eq 'Running') {
            $ready = $true
            break
        }
    }
    Start-Sleep -Milliseconds 250
}
if (-not $ready) { throw 'Independent rollback watchdog did not signal readiness. Worker was not started.' }
Start-ScheduledTask -TaskName $workerName
[ordered]@{ run_id = $runId; run_dir = $runDir; worker_task = $workerName; watchdog_task = $watchdogName } | ConvertTo-Json
