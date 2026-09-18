# lighting-publisher

Home of the Living Lights belief publisher (plan rev 5). This directory holds
the `lighting_beliefs` Python package, the publisher's pure-logic core:

| Module | Purpose |
|---|---|
| `lighting_beliefs/questions.py` | The five Jev questions as data: `tv_attention` (Score, 3 levels), `eating` (Noul), `food_prep`, `settling`, `rest_state` (Score, 3 levels each). `build_questions()`, `questions_as_dicts()` (v1 HTTP shape), `questions_as_sdk()` (needs the `cloud` extra), `QUESTION_IDS`. |
| `lighting_beliefs/reduce.py` | Probability-only reduction: `P(level >= k)` per Score, `P(yes)` per Noul. Vendor `confidence` is stored as metadata and never gates. |
| `lighting_beliefs/packet.py` | Builds and validates the `lighting-beliefs-state/v1` packet from the observer's typed observations. Fail-closed on its own: recursive allow-list with list-vs-mapping strictness, every key and scalar run through the leak guard's roster-free patterns (a hit raises `PacketError` naming the path, never the text; camera, zone and unknown keys appear in error paths as `<key#N>` placeholders, never as the caller's text), 240-character NFKC/ASCII-only text cap with format characters stripped, ages in seconds only (above a day raises; an epoch is a caller bug, not a day-old belief), count caps (16 cameras, 32 zones, 16 media rows, 6 people, 8 claims), name-collision detection after capping, anonymous track labels, no names, relationships, entity ids, modes, light levels or clock. `content_signature()` for change detection with ages bucketed by schema path. |
| `lighting_beliefs/leak_guard.py` | Scans a packet for entity ids (every Home Assistant domain a lighting observer can name, any case, whitespace around the dot tolerated for snake_case object ids), `hav-*` containers, LAN addresses, bare `.local`/`.lan`/`.home`/`.internal` hostnames, JWT prefixes, model names, the OS username (parameter), RTSP and HTTP URLs, digit runs and separated digit groups (phone numbers, thousands separators), emails, ISO timestamps, numeric dates in any order and separator (OSD overlays, two-digit years), month-name dates, times of day (`19:25`, `7pm`, `19h25`, `1925 hours`), Unicode format characters, non-ASCII text, and roster names as whole names and per name part of 3+ characters, where parts of 5+ characters also match inside longer words (bare possessives, plurals) and shorter parts stay whole-word so `Teo` does not block `stereo` (roster file must be mode 0600). Text is NFKC-normalised with format characters removed before matching. Findings name the pattern and key path only; a leaking key is reported as a `<key#N>` placeholder so nothing that journals the finding echoes it. Findings block; there is no strip helper. |
| `lighting_beliefs/egress.py` | `EgressGate`: allows a call only when the egress record (a regular file, no symlink, not group- or other-writable, optionally owner-checked) is enabled and lists the scope, `TYPESAFE_EGRESS=1`, and the HA kill-switch mirror reads `on` with a finite, non-negative age strictly below 300 s (NaN, infinite, negative beyond 5 s skew, future or non-numeric ages deny as `toggle_invalid`); 6 calls per minute, one in flight, an unreleased ticket expires after 120 s and is journaled as `in_flight_expired`; every decision journaled as JSONL. |

## No network code exists yet

Nothing in this package opens a socket, imports an HTTP client, or reads an API
key. The TypeSafe SDK is only an optional extra (`pip install
'lighting_beliefs[cloud]'`, pinned to `typesafe-sdk==0.6.0`) and is imported
lazily by `questions_as_sdk()`. The container, the MQTT mirror, the observer
adapter and the actual call site arrive in M4 and later, and every call must
pass `leak_guard.assert_clean()` and an allowed `EgressGate.request()` first.

## Install and test

```bash
cd stack/services/lighting-publisher
python3 -m pip install -e .          # stdlib only
python3 -m unittest discover -s tests -t . -v
```

From the repository root:

```bash
python3 -m unittest discover -s stack/services/lighting-publisher/tests -t stack/services/lighting-publisher -v
```

Tests are pure logic: no network, no Home Assistant, no observer. Golden
packets for a TV evening and a quiet night live in `tests/fixtures/`.

## Egress record

`egress-record.example.json` is a redacted example of the record the gate
reads. The real record is signed by the owner, lives outside the repo, and is
enabled only for the scopes it lists. It must be a regular file (not a
symlink) with mode 0600 or 0644 (no group or other write bit); the gate
refuses it as `record_insecure` otherwise, so a machine with umask 0002 needs
an explicit `chmod`. Disabling the record is the first rollback rung; the HA
kill switch and `TYPESAFE_EGRESS` are the other two.

See `docs/ARCHITECTURE_DECISIONS.md`, ADR-005, for the outbound-traffic
amendment this package requires.
