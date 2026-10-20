# Den activity lighting: go-live runbook (draft for AL-6)

**Status:** draft. Nothing here has been run. Every step is a host operation
for AL-6 (#326), and each one needs the owner's approval in the planning
session. Physical output stays disabled: everything here drives Home's
virtual fixtures only.

Placeholders in `<angle brackets>` are private values: usernames, paths,
origins and tokens. They live in private files on the hosts and never in
the repo. `render_units.py` refuses any unit value that is still a
placeholder.

## 0. Before anything

- **Merged to `main`:**
  - AL-1 brain (#332);
  - AL-2 bridge (#331);
  - AL-3 activity loop (its PR);
  - AL-4 Home (its PR);
  - the AGENTS.md always-on policy (#343);
  - this PR (AL-5).
- **Release id:** choose one `<release>` (a `main` commit) for the whole
  rollout. The soak receipt names it.
- **Admission check, LA host:** the AGENTS.md maintenance admission check,
  right before the LA steps. Abort on any failure.

  ```bash
  systemctl is-system-running                 # running
  systemctl --failed --no-legend              # nothing new
  nvidia-smi --query-gpu=temperature.gpu,memory.used,memory.total --format=csv
  sensors | grep -E 'Tctl|Tccd|Composite'     # normal temperatures
  df -h /                                     # >= 50 GiB free
  journalctl -k -b --no-pager | grep -Ei 'mce|hardware error|lockup|thermal|out of memory|oom-kill|panic|i/o error|segfault|xid'
  ```
- **Admission check, Jetson:** the same check, before the Jetson steps. Use
  `cat /sys/class/thermal/thermal_zone*/temp` for temperatures; they must be
  below 80 °C. Free memory must be at least 3 GiB and storage at least
  20 GiB.

## 1. Owner one-time setup

| # | What | How | Result |
|---|---|---|---|
| O1 | Mark the sofa | In Home, open Apartment/Victoria and use **Mark sofa** (`docs/runbooks/home-den-sofa-mark.md`). Keep the private download. | the sofa record |
| O2 | Geometry export | On the workstation, run `node tools/den-room/brain-targets.mjs --sofa <sofa.json> --equipment <equipment export> --out <geometry.json>`. Check the summary's `near_check`. | `lighting-brain-geometry-1` |
| O3 | Frigate zones | Draw `den_desk` and `den_sofa` on the Pi, following `frigate-zones-den.proposal.yaml` (AL-2). Then set the bridge config `"zones": {"desk": "den_desk", "sofa": "den_sofa"}`. | zones in Frigate |
| O4 | HA non-admin user | In Victoria HA, create a non-admin user `<ha lighting user>`. Make two long-lived tokens: one for the bridge's states seed (read), one for the monitors' `notify` call. | 2 HA tokens |
| O5 | Producer tokens | Make two random tokens (`python3 -c "import secrets; print(secrets.token_urlsafe(32))"`): one for `victoria-ha-bridge` and one for `victoria-jetson-activity-loop`. They go into the brain's `tokens.json`, scoped as in `config/tokens.example.json` (AL-1). | brain `tokens.json`, two producer token files |
| O6 | Monitor tokens | Make one token per monitor (`la-host`, `victoria-jetson`), and record each token's SHA-256 digest (see the commands below this table). | the gateway's `den-ops-monitors.json`, two monitor token files |
| O7 | Lab monitor grant | Add a Lab credential grant on the Jetson for the monitor, with capabilities `read` and `record` (`token_sha256` of a new token, at least 32 characters), in the managed Lab settings. It is picked up at the J2 restart. | the monitor's Lab token file |
| O8 | Approve | Approve the deploy window. J2 restarts `perception-lab-managed.service`, which interrupts camera chat and Lab briefly. | |

For O6, make each token and its digest with:

```bash
python3 -c "import secrets,hashlib; t=secrets.token_urlsafe(32); print(t); print(hashlib.sha256(t.encode()).hexdigest())"
```

### Private file placement

Each file is mode 0600, owned by the user that reads it. Private
directories are 0700.

| Host | Private file | Owner | Content |
|---|---|---|---|
| LA | `<brain private dir>/geometry.json` | root | O2 export (via `LoadCredential`) |
| LA | `<brain private dir>/tokens.json` | root | O5 producer tokens, `lighting-brain-producer-tokens-1` |
| LA | `<brain private dir>/settings.json` | root | `{}` (defaults) or the settings overrides |
| LA | `<gateway user config>/den-ops-monitors.json` | gateway user | O6 digests, `den-ops-monitor-tokens-1` |
| LA | `<gateway user config>/web-gateway.env` | gateway user | add `HOME_WEB_DEN_OPS_TOKENS_FILE=<…>/den-ops-monitors.json` |
| LA | `<monitor private dir>/{monitor.json, fault-baseline.json, den-ops-monitor.token, ha-notify.token, den-ops-monitor.env}` | `<monitor user>` | from `monitor/config/*.example.json`. The env file holds `DEN_OPS_MONITOR_CONFIG=<…>/monitor.json`. |
| Jetson | `<bridge private dir>/{bridge.json, brain-producer.token, mqtt-password, ha-token, victoria-ha-bridge.env}` | `<bridge user>` | AL-2 `bridge.example.json` keys. The env file holds `BRIDGE_CONFIG=<…>/bridge.json`. |
| Jetson | `<lab private dir>/{activity-loop.json, brain-producer.token, activity-loop.env}` | `<perception-lab user>` | AL-3 config (hand-over keys). The env file holds `PERCEPTION_LAB_ACTIVITY_LOOP_CONFIG=<…>/activity-loop.json`. |
| Jetson | `<monitor private dir>/{monitor.json, fault-baseline.json, den-ops-monitor.token, lab-monitor.token, ha-notify.token, den-ops-monitor.env}` | `<perception-lab user>` | as LA, plus O7's Lab token and the Lab CA |
| both | `<private>/values.json` | the deployer | `units/values.example.json` filled in |

## 2. Deploy order

The steps go in this order, and each is verified before the next. Each code
drop is a versioned directory from one commit, so a rollback is a symlink
and a restart:

```bash
git -C <repo checkout> archive <release> stack/services/lighting-brain stack/services/lighting-brain-bridges stack/ops/den-activity-lighting \
  | tar -x -C <release root>/<release>
ln -sfn <release root>/<release> <release root>/current
```

### LA host

**L1. Brain.**

```bash
python3.12 -m venv <brain venv>
<brain venv>/bin/pip install -r <release root>/current/stack/services/lighting-brain/requirements.txt
cd <release root>/current/stack/ops/den-activity-lighting
python3 render_units.py --values <private>/values.json --host la --out <scratch>/units
sudo install -m 0644 <scratch>/units/lighting-brain.service <scratch>/units/lighting-brain-health.service \
  units/la/lighting-brain-health.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now lighting-brain.service lighting-brain-health.timer
curl -s http://127.0.0.1:8101/healthz          # ok:true, synthetic_geometry:false
```

**L2. Gateway and Home (AL-4 ships in the same checkout).**

1. Check the deployed checkout is clean (`git status --short`). Do not run
   the pull-based deploy over unrelated changes.
2. Add `HOME_WEB_DEN_OPS_TOKENS_FILE` to `web-gateway.env`.
3. Run `tools/deploy-home-web.sh` (the existing reviewed path, with its own
   rollback).
4. Check the gateway:

   ```bash
   curl -s http://127.0.0.1:5181/healthz | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['denOpsStatus'], [r for r in d['routes'] if r['prefix']=='/api/lighting-brain'])"
   ```

5. In Home, the brain panel shows **online** and the why line reads "Room
   empty" (or the current state).

**L3. LA monitor.**

```bash
sudo install -m 0644 <scratch>/units/den-ops-monitor.service units/la/den-ops-monitor.timer /etc/systemd/system/
cd <release root>/current/stack/ops/den-activity-lighting/monitor
sudo -u <monitor user> python3 -m den_ops --config <monitor private dir>/monitor.json baseline-units --write   # once, ever
sudo -u <monitor user> python3 -m den_ops --config <monitor private dir>/monitor.json validate                # dry run
sudo systemctl daemon-reload && sudo systemctl enable --now den-ops-monitor.timer
```

### Victoria Jetson

**J1. Bridge (AL-2).**

```bash
python3 -m venv <bridge venv>
<bridge venv>/bin/pip install -r <release root>/current/stack/services/lighting-brain-bridges/victoria-ha/requirements.txt
cd <release root>/current/stack/services/lighting-brain-bridges/victoria-ha
sudo -u <bridge user> <bridge venv>/bin/python -m victoria_ha_bridge --config <bridge private dir>/bridge.json --dry-run   # Ctrl-C after a batch prints
cd <release root>/current/stack/ops/den-activity-lighting
python3 render_units.py --values <private>/values.json --host jetson --out <scratch>/units
sudo install -m 0644 <scratch>/units/victoria-ha-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now victoria-ha-bridge.service
```

Verify through the gateway:

- `inputs.health.frigate` is true;
- `occupancy_age_s` stays under 30 s;
- walking in and out flips `occupancy.present`.

**J2. Activity loop (AL-3).** Run this while the den is idle and nobody is
using camera chat.

```bash
cd <managed release>/service && <managed venv>/bin/python -c "import json,activity_loop as a; a.check_config(json.load(open('<lab private dir>/activity-loop.json')))"
install -D -m 0644 <scratch>/units/perception-lab-managed.service.d/50-activity-loop.conf \
  ~/.config/systemd/user/perception-lab-managed.service.d/50-activity-loop.conf
systemctl --user daemon-reload
systemctl --user restart perception-lab-managed.service
journalctl --user -u perception-lab-managed.service --since -2min --no-pager | grep -E 'Application startup|activity'
curl -s --cacert <lab ca> -H "Authorization: Bearer $(cat <lab private dir>/lab-monitor.token)" https://<jetson lab origin>/api/perception-lab/v1/activity-loop
```

- Expect `Application startup complete.` in the journal.
- The status call shows the loop `paused` while the den is empty.
- Confirm the recording retention and the disk headroom: free space should
  sit well above the 30 GiB level-1 threshold.

**J3. Jetson monitor (the acting one).**

```bash
id -nG | grep -Eq '\b(adm|systemd-journal)\b'     # needed to read the kernel journal
install -m 0644 <scratch>/units/den-ops-monitor.service <release root>/current/stack/ops/den-activity-lighting/units/jetson/den-ops-monitor.timer ~/.config/systemd/user/
cd <release root>/current/stack/ops/den-activity-lighting/monitor
python3 -m den_ops --config <monitor private dir>/monitor.json baseline-units --write   # once, ever
python3 -m den_ops --config <monitor private dir>/monitor.json validate
systemctl --user daemon-reload && systemctl --user enable --now den-ops-monitor.timer
loginctl show-user "$USER" -p Linger            # Linger=yes (already needed by the managed service)
```

### Both hosts

**S. The soak.** Follow [SOAK.md](SOAK.md): at least 3 h on both hosts, with
the owner checklist on LA, then `soak combine`. Record
`soak-release.{json,md}` with the release.

## 3. Post-deploy checks

| Check | Command or place | Expected |
|---|---|---|
| Brain healthy | `curl -s http://127.0.0.1:8101/healthz` (LA) | `ok: true`, `synthetic_geometry: false` |
| Gateway routes | `GET /healthz` on the gateway | `/api/lighting-brain` route; `denOpsStatus.enabled: true, monitors: 2` |
| Producers reach the brain | brain `state.inputs` | `occupancy_age_s` < 30, `health.frigate` true; when occupied, `activity_age_s` < 15 and `jev`/`deep_stack` true |
| Producer auth | the bridge and loop logs | no 401/403 from the gateway or the brain |
| Banner | `GET /api/den-ops/v1/status` with the session | `level: 0`, both hosts listed and not stale |
| HA notify | the HA notifications panel | test once: run `den_ops check` on a host with a temporary rule set low, or wait for the first notice |
| Monitor timers | `systemctl list-timers den-ops-monitor.timer` (LA), and `systemctl --user list-timers` (Jetson) | firing every minute |
| No new failed units | `systemctl --failed` on both | only the baseline's units |
| Home | the Apartment/Victoria view | brain **online**, why line, decision log; the fixtures follow the MVP "Done" list |

## 4. Rollback

Each piece rolls back on its own. Stopping the brain or the producers is
always safe: Home shows "brain offline" and holds the fixtures, and the real
den lights keep following the HA automation.

| Piece | Rollback |
|---|---|
| Jetson monitor | `systemctl --user disable --now den-ops-monitor.timer`. If it had stopped the loop, resume it with `POST …/activity-loop/resume`. |
| Activity loop | Durable off: set `"enabled": false` in the loop config and restart the managed service. Full removal: delete the drop-in, run `systemctl --user daemon-reload`, then restart the managed service. |
| Bridge | `sudo systemctl disable --now victoria-ha-bridge.service` |
| LA monitor | `sudo systemctl disable --now den-ops-monitor.timer` |
| Gateway / Home | The deploy script's own rollback to the previous commit. Removing `HOME_WEB_DEN_OPS_TOKENS_FILE` turns `/api/den-ops` off (404). |
| Brain | `sudo systemctl disable --now lighting-brain-health.timer lighting-brain.service`. For an earlier release, re-point `<release root>/current` and restart. |

After any rollback:

- recheck `systemctl --failed`;
- recheck the kernel journal, as in step 0;
- do not reset failed units or edit the fault baseline to make a check pass.

## 5. What the monitor needs from planning (follow-ups)

- **AL-3: a cadence route** (for example
  `POST /api/perception-lab/v1/activity-loop/cadence {"level":"reduced"|"normal"}`).
  Level 1 has nothing to act on until it exists. Plug it into the Jetson
  monitor's `cadence.reduce` and `cadence.restore`.
- **AL-4: show the ops banner.** Home should show the banner from
  `GET /api/den-ops/v1/status` (`den-ops-banner-1`: `level`, and `banner`
  with `severity` and `text`). Poll it every 30–60 s, and hide it when
  `banner` is null.
- **Owner stop vs monitor stop.** The loop's stop does not record who
  stopped it, so a manual stop during a monitor hold is resumed on recovery.
  A `reason` on `POST …/activity-loop/stop` would fix this.
