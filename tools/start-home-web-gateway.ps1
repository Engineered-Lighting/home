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

Set-Location $repo
& $node $server
