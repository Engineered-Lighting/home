#!/usr/bin/env python3
"""Check JSON Lines records against the canon field names in live-v1 schemas.json.

Usage:
  python3 schema_validator.py SCHEMAS_JSON RECORD_ID FILE.jsonl [FILE2.jsonl ...]
  python3 schema_validator.py SCHEMAS_JSON auto FILE.jsonl      # guess the record by key overlap
  python3 schema_validator.py SCHEMAS_JSON --list               # list record ids and their fields

RECORD_ID is a schemas.json record id (observation, track, person_bundle, person_state,
light_state, room_model, fixture, light_model, decision, correction, eval_record,
calibration_record, ...). A file may also be a single JSON object or array (.json).

It reports, per file:
  unknown   top-level keys that are not canon field names (a renamed or invented field),
  missing   canon fields absent from every record (informational: many fields are optional),
  enum      values outside an enum the schema spells out as 'a | b | c',
  nested    sub-keys of '{a, b, c}' typed fields that differ from the canon sub-keys,
  type      a 'float s' / number field that is not a number.
Keys a field's meaning names in backticks (such as `n_zones`) are accepted. Keys ending in _note are ignored (the schema's own examples use them). The version keys
schema_version / record / record_version / version are accepted on every record, because
the spec says records carry 'their version' without naming one key for most records.
Exit code 0 if no unknown/enum/nested/type problems, 1 otherwise. Standard library only.
"""
import json
import re
import sys
from collections import Counter

VERSION_KEYS = {"schema_version", "record", "record_version", "version", "record_type", "kind_version"}


def split_names(name):
    return [p.strip() for p in name.split("/") if p.strip()]


MAP_PLACEHOLDERS = {"field", "condition", "link", "state_id", "episode_cluster", "term", "time_bucket", "point"}
UNIVERSAL_SUBKEYS = {"cov", "note", "sd", "sd_deg", "unit", "status", "source"}


def parse_subkeys(typ):
    m = re.match(r"^\s*\{([^{}]*)\}", typ)
    if not m:
        return None
    inner = m.group(1)
    first = re.split(r"[\s:=,]", inner.strip(), maxsplit=1)[0].strip("`'\"")
    if first in MAP_PLACEHOLDERS or re.match(r"^\s*[A-Za-z_]+\s*:", inner):
        return None  # a map type such as {field: ts}; keys are data, not names
    keys = []
    for part in m.group(1).split(","):
        part = part.strip()
        if not part:
            continue
        tok = re.split(r"[\s:=]", part)[0]
        tok = tok.strip("`'\"")
        if "/" in tok:  # e.g. yaw/pitch/roll
            keys.extend(t for t in tok.split("/") if t)
        elif re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", tok):
            keys.append(tok)
        else:
            return None
    return keys or None


def parse_enum(field):
    typ = field.get("type", "")
    meaning = field.get("meaning", "")
    if "enum" not in typ or "|" not in meaning:
        return None
    head = re.sub(r"\([^)]*\)", "", meaning)
    head = re.split(r"[;.(]", head, maxsplit=1)[0]
    vals = [v.strip().strip("`'\"") for v in head.split("|")]
    vals = [v for v in vals if v and len(v) < 40]
    return set(vals) if len(vals) >= 2 else None


def enum_ok(v, allowed):
    if v in allowed:
        return True
    if " + " in v and all(p.strip() in allowed for p in v.split(" + ")):
        return True
    return any(v.startswith(a + "(") or v.startswith(a + ":") for a in allowed)


def load(schemas_path):
    data = json.load(open(schemas_path, encoding="utf-8"))
    recs = {}
    for r in data["records"]:
        fields = {}
        for f in r["fields"]:
            names = split_names(f["name"])
            if len(names) == 1 and "." in names[0]:
                # dotted sub-field such as inputs.field_support
                parent, child = names[0].split(".", 1)
                fields.setdefault(parent, {"type": "", "meaning": "", "children": set()})
                fields[parent].setdefault("children", set()).add(child)
                continue
            for n in names:
                fields[n] = dict(f, children=set())
        # Keys the record's own field meanings name in backticks (e.g. `n_zones`, `residual_deg_rms`) are canon too:
        # the spec's calibration_record example uses n_zones, which only a meaning text names.
        mentioned = set()
        for f in r["fields"]:
            mentioned |= set(re.findall(r"`([a-z][a-z0-9_]*)`", f.get("meaning", "")))
        # Sub-keys the spec's own example uses under a field (e.g. light_model.error.scored_at) are tolerated too.
        example_sub = {}
        for ex in (r.get("example") if isinstance(r.get("example"), list) else [r.get("example")]):
            if isinstance(ex, dict):
                for key, value in ex.items():
                    if isinstance(value, dict):
                        example_sub.setdefault(key, set()).update(value)
        recs[r["id"]] = {"name": r["name"], "fields": fields, "mentioned": mentioned, "example_sub": example_sub}
    return recs


def numeric_field(f):
    t = f.get("type", "")
    return t.startswith("float") or t in ("0–1", "s", "int", "number")


def iter_records(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        raise ValueError("file is UTF-16 (a PowerShell 5.1 '>' redirection?); JSON Lines must be UTF-8")
    text = raw.decode("utf-8-sig")
    stripped = text.lstrip()
    if path.endswith(".json") or stripped.startswith("["):
        obj = json.loads(text)
        if isinstance(obj, list):
            yield from obj
        else:
            yield obj
        return
    for i, line in enumerate(text.splitlines(), 1):
        if line.strip():
            try:
                yield json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"line {i}: not JSON ({e})")


def guess(recs, keys):
    best, score = None, -1
    for rid, r in recs.items():
        s = len(set(keys) & set(r["fields"]))
        if s > score:
            best, score = rid, s
    return best


def check(recs, rid, path):
    problems = {"unknown": Counter(), "enum": Counter(), "nested": Counter(), "type": Counter()}
    seen = Counter()
    n = 0
    records = list(iter_records(path))
    if rid == "auto":
        keys = set()
        for r in records:
            if isinstance(r, dict):
                keys |= set(r)
        rid = guess(recs, keys)
    spec = recs[rid]["fields"]
    for rec in records:
        if not isinstance(rec, dict):
            problems["type"]["<record is not an object>"] += 1
            continue
        n += 1
        for k, v in rec.items():
            if k.endswith("_note") or k in VERSION_KEYS or k == "note":
                continue
            if k not in spec:
                base = next((f for f in spec if k.startswith(f + "_")), None)
                if base is None and k.endswith("_status"):
                    base = k  # the spec's <field>_status annotation convention
                if base:  # <field>_status, unit-suffixed variants such as residual_deg_rms
                    seen[base] += 1
                    continue
                if k in recs[rid].get("mentioned", ()):  # named in a field's meaning text, e.g. n_zones
                    continue
                problems["unknown"][k] += 1
                continue
            seen[k] += 1
            f = spec[k]
            ev = parse_enum(f)
            if ev and isinstance(v, str) and not enum_ok(v, ev):
                problems["enum"][f"{k}={v!r} not in {sorted(ev)}"] += 1
            if numeric_field(f) and v is not None and not isinstance(v, (int, float)):
                problems["type"][f"{k} is {type(v).__name__}"] += 1
            sub = parse_subkeys(f.get("type", "")) if f.get("type") else None
            if sub and isinstance(v, dict):
                extra = set(v) - set(sub) - UNIVERSAL_SUBKEYS - recs[rid]["example_sub"].get(k, set())
                extra = {e for e in extra if not e.endswith("_note") and not any(e.startswith(b + "_") for b in sub)}
                if extra:
                    problems["nested"][f"{k}: unknown sub-keys {sorted(extra)} (canon {sub})"] += 1
    missing = sorted(set(spec) - set(seen))
    return rid, n, problems, missing


def main(argv):
    if len(argv) >= 3 and argv[2] == "--list":
        recs = load(argv[1])
        for rid, r in recs.items():
            print(rid, sorted(r["fields"]))
        return 0
    if len(argv) < 4:
        print(__doc__)
        return 2
    recs = load(argv[1])
    rid = argv[2]
    if rid != "auto" and rid not in recs:
        print(f"unknown record id {rid!r}; known: {sorted(recs)}")
        return 2
    bad = 0
    for path in argv[3:]:
        try:
            got, n, problems, missing = check(recs, rid, path)
        except (ValueError, OSError) as e:
            print(f"FAIL {path}: {e}")
            bad += 1
            continue
        nprob = sum(sum(c.values()) for c in problems.values())
        status = "PASS" if nprob == 0 else "FAIL"
        print(f"{status} {path} as {got}: {n} records")
        for kind, c in problems.items():
            for k, cnt in c.most_common():
                print(f"  {kind}: {k} (x{cnt})")
        if missing:
            print(f"  missing (informational): {missing}")
        bad += nprob > 0
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
