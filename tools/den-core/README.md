# den-core

The single offline replay path for the den's decision foundations, with
den_lighting wrapped unchanged as the frozen baseline (incumbent and
comparator). This is K19 record/replay, ladder rung 0 "Record and replay" in
miniature: every logged decision, den_lighting's included, replays from its
logged inputs and versions to the same requested_scene.

Fake data tonight, copied real files later and a live writer eventually all
enter only as den-event-1 lines, so nothing downstream changes when the source
does.

- Standard-library Python 3.12, run with `python -B`. Offline only: no
  network, Home Assistant, MQTT, camera, Jetson or LA host, and no light
  commands of any kind. "Decisions" here are log lines.
- Nothing is ever written inside a git checkout. `events.write` and `run.py
  --out` refuse any path whose folder (or a parent) holds `.git`. Tests write
  only to temp folders.
- **Synthetic runs are never evidence.** They prove the plumbing, not the
  lighting, and never count toward any gate. `run.py` prints
  `synthetic: plumbing only, never a gate` for them.

## Run and check

From the worktree root, with an absolute output folder outside every checkout:

```bash
python -B tools/den-core/run.py --synthetic evening --touch --game --policies incumbent --out C:/tmp/den-core/evening-1
```

```bash
python -B tools/den-core/check.py C:/tmp/den-core/evening-1/log.jsonl
```

`run.py` writes `log.jsonl` and prints the inputs per kind, each policy's
decisions (UTC time, seconds from the first input, trigger, one-line scene)
and, when both `incumbent` and `decide` ran, the intervals where decide's
latest requested scene for a bulb does not match den_lighting's latest
commanded target (game time left out). With `--synthetic` it also writes
`truth.json` (reading-shadow-truth-1) and the generated
`la-fact-log-1-YYYY-MM-DD.jsonl`, so `tools/reading-shadow/replay.py` can
score the same fake evening.

Other inputs: `--facts PATH ...` (day files, or folders of them), `--poll FILE
...` (den-desk-observe HA poll logs), `--mqtt FILE --mqtt-t0 EPOCH` (the
victoria-ha Frigate step lists), `--assume-occupied` (logged as occupancy
`via: assumed`, for facts-only replays), `--no-plant`, `--policies need
incumbent decide`.

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
| `sources.py` | file adapters: `fact_log`, `poll_log`, `mqtt_timeline` |
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

## How real data plugs in later (copied files only)

Agents do not reach the Jetson, LA or Victoria HA. The owner copies files to the
workstation, outside every checkout, and these tools replay them.

- **Jetson `fact_log_directory`** (the LA perceiver's la-fact-log-1 day
  files): `--facts COPIED_FOLDER` works today. **Copy the whole folder before
  any perceiver restart:** `FactLog` deletes day files 14 or more days old
  when it rotates, and it checks when the log opens.
- **tools/reading-shadow run folders**: they hold la-fact-log-1 day files and
  `truth.json`. `--facts RUN_FOLDER --assume-occupied` works today, because
  facts-only folders carry no occupancy.
- **Jetson journal occupancy edges** (`occupancy edge: entity=... from=...
  to=... observed_at=...`): a future adapter that reuses
  `tools/la-latency/occupancy_flaps.py`'s `parse()` and emits `occupancy`.
- **HA recorder export of the four bulbs with Context, plus den_lighting's
  status entity**: a future adapter that emits `light_observed` with `context`
  own/person/unknown. Turning a person's change into a `correction` needs the
  attribution rules, which are parked.
- **`den_lighting_command` events** (entity_id, revision, target,
  context_id): a future adapter that lets a real run's logged incumbent
  decisions be compared with what den_lighting actually sent.
- **Eventually a live writer** that emits den-event-1 lines itself. It needs
  the owner's approval, and it only replaces the adapter.

The two Oct 28 den-desk-observe poll logs replay through `--poll`. They are
adapter smoke tests, not evidence: about 2.5 minutes each, on/off only, with no
brightness or kelvin. **den_lighting ignores HA OFF** (vacancy needs a
non-retained MQTT zero), so a poll log's `occ=off` only approximates its
input, and a replay may turn the bulbs off where the live integration did not.

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
