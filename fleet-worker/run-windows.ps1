# Windows PC peer worker
# Copy .env.peer.example → .env and fill JARVIS_FLEET_TOKEN, then:
#   powershell -ExecutionPolicy Bypass -File .\run-windows.ps1

$ErrorActionPreference = "Stop"
$Dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvFile = Join-Path $Dir ".env"
if (-not (Test-Path $EnvFile)) {
  Write-Error "Missing $EnvFile — copy .env.peer.example to .env and fill JARVIS_FLEET_TOKEN"
}
Get-Content $EnvFile | ForEach-Object {
  $line = $_.Trim()
  if (-not $line -or $line.StartsWith("#")) { return }
  $parts = $line.Split("=", 2)
  if ($parts.Length -eq 2) {
    [System.Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), "Process")
  }
}
if (-not $env:JARVIS_CORE_URL) { $env:JARVIS_CORE_URL = "http://192.168.178.159:8787" }
if (-not $env:JARVIS_FLEET_NODE_NAME) { $env:JARVIS_FLEET_NODE_NAME = "Windows PC" }
if (-not $env:JARVIS_FLEET_NODE_ROLE) { $env:JARVIS_FLEET_NODE_ROLE = "tester" }
if (-not $env:JARVIS_FLEET_CAPABILITIES) { $env:JARVIS_FLEET_CAPABILITIES = "tester,shell,gpu,dual_monitor" }
if (-not $env:JARVIS_FLEET_TOKEN) { Write-Error "JARVIS_FLEET_TOKEN is empty in .env" }

python (Join-Path $Dir "worker.py") --tags test,gpu,shell,general @args
