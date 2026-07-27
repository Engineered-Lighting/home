$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
$server = Join-Path $repo "web-gateway\server.mjs"
$node = "C:\Program Files\nodejs\node.exe"

if (-not (Test-Path -LiteralPath $node)) {
  $node = (Get-Command node -ErrorAction Stop).Source
}

$auth = [Environment]::GetEnvironmentVariable("HOME_WEB_BASIC_AUTH", "Process")
if (-not $auth) {
  $auth = [Environment]::GetEnvironmentVariable("HOME_WEB_BASIC_AUTH", "User")
}
if ($auth) {
  $env:HOME_WEB_BASIC_AUTH = $auth
}
if (-not $env:HOME_WEB_AUTH_REQUIRED) {
  $env:HOME_WEB_AUTH_REQUIRED = "1"
}
if (-not $env:HOME_WEB_ENABLE_LEGACY_HA_PROXY) {
  $env:HOME_WEB_ENABLE_LEGACY_HA_PROXY = "0"
}
if (-not $env:HOME_WEB_ENABLE_LEGACY_VISION_PROXY) {
  $env:HOME_WEB_ENABLE_LEGACY_VISION_PROXY = "0"
}

Set-Location $repo
& $node $server
