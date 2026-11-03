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
latest requested scene for a bulb does not match the bulb as den_lighting left
it: its latest command, or else the latest correction or observed state, since
den_lighting leaves held bulbs, and bulbs already where it wants them, alone.
Game time is left out, and events at one instant are compared once, after all
of them. When `--live-commands` gave the commands the live den_lighting sent,
it also prints, per bulb, how many commands each side sent and the intervals
where the incumbent's latest replayed command does not match the latest live
one (see "How real data plugs in"). With `--synthetic` it also writes
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
`--no-plant`, `--policies need incumbent decide`.

`check.py LOG` re-imports each logged policy module, refuses `code changed:
<module>` when its file's sha256 differs, rebuilds it from the logged config
(den_lighting refuses a changed `policy.py` by itself), re-runs the logged
adapter inputs with the logged parameters and compares every line byte for
byte, header included. It prints `OK` or the first mismatching line and exits
0 or 1.

Tests:

```bash
python -B -m unittest discover -s tools/den-core -p "test_*.py"
```

## Files

| File | What it is |
| --- | --- |
| `events.py` | den-event-1: constants, `dumps`, `validate`, `read`, `write`, `inside_checkout`, `scene_matches` |
| `sources.py` | file adapters: `fact_log`, `poll_log`, `mqtt_timeline`, `occupancy_journal`, `recorder_commands` |
| `synthetic.py` | `evening()`: a fake evening in the real la-fact-log-1 format, built with the perceiver's own builders |
| `runner.py` | `run()`: the deterministic heap runner; `load_policies()` |
| `incumbent.py` | den_lighting's `policy.py`, loaded by path and unchanged, as a policy |
| `check.py` | the byte-identical replay check |
| `run.py` | the CLI and its summary/diff |
| `need.py`, `decide.py` | written on their own branches against the interface below; the runner loads them by name |

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
- **HA recorder export of the four bulbs with Context, plus den_lighting's
  status entity**: a future adapter that emits `light_observed` with `context`
  own/person/unknown. Turning a person's change into a `correction` needs the
  attribution rules, which are parked. **Until it exists, a facts + journal
  replay sees no bulb state**: den_lighting never turns on a bulb it has never
  seen, so the replayed incumbent sends only vacancy OFFs, and after one of
  its own OFFs a last-level ON with no level. Expect every live ON to differ
  from the replay until then.
- **Eventually a live writer** that emits den-event-1 lines itself. It needs
  the owner's approval, and it only replaces the adapter.

The two Oct 28 den-desk-observe poll logs replay through `--poll`. They are
adapter smoke tests, not evidence: about 2.5 minutes each, on/off only, with no
brightness or kelvin. **den_lighting ignores HA OFF** (vacancy needs a
non-retained MQTT zero), so a poll log's `occ=off` only approximates its
input, and a replay may turn the bulbs off where the live integration did not.

A full replay of copied files, from the worktree root:

```bash
python -B tools/den-core/run.py --facts C:/tmp/den-core/in/facts --journal C:/tmp/den-core/in/occupancy-edges.txt --live-commands C:/tmp/den-core/in/den-lighting-commands.jsonl --policies need incumbent decide --out C:/tmp/den-core/real-1
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

To cut it to one evening, add `--since 'YYYY-MM-DD HH:MM' --until 'YYYY-MM-DD
HH:MM'` (the Jetson's local time) after `-o cat`. Otherwise the replay spans
every edge the journal still holds, with a tick every 2 s. Copy it before
journald's size limit rotates the evening out.

How a line becomes an event:

- The lines are matched by `occupancy_flaps.EDGE` and read by its `parse()`,
  imported from `tools/la-latency`. Other lines are ignored.
- Only `entity=binary_sensor.den_oak_person_occupancy` (the den's Frigate
  person-occupancy entity, which den_lighting also reads) is kept.
- `to=on` becomes occupancy `on`, `to=off` becomes `off`, and any other `to=`
  becomes `unknown`. `from=` is not used. The event is
  `{value, via: 'jetson_journal'}` at `t = observed_at`.
- An edge that appears twice, such as in overlapping copies, counts once.
- The reader logs only edges into `on` and from `on` into `off`. A dropout
  (stale or unavailable) logs nothing until the next `from=unknown to=on`, so
  the replay keeps the last edge's value through it. den_lighting itself
  turns a dropout into unknown.
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
  (from the first input to 150 s after the last) and says how many it kept.
  So a whole 10-day export can serve any evening.
- The summary's second comparison prints each bulb's command count, live and
  replayed. It then lists the intervals where the incumbent's latest replayed
  command for a bulb does not match the latest live one, in the style of
  `diff()`.
  - The live command is the target, so a replayed null level does not match a
    live level.
  - A bulb is compared from its first command on either side.
  - Game time is included.
- `time_fired_ts` is when HA fired the event, after the light call returned,
  and the replay's timed decisions (vacancy OFF) fall on its 2 s tick grid.
  So agreeing commands still show as short intervals of a second or two. Read
  the long ones.
- Both files are ASCII. If you redirect with `>` instead of `Set-Content`,
  PowerShell 5.1 writes UTF-16, which the adapters also read.

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
- More source adapter names: 'occupancy_journal' (emits occupancy, via 'jetson_journal') and 'recorder_commands'.
- Reference kinds: logged with the inputs, but never handed to a policy, and they set neither the span nor the ticks.
  - live_command: {entity_id, revision: int, target: scene, context_id: str}, one den_lighting_command event as the live integration fired it, at its time_fired_ts.
