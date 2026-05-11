# Troubleshooting

Failures we've hit while building this and how we fixed them. If you
hit something new, please open an issue.

## Stack — bringing things up

### vLLM won't start on Blackwell (sm_120)
Symptom: container restart-loops; logs show `no kernel image is
available for execution on the device` or PyTorch CUDA capability
errors.

Cause: the vanilla `vllm/vllm-openai:latest` image bundles CUDA 12.4
binaries that don't include sm_120 (Blackwell) kernels.

Fix: rebuild with cu128 + Torch 2.7. Until upstream catches up, we
maintain a custom Dockerfile in
`stack/services/vllm-blackwell/Dockerfile`. Swap the image reference
in compose:
```yaml
  vllm:
    image: home-ai-voice/vllm-blackwell:local
    build:
      context: ./services/vllm-blackwell
```

### Kokoro can't load voices on Blackwell
Same root cause as vLLM. We ship a rebuilt Kokoro image at
`stack/services/kokoro-fastapi/Dockerfile` with cu128 + Torch 2.7.

### vLLM tool-calls return empty `tool_calls: []`
Symptom: HA's intent classification works but no device actions fire.

Cause: vLLM defaulted to the `hermes` tool-call parser, which doesn't
match Qwen 3's output format.

Fix: set `--tool-call-parser qwen3_xml` in the vLLM command (already in
the compose).

### Tailscale subnet route hijacks LAN
Symptom: HA loses contact with the AI box on the same LAN after
enabling Tailscale's subnet routing.

Fix: install the `hav-lan-priority.service` systemd unit shipped with
`stack/`. It adds an `ip rule` that prefers the LAN route over the
Tailscale-advertised one for the `192.168.0.0/24` subnet. Verify with
`ip rule list | grep lookup`.

### `stack.sh up` shows everything green but conversations time out
Likely the LAN-priority rule got blown away by a reboot. Run:
```bash
sudo systemctl restart hav-lan-priority
```

## Home Assistant

### HA returns 502 / hangs on `homeassistant.restart`
Symptom: applying a custom_components change and trying to restart via
the UI just spins forever.

Cause: pipeline state in-flight, restart enqueues but never fires.

Fix: restart via the supervisor API instead of the homeassistant
service:
```bash
ssh -p 22222 root@<ha-host> \
  'curl -X POST http://supervisor/core/stop  -H "Authorization: Bearer $SUPERVISOR_TOKEN"; \
   sleep 3; \
   curl -X POST http://supervisor/core/start -H "Authorization: Bearer $SUPERVISOR_TOKEN"'
```

### Voice PE doesn't keep listening after a reply
By default HA closes the mic after each turn. To enable always-on
follow-up, see [RUNBOOK.md → Always-on
follow-up](RUNBOOK.md#always-on-follow-up).

### "auth_invalid" in the Home desktop app
The Long-Lived Access Token was rejected. Generate a new one (Profile
→ Security → LLATs) and paste it via `/token <new-token>` or by
wiping `localStorage` and re-running FirstRun.

## The Home desktop app

### SmartScreen warning on first install
Expected. We don't have an EV code-signing cert. Click `More info →
Run anyway`. The MSI is verified by hash in the GitHub release notes.

### "Couldn't reach that home assistant"
Cause: the URL is wrong, HA is down, or there's a firewall blocking
port 8123.

Verify:
```powershell
curl http://<ha-host>:8123/api/  # should return JSON with version info
```
If that fails, the problem is upstream of the desktop app.

### Connected, but device actions never fire
Cause: HA's agent isn't your default. Check Settings → Voice Assistants
→ which assistant is the default. The Extended OpenAI Conversation
agent needs to be the one driving the pipeline you're calling.

### Metrics tray shows `0%` everywhere forever
Cause: the metrics-sidecar isn't reachable or is itself unhealthy.

Verify:
```powershell
curl http://<ai-box>:8092/metrics
```
If that fails, `ssh hav-ubuntu 'bash /opt/home/stack/scripts/stack.sh
status'` and look for `metrics-sidecar /healthz`.

Override the sidecar URL in the app:
```
/metrics http://<correct-host>:8092
```

### Window-snap behavior on Windows feels off
Tauri 2 is frameless by default. We use `data-tauri-drag-region` on the
header for drag, plus a minimize + close button cluster on the right.
If aero-snap (Win + Arrow keys) misbehaves, file an issue — Tauri 2.x
window-management is still maturing.

### App opens but instantly closes on first launch
Most often the WebView2 runtime is missing. Windows 11 ships with it;
some older Windows 10 installs may not. Install from
[https://developer.microsoft.com/microsoft-edge/webview2/](https://developer.microsoft.com/microsoft-edge/webview2/).

## Performance

### Streaming feels chunky / janky
HA's `intent-progress` events arrive every ~50–100 ms depending on
the LLM. That's a 10–20 Hz update rate, which can look uneven for
short replies. Not fixable from our side without dropping back to
direct vLLM streaming and re-implementing HA's tool dispatch.

### TTFT > 2 s
Check `nvidia-smi`. If the GPU is idle, the cost is probably HA's
intent classification + tool definition serialization on big tool
lists. Trim the exposed entities/areas in HA's voice config or use a
smaller model.

### Metrics-sidecar VRAM_used_gb looks inflated
NVML reports total GPU memory in use by any process, not just vLLM.
If you also run a vision model (Ollama qwen3-vl), KV cache reservations
on vLLM, etc., they all add up.

## Asking for help

When opening an issue, include:
1. Output of `bash scripts/stack.sh status` from the AI box.
2. Last ~50 lines of the affected service's logs (`docker compose logs --tail 50 <service>`).
3. HA version (Settings → About).
4. Home desktop app version (`/about` in the app).
5. What you were doing when it broke.

That's enough context to triage 90% of failures.
