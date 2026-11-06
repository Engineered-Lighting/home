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

**PowerShell 5.1:** don't redirect with `>`. It writes UTF-16, which `run.py`, `check.py`, `score.py` and `validate.py`
refuse by name with exit 2, on every file they read. Pipe to `Set-Content` as the commands below do, or repair a file with
`Get-Content bad.jsonl | Set-Content -Encoding utf8 good.jsonl`. They read the byte-order mark and CRLF that writes. What they
print to a pipe goes out in the console's code page, so a name it can't write comes out as a `\uXXXX` escape.

**What they refuse.** Any input that is missing, not JSON or the wrong shape is one sentence and exit 2, never a traceback. That
goes for what is inside a light model, a mark or `--truth`, for a log's header, its logged config and its input lines, as far as
`check.py` can't replay them, and for a time past any clock or inputs more than about a year of 2 s ticks apart, which is what
`--mqtt` with no `--mqtt-t0` beside real facts would be. A log of the right shape with a line changed or wrong inside is a
failed check, not a refusal, with exit 1: `check.py` prints the line, `validate.py` goes on check by check, the check that can't
read the line failing and saying so, and `score.py` fails its replay line and says where it stopped reading. Anything no check
named still ends in one sentence and exit 2: `<tool> stopped on an input of the wrong shape, or a bug of its own`, with the
exception and where it happened, so a bug in the tool can still be found.

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
`--no-plant`, `--policies need incumbent decide`.

`check.py LOG` re-imports each logged policy module, refuses `code changed:
<module>` when its file's sha256 differs, rebuilds it from the logged config
(den_lighting refuses a changed `policy.py` by itself), re-runs the logged
adapter inputs with the logged parameters and compares every line byte for
byte, header included. It prints `OK` or the first mismatching line and exits
0 or 1, or 2 when it can't read the log or a logged light model, or when the
header, a logged config or an input line is the wrong shape to replay. A copy
with a byte-order mark and CRLF compares the same.

```bash
python -B tools/den-core/validate.py C:/tmp/den-core/evening-1/log.jsonl --truth C:/tmp/den-core/evening-1/truth.json
```

`validate.py LOG [--truth TRUTH_JSON]` says check by check whether a run
behaved as designed against den_lighting: the replay above; need's reading
on/off moments against den_lighting's own dwell; that every interval of the
diff above is decide's labelled placeholder reading look; that decide pins
and skips held bulbs; and that it stays silent in games and while occupancy
is unknown. It prints OK, FAIL or n/a per check and exits 0 or 1, or 2 when it
can't read a file. It is a consistency check of den-core's own code, not a
lighting-quality gate.
`--truth` adds, as information only, how long need took to follow each
reading block's cues; `tools/reading-shadow` is the gate for those. A truth file
that is missing or the wrong shape is refused like any other file, before any
check runs. One with no reading blocks, or a log with no `need`, reads n/a.

Tests:

```bash
python -B -m unittest discover -s tools/den-core -p "test_*.py"
```

On a branch without den-light, `test_seam.py`'s `RealPredictTest` skips and the scoring tests stand on
`testdata/predict_stand_in.py`. Set `DEN_LIGHT_DIR` to a den-light folder to run them on the real `predict.py`. Only tests
read that variable. On the merged tree they use the sibling.

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
| `need.py`, `decide.py` | written on their own branches against the interface below; the runner loads them by name |
| `coordinator.py` | rung 9's coordinator v0: decide's rules behind the light model, as policy `decide` (below) |
| `light_adapter.py` | the one adapter to rung 8's light model; every prediction goes through it, into den-light's `predict.py` |
| `fake_light.py` | rung 8's prediction rule from its plan, for a model marked `fake: true` or a checkout with no `tools/den-light`; plumbing only |
| `testdata/predict_stand_in.py` | `predict.py`'s call on the fake's rule, for the scoring tests on a branch without den-light; only a test reaches it |
| `rung9-gate-v0.json` | rung 9's fixed numbers and its open slots |
| `score.py` | rung 9's gate on a replayed log; refuses a scored run while a slot is open |
| `hold_fixtures.py` | cases V6-V8 and V62-V64 under each compensation option; not admitted as pass or fail |

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
- All three exports are ASCII, because `Set-Content` writes them. See
  "Run and check" on `>`.

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

## Rung 9: coordinator v0, scored on replay

`coordinator.py` is decide v0's rules with rung 8's light model behind them. It runs beside the frozen den_lighting
and sends nothing. Nothing has run on real data. A model file goes through den-light's own `predict.py`. With no
`--light-model` the coordinator uses a fake built from rung 8's plan and marked `fake: true`, so the commands below are
plumbing and never evidence.

From the worktree root, in PowerShell 5.1:

```powershell
python -B tools/den-core/run.py --synthetic evening --touch --policies need incumbent coordinator --out C:/tmp/den-core-r9/evening-1
```

```powershell
python -B tools/den-core/check.py C:/tmp/den-core-r9/evening-1/log.jsonl
```

```powershell
python -B tools/den-core/score.py C:/tmp/den-core-r9/evening-1/log.jsonl --preview
```

```powershell
python -B tools/den-core/hold_fixtures.py
```

```powershell
python -B tools/den-core/score.py --slots
```

`run.py` takes `--policies need incumbent coordinator` in place of `decide`, and with it `--light-model PATH`
(rung 8's `light-model.json`; the default is the fake), `--surfaces FILE` (JSON `{surface_id: [point_id, ...]}`, to
preview a map; the default is the gate file's `surface_map` slot, and with neither the whole view is one surface, `room`),
`--light-states FILE` (below), `--compensation P0|P1|P2|P3` and `--compensation-form strict|mode`. All of these need the
coordinator. `--actuation shadow` labels a run shadow in place of replay, with or without the coordinator, and needs the
real bulbs' reports (`--lights` or `--no-plant`). Until a live writer exists it labels a run of copied files from such a
time. Nothing is sent either way, and a synthetic run is only ever synthetic.

### From a real light model to rung 10

Rung 8 Stage 1 writes `light-model.json` in its session folder (`SESSION` below), and rung 7 writes `light_state.jsonl`. With
those and the evening's copied files, in PowerShell 5.1, from the worktree root. `surfaces.json` is your map over the model's
`rRRcCC` blocks, to preview; a scored run takes it from the gate's `surface_map` slot instead, as below:

```powershell
python -B tools/den-core/run.py --facts C:/tmp/den-core/in/facts --journal C:/tmp/den-core/in/occupancy-edges.txt --policies need incumbent coordinator --light-model C:/den-light/SESSION/light-model.json --surfaces C:/tmp/den-core/in/surfaces.json --light-states C:/tmp/den-core/in/light_state.jsonl --out C:/tmp/den-core-r9/real-1
```

```powershell
python -B tools/den-core/check.py C:/tmp/den-core-r9/real-1/log.jsonl
```

```powershell
python -B tools/den-core/score.py C:/tmp/den-core-r9/real-1/log.jsonl --preview
```

Then rung 10 reads what that wrote:

```powershell
python -B tools/den-verify/verify.py run --decisions C:/tmp/den-core-r9/real-1/decisions.jsonl --surfaces C:/tmp/den-core-r9/real-1/surfaces.json --light-states C:/tmp/den-core/in/light_state.jsonl --model C:/den-light/SESSION/light-model.json --preview --out C:/tmp/den-verify/real-1
```

den-core's tests don't run that line: they check only that it names these files, and it needs a `verify.py` with `--surfaces`.

When the coordinator ran, and only after the log is complete, `run.py` writes two more files beside `log.jsonl`. A run
without the coordinator writes neither, and every output it has stays byte-identical.

- `decisions.jsonl`: the coordinator's Decisions only, one per line, UTF-8, LF, in log order. Each line is its `decision`
  event's `data` from `log.jsonl`, plus one key, `version: "decision/v2"`, because `docs/architecture/live-v1/README.md`
  asks a record in JSON Lines to carry its version, and v2 is the major `schemas.json` proposes for a Decision.
- `surfaces.json`: `{surface_id: [point_id, ...]}`, the map the coordinator actually ran on, `room` spelled out. Giving it
  back as `--surfaces` gives the same map.

`--light-states FILE` takes canon LightState JSON Lines in the shape den-photometry writes: no `id`, `ts` present. A frame's id
is its `id` key if it has one (64 characters at most), else `"ls-%.3f" % ts`. Frames may be in any order in the file; two
within 0.0005 s, which rung 10 could not tell apart, are refused. Each Decision then names the newest frame at or before its
`ts`, never a later one, in `inputs.light_state` and `inputs.light_state_ts`. With none at or before it, both are null and
`inputs.light_state_status` says `no LightState at or before this decision`; with no file it says `no LightState given`. It is
dropped when a frame is named.

The frames are references, not inputs to the solve. Only each frame's id and ts reach the log, in the coordinator's logged
`config.light_states`, so `check.py` replays them. No other policy sees them. A run with the file writes the same Decisions as
one without, apart from the `light_state` fields, which go on a line after it is fitted, in room held for them. The config hash
each Decision logs leaves the frames out, so it is the same either way; the header's own hash and `sources` do move.

All of this but rung 10's line has run, on synthetic data only: `test_seam.py` runs it on a model in the shape Stage 1 writes,
through den-light's own `predict.py` on the merged tree.

### What a scored run needs

`score.py LOG [--marks FILE]` refuses, and exits 2, on any of these. `--preview` runs anyway on the proposals logged in
the header, prints no verdict and is never a gate. A log with no `incumbent` in it, whose comparator is den_lighting re-run
beside it, or with no coordinator, is refused either way.

- an open slot in `rung9-gate-v0.json`. `score.py --slots` lists them with their proposals. To close one, set its
  `status` to `closed` and its `value`, then rerun the evening: a log run under another gate file refuses;
- a synthetic log;
- a fake light model: one marked `fake: true`, which goes through `fake_light`, or any model on a checkout with no
  `tools/den-light/predict.py`, which the same fake then stands in for. `run.py` still works there and labels the run
  fake, and `score.py` says which of the two it was. Any other model goes through den-light's own `predict.py`, loaded by
  file path under a private name and never through `sys.path`, and `prepare()`d once. `score.py` names the backend, and the
  logged light model entry carries `backend`, `fake` and `predict_sha256`: `check.py` and `score.py` refuse
  `code changed: den-light predict.py` when that file's hash moves. No environment variable can move the backend;
  a test can, by patching `light_adapter.PREDICT_PATH`. `fake_light.predict` follows Stage 1's published call, and
  `test_seam.py` pins that the two agree exactly on the fake's own shape. den-light's model has no aim, so a real-path
  model with a `spot_model` is refused rather than having its aims ignored;
- a log run under a compensation option other than the gate's. Those are the counterfactual scenes the register
  suggests: preview them;
- a surface map that can't stand behind a score. Rung 8 v1's blocks have no surface id, so which blocks form each surface
  is the owner's mark on its grid (Stage 1 names them `rRRcCC`), in the `surface_map` slot. A closed map reaches a run on
  a real model with no flag; a fake keeps its own surfaces, so plumbing runs do not change when the slot closes. It must
  name `room`, because need v0 has no working region, so its one track is scored at `room`, and an unnamed `room` is the
  whole view's mean. Every surface needs a block and every block must be in the model: `run.py` and a preview name any
  surface that breaks this. A log run on another map (`--surfaces`) refuses; preview it. The slot's proposal names no
  block, so closing it at the proposal still refuses.

### What it gates and what it reports

Both arms go through one light model version: the candidate's applied scenes, and den_lighting re-run beside the log
(`incumbent.Shadow`), with held bulbs pinned at read-back in both. Game time leaves the comparison.

Gated, exit 1 on a failure and 3 on too little evidence:

- **replay**: every line byte for byte (`check.py`), and **decision time**: the slowest call under 100 ms. On Hue bulbs
  an aim is the null path; `test_coordinator.py` times the solve on the real grid's 576 blocks, from a cold cache, with
  a simulated spot's 32 aims on one channel;
- **records**: every Decision carries `requested_scene`, `applied_scene` and `recipients`, plus `predicted` and
  `targets`. Only the candidate's do: den_lighting's lines stay as they were;
- **exclusions**: `predicted.eyes` recomputes from `inputs.person_state` (the `state_id`s), `inputs.field_support`,
  `inputs.body_envelope.version` and `inputs.t_exec`, and nothing else. A kept channel's `t_exec` is null with a status,
  as the schema says. Envelope parameters are pending, so nothing grows with age yet;
- **steps**: every step logged as sent passed its check;
- **hard constraints**: the seed Look's glare limit holds on free channels, and each held light that alone breaks it is
  in `hard_conflicts`. It stays as set (manual-vs-safety S0) and free channels may not raise their part of it. Rung 8
  writes `glare: null`, so on its model `hard_conflicts` stays empty and `G`, `G_hi` and `limit` are null, with
  `eyes_note` saying why. The numbers appear when a model has a glare table, as the tests' fake does;
- **anticipation and stability**, the replay surrogates, each against its floor.

Reported, never gated, each labelled `predicted, replay surrogate`: predicted service coverage and missed service per
occupied hour at the owner's marks, `hold_limited` minutes at the marks under P1 to P3, and each arm's mean predicted
light. Light is electric only: rung 8 does not model daylight. Floors are in lux, so a relative model leaves every mark
`unknown` rather than guessing a scale, and photometry-commissioning is still open.

**The surrogates, `need-raise-v0`.** The contracts name both rates and say den_lighting scores 0 and 1 by
construction, but give no formula (the design sheets are not copied), so this is a proposal, and closing the
`surrogate_definitions` slot accepts it.

- *A need episode* is a person's move into `engaged` from `none` or `starting`, while occupied and out of a game.
- *Anticipation rate*: the share of episodes where, within `anticipation_window_s` after that move and before any
  correction, a decision triggered by `person_state change` raised the predicted light at the person's surface by at
  least `anticipation_rise_min`. den_lighting makes no decision on a person's state, so it scores 0.
- *Stability rate*: the share of an arm's `person_state change` scene changes that stood at least `stability_dwell_s`
  before the next one. An arm with none scores 1, which is den_lighting.

**Marks** (`--marks`) are JSON Lines, one gold need episode each, following `EvalRecord.R_eval`:

```json
{"episode_id":"e1","track_id":"A","band":"fine","start":1793766000.0,"end":1793766300.0,"R_eval":{"surface_id":"armchair-book","placement_sd":3,"source":"gold_mark"}}
```

A mark is served when at least 70% of the visible patch is at the band's floor (300 lx fine, 200 lx casual), with at
least 50% of the patch visible, standing uncorrected for 120 s or for the whole of a shorter mark. A mark names its
surface; a surface is the blocks `surface_map` gives it, because rung 8 v1 has no room geometry for a mark's outline.

### The solve

A reading scene's target is the seed Look's reference scene, predicted with nothing held, at each reader's surface. The
cost is the squared relative shortfall and excess there, plus a small pull toward the look's level and P2's penalty. The
solve is coordinate descent over the free bulbs. Base and vacancy-off stay den_lighting's own scenes, as in decide v0.

Look and Preference are fixed stand-ins, logged in the header: one seed Look, a level factor of 1, and `mired_shift` and
`contrast_shift` at 0. `make_policy` refuses any other offsets.

### Compensation: built as named options, not admitted

`decisions.json` leaves `compensation` to the owner, so V6-V8 and V62-V64 are not admitted as pass or fail.
`hold_fixtures.py` prints what the coordinator does in each under P0, P1, P2 and P3, strict and mode, and P3's two
fallbacks. V8 also runs under local-completion allowed, and V63 follows den_lighting's moves as `complete-policy`, the
gaming option its expected results describe. `test_hold_fixtures.py` checks that each option does what its register
entry and ladder rung 11's V63 text say. That tests the code and admits nothing.

- A hold's direction and footprint come from what the person's change did to predicted light at each block: the
  register's "points it moved", because a surface's mean can hide them. A dim reaching 12 blocks of a 144-block surface
  barely moves the surface, but P1 binds free light at those 12. Free light is measured against a reference that only
  moves with the change, so a bound never lets a bulb back.
- Under P1 and P3 a free bulb stays where it is rather than move against the change; P3 binds only at the footprint's
  blocks in the changer's own task surface, and falls back when the changer is unknown. Real corrections carry no changer
  yet, so they all fall back.
- Under P2 a solve pays `lambda_h` times the squared share of the light at each surface's footprint blocks that free light
  moved against the hold, so it makes up part of a gap, and a rule's own move goes `1 / (1 + lambda_h)` of the way.
- In the mode form, vacancy-off, the arrival after it and a game's moves sit outside the bound. A return to base because
  reading ended stays inside.

### Why decide.py is unchanged

`check.py` refuses a log whose policy file hash has changed. Editing `decide.py` would make every existing log refuse
with `code changed: decide`, so the coordinator imports it and adds to it instead, as policy id `decide`, so the
runner's order, `run.py`'s diff and `validate.py` read it as they read decide. `make_policy` pins `decide.py`,
`light_adapter.py` and `fake_light.py` by hash in its config, as the incumbent pins `policy.py`, and the light model entry
pins den-light's `predict.py`.

`--policies need incumbent decide` is byte-identical to before rung 9: `test_coordinator.py` pins the sha256 of the
four synthetic evenings' whole logs, header included.

### A Decision's size

`events.validate` refuses a line over 16,384 bytes, and the run then stops with no log. A Decision never lists blocks, but
under P1 to P3 each held bulb's footprint carries a mask of one hex digit per 4 blocks, whichever blocks it covers: 144
digits on rung 8's proposed grid. A line cannot shed a mask, so with every bulb held it grows by a byte a block. P0 writes
no mask.

That limits `block_px`, rung 8's open slot, which must divide 640 and 360. `block_px` 8 or coarser fits with every bulb
held: about 8.6 KB at the proposed 20 (576 blocks), 10.3 KB at 10 and 11.6 KB at 8 (3,600 blocks), with one reader. `block_px`
5 (9,216 blocks) fits three held bulbs, and the fourth stops the run with `decision line over 16384 bytes` and no log.
`block_px` 4 fits two, and at 2 or 1 the first hold stops it. Closing the slot finer than 8 would need a shorter mask first.
`test_coordinator.py` pins these through `runner.run`, which validates each line, and runs every hold fixture under every
option on the proposed grid.

`predicted.surfaces` lists the whole map, because rung 10 takes its verify points from those keys: a surface nobody is at
can only be scored as a miss if it is listed. That costs about 40 bytes a surface with a 30-character name, so with every
bulb held and one reader on the proposed grid, about 420 surfaces with 10-character names or about 200 with 30 fit whole,
and each more track or a finer grid's masks take some of that room. On a wider map `room` and each tracked person's
surface stay, the rest go on by name for as long as they fit, and `predicted.surfaces_not_logged` counts the ones that did
not. Surfaces go before any track does. `score.py` says when this happened, and rung 10 can verify only what a Decision
lists.

A line also grows with the tracks: about 1.4 KB each with need v0's one field, 2 KB with every field the schema names, or
3.3 KB in the worst shape the contracts allow, with a glare table and every bulb held. The coordinator never forgets a track,
because an id is never reused and a PersonState says nothing when its track ends, so a long evening on rungs 5 and 6 would pass the limit. When a Decision would not fit, the rows of
the tracks longest unheard from go, then readers, and last any track a light is over the glare limit at.
`inputs.tracks_not_logged` counts them. They go whole, from every list, so `predicted.eyes` still reproduces, and they stay
in the plan: a track left off the line is still kept under the glare limit. `score.py` says when this happened. If so many
tracks are over the limit that a row saying so has to go too, `inputs.over_limit_not_logged` counts it and **hard
constraints** fails, because the log can no longer show every conflict. need v0 has one track, so none of this happens today.

The last track always stays and a mask never goes, so one line can still break in two ways: on a grid finer than `block_px`
8, as above, or with a lone track whose id runs to a couple of hundred characters, in that worst shape, fewer on a finer
grid. Contract ids are far shorter.

### Keys the contracts do not name

Behind the `record_extensions` slot, proposed `coordinator-ext/v1`. They appear only in the coordinator's Decisions:

- `candidates[]`: `scene_id` (v0's) and the candidate's `scene`.
- `requested_scene` and `inputs.actuator_state` keep the hue-den-4 dialect `{on, brightness, kelvin}`, with `aim` when a spot
  model has one, beside the schema's `level` and `cct_k`. `level` is the command as a share of Hue's 254, 0.0 when off, and
  null when a bulb is on with no brightness, because a recorded bulb has none. A bulb never seen is null in `actuator_state`.
- `inputs.hold_set.held[].pinned` is the logged copy of a hold's pin: hue-den-4's keys beside the schema's `level_pct` (0 to
  100, null when unknown), `cct_k` and `source: "read-back"`. The mode's own pin keeps only the three keys, for `decide`.
- `applied_scene[]`: `readback` also holds `{on, brightness, kelvin}` beside the schema's `level` and `cct_k`;
  `readback_source`, and a `reason` when a step was dropped. A step adds `transition_s`, which rung 10's settle rule
  reads, and its `checks` is `{revision_current, fresh_check, not_held}`.
- `targets[track]` and `recipients[].targets[]`: `value` and `unit` beside `lx`, because a relative model has no lux.
- `recipients[]`: `light_model`, and each target's `interval` (null until the model has an error score).
- `predicted.eyes[]`: `support_status` and `excluded`. `predicted.daylight` says electric only.
- `inputs`: `preference`, `compensation`, `coordinator`, `recipient_rule`, `light_state_ts`, `light_state_status`.
- The logged light model entry in the header: `backend`, `fake`, `predict_sha256` (null on a fake backend).
- `inputs.hold_set.held[].footprint`: the contracts name it and give it no type. Here it is
  `{points, of, mask, surfaces}`: how many blocks of how many, the blocks as hex bits over the model's `sample_points`
  (bit 0 the first; `LightModel.unmask` reads it), and which of the tracked people's surfaces it touches.
- `predicted.surfaces_not_logged`: only on a Decision that left surfaces of the map out to fit its line.
- `inputs.tracks_not_logged`: only on a Decision that left a track's rows out to fit its line.
- `inputs.over_limit_not_logged`: only when rows that put a light over the glare limit at an eye left the line with their
  track. Any at all fails **hard constraints**.

### Left for later

The simulated-spot execution cases V9a-V9h, V9k, V9l and V65(b), since den-light's `predict.py` has no aim; rung 9's loop
check (non-inferiority, every delta open); per-person need and eye positions (rungs 5 and 6: today a track's surface is its `working_region.surface_id`, else
`room`, and an eye is named by its `track_id`); when a track has left (rungs 5 and 6: a PersonState says nothing when its
track ends and need v0 has no heartbeat, so a track stays in the plan, a reader who left included); and the kept_current
outcome, whose deadline is pending.

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
- Reference kinds: logged with the inputs, but never handed to a policy, and they set neither the span nor the ticks.
  - live_command: {entity_id, revision: int, target: scene, context_id: str}, one den_lighting_command event as the live integration fired it, at its time_fired_ts.
  - live_status: {automatic: bool|null, gaming: bool|null}, whether den_lighting's status sensor showed it automatic and in a game (null without its status), on each change.
- A third actuation, 'shadow', set by the runner like the other two; see "Rung 9" above for what the coordinator's decisions add.
