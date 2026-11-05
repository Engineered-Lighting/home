# den-core

The single offline replay path for the den's decision foundations, with
den_lighting wrapped unchanged as the frozen baseline (incumbent and
comparator). This is K19 record/replay, ladder rung 0 "Record and replay" in
miniature: every logged decision, den_lighting's included, replays from its
logged inputs and versions to the same requested_scene.

Fake data tonight, copied real files later and a live writer eventually all
enter only as den-event-1 lines, so nothing downstream changes when the source
does.

- Standard-library Python 3.12, run with `python -B` (`python3 -B` on Linux). Offline only: no
  network, Home Assistant, MQTT, camera, Jetson or LA host, and no light
  commands of any kind. "Decisions" here are log lines.
- Nothing is ever written inside a git checkout. `events.write` and `run.py
  --out` refuse any path whose folder (or a parent) holds `.git`. Tests write
  only to temp folders.
- **Synthetic runs are never evidence.** They prove the plumbing, not the
  lighting, and never count toward any gate. `run.py` prints
  `synthetic: plumbing only, never a gate` for them.

## Run and check

From the worktree root, with an absolute output folder outside every checkout
(`C:/tmp/...` on Windows, `/tmp/...` on Linux):

```bash
python -B tools/den-core/run.py --synthetic evening --touch --game --policies need incumbent decide --out C:/tmp/den-core/evening-1
```

```bash
python -B tools/den-core/check.py C:/tmp/den-core/evening-1/log.jsonl
```

`run.py` writes `log.jsonl` and prints the inputs per kind, each policy's
decisions (UTC time, seconds from the first input, trigger, one-line scene)
and, when both `incumbent` and `decide` ran, the intervals where decide's
latest requested scene for a bulb differs from what den_lighting wanted for
it. That comes from re-running den_lighting in memory beside the log
(`incumbent.Shadow`): its own `desired()` scene, or, while it has none
(vacancy wait, unknown occupancy), the target it last acted on, a command or
a bulb it found already there. Recorded bulb states move neither side. The
two scenes have to match each way, so a level decide asks for counts against
den_lighting's last-level ON with no level. Game time is left out, and a
bulb is compared once per instant, only at instants where decide plans
(occupancy, bulb state, person state, mode, tick, or its own decision). When
`--live-commands` gave the commands the live den_lighting sent, it also
prints, per bulb, how many commands each side sent and the intervals where
the target the replay last acted on does not match the latest live command
(see "How real data plugs in"). With `--synthetic` it also writes
`truth.json` (reading-shadow-truth-1) and the generated
`la-fact-log-1-YYYY-MM-DD.jsonl`, so `tools/reading-shadow/replay.py` can
score the same fake evening.

Other inputs: `--facts PATH ...` (day files, or folders of them), `--poll FILE
...` (den-desk-observe HA poll logs), `--mqtt FILE --mqtt-t0 EPOCH` (the
victoria-ha Frigate step lists), `--journal FILE ...` (the Jetson occupancy
reader's journal edges: real occupancy for facts replays), `--assume-occupied`
(logged as occupancy `via: assumed`, for facts-only replays with no journal;
`run.py` refuses it together with `--journal`), `--live-commands FILE ...`
(den_lighting_command events exported from the HA recorder: a reference to
compare with, never a policy input; needs `incumbent` in `--policies`),
`--lights FILE ...` (the four bulbs' on/off states exported from the HA
recorder; needs `--live-commands`, and turns the plant off), `--status FILE
...` (den_lighting's own status sensor export: its occupancy; no other
occupancy source with it), `--corrections FILE ...` (correction-capture's
`corrections.jsonl`: the holds the live den_lighting opened; needs `incumbent`),
`--no-plant`, `--policies need incumbent decide`, and for decide (rung 9,
below) `--actuation shadow`, `--light-model FILE` and `--compensation
none-v0|P0|P1|P2|P3`.

`check.py LOG` re-imports each logged policy module, refuses `code changed:
<module>` when its file's sha256 differs, rebuilds it from the logged config
(den_lighting refuses a changed `policy.py` by itself), re-runs the logged
adapter inputs with the logged parameters and compares every line byte for
byte, header included. It prints `OK` or the first mismatching line and exits
0 or 1.

```bash
python -B tools/den-core/validate.py C:/tmp/den-core/evening-1/log.jsonl --truth C:/tmp/den-core/evening-1/truth.json
```

`validate.py LOG [--truth TRUTH_JSON]` says check by check whether a run
behaved as designed against den_lighting: the replay above; need's reading
on/off moments against den_lighting's own dwell; that every interval of the
diff above is decide's labelled placeholder reading look; that decide pins
and skips held bulbs; and that it stays silent in games and while occupancy
is unknown. It prints OK, FAIL or n/a per check and exits 0 or 1. It is a
consistency check of den-core's own code, not a lighting-quality gate.
`--truth` adds, as information only, how long need took to follow each
reading block's cues; `tools/reading-shadow` is the gate for those.

Tests:

```bash
python -B -m unittest discover -s tools/den-core -p "test_*.py"
```

## Files

| File | What it is |
| --- | --- |
| `events.py` | den-event-1: constants, `dumps`, `validate`, `read`, `write`, `inside_checkout`, `scene_matches` |
| `sources.py` | file adapters: `fact_log`, `poll_log`, `mqtt_timeline`, `occupancy_journal`, `recorder_commands`, `recorder_lights`, `den_lighting_status`, `correction_capture` |
| `synthetic.py` | `evening()`: a fake evening in the real la-fact-log-1 format, built with the perceiver's own builders |
| `runner.py` | `run()`: the deterministic heap runner; `load_policies()` |
| `incumbent.py` | den_lighting's `policy.py`, loaded by path and unchanged, as a policy; `Shadow`, the same re-run beside a logged run for the comparisons |
| `check.py` | the byte-identical replay check |
| `validate.py` | the check-by-check consistency check against den_lighting |
| `victoria_den_bulb_scenes.yaml` | a draft HA package for Victoria that records each bulb's level (below); nothing deploys it |
| `run.py` | the CLI and its summary/diff |
| `need.py`, `decide.py` | written on their own branches against the interface below; the runner loads them by name. `decide.py` is now rung 9's coordinator v0 (below) |
| `light_model.py` | the one adapter for rung 8's light model, and a fake built from rung 8's plan |
| `score.py` | rung 9's score on replay against `rung9-gate-v0.json` |
| `rung9-gate-v0.json` | rung 9's gate, with its open slots |
| `holds.py` | the hold fixtures V6-V8 and V62-V64 under each compensation option |

## Rung 9: coordinator v0 on replay

`decide` is rung 9's coordinator v0 (`docs/architecture/live-v1/ladder.json`,
rung 9; E10). It still requests what v0 did: den_lighting's normal scene, a
labelled placeholder reading look on the free bulbs while someone reads, off
after 120 s of vacancy, held bulbs pinned at their read-back. Around that,
every Decision now carries rung 9's record:

- `applied_scene` per channel: nothing is sent, so a held bulb is `held` at
  its pinned read-back and a free one `not_sent` at its last read-back.
- `recipients` per channel that is on: its share of the predicted light at
  each person's task points, on the requested scene with held bulbs pinned,
  and the tracks it serves (`s_serve`). `predicted` gives the light there.
- `inputs`: the light model version, the seed Look (`reading@0`), the fixed
  Preference stand-in (level factor 1, `mired_shift` and `contrast_shift` 0,
  applied through the look's guardrails), the comfort-limits version,
  `body_envelope` (body-envelope/v1, parameters pending), `t_exec` per channel,
  the `exclusions` those give (none on hue-den-4, which has no narrow beam;
  `decide.replay_exclusions()` recomputes them from the decision alone), the
  compensation option, the glare check and `open_slots`.
- `hard_conflicts`: a held bulb that alone breaks the seed Look's glare limit;
  a free bulb may not be chosen over it. Rung 8 writes `glare: null` and the
  limit is an open slot, so every check is logged `unchecked` and the list is
  always empty today.

The light model: every call goes through `light_model.LightModel`. Rung 8
Stage 1 (`claude/rung-8-stage1`) does not exist yet, so it predicts with the
plan's formula (each bulb alone at full, times its dimming curve, piecewise
linear in mired) on `light_model.fake()`, invented numbers in the shape of the
plan's `light-model.json`. When Stage 1 lands, `LightModel.predict` calls its
`predict()` and `run.py --light-model FILE` gives its file; nothing else
changes. Both arms use the model in decide's logged config.

Compensation is pending (decisions.json `compensation`), so it is built as
named options, `run.py --compensation`: `none-v0` (the default: v0, no solve),
and `P0` to `P3`, which, while a bulb is held, choose each free bulb's level
against each person's task light. P1 to P3 log `held[].direction` and
`footprint`, P3 `changer_track`, and they write `hold_limited`; none-v0 and P0
leave those fields out, as Decision v2 says.

`actuation`: `synthetic`, `replay` or, with `run.py --actuation shadow`,
`shadow`: decided beside the live den_lighting on its own inputs, with the
real bulbs' states (`--lights` or `--no-plant`). Until a live writer exists it
labels a run of copied files from such a time.

### The hold fixtures

```powershell
python -B tools/den-core/holds.py
```

prints V6 (a dim held), V7 (a brighten held), V8 (a hold between solve and
send), V62 (a shared bulb dimmed while another person still needs light), V63
(gaming entry and exit under a hold) and V64 (overlapping holds), each under
none-v0 and P0 to P3. The ladder lists the six cases and six ids in one list;
`holds.py` pairs them in that order. Their expected results depend on the
compensation decision, so **none is admitted as pass or fail**. The tests check
only what holds under every option: held bulbs pinned and skipped, no decision
in a game, no hard conflict, the same bytes every run, each decision under
100 ms.

### Scoring

```powershell
python -B tools/den-core/score.py C:/tmp/den-core/real-1/log.jsonl
```

`score.py LOG [--gate FILE] [--marks FILE] [--plumbing] [--out FILE]` scores
a log with need, incumbent and decide against `rung9-gate-v0.json`:

- the log replays byte for byte, with each decide decision timed: each under
  100 ms (A9; hue-den-4 has no aims);
- every decide decision carries its requested and applied scenes and the
  recipients of each channel that is on, its exclusions recompute from its
  own inputs, and its hard conflicts from its scene;
- the candidate's anticipation and stability rates, replay surrogates, each
  against a floor fixed in the gate before the run. The incumbent scores 0
  and 1 by construction, and is reported beside it;
- reported, never gated, labelled "predicted, replay surrogate": predicted
  service coverage and missed service at the owner's marks (`--marks`, one
  JSON object per line: `{mark_id, track_id, start, end, band: fine|casual,
  points}`), for both arms, with the owner's 300 and 200 lx floors, 120 s
  quiet window and 70% area share. They need a light model in lx; until rung 7
  anchors one they are n/a.

**It refuses a scored run** (exit 2, with every reason) while any slot in the
gate or in decide's logged config is open, while the light model is the fake,
for a synthetic log, or when the log used another compensation option than
the gate's. `--plumbing` runs anyway on the proposed values and labels every
line `[plumbing]`, not a score. On a synthetic evening:

```powershell
python -B tools/den-core/score.py C:/tmp/den-core/evening-1/log.jsonl --plumbing
```

The open slots, each with its proposed value:

| Where | Slot | Proposed |
| --- | --- | --- |
| gate | `surrogates.anticipation.floor` | 0.5 |
| gate | `surrogates.stability.floor` | 0.9 |
| gate | `parameters.anticipation_window_s` | 10 s |
| gate | `parameters.anticipation_rise` | 0.2 |
| gate | `parameters.stability_change` | 0.1 |
| gate | `parameters.compensation` | P1 (the register's advice: mode form, with P0 and P3 as counterfactual logs) |
| decide | `task_points` | per track or surface, the light model's task points; the fake's `desk-1` and `armchair-1` |
| decide | `s_serve` | 0.5 (attribution-thresholds, deferred) |
| decide | `glare_limit` | `unchecked` on hue-den-4 until rung 8 models glare |
| decide | `compensation_form` | mode |
| decide | `footprint_threshold` | 0.1 |
| decide | `membership` | at or above the threshold is in |
| decide | `lambda_h` | 1.0 (P2) |
| decide | `p3_fallback` | P1 |

To fill one, set its `value` and its `status` to `set`. Each changed decide
slot changes decide's config, so a log made before it stays as it was.

## Adding a source adapter

1. Write one function in `sources.py` that reads a copied file and returns a
   time-ordered list of `(t, kind, data)` in one of the input kinds below. `t`
   is when the event became available to its consumer.
2. Add its name to `events.ADAPTER_SOURCES` (that is how `check` knows which
   logged lines are inputs).
3. Add one flag to `run.py` that tags its items with `runner.tagged(items,
   name)` and records `{name: basename, sha256}` in `sources`.
4. Add a test with a small temp file. Nothing downstream changes.

A source that is a reference rather than an input (what a live system did, to
compare with) gets its kind in `events.REFERENCE_KINDS` too: the runner logs
it in time order but never hands it to a policy, and it does not set the
span or the ticks, so the policies decide exactly as they would without it.

## How real data plugs in (copied files only)

Agents do not reach the Jetson, LA or Victoria HA. The owner copies files to the
workstation, outside every checkout, and these tools replay them.

- **Jetson `fact_log_directory`** (the LA perceiver's la-fact-log-1 day
  files): `--facts COPIED_FOLDER` works today. **Copy the whole folder before
  any perceiver restart:** `FactLog` deletes day files 14 or more days old
  when it rotates, and it checks when the log opens.
- **tools/reading-shadow run folders**: they hold la-fact-log-1 day files and
  `truth.json`. Facts-only folders carry no occupancy, so give them
  `--journal` (below) when the journal covers the session, or else
  `--assume-occupied`. `run.py` refuses both together.
- **Jetson journal occupancy edges**: `--journal FILE ...` works now (adapter
  `occupancy_journal`). See "Copying the journal" below.
- **`den_lighting_command` events** from the HA recorder: `--live-commands
  FILE ...` works now (adapter `recorder_commands`). See "Exporting the live
  commands" below.
- **The four bulbs' states** from the HA recorder: `--lights FILE ...` works
  now (adapter `recorder_lights`), **on/off only**. See "Exporting the bulb
  states" below. HA's recorder keeps no light brightness or colour
  temperature, so no export can give past levels. Each state comes with its
  `context`: own, person or unknown. Turning a person's change into a
  `correction` needs the attribution rules, which are still parked; until then
  `--corrections` takes den_lighting's own holds from correction-capture.
- **den_lighting's status sensor** from the HA recorder: `--status FILE ...`
  works now (adapter `den_lighting_status`), for den_lighting's own occupancy.
  See "Replaying from the Home Assistant exports alone" below.
- **correction-capture's `corrections.jsonl`**: `--corrections FILE ...` works
  now (adapter `correction_capture`). See the same section.
- **Eventually a live writer** that emits den-event-1 lines itself. It needs
  the owner's approval, and it only replaces the adapter.

The two Oct 28 den-desk-observe poll logs replay through `--poll`. They are
adapter smoke tests, not evidence: about 2.5 minutes each, on/off only, with no
brightness or kelvin. **den_lighting ignores HA OFF** (vacancy needs a
non-retained MQTT zero), so a poll log's `occ=off` only approximates its
input, and a replay may turn the bulbs off where the live integration did not.

A full replay of copied files, from the worktree root:

```bash
python -B tools/den-core/run.py --facts C:/tmp/den-core/in/facts --journal C:/tmp/den-core/in/occupancy-edges.txt --live-commands C:/tmp/den-core/in/den-lighting-commands.jsonl --lights C:/tmp/den-core/in/den-bulbs.jsonl --policies need incumbent decide --out C:/tmp/den-core/real-1
```

```bash
python -B tools/den-core/check.py C:/tmp/den-core/real-1/log.jsonl
```

### Copying the journal (Windows PowerShell 5.1)

`JETSON` below is how you ssh to victoria-den-jetson (a `Host` in
`~/.ssh/config`, or `user@host`). This reads the journal over ssh and writes
an ASCII file on the workstation:

```powershell
New-Item -ItemType Directory -Force C:\tmp\den-core\in | Out-Null
```

```powershell
ssh JETSON "journalctl _SYSTEMD_USER_UNIT=perception-lab-managed.service -o cat | grep 'occupancy edge'" | Set-Content -Encoding ascii C:\tmp\den-core\in\occupancy-edges.txt
```

Copying the whole journal is simplest. The replay spans every edge it holds,
with a tick every 2 s, which comes to about 30 MB of log and 15 s per week of
span. To cut it to one evening, add `--since 'YYYY-MM-DD HH:MM' --until
'YYYY-MM-DD HH:MM'` (the Jetson's local time) after `-o cat`. The replay
starts with occupancy unknown, and den_lighting does not treat unknown to on
as an arrival: it makes no last-level ON. So the cut must keep a
`from=on to=off` line at least 120 s before the evening's first `to=on`.
Start `--since` while the den is still occupied before it last empties, or
during the evening before. `run.py` warns when a journal's first edge is an
arrival. Give it the fact files that cover the cut: they are named by UTC day,
so a Victoria evening spans two of them. Copy the journal before journald's
size limit rotates the evening out.

How a line becomes an event:

- The lines are matched by `occupancy_flaps.EDGE` and read by its `parse()`,
  imported from `tools/la-latency`. Other lines are ignored.
- Only `entity=binary_sensor.den_oak_person_occupancy` (the den's Frigate
  person-occupancy entity, which den_lighting also reads) is kept.
- `to=on` becomes occupancy `on`, `to=off` becomes `off`, and any other `to=`
  becomes `unknown`. The event is `{value, via: 'jetson_journal'}` at
  `t = observed_at`.
- An edge that appears twice, such as in overlapping copies, counts once.
- The reader logs only edges into `on`, and an off only straight from `on`.
  A dropout (stale or unavailable) that ends on logs `from=unknown to=on`. One
  that ends off logs nothing at all, and the replay stays occupied through the
  dropout and the vacancy after it. den_lighting itself turned the dropout
  into unknown and still saw the MQTT zero. The next line is then
  `from=off to=on` at the next arrival, or `from=unknown to=on` when the person
  comes back out of a second dropout, which looks just like a dropout that
  ended on. `run.py` prints both kinds of span, from the last `to=on` to that
  line: a `from=off` one as an off the reader never logged, a `from=unknown`
  one as a dropout that may hide an off. Read the replay with care there.
- The reader polls the HA entity at 2 Hz. den_lighting reads Frigate's MQTT
  count and ignores HA OFF, so the edges match its input only to within a
  poll or so.

### Exporting the live commands (Windows PowerShell 5.1)

den_lighting fires `den_lighting_command` with `{entity_id, revision, target:
{on, brightness, kelvin}, context_id}` after each light call that succeeds. HA's
recorder keeps events for 10 days by default (`purge_keep_days`), so export
within that time. The current recorder schema keeps the type and the data in
their own tables, so the query joins `events` with `event_types` and
`event_data`. It opens the database read-only (`sqlite3 -readonly`) and writes
nothing. The recorder's WAL mode lets it read while HA keeps writing.

`HA` below is how you ssh into Victoria HA's SSH app (a `Host` in
`~/.ssh/config`, or `-p PORT root@host`). The app needs the `sqlite3`
command-line tool, so check first with `ssh HA sqlite3 -version`. If it is
missing, add `sqlite` to the app's `apks` option (Terminal & SSH) or
`packages` option (Advanced SSH & Web Terminal), then restart the app. If the
app has no `/config`, use `/homeassistant/home-assistant_v2.db` instead.

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
$sql | ssh HA "sqlite3 -readonly /config/home-assistant_v2.db" | Set-Content -Encoding ascii C:\tmp\den-core\in\den-lighting-commands.jsonl
```

Each line looks like this (the values here are invented):

```json
{"time_fired_ts":1793765042.12346,"event_data":{"entity_id":"light.den_front_left","revision":12,"target":{"on":true,"brightness":180,"kelvin":2700},"context_id":"01EXAMPLECONTEXTID00000000"}}
```

How the export is used:

- Each line becomes a `live_command` event (`events.REFERENCE_KINDS`) at
  `t = time_fired_ts`, with `event_data` unchanged. A bad line, or a
  `context_id` seen twice, stops the run with the line number.
- The live commands are a reference, not an input. No policy sees them, and
  `incumbent.py` and den_lighting's `policy.py` are untouched.
- `run.py` keeps only the commands inside the span the other inputs replay
  (from the first input to 150 s after the last; `--lights` does not count)
  and says how many it kept. So a whole 10-day export can serve any evening.
- The summary's second comparison prints each bulb's command count, live and
  replayed. It then lists the intervals where the target the replayed
  den_lighting last acted on for a bulb does not match the latest live
  command, in the style of `diff()`. That target is its latest command, or a
  bulb it found already there and so left alone, as when `--lights` shows it
  the live bulb's recorded result before its own tick. So with `--lights` the
  replay may send fewer commands than the live side without disagreeing. When
  the replay finds a bulb already at its target on a recorded state, the live
  den_lighting saw the same bulb, so the live side takes that target too. That
  covers, for example, a bulb a person turned off before leaving: neither side
  sends it the vacancy OFF.
  - A null level on either side matches any level, because a replay of
    recorded bulbs knows no levels and sends its ONs without one. On/off
    still has to agree, and so do levels both sides know.
  - A bulb is compared from its first command on either side.
  - Game time is included.
- `time_fired_ts` is when HA fired the event, after the light call returned,
  and the replay's timed decisions (vacancy OFF) fall on its 2 s tick grid.
  So agreeing commands still show as short intervals of a second or two. Read
  the long ones.
- All three exports are ASCII. If you redirect with `>` instead of
  `Set-Content`, PowerShell 5.1 writes UTF-16, which the adapters also read.

### Exporting the bulb states (Windows PowerShell 5.1)

Same SSH app, same read-only `sqlite3` and same 10-day recorder window as the
live commands. The current schema keeps entity ids in `states_meta`, and the
Context ids as 16-byte values, which `hex()` prints. Export the commands too:
`--lights` needs `--live-commands`.

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
$sql | ssh HA "sqlite3 -readonly /config/home-assistant_v2.db" | Set-Content -Encoding ascii C:\tmp\den-core\in\den-bulbs.jsonl
```

Each line looks like this (the values here are invented). An absent id
prints as `""`:

```json
{"last_updated_ts":1793765042.5,"entity_id":"light.den_front_left","state":"on","context_id":"01563E3AB5D3D6764C61EFB99302BD5B","context_user_id":"","context_parent_id":""}
```

How the export is used:

- Each `on` or `off` row becomes `light_observed` at `t = last_updated_ts`,
  with `brightness` and `kelvin` null. Other states (`unavailable`,
  `unknown`) are skipped, as den_lighting skips them. A bad row, or one bulb
  recorded twice at one time, stops the run with the line number.
- **No levels.** HA's light component leaves `brightness`,
  `color_temp_kelvin`, the colour mode and `min_color_temp_kelvin` out of the
  recorder. So the replayed incumbent learns its last-level preferences with
  no level. Its ONs go out with no level, and a game uses den_lighting's
  default warmest colour temperatures. Recording the levels from now on
  needs the sensors in "Recording the bulb levels on Victoria HA" below.
- `context` follows den_lighting's own first test:
  - `own` when the state's context or its parent is the `context_id` of one of
    den_lighting's commands in the `--live-commands` export;
  - `person` when the context carries a user, such as the HA app;
  - otherwise `unknown`, which covers a wall switch or a Hue dimmer.
  den_lighting also counts as its own a device report, with a new context,
  that lands on its target within a few seconds. Here that report comes out
  `unknown`. No policy reads `context`, so this changes no decision.
- The states are cut to the span set by the other inputs, like the commands,
  but only up to the last input. Each bulb's latest state before the span is
  moved to its start, the way den_lighting reads every bulb when it starts.
  `run.py` prints how many it kept and their contexts.
- **`--lights` turns the plant off.** The recorded states are what the live
  den_lighting really saw, including the effects of its own commands, so the
  replay sees the same thing instead of its own echoes.
- **A person's change is a correction only with `--corrections`.** The live
  den_lighting holds a bulb a person changed and leaves it alone. Without
  `--corrections` the replay only observes the change, so it may set the bulb
  back where the live one did not. The `person` and `unknown` rows show where
  that can happen.

### Replaying from the Home Assistant exports alone

correction-capture's README (steps 4 to 6 and 8) already has the owner export
den_lighting's status sensor, the four bulbs' states and den_lighting's
commands, and write `corrections.jsonl` with `--events`. Those four files
replay an evening on den_lighting's own inputs, with no Jetson journal. From
the worktree root, in PowerShell 5.1:

```powershell
python -B tools/den-core/run.py --status C:/tmp/den-core/in/den-status.jsonl --lights C:/tmp/den-core/in/den-bulbs.jsonl --live-commands C:/tmp/den-core/in/den-lighting-commands.jsonl --corrections C:/tmp/den-core/cph-1/corrections.jsonl --policies incumbent --out C:/tmp/den-core/ha-1
```

```powershell
python -B tools/den-core/check.py C:/tmp/den-core/ha-1/log.jsonl
```

Add `--facts` with the evening's fact files to run need and decide beside it
(`--policies need incumbent decide`); the status is then cut to the span the
facts replay.

How `--status` is used:

- Each change of the occupancy den_lighting held (its status's `occupancy`)
  becomes `occupancy` with `via: den_lighting_status`, at `last_updated_ts`. It
  is the occupancy den_lighting itself acted on, so it needs no `--journal`;
  `run.py` refuses it together with any other occupancy source.
- A row without den_lighting's status attributes (the sensor unavailable, as
  across an HA restart) is `unknown`: den_lighting was not running. A row whose
  revision went down follows a restart, which starts den_lighting at unknown,
  so it gives `unknown` and then its own value at the same instant. `run.py`
  prints how many restarts it read.
- Each change of whether the live den_lighting was automatic and in a game
  (`automatic`, `gaming_session`) becomes `live_status`, a reference like
  `live_command`: no policy sees it. The export carries no gaming samples, so
  the replayed den_lighting never enters a game and is never disabled. The
  comparison with the live commands therefore leaves out the time the latest
  `live_status` shows a game, den_lighting switched off, or no status. A hold a
  game's exit cleared live may outlast it in the replay.
- With other inputs (`--facts`) the status is cut to their span. The latest
  occupancy and `live_status` before the span are carried to its start, or to
  150 s before it when older, so an empty den is still empty long enough for
  den_lighting to take the next arrival as one. Alone, the whole export
  replays: about 15 s and 30 MB of log per week of span.

How `--corrections` is used:

- Each line of `corrections.jsonl` (den-event-1 `correction`, source
  `correction_capture`) is a hold the live den_lighting opened from outside:
  the replayed den_lighting holds the bulb at `after` with `manual()`, as the
  live one did. One action on several bulbs is one line per bulb. A line
  repeated in two files counts once; the same correction and bulb with other
  values stops the run.
- The corrections are cut to the span the other inputs replay and never set
  it. `run.py` prints how many it kept.
- With `--lights`, each correction is paired with the recorded row of its bulb
  that it explains: the nearest one within 5 s (correction-capture's window),
  not den_lighting's own, turning the bulb the way the hold says. That row
  takes the hold's brightness and kelvin, which den_lighting's status read
  from the bulb and the recorder drops. A row before the correction (a switch:
  the bulb changes, then den_lighting holds it) takes the correction just
  ahead of it; a row after it (the app: den_lighting holds at the call) leaves
  it in place. Either way the replay holds the bulb first and finds it already
  there, so, like the live one, it sends nothing.
- In the comparison, the live side takes a correction's bulb target too: the
  live den_lighting held the bulb where the person put it and sent nothing.

### Recording the bulb levels on Victoria HA (draft, owner's change)

HA's recorder drops a light's levels, but it keeps a sensor's attributes.
`victoria_den_bulb_scenes.yaml` defines four template sensors. Each copies
one bulb's state, plus the three attributes den_lighting reads
(`brightness`, `color_temp_kelvin`, `min_color_temp_kelvin`), into:

- `sensor.den_front_left_scene`
- `sensor.den_front_right_scene`
- `sensor.den_rear_left_scene`
- `sensor.den_rear_right_scene`

A template sensor takes the context of the bulb change it follows, so each
copy keeps its own, person or unknown label. The sensors only render
templates. They send nothing to a bulb and change nothing den_lighting does.
Past evenings stay on/off only: the sensors record from the moment they
exist.

**den-core does not read them yet.** Once a few evenings are recorded,
`recorder_lights` can take the levels from these rows. That is the next step.

To install it, put the file into Victoria HA's packages folder. If Victoria's
`configuration.yaml` has no `packages:` line yet, add this under its
`homeassistant:` key and restart HA once:
`packages: !include_dir_named packages`. Without packages, paste the file's
`- sensor:` item under an existing `template:` key instead, or the whole file
if there is no `template:` key. Keep the file
name: HA uses it as the package name, which must be lowercase with
underscores. From the worktree root, in PowerShell 5.1:

```powershell
Get-Content tools\den-core\victoria_den_bulb_scenes.yaml | ssh HA "mkdir -p /config/packages && cat > /config/packages/victoria_den_bulb_scenes.yaml"
```

```powershell
ssh HA "ha core check"
```

Then load it: Developer tools, YAML, "Template entities" (or restart HA if
you just added `packages:`). In Developer tools, States, each
`sensor.den_*_scene` should show the bulb's state and its `brightness` and
`color_temp_kelvin`. If HA named one with a `_2` suffix, another entity
already has that name: rename one of them before relying on it.

To see that the recorder keeps the levels, after a bulb has changed once:

```powershell
$sql = @'
SELECT m.entity_id, s.state, a.shared_attrs
FROM states s
JOIN states_meta m ON m.metadata_id = s.metadata_id
LEFT JOIN state_attributes a ON a.attributes_id = s.attributes_id
WHERE m.entity_id LIKE 'sensor.den_%_scene'
ORDER BY s.last_updated_ts DESC LIMIT 8;
'@
```

```powershell
$sql | ssh HA "sqlite3 -readonly /config/home-assistant_v2.db"
```

Each row's `shared_attrs` should hold `brightness` and `color_temp_kelvin`
(both `null` while the bulb is off).

What they cost:

- A few small recorder rows per bulb change. They make no long-term
  statistics, because they have no unit and no state class.
- Text sensors show in the logbook. To hide them, add the four entity ids
  under `logbook:` `exclude:` `entities:` in `configuration.yaml`.

This was tested in a Home Assistant 2026.2.3 core with the real recorder:
- the entity ids came out as listed;
- the levels were integers, and `null` when off;
- each copy carried its bulb change's context and was written about 1 ms
  after the bulb's own row;
- the bulb-state and command export queries, and the check above, read that
  database correctly.

## Choices the interface left open

- `seq` counts from 0 in event order. `check` reports file lines counting from
  1, with the header as line 1.
- Every event is round-tripped through its canonical line before any policy
  sees it, so a first run and its replay hand the policies identical values.
- The incumbent's `decision_id` n is its running decision count, from 1.
- The incumbent maps `light_observed` to `observe()` only, whatever its
  `context`. den_lighting's runtime (`__init__.py`, which is not wrapped) turns
  an unexplained bulb change into a hold; here only a `correction` does.
- The incumbent's config is den_lighting's own (policy.py sha256, manifest
  version, `VACANCY_SECONDS`, `GAMING_TTL`), and `make_policy` refuses any
  other value.
- `live_command` is the one reference kind. The runner logs it with the inputs
  but never delivers it, and takes the span and the ticks from the other
  inputs alone. The incumbent reconciles on every event it is handed, so
  handing it the reference would move its decisions. `check` replays such a
  log byte for byte like any other.
- `recorder_lights` states go first among inputs at one instant, so a
  replay's opening instant observes the bulbs before its occupancy, as
  den_lighting's start does. A correction paired with a later row goes just
  ahead of it.
- `live_status` is the second reference kind, for the same reason as
  `live_command`.
- `poll_log` emits a row's light edges before its occupancy edge, like the
  synthetic preamble. The synthetic fact files are named by the UTC day of
  `available_at`, as `la_fact_log` names its own.

## SHARED INTERFACE

This text is identical in all three den-core briefs. Code against this text,
never against another session's code.

### Folder layout

tools/den-core/ is flat, with no package. Modules put their own folder on
sys.path and import each other by plain name. Three parallel sessions own
disjoint files:
- spine: README.md, events.py, sources.py, synthetic.py, runner.py, incumbent.py, check.py, run.py, test_spine.py
- need: need.py, test_need.py
- decide: decide.py, test_decide.py

### Bulbs and scenes

The bulbs are den_lighting's LIGHTS, with these exact ids:
light.den_front_left, light.front_right, light.den_rear_left,
light.den_rear_right.

A bulb scene is {on: bool, brightness: int 0-255 or null, kelvin: int or
null}, in den_lighting's Scene units. This is the hue-den-4 dialect of the
artifact's Decision v2, where level = brightness/255 and cct_k = kelvin.

Two scenes match (den_lighting Scene.matches) when:
- on is equal; and
- if on, brightness is within 1 and mired (1e6/kelvin) within 2.
A null target field matches anything.

### den-event-1

One JSON object per line, serialised with json.dumps(obj, sort_keys=True,
separators=(',', ':'), ensure_ascii=True, allow_nan=False).

The envelope keys are exactly:
- schema: 'den-event-1'.
- t: float epoch seconds; when the event became available to its consumer. It is the only clock. facts use the line's available_at. Derived events carry the t of the event that caused them.
- seq: int, the position in the log, assigned by the runner.
- kind.
- source: an adapter name ('fact_log', 'poll_log', 'mqtt_timeline' or 'synthetic'); 'runner' for ticks and assumed occupancy; 'plant' for echoes; or the producing policy's id ('need', 'incumbent' or 'decide').
- data: an object.

Input kinds
- facts: data is one la-fact-log-1 line as a dict, unchanged (its 12 keys; observation is a qwen-observation-1 receipt or null).
- occupancy: {value: 'on'|'off'|'unknown', via: str}.
- light_observed: {entity_id, on, brightness, kelvin, context: 'own'|'person'|'unknown'}.
- correction: {correction_id: str, entity_id, after: scene, source: str}. One human action on one bulb; it opens a hold.
- gaming_sample: {status: 'playing'|'idle'|'unknown', sampled_at: float}.
- tick: {}, from the runner every 2.0 s (den_lighting's own interval).

Derived kinds
- person_state (from need):
  - schema_version: 'person_state/v1'
  - state_id: 'room@' + ('%.3f' % emitted_ts)
  - emitted_ts
  - track_id: 'room'
  - identity: 'unbound'
  - focused_engagement: {state: 'none'|'starting'|'engaged'|'ending', since_s: float|null, onset_ts: float|null, confidence: null, kind: 'reading'}
  - visual_demand: {level: null, basis: 'qwen:<activity_hypothesis>'|null, confidence: null}
  - posture: 'standing'|'sitting'|'lying'|'unknown'
  - field_support: {focused_engagement: {last_support_capture_ts: float|null, value_set_ts: float, support_status: 'observed'|'expired'}}
  - sources: {focused_engagement: [{origin: 'qwen', ref: <request_id>}]} (an empty list before any answer)
  - interpreter: {path: 'rules', version: 'need-v0'}
  - flags: [str]
- mode (from the incumbent): the state of whoever owns the bulbs, i.e. the HA-local hold set.
  - policy: 'den_lighting@1.0.4'
  - revision: int
  - gaming: bool
  - occupancy: 'on'|'off'|'unknown'
  - holds: [{entity_id, pinned: scene, since: float, lifetime: 'room_vacant_2_minutes'|'game_ends'}], sorted by entity_id
  - normal: {entity_id: scene}
- decision (from the incumbent and from decide):
  - decision_id: '<policy id>-<round(t*1000)>-<n>'
  - ts
  - trigger: 'person_state change'|'hold change'|'mode change'|'correction'|'time step'
  - policy
  - actuator_domain: 'hue-den-4'
  - actuation: 'synthetic'|'replay' (set by the runner)
  - inputs: {person_state: [state_id], field_support: {state_id: field_support}, hold_set: {revision, held: [hold]}, revisions: {hold_set: int, mode: int}, actuator_state: {entity_id: scene|null}, config_sha256: str}
  - requested_scene: {entity_id: scene}
  - transitions: {entity_id: seconds}
  - candidates: [{scene_id, cost, why}]
  - skipped: [{channel, reason, hold_ref}]
  - hard_conflicts: []
  - status: 'requested'
  - why: str
  'mode change' is a v0 addition, for occupancy and game edges, which A9 counts as moves of the mode revision.

### Policy protocol

A module exposes make_policy(config=None). It returns an object with:
- id (str) and version (str);
- config: a JSON-serialisable dict holding the full effective config;
- code_path: the module's __file__;
- handle(event) -> list of {kind, data}.

handle receives the whole den-event-1 dict and takes time only from
event['t']. It never reads the wall clock, randomness, files, environment or
network.

The runner stamps schema, t, seq and source on each output and logs it. It
passes each output only to the policies after the producer, in the fixed order
need -> incumbent -> decide.

### Added after the briefs

The two adapters built after the three briefs extend the text above without
changing it:
- More source adapter names: 'occupancy_journal' (emits occupancy, via 'jetson_journal'), 'recorder_commands' and 'recorder_lights' (emits light_observed with brightness and kelvin null), 'den_lighting_status' (emits occupancy, via 'den_lighting_status', and live_status) and 'correction_capture' (emits correction).
- actuation: 'synthetic'|'replay'|'shadow' (set by the runner; shadow needs the plant off).
- decide's decisions add rung 9's fields (see "Rung 9: coordinator v0 on replay"): applied_scene, recipients, recipients_basis, predicted, hold_limited (P1-P3 only), and inputs.light_model, preference, comfort_limits, body_envelope, t_exec, exclusions, compensation, hard_constraints, open_slots. A person_state may carry working_region {surface_id}; decide keeps one per track_id.
- Reference kinds: logged with the inputs, but never handed to a policy, and they set neither the span nor the ticks.
  - live_command: {entity_id, revision: int, target: scene, context_id: str}, one den_lighting_command event as the live integration fired it, at its time_fired_ts.
  - live_status: {automatic: bool|null, gaming: bool|null}, whether den_lighting's status sensor showed it automatic and in a game (null without its status), on each change.
