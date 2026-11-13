# Full Home and Den rollout

Use the integrated Full Home checkout containing the measured body stream. The Home candidates are `858e8dd9` and `1d73bb1a`. The Den labels and Qwen producer is based on `b3002536`, the live coordinator on `3774803b`, its policy on `11f694de`, and the offline C12 FC13 byte layout on firmware `cf1c325`. Record the exact built revision and release digest for the gateway, Den producer, BFF and lighting service.

This run sheet covers the virtual sofa preview. It requires a private export for the current Den registration, the synthetic browser gate, and a separate owner-run live check. Keep the incumbent Hue controller in charge throughout. Hue cutover and physical C12 transport have separate owner runs.

## Local gate

Use Node 24.14.1 and the installed build and Chromium dependencies. Obtain the owner's approval before downloading any missing dependencies. Run `node tools/build-app-jsx.mjs`, `node --test tools/test-den-live-serve-browser.mjs` and `node tools/run-web-gateway-look-only-tests.mjs`. The browser harness uses the repository test TLS certificate, fake upstreams and disposable profiles. The default Windows sandbox can reject loopback connections before Chromium launches. Run these synthetic tests with loopback access and use the recorded child output to distinguish that error from a failed build or process bind.

Run the coordinator, room labels, body and computed C12 Python suites under `tools/den-live`, the BFF lighting client tests, and the Core lighting API and service tests. Keep the private TLS and edge services on synthetic fixtures for local validation.

Before the owner install, use the shared synthetic F3 fixture in `tools/den-live/fixtures/preview-f3.json` to check that Home consumes `spot_snapshot`, uses the producer angles, and draws the beam from the modeled fixture using the renderer’s actual surface intersection. Confirm that the same snapshot row links to the exact MOVE bytes, and test Off, Stop, Resume and stale reconnect with fake upstreams. Assert zero coordinator HA bulb writes and zero C12 hardware sends, including during a blocked or failed trace write. Record the browser result separately from the local encoder and simulator results.

## Owner preflight and rollout

1. Record the current Home deploy, the active Den scan and equipment registration, the existing private lighting release, the Serve mapping and the Funnel status. Check the effective tailnet grants and device shares for the normal Home port before granting it write access. Confirm the dedicated gateway port is bound to loopback and used exclusively by Serve with its PROXY prefix. Keep the ordinary gateway listener and its existing authentication.
2. Deploy the private lighting service and BFF with their existing TLS configuration and per-home signing credentials. Provision two independent 64-hex service credentials. The gateway relay file is set by `HOME_WEB_SERVE_LIGHTING_RELAY_CREDENTIAL_FILE`; the matching BFF file is selected by the compose setting `HOME_AGENT_SERVE_LIGHTING_BFF_RELAY_FILE`. Select the second credential in the BFF with `HOME_AGENT_SERVE_LIGHTING_BFF_CORE_FILE` and in Core with `HOME_AGENT_SERVE_LIGHTING_CORE_FILE`. The compose values are mounted container file paths. Verify signed inventory from both homes using their existing light-only services before enabling the card. Record the current private service image and configuration for rollback. Keep all secret values in the private mounts.
3. Land the pinned integrated Den producer in the existing reviewed `claude/den-live` installer lane. Follow the [sofa mark and export runbook](runbooks/home-den-sofa-mark.md) to produce a fresh private `lighting-brain-geometry-1` file from the registered Den sofa mark and active equipment export. Verify the scan and equipment revision match the live Home Den, and record the export digest and active camera transform ID. Keep the existing Hue controller running and record its state and current Den light command events before running the preview. On the workstation, run the installer from the clean committed release with the absolute path to the private export.

   ```powershell
   python -B tools/den-live/install_la.py --preview-geometry "<absolute path to private lighting-brain-geometry.json>"
   ```

   The installer provisions `virtual-fixtures.json` root-only and enables `coordinator.preview` with the active transform and `hue_overlay:false`. Verify the effective settings and credential presence without printing the geometry or tokens. Confirm the live SSE emits `spot_snapshot` for the expected geometry scope and the coordinator GET reports the applied snapshot. The measured `/body` stream needs the pipeline provider described in the den-live README.
4. Configure `HOME_WEB_SERVE_PORT` and `HOME_WEB_SERVE_ORIGIN` for the existing normal Home HTTPS address. Set `HOME_WEB_DEN_LIVE=1` and `HOME_WEB_DEN_VIRTUAL=1` after verifying the matching upstream contracts. Set `HOME_WEB_DEN_BODY_V2=1` when the producer's `/body` responds with `X-Home-Den-Body-Schema: den-live-body-2` and publishes qualified measured figures. Use `tailscale serve --bg --tls-terminated-tcp=443 --proxy-protocol=1 127.0.0.1:5182` for the documented port, or use the verified dedicated port. Leave optional legacy proxies at their current settings. Remove the old look-only option after verifying the replacement. Check Serve and Funnel status again.
5. On fresh desktop and phone browser profiles, open normal Home and the Den without entering a credential. Verify a rendered figure in day and night conditions, save and restore a room label, and test On, Off, Stop and Resume. As a separate check, change and restore one approved ordinary light at each home using the cross-home card. Disconnect Tailscale on cellular and verify a fresh visit is unreachable and a warm tab loses live data and control.
6. In room light at the real sofa, run five positive and five negative cues. At least four positives must light on the open page, and none of the negatives may light. Time opening the book while already seated and sitting down with it separately. Check a held shift, a page turn, stand-to-visible-darkness, second-person release and reading-to-phone patience. Check the beam drawn by Home against the actual page, then match the snapshot’s `last_move_trace_id` to the encoded MOVE in `days/<UTC date>/coordinator-fixture_calls.jsonl`. Keep the trace private, filter to this preview run, and include both files if it crossed UTC midnight.

   ```powershell
   python -B tools/den-live/c12_replay.py "<private ordered run JSONL>" --firmware-zip "<private pinned C12 firmware zip>"
   ```

   Verify the replay and record that all preview trace rows have `sent:false`. Check HA command events again and confirm zero bulb writes from the coordinator. Record scan and equipment revisions, cue outcomes, timings and the page-to-beam result separately from the synthetic tests. This check has no soak.

## Rollback

Disable the affected Den route flags or the new lighting action before downgrading a producer or service. Preserve the lighting journal and outcome lookup for submitted operations. Restore the recorded gateway configuration and Serve mapping, then confirm the prior normal or look-only route and ordinary ingress behave as before. Keep private receipts and real frames on the owner's systems.

To roll back the preview producer, use the installer’s printed **Back to the release before this one** command with the recorded previous release. Restore the previous Den service settings and drop-in if the preview config was installed over them, then reload systemd and restart. If the previous Den service did not run, the installer’s `sudo systemctl disable --now den-live` command stops and disables it. Verify incumbent Hue still owns the lights and remove the preview route flag if the prior producer does not publish `spot_snapshot`.
