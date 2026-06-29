# New Chat Bootstrap Prompt

Private context for trusted coding agents only. This file is the prompt to paste
at the start of a new Codex or Claude chat when you want the agent to understand
the Home app, remote workflows, deployment path, and travel debugging setup.

If this file and the live repo disagree, the live repo and current branch state
win.

## Pasteable Bootstrap Prompt

````text
PRIVATE CONTEXT FOR TRUSTED CODING AGENTS ONLY.

You are helping me work on my private Home app repo, not my public landing-page/website repo.

Primary repo:
- Windows dev checkout: C:\Claude\home
- GitHub repo: Engineered-Lighting/home
- This is separate from the Engineered Lighting landing page repo.

First, ground yourself in the actual repo state:
1. Run `git status --short --branch`.
2. Inspect the current branch and recent diff before assuming features/docs are merged.
3. Use `rg` for search.
4. Do not revert, overwrite, stage, commit, push, restart services, SSH-mutate, or deploy unless I explicitly ask.
5. Read-only diagnostics are okay when relevant: `git status`, `rg`, tests/checks, `curl /healthz`, `systemctl status`, `journalctl`, and non-mutating SSH status/log commands.
6. If you cannot access the repo or machine, ask me for file contents or command output instead of inventing details.

Doc routing:
- General orientation -> `docs/HOME_SYSTEM_OVERVIEW.md`
- Tauri desktop remote access -> `docs/TAURI-REMOTE.md`
- Browser/Tailscale web gateway -> `docs/TAILSCALE-WEB.md`
- Travel readiness / recovery -> `docs/TRAVEL-READINESS.md`
- Stack supervisor / AI stack ops -> `docs/RUNBOOK.md`
- Architecture details -> `docs/ARCHITECTURE.md`

System architecture:
- Windows machine/laptop: my development workstation for Codex/Claude, local edits, Tauri testing, GitHub, SSH, and remote debugging.
- Ubuntu AI box: `engineeredlightingserver1`, full tailnet DNS `engineeredlightingserver1.taild52a15.ts.net`, observed Tailscale IP `100.87.94.18`, LAN `192.168.0.100`.
  - Hosts RTX 6000 Blackwell local AI/model stack.
  - Hosts browser web gateway.
  - Hosts Apartment 3D runtime assets.
  - Runs deploy/recovery services.
- Home Assistant machine: `homeassistant`, full tailnet DNS `homeassistant.taild52a15.ts.net`, observed Tailscale IP `100.116.3.41`, LAN `192.168.0.125`.
  - Runs Home Assistant on `:8123`.
  - Runs Frigate on `:5000`.

Remote access model:
- Tailscale is the private access boundary.
- No public DNS, router forwarding, or Tailscale Funnel unless I explicitly ask.
- `engineered.lighting` is my public website/domain and is not the private Home app access path.
- Browser access uses the web gateway on Ubuntu plus Tailscale Serve.
- Installed Tauri desktop access talks directly to LAN/Tailscale service URLs through app service profiles.

Tauri app workflow:
- For travel, use profile `Remote via Tailscale`.
- Useful in-app commands:
  - `/profile status`
  - `/profile tailscale`
  - `/remote check`
  - `/travel check`
  - `/travel status`
  - `/travel recovery`
  - `/travel bundle`
  - `/debug bundle`
- Tauri should access local AI and Home Assistant directly over Tailscale, not through the web gateway.

Browser web workflow:
- Gateway: `web-gateway/server.mjs`
- Serves `app/src`.
- Uses same-origin proxy routes like `/proxy/ha`, `/proxy/metrics`, `/proxy/vllm`.
- Runs on Ubuntu as systemd service `home-web-gateway`.
- Default bind: `127.0.0.1:5181`.
- Tailscale Serve exposes it privately.
- Gateway health: `/healthz`.

Ubuntu AI service ports:
- vLLM `:8000`
- Vision `:8091`
- Metrics `:8092`
- Stack supervisor `:8093`
- S2S bridge `:8094`
- Intelligence `:8095`
- Tracker `:8098`
- Video labeler `:8099`
- Apartment assets `:5190`

Important caveats:
- Supervisor `:8093` is sensitive stack-control surface. If it is down, most of the app can still work, but remote stack control is degraded.
- Supervisor should be protected by Tailscale plus `STACK_TOKEN`.
- Do not add arbitrary remote shell execution to the Home app UI.
- Apartment scan/mesh assets live outside Git under `app/data/apartment`; they are served by `home-apartment-assets` on Ubuntu and are not bundled into desktop installers.
- For HA-side changes, distinguish repo config from live HA config. Prefer snapshot/backup before risky HA changes. Avoid HAOS/Tailscale/NVIDIA/driver/major stack updates right before travel.

Deploy workflow:
- Source of truth is GitHub.
- Normal flow: branch -> PR -> merge to `main`.
- Browser web deploy is via manual GitHub Actions workflow `deploy home web`.
- The self-hosted runner is deploy-only and should run trusted `main`, not arbitrary PR code.
- Manual fallback on Ubuntu:
  ```bash
  cd ~/code/home
  git pull --ff-only
  npm run web:check
  tools/check-home-web-assets.sh
  sudo systemctl restart home-web-gateway
  ```
- `tools/deploy-home-web.sh` records the previous commit and auto-rolls back if checks or restart fail.
- Prefer rollback to a previous known-good commit over ad hoc live edits on Ubuntu.

Travel readiness:
- Windows:
  ```powershell
  cd C:\Claude\home
  .\tools\travel-readiness.ps1
  ```
- Ubuntu:
  ```bash
  cd ~/code/home
  tools/travel-readiness.sh
  ```
- Tauri:
  ```text
  /profile tailscale
  /travel check
  ```
- Treat blocker failures as must-fix before travel. Degraded failures are acceptable only if I explicitly accept losing that feature.

Recovery path:
- Use SSH, systemd, GitHub Actions, and logs.
- The app diagnoses and copies recovery commands; it should not execute shell commands on home machines.
- Common read-only checks:
  ```bash
  ssh hav-ubuntu 'tailscale status'
  ssh hav-ubuntu 'cd ~/code/home && tools/travel-readiness.sh'
  ssh hav-ubuntu 'sudo systemctl status home-web-gateway home-apartment-assets hav-stack-supervisor --no-pager'
  ssh hav-ubuntu 'journalctl -u home-web-gateway -n 100 --no-pager'
  ssh hav-ubuntu 'journalctl -u home-apartment-assets -n 100 --no-pager'
  ssh hav-ubuntu 'journalctl -u hav-stack-supervisor -n 100 --no-pager'
  ```

Validation expectations:
- For frontend/app changes:
  ```powershell
  npm run test:services
  node tools/check-jsx.js home-app.jsx
  npm run web:check
  ```
- For gateway/server script changes:
  ```powershell
  node --check web-gateway/server.mjs
  node --check tools/serve-apartment-assets.mjs
  ```
- For Bash scripts on Windows:
  ```powershell
  & 'C:\Program Files\Git\bin\bash.exe' -n tools/deploy-home-web.sh
  & 'C:\Program Files\Git\bin\bash.exe' -n tools/travel-readiness.sh
  ```
- For Tauri/release work, also inspect `app/src-tauri`, relevant Cargo files, package versions, and GitHub release workflows.
- If the repo has a change-note system, user-facing/deploy-relevant changes should include a `changes/unreleased/*.md` fragment unless intentionally marked `no-release-note`.
- Always mention what you could not test.

Security posture:
- Tailscale-only access is acceptable and preferred.
- Do not introduce public exposure without explicit approval.
- Do not print or commit HA tokens, `STACK_TOKEN`, camera credentials, HF tokens, OpenAI keys, or auth files.
- Secret scan before publishing remote/deploy changes if relevant.

How I like to work:
- Be proactive and implement when I ask for implementation.
- For reviews, be adversarial: lead with risks, bugs, missing tests, and operational failure modes.
- For plans, make them decision-complete.
- For remote work, assume I want to stay productive while traveling, with Ubuntu AI and Home Assistant reachable through Tailscale.
````

## Optional Task Add-Ons

Paste one of these after the bootstrap prompt when the task is specific.

```text
For a Tauri remote-access task:
Read `docs/TAURI-REMOTE.md` and inspect `app/src/home-services.js` before changing consumers. Preserve browser web mode `/proxy/...` behavior.
```

```text
For a browser web-gateway/deploy task:
Read `docs/TAILSCALE-WEB.md`, inspect `web-gateway/server.mjs`, `.github/workflows/deploy-home-web.yml`, and `tools/deploy-home-web.sh`. Keep deploy manual and trusted-main only.
```

```text
For travel/debugging:
Read `docs/TRAVEL-READINESS.md`. Prefer `/travel check`, `tools/travel-readiness.ps1`, `tools/travel-readiness.sh`, and read-only SSH/log checks before proposing fixes.
```

```text
For Home Assistant work:
Read `docs/RUNBOOK.md` and relevant `ha-config/` files. Treat live HA config as separate from repo config. Recommend snapshot/backup before risky changes.
```

```text
For desktop release work:
Inspect `app/src-tauri`, package/Cargo versions, release workflows, and any change-note policy. Do not assume web deploy and Tauri release are the same pipeline.
```

## Redacted Version Guidance

For non-trusted chats, remove:

- LAN IPs
- Tailscale IPs
- Tailnet DNS suffix
- Host aliases
- Repo owner/name if unnecessary
- Service ports that are not relevant to the question

Keep only the conceptual architecture: Windows dev machine, Ubuntu AI box, Home
Assistant box, Tailscale-only private access, browser gateway vs direct Tauri
profiles.

## Assumptions

- This prompt is for trusted coding/debugging agents.
- Repo docs and current branch state beat this prompt if they disagree.
- Current travel architecture remains Tailscale-only, Ubuntu-hosted for
  web/backend, and direct-Tailscale for installed Tauri.
