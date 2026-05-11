# Standalone HA pipeline probe. Confirms whether HA's assist_pipeline/run is
# actually wired to the LLM agent — independent of the Home desktop app.
#
# Usage:
#   $env:HA_URL = "http://192.168.0.125:8123"
#   $env:HA_TOKEN = "<your long-lived access token>"
#   pwsh .\tools\probe-pipeline.ps1
#
# Outputs each pipeline event as it arrives. If you see intent-progress
# chunks, streaming is wired. If you only see intent-end with empty speech,
# the pipeline's conversation agent is probably the built-in HA one rather
# than your LLM-backed agent.

param(
  [string] $HaUrl = $env:HA_URL,
  [string] $HaToken = $env:HA_TOKEN,
  [string] $Text = "Tell me a short story about a desk lamp in two sentences."
)

if (-not $HaUrl -or -not $HaToken) {
  Write-Error "Set `$env:HA_URL and `$env:HA_TOKEN first"
  exit 1
}

$wsUrl = ($HaUrl -replace '^http', 'ws').TrimEnd('/') + '/api/websocket'
Add-Type -AssemblyName System.Net.Http
$ws = [System.Net.WebSockets.ClientWebSocket]::new()
$cts = [System.Threading.CancellationTokenSource]::new(60000)
$ws.ConnectAsync([Uri]$wsUrl, $cts.Token).GetAwaiter().GetResult()
Write-Host "ws connected" -ForegroundColor DarkGray

$buf = [byte[]]::new(64 * 1024)
$seg = [System.ArraySegment[byte]]::new($buf)

function Recv {
  $r = $ws.ReceiveAsync($seg, $cts.Token).GetAwaiter().GetResult()
  return [System.Text.Encoding]::UTF8.GetString($buf, 0, $r.Count)
}
function Send($obj) {
  $j = $obj | ConvertTo-Json -Depth 6 -Compress
  $bytes = [System.Text.Encoding]::UTF8.GetBytes($j)
  $s = [System.ArraySegment[byte]]::new($bytes)
  $ws.SendAsync($s, [System.Net.WebSockets.WebSocketMessageType]::Text, $true, $cts.Token).GetAwaiter().GetResult()
}

$null = Recv  # auth_required
Send @{ type = 'auth'; access_token = $HaToken }
$ok = Recv | ConvertFrom-Json
if ($ok.type -ne 'auth_ok') { Write-Error "auth: $($ok | ConvertTo-Json -Compress)"; exit 1 }
Write-Host "authed · ha $($ok.ha_version)" -ForegroundColor DarkGray

Send @{
  id = 1
  type = 'assist_pipeline/run'
  start_stage = 'intent'
  end_stage = 'intent'
  input = @{ text = $Text }
}

$t0 = [DateTime]::UtcNow
$events = 0
while ($true) {
  try { $raw = Recv } catch { break }
  if (-not $raw) { break }
  $msg = $raw | ConvertFrom-Json
  $dt = [int]([DateTime]::UtcNow - $t0).TotalMilliseconds
  if ($msg.type -eq 'event') {
    $events++
    $etype = $msg.event.type
    if ($etype -eq 'intent-progress') {
      $delta = $msg.event.data.chat_log_delta.content
      Write-Host ("[{0,5}ms] intent-progress  '{1}'" -f $dt, $delta) -ForegroundColor Cyan
    } elseif ($etype -eq 'intent-end') {
      $speech = $msg.event.data.intent_output.response.speech.plain.speech
      Write-Host ("[{0,5}ms] intent-end  speech={1!r}" -f $dt, "'$speech'") -ForegroundColor Green
    } else {
      Write-Host ("[{0,5}ms] {1}" -f $dt, $etype) -ForegroundColor DarkGray
    }
  } elseif ($msg.type -eq 'result') {
    Write-Host ("[{0,5}ms] result success={1}" -f $dt, $msg.success)
    if (-not $msg.success) { Write-Host ($msg.error | ConvertTo-Json) -ForegroundColor Yellow }
    Write-Host "---"
    Write-Host "events received: $events"
    if ($events -eq 0) {
      Write-Host "no pipeline events. likely a conversation-agent / pipeline config issue." -ForegroundColor Yellow
    }
    break
  }
}
$ws.Dispose()
