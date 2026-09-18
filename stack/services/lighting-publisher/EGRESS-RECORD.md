# The egress record

The egress record is the owner-signed statement that the lighting belief
publisher may send packets to TypeSafe at all, for which purpose, and with
which model. It is the first rollback rung: disabling it stops every caller
(the offline runner `tools/public_story_eval.py` today, the publisher
container from M4) before any other switch is consulted. The gate that reads
it is `lighting_beliefs/egress.py` (`EgressGate`, `load_record`); the
redacted example next to this file is `egress-record.example.json`.

The real record lives outside the repository, in the directory the publisher
reads (group-readable by uid 10001 from M4). It is never committed.

## Schema: `lighting-egress-record/v1`

Every field of `egress-record.example.json`, what it means and who sets it.
"Gate" marks the fields `EgressGate` evaluates; the rest are documentary and
are read by humans, by the runner (`model_id`) and by the receipts.

| Field | Type | Read by | Meaning | Set by |
|---|---|---|---|---|
| `schema` | string | gate | Must be exactly `lighting-egress-record/v1`; any other value makes the record `invalid` and the gate denies with `record_invalid`. The other record-level denials are `record_absent` (no file), `record_not_a_file` (a symlink or a non-regular file) and `record_insecure` (a group or other write bit, or the wrong owner). | owner, when signing |
| `enabled` | boolean | gate | `true` allows calls for the listed scopes; anything else (including a missing field or the string `"true"`) denies with `record_disabled`. Setting it to `false` is the rollback. | owner |
| `scopes` | list of strings | gate | The scopes this signing allows (see below). A request for a scope not in the list denies with `scope_mismatch`. Non-string entries are ignored. | owner, when signing; extending it means re-signing |
| `signed_by` | string | humans | Who signed. The example is redacted; the real record carries the owner's name, which is why the file is 0600 and outside the repo. | owner |
| `signed_at` | string `YYYY-MM-DD` | humans, receipts | The signing date. A new scope gets a new date. | owner |
| `purpose` | string | humans | One paragraph: what the calls are for and what data may go out. | owner |
| `data_classes` | list of strings | humans | The classes of text this signing covers: `public_dataset_text` for level 1 and 2, `household_packet` from `household_shadow` on. | owner |
| `model_id` | string | runner | The pinned versioned model id (for example `jev-1.13.0`, taken from `models.list()` or the models page). Never an alias: `jev-latest` moves without notice and would silently change every threshold. `--execute` refuses when `--model` differs from this field or when the field is not pinned. | owner, from `models.list()` |
| `kill_switch` | string | humans, M4 mirror | The Home Assistant `input_boolean` entity id of the egress toggle that the publisher mirrors. The gate never reads Home Assistant; it reads the mirror (below). | owner |
| `notes` | string | humans | Free text: where the real record lives, which scopes have been added, call counts per signing. The plan's definition of done requires every scope with dates and call counts here. | owner |

Unknown fields are ignored by the gate and preserved by nobody: keep the
record to these fields.

## Scopes

A scope names one kind of caller and one class of data. A record lists the
scopes it allows; a caller asks the gate for exactly one scope per call.

| Scope | Caller | Data that may leave | Milestone |
|---|---|---|---|
| `public_eval` | `tools/public_story_eval.py --execute` (level 1: Charades description rows; level 2: one bounded visual run's typed observations of public clips) | Public-dataset text only, as `lighting-beliefs-state/v1` packets. No household prose, no camera names from the house beyond the generic scene names the exporter writes. | M3, M5 |
| `household_shadow` | The publisher container in shadow: it calls Jev on real household packets, journals the beliefs, and actuates nothing (the belief entities stay `_shadow` twins). | Household packets that passed `packet.build_packet` and `leak_guard.assert_clean` with the roster loaded. | M6 |
| `household_live` | The publisher after cutover: the same packets, and the beliefs drive `tv_watching` and the asleep estimator. | Same as `household_shadow`. | M7 |

Each scope must be added by a new signing (new `signed_at`, updated
`notes` with the expected call count); the gate does not distinguish the
signings, the owner does.

## Required file mode

The record must be a regular file (a symlink is refused as `record_not_a_file`),
owned by the uid that reads it when the caller passes `record_owner_uid`,
and must not be group- or other-writable. The required mode is `0600`; the
gate tolerates `0644` and `0640` (no write bit beyond the owner) because the
M4 container reads a group-readable copy as uid 10001, but the owner's
master copy is `0600`. A machine with `umask 0002` needs an explicit
`chmod` or the gate denies with `record_insecure`.

```sh
chmod 0600 /path/to/egress-record.json
ls -l /path/to/egress-record.json   # -rw------- 1 <owner> <group> ...
```

## The toggle mirror file

The Home Assistant kill switch never reaches the gate directly. The M4
publisher mirrors the `input_boolean` over MQTT into memory; the offline
runner and the tests read the same shape from a local JSON file
(`egress.file_toggle_reader`). The format is:

```json
{"state": "on", "received_at": 1789700000}
```

- `state`: the toggle's state as a string; only `"on"` allows.
- `received_at`: Unix epoch seconds (a number, not a string, not a boolean;
  `NaN` and `Infinity` are rejected) when the state was observed. The gate
  computes `age = now - received_at` and allows only when `0 <= age < 300`
  (a few seconds of clock skew are tolerated; `age >= 300` is `toggle_stale`,
  an invalid or missing file is `toggle_absent` or `toggle_invalid`).

For an offline evaluation the owner writes the mirror by hand immediately
before executing, which is the point: the file goes stale by itself after
five minutes, so a forgotten terminal cannot keep egress open.

```sh
python3 -c 'import json,time; print(json.dumps({"state": "on", "received_at": int(time.time())}))' \
  > /path/to/egress-toggle.json
chmod 0600 /path/to/egress-toggle.json
```

Writing `{"state": "off", ...}` (or deleting the file) is the second rollback
rung.

## The environment switch

`TYPESAFE_EGRESS=1` must be set in the environment of the process making
the call. Any other value (`0`, `true`, `yes`, absent) denies with
`env_off` or `env_absent`. Set it on the command line of the one invocation
rather than in a profile, so it is never on by default. This is the third
rollback rung.

## The API key

The SDK reads the API key from the environment variable `TYPESAFE_API_KEY`
(the runner names it `API_KEY_ENV`). The key lives in a `0600` file outside
the repository (the plan asks for one copy for uid 1000 and one for uid
10001); source it into the shell of the one invocation and never write it
into a record, a receipt, a journal, a change fragment or chat. The runner
checks that the variable is non-empty before it builds the client and
otherwise refuses; it never prints, logs or stores the value, and the SDK
redacts the `Authorization` header from its own logs.

## The journal

Every gate decision, allowed or denied, and every release of an in-flight
ticket is appended as one JSON line to the journal. The runner writes it at
`<out>/egress-journal.jsonl` (mode `0600`); the M4 container writes it to
its own volume. Fields:

| Field | Meaning |
|---|---|
| `event` | `decision`, `release` or `in_flight_expired` |
| `ts`, `ts_iso` | The gate's clock at the event (the journal is the one file with absolute times; it stays under the private directory) |
| `scope` | The scope requested |
| `ticket` | The in-flight ticket number, or null when denied |
| `allowed`, `reason` | The verdict: `allowed`, or the first failing check as `<check>_<status>` (`record_invalid`, `record_absent`, `record_not_a_file`, `record_insecure`, `record_disabled`, `scope_mismatch`, `env_absent`, `toggle_stale`, `rate_6/6`, `in_flight_busy`, ...) |
| `checks` | Every check's `ok` and `status`, in order `record`, `scope`, `env`, `toggle`, `rate`, `in_flight` |
| `outcome` (release) | `done`, `error` (with the exception type name in `error`, never its text), `auth_error`, `dry_run`, `preflight` |

The journal never carries packet text or answers, only verdicts.

## Rate and concurrency

Six allowed calls per minute and one call in flight, enforced by the gate.
The runner waits for the window when the gate answers `rate_*` and aborts
on any other denial. An unreleased ticket expires after 120 s and is
journaled as `in_flight_expired`.

A "call" here is one gate ticket, which is one SDK invocation, not one HTTP
request. The runner builds the client with `RetryPolicy(max_retries=3,
timeout=60)` and a 30 s per-request timeout, and the SDK retries 408, 429,
5xx, connection and timeout errors by itself, so one ticket can be up to
four HTTP requests. `--max-calls N` and the six-per-minute bound therefore
cap invocations; the worst-case request count is four times that. The
runner stops on the first 401 (`TypeSafeAuthenticationError`) or 403
(`TypeSafePermissionDeniedError`), both journaled as `auth_error`, so a
revoked or unauthorised key costs one ticket, not three; any other SDK error
counts toward the three-consecutive-errors abort, and any other exception
raised during a call releases the ticket (`outcome: error`), marks the run
`aborted` and still writes `run.json`. The receipt records the policy under
`call_policy`.

## Worked example: level 1 against Charades rows

Placeholders in angle brackets. `<out>` must be a fresh directory below
`/home/marcelo-lima/vjepa-home/experiments/public-story-eval/`; the runner
creates it with mode `0700` and writes every file with mode `0600`.

Dry run (the default; no network, the SDK client is not imported, `run.json`
says `"executed": false`):

```sh
python3 tools/public_story_eval.py --level 1 \
  --rows /home/marcelo-lima/vjepa-home/experiments/public-story-eval/windows-train/rows.jsonl \
  --record <record dir>/egress-record.json \
  --roster <roster dir>/roster.txt \
  --out /home/marcelo-lima/vjepa-home/experiments/public-story-eval/train-dry-<date>
```

The receipt reports the packets built and the ones the leak guard refused
(by row id and pattern, never text), the would-be call count and bytes, a
spend estimate, the question digests, and the gate's verdict for scope
`public_eval` (`record_disabled` is expected while the record is off). It
also counts descriptions longer than the packet's 240-character claim cap;
they are truncated by default, or packed into several claims with
`--split-long`. The packet camera is named after the question's room, not
the Charades scene (`living_room` for a television row, `kitchen` for a
food-preparation row, the scene when it is a living room, kitchen or dining
room, else `other_room`); the policy and the per-camera counts are in
`run.json` under `camera_policy`, and `scores.md` breaks the a-priori
threshold down per Charades scene. The level-1 acceptance number is recall
at the a-priori threshold for the four positive-only questions and
precision plus recall for `rest_state` (`run.json` `level1_metric`). The runner reads the exporter's `home-v2-public-story-windows/v1`
rows as written (`id`, `description`, `scene`, `native_classes`, `targets`
with `positive`/`negative`/`unobserved`); see the runner's docstring for
the tolerant row contract and `--cutoffs` for the Score level each target
is compared against.

Execute (every switch on for one invocation; `--max-calls` is required and
is the hard cap; the model must equal the record's `model_id`):

```sh
python3 -c 'import json,time; print(json.dumps({"state": "on", "received_at": int(time.time())}))' \
  > <record dir>/egress-toggle.json && chmod 0600 <record dir>/egress-toggle.json
set -a; . <key dir>/typesafe-api-key.env; set +a     # defines TYPESAFE_API_KEY, file mode 0600
TYPESAFE_EGRESS=1 /home/marcelo-lima/.venvs/ts-eval/bin/python tools/public_story_eval.py --level 1 \
  --rows /home/marcelo-lima/vjepa-home/experiments/public-story-eval/windows-train/rows.jsonl \
  --record <record dir>/egress-record.json \
  --roster <roster dir>/roster.txt \
  --toggle <record dir>/egress-toggle.json \
  --model jev-1.13.0 \
  --max-calls <N> \
  --execute \
  --out /home/marcelo-lima/vjepa-home/experiments/public-story-eval/train-exec-<date>
unset TYPESAFE_API_KEY
```

The interpreter must be the venv that has `typesafe-sdk==0.6.0`; the system
Python refuses `--execute` with "not installed". Answers are cached under
`/home/marcelo-lima/vjepa-home/experiments/public-story-eval/cache/` by
(packet digest, question digest, model id), so rerunning into a new `--out`
after a scoring change makes no calls; a second `--execute` into the same
`--out` refuses so no receipt is ever overwritten. A rewording of any
question changes the question digest and misses the cache on purpose.

Receipts under `<out>`: `run.json` (executed flag, model, SDK version,
question digests, thresholds, cutoffs, counts, spend from reported usage,
durations only), `packets.jsonl`, `answers.jsonl`, `scores.md` (the
sensitivity table at 0.5 to 0.9 with the a-priori threshold marked) and
`egress-journal.jsonl`. Both `--out` and `--cache` must resolve below the
eval root; the runner refuses either elsewhere so no answer file lands in
the repository.

## Rollback rungs

1. `enabled: false` in the record (every caller, every scope).
2. The kill switch: Home Assistant toggle off, or the mirror file off,
   stale or deleted.
3. `TYPESAFE_EGRESS` unset.

Any one of them stops the next call; the journal shows which one did.
