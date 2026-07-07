# Changelog

All notable changes to this project will be documented here. Format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning is [SemVer](https://semver.org/spec/v2.0.0.html).

## v0.1.1 - 2026-07-07

### Added

- **Apartment background prewarm** (web) - Warms Apartment 3D modules and scan assets in the background after the Home web app boots, prefers the full-quality Apartment photo splat on mobile, and quietly parses photo/mesh after the cloud view is ready so mode switches feel faster while traveling.
- **Add apartment header button** (web) - The Home app header now includes a dedicated, text-only Apartment button so the 3D apartment view can be opened directly without typing `/apartment`, while the mobile header keeps lower-priority connection profile controls inside the menu.
- **Add desktop release and change-note automation** (desktop) - Added repo-native change-note fragments plus manual GitHub workflows for preparing and tagging reviewed Tauri desktop releases.
- **Add Home web favicon** (web) - The private Home web app now ships the monochrome Home favicon, touch icon, and web manifest so browser tabs and bookmarks render the intended mark.
- **Add Living Lights Travel Mode** (web) - Adds a Travel Mode lock to `/lights` and Home Assistant lighting packages so travel can block automatic light turn-ons, suppress return-home scenes, and force known lighting outputs off while away.
- **Mobile and desktop web audit gates** (web) - Adds deep mobile and desktop screenshot audit profiles, plus safer desktop hit targets, so mobile web improvements can be validated without regressing the desktop Home app layout.
- **Multi-month travel readiness runbooks** (docs) - Added Razer Blade departure checks and an emergency recovery runbook for multi-month remote access to the Home stack.
- **Add /healthz liveness endpoint to the home web gateway** (web) - The home web gateway now answers `GET` and `HEAD /healthz` before auth with a `no-store` JSON body reporting `status`, the running asset `commit`, and process `uptime`, so an external watchdog can poll liveness on a locked-down gateway without tripping the login flow.

### Changed

- **Animate Apartment camera fly-to** (web) - Apartment camera markers now animate through the 3D model into the camera pose before revealing the live feed, making the transition easier to understand and keeping the feed from covering the motion.
- **Desktop-width header action menu** (web) - The desktop-width Home header now uses the same compact hamburger action menu in both the browser web app and installed Tauri app, keeping the apartment shortcut visible while moving less frequent actions into the menu.
- **Shorter Home app tailnet URL** (web) - The private browser Home app now uses `home-app.taild52a15.ts.net` as the preferred Tailscale URL, while keeping the observed Tailscale IP fallback for remote diagnostics and desktop service resolution.
- **Speed up Home web static assets** (web) - The private Home web gateway now uses versioned caching for static app files, faster reuse for Apartment 3D assets, and a conservative service worker that avoids proxy/API/live home-state routes.
- **Harden mobile Apartment photo audit** (web) - Mobile and desktop screenshot audits now fail if Apartment photo mode falls back to an unavailable asset state instead of rendering the runtime scan.
- **Cleaner mobile web surfaces** (web) - Polished the mobile web app's command palette, help output, remote readiness dialog, action menu, and spatial drawer controls so dense utility surfaces fit better on phone screens without changing desktop behavior.
- **Move stale media warning into diagnostics** (web) - The mobile web status strip no longer shows the stale media warning persistently. Stale media details remain available inside the expanded infra/diagnostics tray.
- **Remove the default web gateway login screen** (web) - The Tailscale web gateway now opens the Home app directly by default and only enables the native gateway login when `HOME_WEB_AUTH_REQUIRED=1` is set.
- **Reconnect panels through the AI-box reboot window** (web) - The People, World State, and Explain panels now load over a shared retry-with-backoff helper: during an AI-box reboot they show a "Reconnecting…" banner and keep retrying instead of failing immediately, and after retries are exhausted they surface a persistent error with a "retry now" button.
- **Proxy stack token through the Home web gateway** (web) - The Tailscale web app can now use AI stack controls without storing `STACK_TOKEN` in the browser; the Ubuntu gateway injects it server-side for allowed supervisor API routes.
- **Precache the web app shell in the service worker** (web) - The web service worker now seeds the app shell and top-of-boot-chain modules into its per-deploy cache during `install` (best-effort, never blocking the worker takeover), so a mid-navigation box reboot is more likely to fall back to a warm shell instead of a blank load.

### Fixed

- **Improve Apartment calibrated camera overlays** (web) - Apartment camera snap views now use solved camera centers and calibrated projection matrices when calibration metadata is available, enrich incomplete Home Assistant apartment models from the tracker's live calibration cache, and clearly label uncalibrated camera snaps as estimated previews instead of exact overlays.
- **Apartment camera views fill letterbox with mesh** (web) - Calibrated Apartment camera snaps now keep the live camera feed uninterrupted and render the aligned 3D mesh in the surrounding letterbox space.
- **Fix Apartment camera snap feed alignment** (web) - Apartment camera snap views now wait for mesh mode, reveal live camera feeds reliably after the camera pose is held, and align the mobile video frame with the underlying mesh render.
- **Apartment camera feeds use HA signed streams** (web) - The Apartment camera snap view now prefers the same Home Assistant signed camera stream used by the fast vision tray, with Frigate kept as a fallback.
- **Apartment light state readback** (web) - Apartment light controls now verify Home Assistant state after a tap, and direct chat questions about which lights are on answer from a fresh Home Assistant state read instead of stale conversation context.
- **Apartment live camera feed verification** (web) - Add a real-media audit for the mobile Apartment camera overlay and start mobile camera snapshots from the Frigate `latest.jpg` URL immediately so slow mobile loads do not linger on a blank MJPEG connection.
- **Fix Apartment camera feed loading on mobile** (web) - Apartment camera snap mode now loads mobile Frigate snapshots sequentially instead of replacing the image on a fixed timer, so slow travel connections do not leave calibrated camera views stuck at connecting.
- **Mobile Apartment camera feed no longer shows gray canvas** (web) - Use the raw Frigate snapshot image for mobile Apartment camera snaps instead of the WebGL undistortion canvas that could paint a gray moving rectangle over the footage on iOS.
- **Apartment mobile camera feed stops gray flicker** (web) - Mobile Apartment camera snaps now avoid the iOS WebGL undistortion canvas and prefer the signed Home Assistant stream before falling back to Frigate snapshots, preventing gray bands during live camera playback.
- **Fix Apartment light controls on mobile** (web) - Apartment device cards now treat light and media controls as real mobile touch targets, prevent the 3D canvas from stealing taps, and show immediate state feedback while Home Assistant confirms the service call.
- **Speed up Apartment photo mode on mobile** (web) - Apartment photo mode now prefers a generated mobile splat asset on phone-sized viewports, deploys create that runtime asset from the full scan when needed, and the mobile screenshot audit fails if the UI remains stuck on loading photo.
- **Stabilize Apartment mobile camera overlays** (web) - Apartment camera snap views on mobile now use a cache-busted Frigate snapshot refresh path for calibrated overlays, preload each snapshot before swapping it onscreen, and start reachable camera media before slow mesh pose loading can leave the view black. The feed stays mapped into the same projection frame as the mesh when calibrated pose data is available. Apartment mesh mode also avoids the neon normal-material debug fallback on phones. If the full textured mesh cannot load and the app falls back to the coarse collision mesh, it now renders as a neutral clay fallback and reports the mesh source for diagnostics. Mobile screenshot audits now fail if mesh mode silently uses the coarse fallback during validation.
- **Fix Apartment mesh mode and add zoom controls** (web) - Apartment mesh mode now hides the photo/splat renderer cleanly when switching views, and the Apartment view includes mobile-friendly zoom in/out controls.
- **Harden mobile web boot loading** (web) - The web boot loader now aborts and retries stuck script-file requests so mobile browsers do not remain stranded on a boot-chain stalled screen.
- **Prevent duplicate chat messages** (web) - The Home app now treats repeated chat snapshots and accidental duplicate sends as one turn, preventing doubled user messages and doubled assistant responses.
- **Chat stop button cleanup** (web) - The chat input now clears completed assistant runs reliably so the STOP button does not block the next question after a response has already finished.
- **Collapse overlapping chat stream fragments** (web) - Improved chat streaming cleanup so camera answers do not render as repeated prefix/full/suffix fragments on mobile.
- **Fix light-mode command input contrast** (web) - Light mode now renders the slash-command menu and input controls with dedicated paper-theme surfaces instead of dark translucent glass, improving contrast on mobile.
- **Make Travel Mode toggle tappable after HA reloads** (web) - Kept the Lights Travel Mode button tappable when the browser has a stale HA state cache and verifies the helper after toggling.
- **Keep Travel Mode visible in Lights** (web) - Moved the Living Lights Travel Mode control into a fixed safety strip so it is visible immediately when the Lights drawer opens on mobile.
- **Mobile apartment overview fit** (web) - Keeps the full Apartment 3D model visible on mobile in cloud, photo, and mesh modes, with screenshot audit checks for cropped apartment bounds.
- **Prove mobile Apartment mesh with real assets** (web) - Mobile screenshot audits now serve runtime Apartment assets locally and fail if mesh mode falls back to the missing-mesh state when mesh files are present.
- **Mobile button usability audit** (web) - Expanded the mobile web audit to verify key tap targets and added small mobile hit-target fixes for simulation, connection retry, and expandable log controls.
- **Harden mobile Apartment camera mode checks** (web) - Mobile Apartment camera mode now keeps snap controls locked across calibrated camera views, and the screenshot audit checks for overlapping or clipped controls.
- **Mobile camera snap controls** (web) - Locks the mobile Apartment camera-snap state to its mesh/live view, removes overlapping mode controls, and keeps the live camera overlay aligned with the 3D pose.
- **Fix mobile header actions menu** (web) - The mobile header hamburger now opens a reachable actions menu instead of being clipped by the compact header row. The mobile screenshot audit now checks that menu items are actually visible and tap-reachable.
- **Polish mobile trace diagnostics** (web) - Improved the mobile trace diagnostics panel so labels render cleanly, chart controls wrap intentionally, and the trace tray scrolls clear of the composer.
- **Mobile web and Apartment 3D hardening** (web) - Improved the Home web UI on phone-sized viewports, added mobile screenshot audit tooling, and hardened Apartment photo, mesh, and camera fly-to behavior for travel access.
- **Polish mobile Home web layout** (web) - Mobile web now has cleaner header/status controls, safer feed spacing, compact travel-readiness controls, and better Apartment labels and camera-feed framing.
- **Tauri HTTP bridge for remote Frigate checks** (desktop) - Enabled the Tauri global API so the desktop app can use its native HTTP bridge for remote service probes, including Frigate over Tailscale.
- **Improve travel web startup reliability** (web) - The web app now serves React, ReactDOM, and Babel from the Home gateway and prefetches boot-chain files in a small ordered window, reducing startup stalls on slow travel connections.
- **Mobile web boot is more resilient** (web) - The browser Home app can now continue booting if optional desktop runtime glue is interrupted on a poor mobile connection, and the service worker no longer forcibly reloads pages during boot.
- **Web app cache refresh for mobile fixes** (web) - Force the browser service worker to purge stale app-shell caches and reload controlled Home web tabs when a new build is deployed, so mobile Safari receives Apartment camera fixes promptly.
- **Recover the web app from stale service worker after a box reboot** (web) - The browser/Tailscale web app no longer strands on a red "boot chain stopped" overlay when the AI box is rebooting. The service worker now derives its cache name from the per-deploy asset version (so a new deploy always supersedes a warm cache instead of requiring a manual `v2 → v3` bump), takes over immediately via `skipWaiting()` + `clients.claim()`, applies a generous network timeout with cache fallback for the shell, and prefers the network for JSX modules once it has been alive a while. The boot loader now treats a failed boot-file fetch as a recoverable "Reconnecting to home…" state: it retries with exponential backoff so the app self-heals when the box returns, and offers a "Reload now" button that unregisters the service worker and reloads — a full escape hatch that no longer requires clearing browser site data. Service-worker version and boot outcome are logged to the console for future debugging.

## [0.1.0] — 2026-05-11

### Stack

- **Unified vision + text + tool-call model.** vLLM serves
  `Qwen/Qwen3-VL-30B-A3B-Instruct` — a Mixture-of-Experts model with
  3 B active parameters per token (fast inference) but 30 B total
  knowledge and native multimodal vision. One process handles chat,
  device control, and camera understanding. `--tool-call-parser hermes`
  (Qwen3-VL emits Hermes-format tool calls).
- **`metrics-sidecar` is now also a chat-tee proxy.** Externally on
  port `:8000`, it proxies HA's `/v1/chat/completions` calls through to
  vLLM and broadcasts every captured turn (user text + assistant text +
  tool_calls) on `/conversations/stream` as SSE. The Home app
  subscribes — every typed turn, voice-mode turn, and Voice PE turn
  appears in the same feed regardless of origin. vLLM itself is no
  longer bound to a host port; only the sidecar is.
- **`vision-sidecar` rewritten** to call our multimodal vLLM directly
  (no more Ollama dependency). The LLM's `describe_camera` tool fetches
  a fresh HA `camera_proxy` JPEG and forwards it to vLLM as a
  multimodal `image_url` message.
- Ollama daemon stopped + disabled — model swap is complete; no
  separate vision model.
- HA prompt patched with a "Rule 0: tool use is mandatory for actions"
  preamble + an example payload + a "no `Hass*` built-in tools" rule.
  Stops the LLM from hallucinating action completion without
  dispatching tool calls.
- `max_tokens` bumped from 220 → 2000 so multi-entity tool calls
  (e.g. "turn off all my lights" with 23 lights) don't get truncated.
- HA pipeline's `prefer_local_intents` set to `false` — every turn
  goes through the LLM with full tool access, ensuring tool_calls
  always reach the chat-tee.

### Home desktop app

- **Vision card at the top of the chat.** Pattern E from the design:
  thin collapsed row showing camera count + occupancy; expands to a
  tab strip + live MJPEG feed of the selected camera. Uses HA's
  `auth/sign_path` WS command for auth-free `<img>` streaming. Five
  cameras out of the box (living room, kitchen, dining room, workshop,
  driveway). Activity labels show `undetected` for now — slot ready
  for V-JEPA-2 wiring.
- Tauri 2 desktop app with frameless 420 × 720 window (resizable; min
  360 × 480; matches the three sizes in the design canvas).
- Engineered Lighting visual language: dark default + parchment "paper
  terminal" light mode.
- HA WebSocket `assist_pipeline/run` integration for typed turns,
  voice mode (`stt` start-stage), and pipeline reconfiguration via WS.
- Voice mode: mic captured via `getUserMedia` → PCM s16le @ 16 kHz →
  HA pipeline → STT → intent → TTS → audio playback via `Audio()`.
- SSE-driven conversation rendering — single source of truth.
  Local user-event dedup keeps instant feedback for typed turns and
  shows Voice PE turns inline marked with `◉`.
- Real-time `call_service` action cards: grouped per turn (800 ms
  burst window), one card per `{domain.service}`, collapsed by default
  with `▸ action ...` (click to expand attrs).
- Conversation memory across turns via `conversation_id`. Events,
  `conversation_id`, prefs persisted to `localStorage`.
- Sidecar auto-probe on connect: tries HA host, same `/24` with .100,
  then localhost — sets `metricsBase` from the first responder.
- Slash commands: `/connect`, `/token`, `/model`, `/metrics`, `/clear`,
  `/demo`, `/about`, `/help`.
- Keyboard shortcuts: stop (`Ctrl/⌘+.`), clear (`Ctrl/⌘+L`), focus
  input (`Ctrl/⌘+K`), cancel pending-confirm (`Esc`).
- Sun/moon theme toggle, persisted.
- ASCII `home` wordmark + light-cone banner at the top of the feed,
  scrolls up with the conversation.

### Distribution

- Windows: `.msi` installer (unsigned — Windows SmartScreen + Smart
  App Control will warn; users click `More info → Run anyway`).
- macOS: `.dmg` built via GitHub Actions on a macOS runner (built on
  tag push).
- v0.1 install requires either disabling Smart App Control on Windows
  OR running the dev build via `cargo tauri dev`. EV code-signing is a
  Phase 3 follow-up.

### Deferred to v0.2

- V-JEPA-2 wiring for real activity labels in the vision card (the
  card already accepts the `activity / activityConfidence` data shape).
- Confirmation gating in the HA agent prompt for security-sensitive
  intents.
- EV code-signing cert for the Windows .msi.
- Bounding box overlays in the vision frame (intentionally removed
  in design after a real-image legibility test).
