# correction-capture

Counts how often people correct den_lighting by hand, per occupied hour. Rung 11
hands the den's four bulbs to a new coordinator only if it needs fewer hand
corrections per occupied hour than den_lighting, so this is den_lighting's
baseline.

It reads three read-only exports of Home Assistant's recorder, copied to this
PC, and writes `exposure.json`. It is offline and standard-library Python: it
never contacts Home Assistant, the Jetson or LA, and never sends a light
command.

## Export by 2026-11-07

HA's recorder keeps about 10 days (`purge_keep_days`, default 10).
den_lighting 1.0.4 went live on 2026-10-28 (UTC), so its first evenings start
dropping out around 2026-11-08. **Run the export below by about 2026-11-07.**

If you can't finish it in time, make a full Home Assistant backup with the
history (the database) included (Settings, System, Backups). That keeps the
evenings, and they can be exported from the backup later.

The exports hold your home's data. Keep them in `C:\tmp`, outside every
checkout. Never commit or upload them.

## The owner's export (Windows PowerShell 5.1)

These lines go over the HA SSH app on Victoria HA (`root@192.168.1.60`; add
`-p PORT` after `ssh` if the app listens on another port). Every query opens
the database with `sqlite3 -readonly`, so nothing in HA changes. Paste each
block on its own.

**1. A folder for the exports**

```powershell
New-Item -ItemType Directory -Force C:\tmp\den-core\in | Out-Null
```

**2. Check that the SSH app has `sqlite3`**

```powershell
ssh root@192.168.1.60 "sqlite3 -version"
```

It should print a version such as `3.48.0`. **The SSH app may not have
`sqlite3`.** If it says `sqlite3: not found`, open the app's Configuration tab
in HA, add `sqlite` to its packages list (`apks` in Terminal & SSH, `packages`
in Advanced SSH & Web Terminal), save, restart the app, and run the check
again. If the app has no `/config` folder, use
`/homeassistant/home-assistant_v2.db` instead of
`/config/home-assistant_v2.db` in every line below.

**3. Check what the recorder holds**

```powershell
$sql = @'
SELECT m.entity_id, count(*), datetime(min(s.last_updated_ts), 'unixepoch'), datetime(max(s.last_updated_ts), 'unixepoch')
FROM states s
JOIN states_meta m ON m.metadata_id = s.metadata_id
WHERE m.entity_id LIKE 'sensor.den_lighting%'
   OR m.entity_id IN ('light.den_front_left', 'light.front_right', 'light.den_rear_left', 'light.den_rear_right')
GROUP BY m.entity_id;
'@
```

```powershell
$sql | ssh root@192.168.1.60 "sqlite3 -readonly /config/home-assistant_v2.db"
```

You should see `sensor.den_lighting_control` and the four lights, each with a
row count and its oldest and newest time (UTC). If the status sensor shows
under another name (say `sensor.den_lighting_control_2`), put that name in
step 4. If it is missing, the recorder is told to skip it, and there is
nothing to export.

**4. Export den_lighting's status sensor** (its automatic switch, occupancy,
game and holds)

```powershell
$sql = @'
SELECT json_object('last_updated_ts', s.last_updated_ts, 'entity_id', m.entity_id, 'state', s.state,
  'attributes', json(a.shared_attrs))
FROM states s
JOIN states_meta m ON m.metadata_id = s.metadata_id
LEFT JOIN state_attributes a ON a.attributes_id = s.attributes_id
WHERE m.entity_id = 'sensor.den_lighting_control'
ORDER BY s.last_updated_ts, s.state_id;
'@
```

```powershell
$sql | ssh root@192.168.1.60 "sqlite3 -readonly /config/home-assistant_v2.db" | Set-Content -Encoding ascii C:\tmp\den-core\in\den-status.jsonl
```

**5. Export the four bulbs' states** (the same file den-core's `--lights` reads)

```powershell
$sql = @'
SELECT json_object('last_updated_ts', s.last_updated_ts, 'entity_id', m.entity_id, 'state', s.state,
  'context_id', hex(s.context_id_bin), 'context_user_id', hex(s.context_user_id_bin),
  'context_parent_id', hex(s.context_parent_id_bin))
FROM states s
JOIN states_meta m ON m.metadata_id = s.metadata_id
WHERE m.entity_id IN ('light.den_front_left', 'light.front_right', 'light.den_rear_left', 'light.den_rear_right')
ORDER BY s.last_updated_ts, s.state_id;
'@
```

```powershell
$sql | ssh root@192.168.1.60 "sqlite3 -readonly /config/home-assistant_v2.db" | Set-Content -Encoding ascii C:\tmp\den-core\in\den-bulbs.jsonl
```

**6. Export den_lighting's commands** (the same file den-core's
`--live-commands` reads)

```powershell
$sql = @'
SELECT json_object('time_fired_ts', e.time_fired_ts, 'event_data', json(d.shared_data))
FROM events e
JOIN event_types t ON t.event_type_id = e.event_type_id
JOIN event_data d ON d.data_id = e.data_id
WHERE t.event_type = 'den_lighting_command'
ORDER BY e.time_fired_ts, e.event_id;
'@
```

```powershell
$sql | ssh root@192.168.1.60 "sqlite3 -readonly /config/home-assistant_v2.db" | Set-Content -Encoding ascii C:\tmp\den-core\in\den-lighting-commands.jsonl
```

**7. Fingerprint the installed den_lighting**

```powershell
ssh root@192.168.1.60 "cd /config/custom_components/den_lighting && sha256sum *.py *.json *.yaml" | Set-Content -Encoding ascii C:\tmp\den-core\in\den-lighting-installed.sha256
```

(With `/homeassistant` instead of `/config`, change the `cd` path the same way.)

**8. Count** (on this PC, from the worktree root; offline)

```powershell
python -B tools\correction-capture\capture.py --status C:\tmp\den-core\in\den-status.jsonl --lights C:\tmp\den-core\in\den-bulbs.jsonl --commands C:\tmp\den-core\in\den-lighting-commands.jsonl --installed C:\tmp\den-core\in\den-lighting-installed.sha256 --out C:\tmp\den-core\cph-1 --events
```

It prints a summary and writes `C:\tmp\den-core\cph-1\exposure.json` (and,
with `--events`, `corrections.jsonl`). `--out` must be a new folder outside
every checkout. Or hand the five file paths to an agent and it runs this step.

Later exports can be added to extend the window: give each kind of file more
than once (`--status old.jsonl new.jsonl`). A row that appears in two exports
counts once. Name later files by date so they don't overwrite these.

## The gate, written first

`cph-budget-v0.json` was written on 2026-11-04, before any real export was
opened. The tool refuses to run if its content changes (its sha256 is pinned
in `capture.py`: `bec5b06ce979c66030481c6f1ff8505c605543206e6b4e98f8afe07812bf600d`,
of its canonical JSON) and records that sha256 in `exposure.json`. A changed
rule needs a new id.

Per stratum (no game, game), one verdict, tested in this order:

- `insufficient`: under 20 occupied hours.
- `beyond_budget`: even counting every hold, at the high end of its 95%
  interval, a 30% improvement would need more than 300 occupied hours per arm.
- `within_budget`: people's overrides alone, at the low end of their 95%
  interval, show a 30% improvement within 300 occupied hours per arm.
- `undetermined`: anything else.

Hours per arm are A13's `148 * phi / rate`, at phi = 1. It is a planning value
for rung 11, not a promotion gate.

## How it counts

1. **Candidates.** den_lighting's status sensor lists the held bulbs and each
   one's target. A bulb that becomes held, or whose held target changes, is a
   candidate. A hold den_lighting marks provisional (`provisional_fixtures`) is
   its own fade report. If read-back then keeps it held, it is a candidate.
2. **Who did it** (A11's capture rule), from that bulb's own rows 5 s either side:
   - den_lighting's own rows are set aside: its command's context or parent,
     or a report with no ids within 8 s of its command that does not flip the
     bulb against that command.
   - Of the rest: a user id is the **HA app**, a parent context is **another
     automation**, and no ids is a **switch or dimmer**.
   - Only den_lighting's own rows: **den_lighting**, not counted. No row at
     all: **unknown**.
3. **One action.** Changes in one HA context, or within 3 s of each other, are
   one action, counted once.
4. **Not counted**, each listed with its reason:
   - `held_at_export_start`: already held in the first row.
   - `restored_after_restart`: holds in the first row after an HA restart.
     A restart shows as den_lighting's revision going down, or a row without
     its status.
   - `own_fade_report`: den_lighting's own report.
   - `outside_exposure`: an action while den_lighting was off or its
     occupancy was not on.
5. **Occupied hours.** The time den_lighting's status shows `automatic` with
   its own occupancy `on`, split into game and no game. It is summed in
   24-hour blocks that start at 20:00 UTC (noon in Victoria in winter, 1 pm in
   summer), so an evening stays in one block. Time across an HA restart, and
   after the last status row, is not counted.
6. **Rates.** Actions per occupied hour, two ways:
   - people only (the app, a switch or a dimmer);
   - every hold (people, other automations and unknown).

   Each comes with an exact (Garwood) 95% Poisson interval, the occupied hours
   per arm a 30% improvement needs, and the weeks that takes in one den: both
   arms share the den's occupied time, so it needs twice the hours per arm.

## What it writes

`exposure.json`, schema `den-exposure-1`:

- `rule`: `cph-budget-v0` and its sha256.
- `controller`: the status entity, and with `--installed`, whether the
  installed den_lighting files match this checkout's.
- `inputs`: each file's name and sha256.
- `span`: what the exports cover, and how many HA restarts.
- `exposure`: EvalRecord's exposure block, field for field: `per_session`
  (one per 24-hour block and stratum, `{session_id, occupied_h,
  occupancy_source, mode_stratum, corrections}`), `occupied_h_total`,
  `corrections_total`, `phi_hat`, `phi_hat_status`. `corrections` here are
  people's actions. `phi_hat` is the Pearson dispersion of those counts per
  block. It is reported, never used by the verdict.
- `corrections`: one per counted action:
  - its `source` and HA context; user ids are reduced to `"present"`;
  - each bulb's `before`: its held target, else den_lighting's last command
    to it, else its last recorded on/off;
  - each bulb's `after`: the hold's target;
  - `attribution`, left empty.
- `not_counted`: every hold it did not count, with its reason.
- `planning`: per stratum, the occupied hours, both rates with their
  intervals, hours per arm and weeks, actions by source, and the verdict.

`corrections.jsonl` (with `--events`): den-event-1 `correction` lines. They
cover every hold den_lighting opened from outside (counted, outside exposure
or held at the start), but not restored holds or its own reports. A den-core
replay holds bulbs the way the live controller did with them: `run.py
--corrections` (see "Replaying from the Home Assistant exports alone" in
`tools/den-core/README.md`).

## Limits

- HA's recorder keeps no light levels, so the bulb rows are on/off only. The
  levels in `before` and `after` come from den_lighting's commands and its
  status.
- A remote that works through an HA automation shows as `automation`. Its
  presses count in every hold, not in people only.
- A touch that sets a held bulb to its held target again does not change the
  status, so it is not seen.
- The last status row's time is not counted, because the export does not say
  how long it lasted.

## Tests

```bash
python -B -m unittest discover -s tools/correction-capture -p "test_*.py"
```

They run on synthetic exports only.
