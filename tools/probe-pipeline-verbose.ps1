# Verbose HA pipeline probe. Captures the FULL event JSON (depth 8) for every
# event, with special attention to intent-progress and intent-end (where the
# chat_log + tool turns live). Writes a transcript file alongside the live
# console summary.
#
# Why this exists: the Phase A gate of remind-me-why-we-peaceful-sonnet.md
# needs to confirm three things before any chat-side wiring lands:
#   A.1.a  Does intent-progress fire with non-empty chat_log_delta.content?
#   A.1.b  Do tool_calls appear mid-stream in chat_log_delta, or only in
#          intent-end's full chat_log?
#   A.1.c  For a visual question that triggers describe + look, does the LLM
#          interleave prose + tools, or bundle both tool_calls in one assistant
#          turn with a single trailing text turn?
#
# Usage:
#   $env:HA_URL   = "http://192.168.0.125:8123"
#   $env:HA_TOKEN = "<token>"
#   pwsh .\tools\probe-pipeline-verbose.ps1 -Text "what is on the kitchen counter right now?"

param(
  [string] $HaUrl    = $env:HA_URL,
  [string] $HaToken  = $env:HA_TOKEN,
  [string] $Text     = "what is on the kitchen counter right now?",
  [string] $OutDir   = (Join-Path $PSScriptRoot "..\tmp\probe-out"),
  [int]    $TimeoutS = 120
)

if (-not $HaUrl -or -not $HaToken) {
  Write-Error "Set `$env:HA_URL and `$env:HA_TOKEN first"
  exit 1
}

if (-not (Test-Path $OutDir)) {
  New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
}
$stamp     = (Get-Date -Format "yyyyMMdd-HHmmss")
$transPath = Join-Path $OutDir "probe-$stamp.jsonl"
Write-Host "writing transcript -> $transPath" -ForegroundColor DarkGray

$wsUrl = ($HaUrl -replace '^http', 'ws').TrimEnd('/') + '/api/websocket'
Add-Type -AssemblyName System.Net.Http
$ws  = [System.Net.WebSockets.ClientWebSocket]::new()
$cts = [System.Threading.CancellationTokenSource]::new($TimeoutS * 1000)
$ws.ConnectAsync([Uri]$wsUrl, $cts.Token).GetAwaiter().GetResult()
Write-Host "ws connected" -ForegroundColor DarkGray

# Robust Recv: WebSocket messages can fragment across multiple frames. The
# existing probe's single-shot ReceiveAsync drops the tail of large messages
# (intent-end's chat_log can easily exceed 64KB with multi-turn tool history).
function Recv {
  $sb = [System.Text.StringBuilder]::new()
  $buf = [byte[]]::new(64 * 1024)
  $seg = [System.ArraySegment[byte]]::new($buf)
  do {
    $r = $ws.ReceiveAsync($seg, $cts.Token).GetAwaiter().GetResult()
    if ($r.Count -gt 0) {
      [void]$sb.Append([System.Text.Encoding]::UTF8.GetString($buf, 0, $r.Count))
    }
  } until ($r.EndOfMessage)
  return $sb.ToString()
}

function Send($obj) {
  $j = $obj | ConvertTo-Json -Depth 8 -Compress
  $bytes = [System.Text.Encoding]::UTF8.GetBytes($j)
  $s = [System.ArraySegment[byte]]::new($bytes)
  $ws.SendAsync($s, [System.Net.WebSockets.WebSocketMessageType]::Text, $true, $cts.Token).GetAwaiter().GetResult()
}

$null = Recv  # auth_required
Send @{ type = 'auth'; access_token = $HaToken }
$ok = Recv | ConvertFrom-Json
if ($ok.type -ne 'auth_ok') {
  Write-Error "auth: $($ok | ConvertTo-Json -Compress)"
  exit 1
}
Write-Host "authed | ha $($ok.ha_version)" -ForegroundColor DarkGray

# end_stage = 'intent' keeps us in text-only territory (no TTS). The pipeline
# is the user's preferred default (which they configured to Local AI).
Send @{
  id          = 1
  type        = 'assist_pipeline/run'
  start_stage = 'intent'
  end_stage   = 'intent'
  input       = @{ text = $Text }
}

$t0       = [DateTime]::UtcNow
$events   = 0
$progress = 0
$summary  = [ordered]@{
  text                  = $Text
  ha_version            = $ok.ha_version
  intent_progress_count = 0
  intent_progress_has_delta = $false
  intent_progress_has_toolcalls_midstream = $false
  intent_end_seen       = $false
}

Add-Content -Path $transPath -Value ("# probe @ $stamp text=" + ($Text -replace "`r?`n"," ") + " ha=$($ok.ha_version)") -Encoding utf8

while ($true) {
  try {
    $raw = Recv
  } catch {
    Write-Host "recv error: $($_.Exception.Message)" -ForegroundColor Red
    break
  }
  if (-not $raw) { break }
  $msg = $raw | ConvertFrom-Json
  $dt  = [int]([DateTime]::UtcNow - $t0).TotalMilliseconds

  # Persist every frame (depth 10 to capture nested chat_log + tool arguments).
  $rec = [ordered]@{
    t_ms = $dt
    msg  = $msg
  }
  Add-Content -Path $transPath -Value (($rec | ConvertTo-Json -Depth 10 -Compress)) -Encoding utf8

  if ($msg.type -eq 'event') {
    $events++
    $etype = $msg.event.type
    $data  = $msg.event.data

    if ($etype -eq 'intent-progress') {
      $progress++
      $summary.intent_progress_count = $progress
      $delta   = $data.chat_log_delta
      $content = $null
      $tc      = $null
      if ($null -ne $delta) {
        $content = $delta.content
        $tc      = $delta.tool_calls
      }
      # Also catch the raw-delta fallback (some integrations emit `data.delta`)
      $rawDelta = $data.delta
      if ($content -or $rawDelta) { $summary.intent_progress_has_delta = $true }
      if ($tc)                    { $summary.intent_progress_has_toolcalls_midstream = $true }
      $line = ("[{0,6}ms] intent-progress" -f $dt)
      if ($content)  { $line += "  content='" + $content + "'" }
      if ($rawDelta) { $line += "  raw_delta='" + $rawDelta + "'" }
      if ($tc)       { $line += "  tool_calls=" + (($tc | ConvertTo-Json -Compress -Depth 4)) }
      if (-not $content -and -not $rawDelta -and -not $tc) {
        $line += "  (no delta content) data=" + (($data | ConvertTo-Json -Compress -Depth 4))
      }
      Write-Host $line -ForegroundColor Cyan
    }
    elseif ($etype -eq 'intent-end') {
      $summary.intent_end_seen = $true
      $speech = $data.intent_output.response.speech.plain.speech
      Write-Host ("[{0,6}ms] intent-end  speech_len={1}" -f $dt, ($speech | Measure-Object -Character).Characters) -ForegroundColor Green
      # Always dump the chat_log explicitly for A.1.c analysis.
      $chatLog = $data.chat_log
      if ($null -ne $chatLog) {
        Write-Host "  chat_log turns:" -ForegroundColor Green
        $i = 0
        foreach ($turn in $chatLog) {
          $role = $turn.role
          $tname = $turn.tool_name
          $tlen  = if ($turn.content) { ($turn.content | Out-String).Length } else { 0 }
          $tcCount = if ($turn.tool_calls) { ($turn.tool_calls | Measure-Object).Count } else { 0 }
          $tag = "    [$i] role=$role"
          if ($tname) { $tag += " tool_name=$tname" }
          if ($tcCount -gt 0) { $tag += " tool_calls=$tcCount" }
          if ($tlen -gt 0) { $tag += " content_len=$tlen" }
          Write-Host $tag -ForegroundColor DarkGreen
          if ($turn.tool_calls) {
            foreach ($call in $turn.tool_calls) {
              $cn = if ($call.tool_name) { $call.tool_name } else { $call.function.name }
              Write-Host ("        > tool_call name=$cn args=" + (($call.tool_args | ConvertTo-Json -Compress -Depth 4))) -ForegroundColor DarkGreen
            }
          }
          if ($turn.role -eq 'tool_result') {
            $contStr = "$($turn.content)"
            if ($contStr.Length -gt 240) { $contStr = $contStr.Substring(0,240) + "..." }
            Write-Host ("        < tool_result content: " + $contStr) -ForegroundColor DarkGreen
          }
          if ($turn.role -eq 'assistant' -and $turn.content) {
            $contStr = "$($turn.content)"
            if ($contStr.Length -gt 240) { $contStr = $contStr.Substring(0,240) + "..." }
            Write-Host ("        ! assistant content: " + $contStr) -ForegroundColor DarkGreen
          }
          $i++
        }
      } else {
        Write-Host "  chat_log: <not present in event.data>" -ForegroundColor Yellow
      }
    }
    else {
      Write-Host ("[{0,6}ms] {1}" -f $dt, $etype) -ForegroundColor DarkGray
    }
  }
  elseif ($msg.type -eq 'result') {
    # `result` is just the WS-command ack. Pipeline events follow on the same
    # subscription. Keep listening until we see a `run-end` event or timeout.
    Write-Host ("[{0,6}ms] result success={1}  (ack -- continuing to listen for events)" -f $dt, $msg.success) -ForegroundColor DarkGray
    if (-not $msg.success) {
      Write-Host ($msg.error | ConvertTo-Json) -ForegroundColor Yellow
      break
    }
    continue
  }
  # Break once the pipeline emits its run-end event (or stt-end if end_stage='intent' gates the run earlier).
  if ($msg.type -eq 'event' -and ($msg.event.type -eq 'run-end' -or $msg.event.type -eq 'error')) {
    Write-Host ("[{0,6}ms] {1} done" -f $dt, $msg.event.type) -ForegroundColor DarkGray
    break
  }
}
$ws.Dispose()

Write-Host "---"
Write-Host ("events: $events | intent-progress: $progress | intent-end: " + $summary.intent_end_seen)
Write-Host ("A.1.a intent_progress_has_delta            = " + $summary.intent_progress_has_delta) -ForegroundColor Yellow
Write-Host ("A.1.b intent_progress_has_toolcalls_midstr = " + $summary.intent_progress_has_toolcalls_midstream) -ForegroundColor Yellow
Write-Host ("transcript saved: $transPath") -ForegroundColor DarkGray
