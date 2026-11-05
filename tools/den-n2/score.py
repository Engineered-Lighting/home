#!/usr/bin/env python3
"""N2's scorer: one person's need logs against N1's frozen blind labels, under GATE-V1.

    python -B tools/den-n2/score.py --split development
    python -B tools/den-n2/score.py --split held_out

Standard library only. It reads N1's frozen labels and fixture through
den-label's own loaders (n1.py), and from the N2 folder the ask lines
(asks/<take>.jsonl) and one den-core replay log per usable segment
(replay/<take>/seg-<k>/log.jsonl), each checked with den-core's check.py first.
The numbers come from GATE-V1.md. It writes one canonical JSON report to
<n2>/eval/ and nothing else; a refusal writes nothing.

Development mode opens only development sessions' files, never a held-out
label. Nothing here contacts a camera, the Jetson, LA, Home Assistant or any
other host. The plan is docs/plans/n2-need-test/PLAN.md: section 4 defines
every measure, section 8 the gate.
"""
import argparse
import bisect
import csv
import importlib.util
import io
import json
import math
import random
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEN_LABEL = HERE.parent / "den-label"
if str(DEN_LABEL) not in sys.path:
    sys.path.insert(0, str(DEN_LABEL))
import n1  # noqa: E402  N1's formats, loaders and freeze lines

DEFAULT_TAKES = r"C:\den-takes"
GATE_FILE = HERE / "GATE-V1.md"
GATE_BLOCK = "gate-v1"
FREEZES_FILE = DEN_LABEL / "FREEZES.md"
DEFAULT_DEN_CORE = HERE.parent / "den-core"
FIXED_KEYS = ("tau_on_s", "tau_off_s", "grace_s", "cut.fine", "cut.casual", "cap.false_fine", "cap.onset_s",
              "cap.ece", "ece_bins", "sample.units", "sample.onsets", "sample.sessions", "sample.days",
              "interval.level", "interval.rounds", "a7.theta_on", "a7.theta_off", "a7.entry_dwell_s",
              "a7.exit_negative_s", "a7.demand_rise_s", "a7.demand_fall_s", "a7.levels", "schedule_s",
              "max_age_s", "tick_s", "field_assignment")
SLOTS = ("scope", "prompt", "pacing_s", "onset_mode", "facts_map", "confidence_table", "rule_changes",
         "floor.competence.fine", "floor.competence.casual", "floor.competence.none", "floor.fine_recall",
         "floor.coverage", "cap.early_off", "cap.false_on", "detector", "stand_ins", "s_dev", "d_dev", "look_test")
OWNER_SLOTS = ("scope", "floor.competence.fine", "floor.competence.casual", "floor.competence.none",
               "floor.fine_recall", "floor.coverage", "cap.early_off", "cap.false_on")
SCOPES = ("stand_in", "qwen_only")
ONSET_MODES = ("gated", "report_only")
SPLITS = n1.SPLITS
STATES = ("none", "starting", "engaged", "ending")
ON = ("starting", "engaged")
OUTCOMES = ("replied", "no_box", "failed")
HOLD_ROWS = ("unclear", "invalid", "failed", "stale")
ASK_KEYS = ("slot", "segment", "r", "frames", "outcome", "S", "attempts", "asked_unix", "receipt")
# The case criterion's cases and their gold bands; phone_at_counter takes the protocol's phone band.
CASES = ("sorting_mail", "still_reader", "wiping", "phone_at_counter", "idle_seated", "idle_standing")
CASE_BANDS = {"sorting_mail": "fine", "still_reader": "fine", "wiping": "casual", "idle_seated": "none",
              "idle_standing": "none"}
PER_SECOND = ("early_off", "false_on", "fine_recall", "false_fine")
INF = math.inf
EXIT_DONE, EXIT_REFUSED = 0, 2
NAME = re.compile(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*")
SHA256 = re.compile(r"[0-9a-f]{64}")
Refused = n1.Refused
now = time.time  # patched in tests


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


# ---- GATE-V1 ---------------------------------------------------------------------

@dataclass(frozen=True)
class Gate:
    file: str
    sha256: str
    values: dict

    def __getitem__(self, name):
        return self.values[name]

    def empty_slots(self):
        """Slots still null. detector is option 1's only, so it may stay null under qwen_only."""
        return [s for s in SLOTS if self.values[s] is None
                and not (s == "detector" and self.values["scope"] == "qwen_only")]


def parse_gate(text):
    """{name: value} from the one fenced gate-v1 block: `name = JSON`, a value may run over lines."""
    text = text.replace("\r\n", "\n")
    blocks = re.findall(r"^```([^\n]*)\n(.*?)^```", text, re.M | re.S)
    if len(blocks) != 1 or blocks[0][0].strip() != GATE_BLOCK:
        raise Refused(f"the gate must hold exactly one ```{GATE_BLOCK} block")
    values, name, pending = {}, None, ""

    def refuse_constant(_):
        raise ValueError("non-finite number")
    for line in blocks[0][1].split("\n"):
        if name is None:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            name, sep, pending = (part.strip() for part in stripped.partition("="))
            if not sep or not NAME.fullmatch(name) or name in values:
                raise Refused(f"bad gate line: {stripped!r}")
        else:
            pending += "\n" + line
        try:
            values[name] = json.loads(pending, parse_constant=refuse_constant)
            name = None
        except ValueError:
            continue
    if name is not None:
        raise Refused(f"the gate's value for {name} is not JSON")
    if set(values) != set(FIXED_KEYS) | set(SLOTS):
        missing = sorted(set(FIXED_KEYS) | set(SLOTS) - set(values))
        extra = sorted(set(values) - set(FIXED_KEYS) - set(SLOTS))
        raise Refused(f"the gate's names differ from score.py's list (missing {missing}, extra {extra})")
    for key in FIXED_KEYS:
        if values[key] is None:
            raise Refused(f"{key} is a fixed value, never a slot")
    return values


def load_gate(path=GATE_FILE):
    """The gate, hashed over LF line ends so a CRLF copy reads and hashes the same."""
    path = Path(path)
    raw = path.read_bytes().replace(b"\r\n", b"\n")
    return Gate(path.name, n1.sha256_bytes(raw), parse_gate(raw.decode("utf-8")))


def check_slots(gate):
    """Held-out needs every slot filled, and filled with the right kind of value."""
    empty = gate.empty_slots()
    if empty:
        raise Refused("GATE-V1 has empty slots, so the held-out set stays closed: " + ", ".join(empty))
    v, problems = gate.values, []
    if v["scope"] not in SCOPES:
        problems.append(f"scope must be one of {SCOPES}")
    if v["onset_mode"] not in ONSET_MODES:
        problems.append(f"onset_mode must be one of {ONSET_MODES}")
    if not number(v["pacing_s"]) or v["pacing_s"] <= 0:
        problems.append("pacing_s must be a positive number")
    for key in OWNER_SLOTS[1:]:
        if not number(v[key]) or not 0 <= v[key] <= 1:
            problems.append(f"{key} must be a number from 0 to 1")
    prompt = v["prompt"]
    if type(prompt) is not dict or type(prompt.get("version")) is not str \
            or not SHA256.fullmatch(str(prompt.get("sha256"))):
        problems.append('prompt must be {"version": ..., "sha256": ...}')
    s = v["stand_ins"]
    try:
        ok = (SHA256.fullmatch(s["protocol_sha256"]) and number(s["pad"]["fraction"]) and number(s["pad"]["min_px"])
              and SHA256.fullmatch(s["need"]["code_sha256"]) and SHA256.fullmatch(s["need"]["config_sha256"]))
    except (TypeError, KeyError):
        ok = False
    if not ok:
        problems.append('stand_ins must be {"protocol_sha256", "pad": {"fraction", "min_px"}, '
                        '"need": {"code_sha256", "config_sha256"}}')
    if problems:
        raise Refused("GATE-V1: " + "; ".join(problems))


def gate_commit(path):
    """The gate file's last commit and its time, and whether the working copy differs (local git only)."""
    path = Path(path).resolve()
    try:
        log = subprocess.run(["git", "-C", str(path.parent), "log", "-1", "--format=%H %ct", "--", path.name],
                             capture_output=True, text=True, timeout=60)
        status = subprocess.run(["git", "-C", str(path.parent), "status", "--porcelain", "--", path.name],
                                capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "commit_unix": None, "dirty": None}
    parts = log.stdout.split()
    if log.returncode or len(parts) != 2:
        return {"commit": None, "commit_unix": None, "dirty": None}
    return {"commit": parts[0], "commit_unix": int(parts[1]), "dirty": bool(status.stdout.strip())}


# ---- N1's frozen labels and fixture (through n1.py) -----------------------------------------

@dataclass
class Session:
    take: str
    split: str
    started_unix: float
    day: str
    segments: list  # [(a, b)] in t_s, the fixture's order: usable segment [a, b)
    intervals: list
    spans: list
    asks: list = field(default_factory=list)
    logs: list = field(default_factory=list)  # per segment: [Rec]

    @property
    def t0(self):
        """den-core's clock for this take: t = started_unix in whole seconds + t_s (the adapter's rule)."""
        return math.floor(self.started_unix)


def latest_logged(freezes_text, kind):
    found = [version for k, version, _ in n1.freeze_lines(freezes_text) if k == kind]
    if not found:
        raise Refused(f"no {kind} freeze line in FREEZES.md: N2 opens nothing before N1's freeze")
    return max(found)


def frozen_manifest(n1root, kind, version, freezes_text):
    """A frozen folder's manifest, only if its sha256 is logged for that version in FREEZES.md."""
    folder = Path(n1root) / f"{kind}-v{version}"
    name = "fixture.json" if kind == "fixture" else "labels.json"
    try:
        raw = (folder / name).read_bytes()
    except OSError:
        raise Refused(f"no {kind}-v{version}/{name} in {n1root}") from None
    digest = n1.sha256_bytes(raw)
    if (kind, version, digest) not in n1.freeze_lines(freezes_text):
        raise Refused(f"{kind}-v{version}'s {name} (sha256 {digest[:12]}) matches no freeze line in "
                      "FREEZES.md; run git pull, or the folder was changed after its freeze")
    manifest = json.loads(raw.decode("ascii"))
    if n1.canonical(manifest) != raw:
        raise Refused(f"{kind}-v{version}/{name} is not canonical JSON")
    return folder, manifest, digest


def opened(folder, rel, sha256):
    """One file a frozen manifest names, read once and checked against the manifest's sha256."""
    try:
        raw = (Path(folder) / rel).read_bytes()
    except OSError:
        raise Refused(f"{Path(folder).name}/{rel} is missing") from None
    if n1.sha256_bytes(raw) != sha256:
        raise Refused(f"{Path(folder).name}/{rel} does not match its manifest")
    return raw


def load_n1(n1root, split, freezes_text, protocol=None, labels_version=None, fixture_version=None):
    """(gold info, [Session]) for one split. Opens session files of that split only."""
    labels_dir, labels, labels_sha = frozen_manifest(n1root, "labels",
                                                     labels_version or latest_logged(freezes_text, "labels"),
                                                     freezes_text)
    fixture_dir, fixture, fixture_sha = frozen_manifest(n1root, "fixture",
                                                        fixture_version or latest_logged(freezes_text, "fixture"),
                                                        freezes_text)
    if labels.get("schema") != "den-n1-labels-1" or fixture.get("schema") != "den-n1-fixture-1":
        raise Refused("not a den-n1 labels and fixture pair")
    protocol = protocol or n1.load_protocol()
    for kind, manifest in (("labels", labels), ("fixture", fixture)):
        if manifest["protocol"]["sha256"] != protocol.sha256:
            raise Refused(f"the {kind} were frozen under protocol {manifest['protocol']['sha256'][:12]}, not this "
                          f"checkout's PROTOCOL-V1.md ({protocol.sha256[:12]}); run git pull")
    if labels["phone_at_counter_band"] != protocol.phone_band or fixture.get("person_id") != n1.PERSON_ID:
        raise Refused("the labels' phone band or the fixture's person id differs from the protocol")
    if split == "held_out":  # the scoring run: every file of both folders, as label.py verify does
        for folder in (labels_dir, fixture_dir):
            _, problems = n1.verify_folder(folder)
            if problems:
                raise Refused(f"{folder.name}: " + "; ".join(problems))
    opened(labels_dir, labels["split"]["file"], labels["split"]["sha256"])
    entries = {e["session"]: e for e in n1.load_split(labels_dir / labels["split"]["file"])}
    boxes = {s["take"]: s for s in fixture["sessions"]}
    sessions = []
    for item in labels["sessions"]:
        if item["split"] != split:
            continue  # another split's files are never opened
        take = item["take"]
        entry, fx = entries.get(take), boxes.get(take)
        if entry is None or entry["split"] != split:
            raise Refused(f"{take}'s split differs between labels.json and split.json")
        if fx is None or fx["check_sha256"] != item["check_sha256"]:
            raise Refused(f"fixture-v{fixture['version']} does not hold {take} under the labels' check")
        record = json.loads(opened(labels_dir, item["file"], item["sha256"]))
        if (record.get("schema") != "den-n1-session-labels-1" or record["take"] != take
                or record["split"] != split or record["protocol_sha256"] != protocol.sha256):
            raise Refused(f"{item['file']} is not {take}'s {split} labels under this protocol")
        rows = csv.DictReader(io.StringIO(opened(fixture_dir, fx["boxes"]["file"], fx["boxes"]["sha256"])
                                          .decode("ascii")))
        t_of = {int(row["n"]): float(row["t_s"]) for row in rows}
        bounds = [(first, last) for first, last, _, _ in fx["segments"]]

        def seg_of(n, where):
            for j, (first, last) in enumerate(bounds):
                if first <= n <= last and n in t_of:
                    return j
            raise Refused(f"{take}: {where} at frame {n} is not a usable frame of the fixture")

        def time_of(n):
            seg_of(n, "a span end")
            return t_of[n]
        intervals = [{"on": x["onset_t_s"], "off": x["offset_t_s"], "onset_kind": x["onset_kind"],
                      "offset_kind": x["offset_kind"], "band": x["band"], "case": x["case"], "sure": x["sure"],
                      "seg": seg_of(x["onset_n"], "an interval")} for x in record["intervals"]]
        spans = [{"kind": s["kind"], "case": s.get("case"), "start": time_of(s["start_n"]), "end": time_of(s["end_n"]),
                  "seg": seg_of(s["start_n"], "a span")} for s in record["spans"]]
        sessions.append(Session(take, split, entry["started_unix"], n1.local_date(entry["started_unix"]),
                                [(float(a), float(b)) for _, _, a, b in fx["segments"]], intervals, spans))
    if not sessions:
        raise Refused(f"labels-v{labels['version']} has no {split} session")
    gold = {"n1": {"fixture": {"version": fixture["version"], "hash": fixture_sha},
                   "labels": {"version": labels["version"], "hash": labels_sha}},
            "n_intervals": labels["n_intervals"], "label_version": labels["version"], "hash": labels_sha,
            "refreshed": n1.utc_date(labels["frozen_unix"]),
            "regions": None, "regions_status": "no worked-patch marks (N1/N2 make no working-region claim)",
            "R_eval": [], "tau_R": None, "tau_R_status": "pending (reference-geometry)"}
    meta = {"frozen_unix": labels["frozen_unix"], "padding": fixture["padding"], "protocol": protocol}
    return gold, meta, sessions


# ---- N2's own inputs: ask lines and den-core replay logs -----------------------------------------

@dataclass(frozen=True)
class Rec:
    """One person_state record on the take's t_s clock."""
    t: float
    state: str
    eng_conf: object
    level: object
    dem_conf: object
    cap: object  # t_s of the capture behind the level (its valid_until is cap + max_age_s)


def read_jsonl(path):
    try:
        text = Path(path).read_bytes().decode("utf-8")
        return [json.loads(line) for line in text.replace("\r\n", "\n").split("\n") if line.strip()]
    except (OSError, UnicodeDecodeError, ValueError):
        raise Refused(f"{path} is missing or not JSON lines") from None


def load_asks(n2root, session, frozen_unix):
    """asks/<take>.jsonl, one line per slot. Refuses an ask line made before the labels freeze."""
    lines = read_jsonl(Path(n2root) / "asks" / f"{session.take}.jsonl")
    for line in lines:
        where = f"{session.take} ask slot {line.get('slot')!r}" if type(line) is dict else session.take
        if type(line) is not dict or not set(ASK_KEYS) <= set(line):
            raise Refused(f"{where}: an ask line needs {', '.join(ASK_KEYS)}")
        if not number(line["asked_unix"]):
            raise Refused(f"{where}: asked_unix is not a number")
        if line["asked_unix"] < frozen_unix:
            raise Refused(f"{where} was asked before the labels freeze; no estimator output may predate it")
        frames = line["frames"]
        if (line["outcome"] not in OUTCOMES or type(line["segment"]) is not int
                or not 1 <= line["segment"] <= len(session.segments) or not number(line["r"])
                or type(frames) is not list or not all(type(f) is dict and number(f.get("t_s")) for f in frames)
                or type(line["attempts"]) is not int):
            raise Refused(f"{where}: outcome, segment, r, frames or attempts is malformed")
        if line["outcome"] == "replied" and (not number(line["S"]) or type(line["receipt"]) is not dict
                                             or not frames):
            raise Refused(f"{where}: a replied slot needs S, its frames and a receipt")
    return lines


def load_checker(den_core):
    """den-core's check.check, loaded from its folder under its own name (den-take has a check.py too)."""
    den_core = Path(den_core)
    if not (den_core / "check.py").is_file():
        raise Refused(f"den-core's check.py is not in {den_core}; pass --den-core with den-core's folder")
    if str(den_core) not in sys.path:
        sys.path.insert(0, str(den_core))
    spec = importlib.util.spec_from_file_location("den_core_check", den_core / "check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.check


def read_log(path, t0):
    """(header, need entry, [Rec]) from a den-replay-log-1 file check.py has passed."""
    lines = [json.loads(line) for line in Path(path).read_bytes().decode("ascii").split("\n") if line]
    if not lines:
        raise Refused(f"{path} is empty")
    header, needs = lines[0], [p for p in lines[0].get("policies", []) if p.get("id") == "need"]
    if len(needs) != 1:
        raise Refused(f"{path} has no need policy")
    recs = []
    for event in lines[1:]:
        if event.get("kind") != "person_state" or event.get("source") != "need":
            continue
        data = event["data"]
        engagement, demand = data.get("focused_engagement") or {}, data.get("visual_demand") or {}
        level, cap = demand.get("level"), None
        if engagement.get("state") not in STATES:
            raise Refused(f"{path}: a person_state with no engagement state")
        if level is not None:
            support = ((data.get("field_support") or {}).get("visual_demand") or {}).get("last_support_capture_ts")
            if not number(level) or not 0 <= level <= 1 or not number(support):
                raise Refused(f"{path}: a visual_demand level needs a number from 0 to 1 and "
                              "field_support.visual_demand.last_support_capture_ts")
            cap = support - t0
        confidences = [engagement.get("confidence"), demand.get("confidence")]
        if any(c is not None and (not number(c) or not 0 <= c <= 1) for c in confidences):
            raise Refused(f"{path}: a confidence outside 0 to 1")
        recs.append(Rec(event["t"] - t0, engagement["state"], confidences[0], level, confidences[1], cap))
    recs.sort(key=lambda r: r.t)
    return header, needs[0], recs


def load_logs(n2root, session, checker):
    """One checked log per usable segment, replay/<take>/seg-<k>/log.jsonl. Refuses on a failed check."""
    replay = []
    for k in range(1, len(session.segments) + 1):
        path = Path(n2root) / "replay" / session.take / f"seg-{k}" / "log.jsonl"
        if not path.is_file():
            raise Refused(f"no replay log for {session.take} segment {k}: {path}")
        result = checker(path)
        if not result.get("ok"):
            raise Refused(f"{session.take} seg-{k}: den-core's check.py fails the log: {result.get('reason')}")
        header, need, recs = read_log(path, session.t0)
        session.logs.append(recs)
        replay.append({"take": session.take, "segment": k, "sha256": n1.file_sha256(path),
                       "check": result.get("reason"), "need": {key: need.get(key) for key in
                                                               ("version", "code_sha256", "config_sha256")},
                       "tick_s": header.get("tick_s")})
    return replay


# ---- rows, the gold grid and the system in force ---------------------------------------------------

def row_of(line, max_age):
    """need v1's row for one ask line: (activity_hypothesis, held_object), no_box, or a hold row."""
    if line["outcome"] in ("no_box", "failed"):
        return line["outcome"]
    receipt = line["receipt"]
    if receipt.get("status") != "valid":
        return "invalid"
    if line["r"] + line["S"] >= line["frames"][-1]["t_s"] + max_age:
        return "stale"
    facts = receipt.get("facts") or {}
    activity, held = facts.get("activity_hypothesis"), facts.get("held_object")
    if facts.get("people_visible") != "one" or None in (activity, held) or "unclear" in (activity, held):
        return "unclear"
    return f"{activity}/{held}"


class Timeline:
    def __init__(self, recs):
        self.recs, self.ts = recs, [r.t for r in recs]

    def index_at(self, x):
        """The latest record at or before x, or -1 before the first one."""
        return bisect.bisect_right(self.ts, x) - 1

    def at(self, x):
        i = self.index_at(x)
        return self.recs[i] if i >= 0 else None


def band_of(level, gate):
    if level is None:
        return None
    return "fine" if level >= gate["cut.fine"] else "casual" if level >= gate["cut.casual"] else "none"


def inside(x, spans):
    return any(a <= x <= b for a, b in spans)


@dataclass
class Second:
    k: int
    seg: int
    x: float
    iv: object  # the interval holding x, or None
    quiet: object  # the quiet span holding x (outside intervals), or None
    w_on: bool
    grace: bool
    w_off: bool
    low: bool
    on: bool
    level: object
    fresh: bool
    eng_conf: object
    dem_conf: object


@dataclass
class Unit:
    take: str
    kind: str  # interval or quiet
    case: str
    gold_band: str
    gold_on: bool
    label_on: bool
    band: object  # None: abstained
    eng_conf: object
    dem_conf: object
    coverage: object
    low: bool

    @property
    def right(self):
        return self.label_on == self.gold_on and self.band == self.gold_band


def median(values):
    return percentile(sorted(values), 0.5) if values else None


def percentile(ordered, q):
    """Linear interpolation on a sorted list; anything next to +inf is +inf."""
    pos = q * (len(ordered) - 1)
    lo, hi = math.floor(pos), math.ceil(pos)
    a, b = ordered[lo], ordered[hi]
    if lo == hi or a == b:
        return a
    if math.isinf(b):
        return INF
    return a + (b - a) * (pos - lo)


def majority_on(seconds):
    on = sum(1 for s in seconds if s.on)
    return on > len(seconds) - on  # a tie is off


def mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def make_unit(session, kind, case, gold_band, gold_on, engagement, demand, gate):
    label_on = majority_on(engagement)
    fresh = [s.level for s in demand if s.fresh]
    band = band_of(median(fresh), gate) if fresh else None
    seen = {id(s) for s in engagement}
    every = engagement + [s for s in demand if id(s) not in seen]
    return Unit(session.take, kind, case, gold_band, gold_on, label_on, band,
                mean([s.eng_conf for s in engagement if s.on == label_on]),
                None if band is None else mean([s.dem_conf for s in demand
                                                if s.fresh and band_of(s.level, gate) == band]),
                (sum(1 for s in demand if s.fresh) / len(demand)) if demand else None,
                2 * sum(1 for s in every if s.low) > len(every))


def onset_of(iv, tl, prev_off, a, pacing):
    """Section 4.2: d_on and its tag for one onset, and the schedule's wait w from the touch."""
    t_on, i = iv["on"], tl.index_at(iv["on"])
    rec, out = (tl.recs[i] if i >= 0 else None), None
    if rec is not None and rec.state in ON:
        start = i
        while start > 0 and tl.recs[start - 1].state in ON:
            start -= 1
        if prev_off is None or tl.recs[start].t > prev_off:
            d, tag, out = 0.0, "entered_on", start
        else:
            d, tag = INF, "stayed_on"
    else:
        d, tag = INF, "missed"
        for m in range(i + 1, len(tl.recs)):
            r = tl.recs[m]
            if r.t >= iv["off"]:
                break
            if r.state in ON:
                before = tl.recs[m - 1].state if m > 0 else "none"
                d, tag, out = r.t - t_on, ("entered_from_ending" if before == "ending" else "entered"), m
                break
    k = max(1, math.ceil((t_on - a) / pacing - 1e-9))
    r_next = a + k * pacing
    dwell = None
    if out is not None and tl.recs[out].state == "starting":
        for r in tl.recs[out + 1:]:  # the same on-run only
            if r.state not in ON:
                break
            if r.state == "engaged":
                dwell = r.t - tl.recs[out].t
                break
    return {"t_on": t_on, "d_on": d, "w": r_next - t_on, "tag": tag, "r_next": r_next, "dwell": dwell,
            "out_t": tl.recs[out].t if out is not None else None}


# ---- one session --------------------------------------------------------------------------

@dataclass
class Analysis:
    session: Session
    seconds: list
    units: list
    onsets: list  # timed onsets
    no_touch: list
    early_off: list  # (low, off) per counted second
    missed_intervals: int
    excluded: dict
    groups: dict  # (case, band) -> {row: count}


def analyse(session, gate, pacing):
    tau_on, tau_off, grace, max_age = gate["tau_on_s"], gate["tau_off_s"], gate["grace_s"], gate["max_age_s"]
    uncertain = [(s["start"], s["end"]) for s in session.spans if s["kind"] == "uncertain"]
    low = [(s["start"], s["end"]) for s in session.spans if s["kind"] == "low_light"]
    seconds, by_key = [], {}
    for j, (a, b) in enumerate(session.segments):
        tl = Timeline(session.logs[j])
        ivs = [x for x in session.intervals if x["seg"] == j]
        quiet = [s for s in session.spans if s["kind"] == "quiet" and s["seg"] == j]
        offsets = [x["off"] for x in ivs if x["offset_kind"] != "carried"]  # a carried offset has no window
        for k in range(math.ceil(a), math.floor(b)):
            if any(k < end and k + 1 > start for start, end in uncertain):
                continue  # uncertain seconds are counted, never scored
            x = k + 0.5
            iv = next((v for v in ivs if v["on"] <= x <= v["off"]), None)
            q = None if iv else next((s for s in quiet if s["start"] <= x <= s["end"]), None)
            rec = tl.at(x)
            level = rec.level if rec else None
            start = (a if iv["onset_kind"] == "carried" else iv["on"]) if iv else None
            second = Second(k, j, x, iv, q, bool(iv) and x < start + tau_on, bool(iv) and x < start + grace,
                            iv is None and any(o < x < o + tau_off for o in offsets), inside(x, low),
                            bool(rec) and rec.state in ON, level,
                            level is not None and x < rec.cap + max_age,
                            rec.eng_conf if rec else None, rec.dem_conf if rec else None)
            seconds.append(second)
            by_key[j, k] = second
    units, onsets, no_touch, early_off, missed_intervals = [], [], [], [], 0
    excluded = {"uncertain_s": round(sum(b - a for a, b in uncertain), 3),
                "unsure": {end: sum(1 for x in session.intervals if not x["sure"][end])
                           for end in ("onset", "offset", "band")},
                "carried_pieces": sum(1 for x in session.intervals if "carried" in (x["onset_kind"], x["offset_kind"])),
                "intervals_without_a_scored_second": 0}
    for j, (a, b) in enumerate(session.segments):
        tl = Timeline(session.logs[j])
        ivs = sorted((x for x in session.intervals if x["seg"] == j), key=lambda x: x["on"])
        for iv in ivs:
            mine = [s for s in seconds if s.iv is iv]
            if not mine:
                excluded["intervals_without_a_scored_second"] += 1
                continue
            i = tl.index_at(iv["on"])
            if i >= 0 and tl.recs[i].state in ON:
                first_on = iv["on"]
            else:
                first_on = next((r.t for r in tl.recs[i + 1:] if r.t < iv["off"] and r.state in ON), None)
            missed_intervals += first_on is None
            if iv["sure"]["offset"]:  # an unsure offset's seconds leave early_off
                early_off += [(s.low, not s.on) for s in mine
                              if not s.w_on and (first_on is None or s.x >= first_on)]
            if "carried" in (iv["onset_kind"], iv["offset_kind"]) or not iv["sure"]["band"]:
                continue  # not a unit, never timed; its seconds still count
            units.append(make_unit(session, "interval", iv["case"], iv["band"], True,
                                   [s for s in mine if not s.w_on], [s for s in mine if not s.grace], gate))
            if not iv["sure"]["onset"] or iv["onset_kind"] not in ("touch", "no_touch"):
                continue
            prev_off = max((x["off"] for x in ivs if x["off"] < iv["on"]), default=None)
            onset = onset_of(iv, tl, prev_off, a, pacing)
            onset.update(take=session.take, segment=j + 1, low=inside(iv["on"], low))
            onset.update(link(onset, session.asks, j + 1, pacing))
            (onsets if iv["onset_kind"] == "touch" else no_touch).append(onset)
        for span in (s for s in session.spans if s["kind"] == "quiet" and s["seg"] == j):
            mine = [s for s in seconds if s.quiet is span and not s.w_off]
            if mine:
                units.append(make_unit(session, "quiet", span["case"], "none", False, mine, mine, gate))
    groups = {}
    for line in session.asks:  # each reply in the group whose scored second holds its latest frame
        second = by_key.get((line["segment"] - 1, math.floor(line["frames"][-1]["t_s"]))) if line["frames"] else None
        if second is None:
            continue
        if second.iv and second.iv["sure"]["band"]:
            key = (second.iv["case"], second.iv["band"])
        elif second.quiet and not second.w_off:
            key = (second.quiet["case"], "none")
        else:
            continue
        counts = groups.setdefault(key, {})
        row = row_of(line, max_age)
        counts[row] = counts.get(row, 0) + 1
    return Analysis(session, seconds, units, onsets, no_touch, early_off, missed_intervals, excluded, groups)


def link(onset, asks, segment, pacing):
    """The request whose reply made the onset output: its r and S, and m, the replies before it after the touch."""
    if onset["tag"] not in ("entered", "entered_from_ending"):
        return {"S": None, "m": None}
    for line in asks:
        if (line["segment"] == segment and line["outcome"] == "replied"
                and abs(line["r"] + line["S"] - onset["out_t"]) < 2e-3):
            return {"S": line["S"], "m": max(0, round((line["r"] - onset["r_next"]) / pacing))}
    return {"S": onset["d_on"] - onset["w"], "m": 0}


# ---- per-session stats, the bootstrap and exact binomial --------------------------------------------

def session_stats(an, gate, keep=None):
    """The sums every metric needs, from one session; keep filters on the low-light flag (None: all)."""
    want = (lambda low: True) if keep is None else (lambda low: low == keep)
    ratio = {name: [0, 0] for name in PER_SECOND}
    f1 = [0, 0, 0]
    for s in an.seconds:
        if not want(s.low):
            continue
        fine = band_of(s.level, gate) == "fine"
        if s.iv is not None:
            if not s.w_on:
                f1[0 if s.on else 2] += 1
            if s.iv["sure"]["band"] and not s.grace:
                if s.iv["band"] == "fine":
                    ratio["fine_recall"][0] += s.fresh and fine
                    ratio["fine_recall"][1] += 1
                else:
                    ratio["false_fine"][0] += fine
                    ratio["false_fine"][1] += 1
        elif not s.w_off:
            if s.on:
                f1[1] += 1
            ratio["false_fine"][0] += fine
            ratio["false_fine"][1] += 1
            if s.quiet is not None:
                ratio["false_on"][0] += s.on
                ratio["false_on"][1] += 1
    for low, off in an.early_off:
        if want(low):
            ratio["early_off"][0] += off
            ratio["early_off"][1] += 1
    units = [u for u in an.units if want(u.low)]
    bins = gate["ece_bins"]
    ece = {"engagement": [[0, 0.0, 0.0] for _ in range(bins)], "demand": [[0, 0.0, 0.0] for _ in range(bins)]}
    for u in units:
        for name, conf, acc in (("engagement", u.eng_conf, u.label_on == u.gold_on),
                                ("demand", u.dem_conf, u.band == u.gold_band)):
            if conf is None or (name == "demand" and u.band is None):
                continue
            b = ece[name][min(int(conf * bins), bins - 1)]
            b[0], b[1], b[2] = b[0] + 1, b[1] + conf, b[2] + acc
    onsets = [o for o in an.onsets if want(o["low"])]
    return {"ratio": ratio, "f1": f1, "ece": ece,
            "onset": [max(o["d_on"], 0.0) for o in onsets],
            "d": [max(o["d_on"] - o["w"], 0.0) for o in onsets],
            "dwell": [o["dwell"] for o in onsets if o["dwell"] is not None],
            "coverage": [u.coverage for u in units if u.kind == "interval" and u.coverage is not None],
            "competence": {band: [sum(1 for u in units if u.gold_band == band and u.right),
                                  sum(1 for u in units if u.gold_band == band)] for band in ("fine", "casual")}}


def m_ratio(name):
    def value(stats):
        num, den = sum(s["ratio"][name][0] for s in stats), sum(s["ratio"][name][1] for s in stats)
        return num / den if den else None
    return value


def m_list(name, how):
    def value(stats):
        values = [v for s in stats for v in s[name]]
        return how(values) if values else None
    return value


def m_ece(name):
    def value(stats):
        total, gap = 0, 0.0
        for b in range(len(stats[0]["ece"][name]) if stats else 0):
            n = sum(s["ece"][name][b][0] for s in stats)
            total += n
            gap += abs(sum(s["ece"][name][b][2] for s in stats) - sum(s["ece"][name][b][1] for s in stats))
        return gap / total if total else None
    return value


def m_f1(stats):
    tp, fp, fn = (sum(s["f1"][i] for s in stats) for i in range(3))
    return 2 * tp / (2 * tp + fp + fn) if tp + fp + fn else None


def m_competence(band):
    def value(stats):
        right, n = sum(s["competence"][band][0] for s in stats), sum(s["competence"][band][1] for s in stats)
        return right / n if n else None
    return value


# name: (unit, counted per, better, bound, value function)
METRICS = {
    "onset": ("s", "median of max(d_on, 0) over timed onsets", "lower", "bootstrap", m_list("onset", median)),
    "early_off": ("fraction", "per second", "lower", "bootstrap", m_ratio("early_off")),
    "false_on": ("fraction", "per second", "lower", "bootstrap", m_ratio("false_on")),
    "competence.fine": ("fraction", "per unit", "higher", "exact binomial", m_competence("fine")),
    "competence.casual": ("fraction", "per unit", "higher", "exact binomial", m_competence("casual")),
    "fine_recall": ("fraction", "per second", "higher", "bootstrap", m_ratio("fine_recall")),
    "false_fine": ("fraction", "per second", "lower", "bootstrap", m_ratio("false_fine")),
    "coverage": ("fraction", "per interval unit, averaged", "higher", "bootstrap", m_list("coverage", mean)),
    "ece.engagement": ("ECE", "per unit, equal-width bins", "lower", "bootstrap", m_ece("engagement")),
    "ece.demand": ("ECE", "per unit, equal-width bins", "lower", "bootstrap", m_ece("demand")),
    "f1": ("F1", "per second, positive = on", "higher", "bootstrap", m_f1),
    "dwell": ("s", "median from the onset output to engaged", "reported", "bootstrap", m_list("dwell", median)),
}


def resamples(n, rounds, seed_hex):
    """Session indices for each bootstrap round, seeded from the labels hash."""
    rng = random.Random(int(seed_hex[:16], 16))
    return [[rng.randrange(n) for _ in range(n)] for _ in range(rounds)]


def bootstrap(stats, value, draws, level):
    values = sorted(v for v in (value([stats[i] for i in draw]) for draw in draws) if v is not None)
    if not values:
        return None
    tail = (1 - level) / 2
    return [percentile(values, tail), percentile(values, 1 - tail)]


def binomial_cdf(k, n, p):
    """P(X <= k) for X ~ Binomial(n, p)."""
    if p <= 0:
        return 1.0
    if p >= 1:
        return 1.0 if k >= n else 0.0
    logs = [math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * math.log(p)
            + (n - i) * math.log1p(-p) for i in range(k + 1)]
    top = max(logs)
    return min(1.0, math.exp(top) * sum(math.exp(v - top) for v in logs))


def clopper_pearson(k, n, level=0.95):
    """The exact two-sided binomial interval for k successes in n."""
    if n == 0:
        return None
    tail = (1 - level) / 2

    def solve(f, target):  # f rises with p; the p where it reaches target
        lo, hi = 0.0, 1.0
        for _ in range(100):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if f(mid) < target else (lo, mid)
        return (lo + hi) / 2
    lower = 0.0 if k == 0 else solve(lambda p: 1 - binomial_cdf(k - 1, n, p), tail)
    upper = 1.0 if k == n else solve(lambda p: -binomial_cdf(k, n, p), -tail)
    return [lower, upper]


def judge(kind, threshold, value, interval):
    """met, failed or straddled from the interval (section 4.6); unset or no_data otherwise."""
    if threshold is None:
        return "unset"
    if value is None or interval is None:
        return "no_data"
    lo, hi = interval
    if kind == "floor":
        return "met" if lo >= threshold else "failed" if hi < threshold else "straddled"
    return "met" if hi <= threshold else "failed" if lo > threshold else "straddled"


def thresholds(gate):
    g = gate.values
    onset = None if g["onset_mode"] == "report_only" else ("cap", g["cap.onset_s"])
    return {"onset": onset, "early_off": ("cap", g["cap.early_off"]), "false_on": ("cap", g["cap.false_on"]),
            "competence.fine": ("floor", g["floor.competence.fine"]),
            "competence.casual": ("floor", g["floor.competence.casual"]),
            "fine_recall": ("floor", g["floor.fine_recall"]), "false_fine": ("cap", g["cap.false_fine"]),
            "coverage": ("floor", g["floor.coverage"]), "ece.engagement": ("cap", g["cap.ece"]),
            "ece.demand": ("cap", g["cap.ece"])}


def rounded(value):
    if value is None or math.isinf(value):
        return None if value is None else "inf"
    return round(value, 6)


def score_metrics(analyses, gate, seed_hex):
    stats = [session_stats(an, gate) for an in analyses]
    split = {flag: [session_stats(an, gate, flag) for an in analyses] for flag in (True, False)}
    draws = resamples(len(stats), gate["interval.rounds"], seed_hex)
    level, limits, out = gate["interval.level"], thresholds(gate), {}
    for name, (unit, per, better, bound, value_of) in METRICS.items():
        value = value_of(stats)
        if bound == "exact binomial":
            band = name.split(".")[1]
            right, n = (sum(s["competence"][band][i] for s in stats) for i in range(2))
            interval = clopper_pearson(right, n, level)
        else:
            interval = bootstrap(stats, value_of, draws, level)
        limit = limits.get(name)
        out[name] = {"unit": unit, "counted_per": per, "better": better, "bound": bound,
                     "value": rounded(value), "interval": interval and [rounded(v) for v in interval],
                     "threshold": limit and {"kind": limit[0], "value": limit[1]},
                     "status": judge(limit[0], limit[1], value, interval) if limit else "reported",
                     "low_light": {"low": rounded(value_of(split[True])), "rest": rounded(value_of(split[False]))}}
    return out, stats


# ---- gates, cases, the verdict ------------------------------------------------------------------

def sample_gate(name, n, needed, sessions, days_of, gate):
    days = {days_of[t] for t in sessions}
    met = n >= needed and len(sessions) >= gate["sample.sessions"] and len(days) >= gate["sample.days"]
    return {"name": name, "n": n, "needed": needed, "sessions": len(sessions), "days": len(days), "met": met}


def sample_gates(analyses, stats, gate, onset_gated):
    days_of = {an.session.take: an.session.day for an in analyses}
    units = [u for an in analyses for u in an.units]
    out = []
    for band in ("fine", "casual"):
        mine = [u for u in units if u.kind == "interval" and u.gold_band == band]
        out.append(sample_gate(f"units.{band}", len(mine), gate["sample.units"], {u.take for u in mine},
                               days_of, gate))
    if onset_gated:
        timed = [o for an in analyses for o in an.onsets]
        out.append(sample_gate("onsets", len(timed), gate["sample.onsets"], {o["take"] for o in timed}, days_of,
                               gate))
    for name in PER_SECOND:
        with_data = {an.session.take for an, s in zip(analyses, stats) if s["ratio"][name][1]}
        out.append(sample_gate(f"data.{name}", len(with_data), 0, with_data, days_of, gate))
    return out


def score_cases(analyses, gate, phone_band, level):
    units, days_of = [u for an in analyses for u in an.units], {an.session.take: an.session.day for an in analyses}
    out, gates = {}, []
    for case in CASES:
        band = CASE_BANDS.get(case, phone_band)
        mine = [u for u in units if u.case == case]
        right = sum(1 for u in mine if u.right)
        interval = clopper_pearson(right, len(mine), level)
        floor = gate[f"floor.competence.{band}"]
        status = ("unset" if floor is None else "no_data" if interval is None
                  else "failed" if interval[1] < floor else "not_failed")
        out[case] = {"band": band, "units": len(mine), "right": right, "rate": rounded(right / len(mine))
                     if mine else None, "interval": interval and [rounded(v) for v in interval],
                     "floor": floor, "status": status}
        gates.append(sample_gate(f"case.{case}", len(mine), 0, {u.take for u in mine}, days_of, gate))
    return out, gates


def verdict(metrics, cases, gates, onset_gated):
    """Section 4.7, rules 2 to 5 (rule 1, a refusal, never reaches here)."""
    gated = [n for n in metrics if metrics[n]["threshold"] and (n != "onset" or onset_gated)]
    unmet = [f"sample gate {g['name']} unmet" for g in gates if not g["met"]]
    failed = ([f"{n} failed" for n in gated if metrics[n]["status"] == "failed"]
              + [f"case {c} failed" for c in cases if cases[c]["status"] == "failed"])
    straddled = [f"{n} {metrics[n]['status']}" for n in gated if metrics[n]["status"] in ("straddled", "no_data")]
    if unmet:
        return "insufficient", ["rule 2: a sample gate is unmet"] + unmet + failed + straddled
    if failed:
        return "not_feasible", ["rule 3: a gated metric or a case failed"] + failed + straddled
    if straddled:
        return "insufficient", ["rule 4: a gated metric straddled"] + straddled
    return "feasible", ["rule 5: every gated metric was met"]


# ---- development: the onset rule, pacing and prompt-2 ----------------------------------------------

def onset_rule(onsets, pacing, limit):
    """Section 1(a): D, whether P/2 + D fits, and the largest P whose prediction from these onsets fits."""
    d = median([max(o["d_on"] - o["w"], 0.0) for o in onsets])
    if d is None:
        return {"D": None, "fits": None, "largest_fitting_P": None, "predictions": []}
    predictions = []
    for tenth in range(int(round(pacing * 10)), 0, -5):
        p = tenth / 10
        values = [0.0 if o["tag"] == "entered_on" else INF if o["tag"] in ("stayed_on", "missed")
                  else o["S"] + p * o["m"] for o in onsets]
        predicted = median(values)
        predictions.append({"P": p, "median": rounded(predicted), "fits": p / 2 + predicted <= limit})
    fitting = [x["P"] for x in predictions if x["fits"]]
    return {"D": rounded(d), "fits": pacing / 2 + d <= limit, "largest_fitting_P": max(fitting, default=None),
            "predictions": predictions}


def top_rows(counts):
    seen = {row: n for row, n in counts.items() if row not in HOLD_ROWS + ("no_box",)}
    most = max(seen.values(), default=0)
    return {row for row, n in seen.items() if n == most and most}


def prompt2_trigger(groups):
    """Section 1(b): fires when a fine group's most common row is also a casual or none group's."""
    pairs = []
    for (case, band), counts in sorted(groups.items()):
        if band != "fine":
            continue
        for (other, other_band), other_counts in sorted(groups.items()):
            shared = top_rows(counts) & top_rows(other_counts)
            if other_band in ("casual", "none") and shared:
                pairs.append({"fine": case, "other": other, "other_band": other_band, "rows": sorted(shared)})
    return {"fires": bool(pairs), "pairs": pairs}


def row_table(analyses):
    pooled = {}
    for an in analyses:
        for key, counts in an.groups.items():
            for row, n in counts.items():
                pooled.setdefault(key, {})[row] = pooled.setdefault(key, {}).get(row, 0) + n
    table = [{"case": case, "band": band,
              "rows": {r: n for r, n in sorted(counts.items()) if r not in HOLD_ROWS + ("no_box",)},
              "apart": {r: n for r, n in sorted(counts.items()) if r in HOLD_ROWS + ("no_box",)}}
             for (case, band), counts in sorted(pooled.items())]
    return table, prompt2_trigger(pooled)


# ---- the run -------------------------------------------------------------------------------

def service(sessions, max_age):
    lines = [line for session in sessions for line in session.asks]
    rows = {}
    for line in lines:
        row = row_of(line, max_age)
        row = row if row in HOLD_ROWS + ("no_box",) else "seen"
        rows[row] = rows.get(row, 0) + 1
    s = sorted(line["S"] for line in lines if line["outcome"] == "replied")
    receipts = [line["receipt"] for line in lines if line["outcome"] == "replied"]
    return {"requests": len(lines), "replied": len(s), "S_median": rounded(median(s)),
            "S_p95": rounded(percentile(s, 0.95)) if s else None,
            "resends": sum(max(0, line["attempts"] - 1) for line in lines),
            "invalid": sum(1 for r in receipts if r.get("status") != "valid"),
            "failed": sum(1 for line in lines if line["outcome"] == "failed"),
            "no_box": sum(1 for line in lines if line["outcome"] == "no_box"),
            "rows": dict(sorted(rows.items())),
            "engine_epochs": sorted({str(r.get("engine_epoch")) for r in receipts}),
            "prompt_versions": sorted({str(r.get("prompt_version")) for r in receipts})}


def check_stand_ins(gate, meta, replay, served):
    """Rule 1: a stand-in that differs from GATE-V1's refuses the held-out run."""
    s, problems = gate["stand_ins"], []
    if s["protocol_sha256"] != meta["protocol"].sha256:
        problems.append("the protocol")
    if s["pad"] != {"fraction": meta["padding"]["fraction"], "min_px": meta["padding"]["min_px"]}:
        problems.append("the fixture's pad")
    for entry in replay:
        if (entry["need"]["code_sha256"], entry["need"]["config_sha256"]) != (s["need"]["code_sha256"],
                                                                             s["need"]["config_sha256"]):
            problems.append(f"need's code or config in {entry['take']} seg-{entry['segment']}")
        if entry["tick_s"] != gate["tick_s"]:
            problems.append(f"the tick in {entry['take']} seg-{entry['segment']}")
    if any(v != gate["prompt"]["version"] for v in served["prompt_versions"]):
        problems.append("the prompt version of a receipt")
    if problems:
        raise Refused("a stand-in differs from GATE-V1: " + ", ".join(dict.fromkeys(problems)))


def file_sha(path):
    return n1.file_sha256(path) if Path(path).is_file() else None


def run(split, n1root, n2root, *, gate_path=GATE_FILE, freezes_path=FREEZES_FILE, den_core=DEFAULT_DEN_CORE,
        checker=None, protocol=None, labels_version=None, fixture_version=None):
    """(report, the file it was written to). Raises Refused, having written nothing."""
    if split not in SPLITS:
        raise Refused(f"--split must be one of {SPLITS}")
    n2root = Path(n2root)
    if not n2root.is_absolute():
        raise Refused(r"the N2 folder must be absolute, for example C:\den-takes\n2")
    if n1.den_take.inside_checkout(n2root) or n1.den_take.in_onedrive(n2root):
        raise Refused("the N2 folder is inside a git checkout or OneDrive; N2's output stays on this PC only")
    gate = load_gate(gate_path)
    if split == "held_out":
        check_slots(gate)
    freezes_text = Path(freezes_path).read_text(encoding="utf-8")
    gold, meta, sessions = load_n1(n1root, split, freezes_text, protocol, labels_version, fixture_version)
    checker = checker or load_checker(den_core)
    pacing = gate["pacing_s"] or gate["schedule_s"]
    replay = []
    for session in sessions:
        session.asks = load_asks(n2root, session, meta["frozen_unix"])
        replay += load_logs(n2root, session, checker)
    served = service(sessions, gate["max_age_s"])
    if split == "held_out":
        check_stand_ins(gate, meta, replay, served)
    analyses = [analyse(session, gate, pacing) for session in sessions]
    metrics, stats = score_metrics(analyses, gate, gold["hash"])
    onset_gated = split == "held_out" and gate["onset_mode"] == "gated"
    gates = sample_gates(analyses, stats, gate, onset_gated or split == "development")
    cases, case_gates = score_cases(analyses, gate, meta["protocol"].phone_band, gate["interval.level"])
    gates += case_gates
    timed = [o for an in analyses for o in an.onsets]
    tags = {}
    for o in timed:
        tags[o["tag"]] = tags.get(o["tag"], 0) + 1
    excluded = {"uncertain_s": round(sum(an.excluded["uncertain_s"] for an in analyses), 3),
                "unsure": {k: sum(an.excluded["unsure"][k] for an in analyses) for k in ("onset", "offset", "band")},
                "carried_pieces": sum(an.excluded["carried_pieces"] for an in analyses),
                "intervals_without_a_scored_second": sum(an.excluded["intervals_without_a_scored_second"]
                                                         for an in analyses),
                "onset_tags": tags, "missed_onsets": sum(1 for o in timed if math.isinf(o["d_on"])),
                "missed_intervals": sum(an.missed_intervals for an in analyses),
                "no_touch_onsets": sum(len(an.no_touch) for an in analyses)}
    served.update(D=rounded(median([max(o["d_on"] - o["w"], 0.0) for o in timed])))
    table, trigger = row_table(analyses)
    needs = sorted({json.dumps(e["need"], sort_keys=True) for e in replay})
    report = {
        "schema": "den-n2-eval-1", "split": split, "scored_unix": now(),
        "gate": dict({"file": gate.file, "sha256": gate.sha256}, **gate_commit(gate_path)),
        "variant": {"scope": gate["scope"], "phone_band": meta["protocol"].phone_band, "prompt": gate["prompt"],
                    "request": {"frames": 3, "span_s": 3, "crop": "the latest frame's padded box, every frame",
                                "context": "placement, a local time label, prior null"},
                    "engine_epochs": served["engine_epochs"], "prompt_versions": served["prompt_versions"],
                    "need": [json.loads(n) for n in needs],
                    "adapter_sha256": file_sha(Path(den_core) / "sources.py"),
                    "ask_sha256": file_sha(HERE / "ask.py"), "pacing_s": pacing,
                    "tick_s": sorted({e["tick_s"] for e in replay}, key=str), "onset_mode": gate["onset_mode"],
                    "detector": gate["detector"]},
        "gold_set": gold,
        "sessions": [{"take": an.session.take, "day": an.session.day} for an in analyses],
        "metrics": metrics,
        "sample_gates": gates,
        "cases": cases,
        "service": served,
        "excluded": excluded,
        "onsets": [{k: rounded(v) if type(v) is float else v for k, v in o.items() if k != "out_t"}
                   for o in timed],
        "rows": {"table": table, "prompt2_trigger": trigger},
        "replay": replay,
        "promoted": False,
        "claims": "none: no working-region, room-position, two-person or routed-timing claim",
        "notes": [f"session bootstrap over {len(analyses)} sessions: few distinct resamples, so its intervals "
                  "are coarse and likely too narrow"]
                 + (["phone band casual: A13 scores the phone at the counter as no need"]
                    if meta["protocol"].phone_band == "casual" else []),
    }
    if split == "development":
        report.update(verdict=None, reasons=[], diagnosis=None,
                      onset_rule=onset_rule(timed, pacing, gate["cap.onset_s"]))
    else:
        report["verdict"], report["reasons"] = verdict(metrics, cases, gates, onset_gated)
        report["diagnosis"] = None if report["verdict"] != "not_feasible" else {
            "engagement": [n for n in ("onset", "early_off", "false_on", "ece.engagement")
                           if metrics[n]["status"] == "failed"],
            "demand": [n for n in ("competence.fine", "competence.casual", "fine_recall", "false_fine", "coverage",
                                   "ece.demand") if metrics[n]["status"] == "failed"],
            "cases": [c for c in cases if cases[c]["status"] == "failed"],
            "unclear_share": rounded(served["rows"].get("unclear", 0) / max(1, served["requests"]))}
    out = n2root / "eval"
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(report["scored_unix"]))
    path, k = out / f"{split}-{stamp}.json", 1
    while path.exists():
        k += 1
        path = out / f"{split}-{stamp}-{k}.json"
    with open(path, "xb") as target:
        target.write(n1.canonical(report))
    return report, path


def results_line(report, path):
    """The one RESULTS.md line for a held-out run: verdict, hashes and counts."""
    gates = {g["name"]: g for g in report["sample_gates"]}
    return (f"den-n2 {report['split']} verdict={report['verdict']} eval={n1.file_sha256(path)} "
            f"gate={report['gate']['sha256']} labels=labels-v{report['gold_set']['label_version']}/"
            f"{report['gold_set']['hash']} scored={n1.utc_date(report['scored_unix'])} "
            f"sessions={len(report['sessions'])}s/{len({s['day'] for s in report['sessions']})}d "
            f"units=fine{gates['units.fine']['n']}/casual{gates['units.casual']['n']} "
            f"onsets={len(report['onsets'])}")


def summary(report, path):
    """What to print: counts, rates and hashes only (no times), so it can be pasted to an agent."""
    lines = [f"N2 {report['split']}: {len(report['sessions'])} sessions over "
             f"{len({s['day'] for s in report['sessions']})} days; gate {report['gate']['sha256'][:12]}, "
             f"labels-v{report['gold_set']['label_version']} {report['gold_set']['hash'][:12]}."]
    for name, m in report["metrics"].items():
        interval = "n/a" if not m["interval"] else f"[{m['interval'][0]}, {m['interval'][1]}]"
        lines.append(f"  {name}: {m['value']} {interval} {m['status']}")
    lines.append("Sample gates: " + ", ".join(f"{g['name']} {g['n']}/{g['sessions']}s/{g['days']}d "
                                              f"{'met' if g['met'] else 'unmet'}" for g in report["sample_gates"]))
    lines.append("Cases: " + ", ".join(f"{c} {v['right']}/{v['units']} {v['status']}"
                                       for c, v in report["cases"].items()))
    s = report["service"]
    lines.append(f"Service: S median {s['S_median']}, p95 {s['S_p95']}, D {s['D']}; {s['requests']} slots, "
                 f"{s['resends']} re-sends, {s['invalid']} invalid, {s['failed']} failed, {s['no_box']} no box.")
    lines.append(f"Excluded: {json.dumps(report['excluded'], sort_keys=True)}")
    if report["split"] == "development":
        rule = report["onset_rule"]
        lines.append(f"Onsets: " + ", ".join(f"{o['take']} seg-{o['segment']} w={o['w']} d_on={o['d_on']} {o['tag']}"
                                             for o in report["onsets"]))
        lines.append(f"Onset rule: D_dev {rule['D']}; P/2 + D_dev <= 3 s: {rule['fits']}; largest fitting P: "
                     f"{rule['largest_fitting_P']}.")
        for row in report["rows"]["table"]:
            lines.append(f"  rows {row['case']}/{row['band']}: {json.dumps(row['rows'], sort_keys=True)} "
                         f"apart {json.dumps(row['apart'], sort_keys=True)}")
        trigger = report["rows"]["prompt2_trigger"]
        lines.append("Prompt-2 trigger: " + ("fires: " + "; ".join(f"{p['fine']} vs {p['other']} ({p['other_band']}) "
                                                                   f"share {', '.join(p['rows'])}"
                                                                   for p in trigger["pairs"])
                                             if trigger["fires"] else "does not fire"))
    else:
        lines.append(f"Verdict: {report['verdict']}. " + "; ".join(report["reasons"]))
        lines.append("RESULTS.md line:")
        lines.append(results_line(report, path))
    lines.append(f"Written: {path}")
    return lines


def main(argv=None, out=None, **hooks):
    out = out or sys.stdout
    parser = argparse.ArgumentParser(description="N2's scorer (docs/plans/n2-need-test/PLAN.md)")
    parser.add_argument("--split", required=True, choices=SPLITS)
    parser.add_argument("--takes", default=DEFAULT_TAKES, help="the takes folder, default %(default)s")
    parser.add_argument("--n1", help=r"N1's folder, default <takes>\n1")
    parser.add_argument("--n2", help=r"N2's folder, default <takes>\n2")
    parser.add_argument("--den-core", default=str(DEFAULT_DEN_CORE), help="den-core's folder (its check.py)")
    parser.add_argument("--gate", default=str(GATE_FILE))
    parser.add_argument("--labels-version", type=int, help="default: the newest logged in FREEZES.md")
    parser.add_argument("--fixture-version", type=int, help="default: the newest logged in FREEZES.md")
    args = parser.parse_args(argv)
    n1root = Path(args.n1 or Path(args.takes) / "n1")
    n2root = Path(args.n2 or Path(args.takes) / "n2")
    try:
        report, path = run(args.split, n1root, n2root, gate_path=args.gate, den_core=args.den_core,
                           labels_version=args.labels_version, fixture_version=args.fixture_version, **hooks)
    except Refused as exc:
        print(f"Refused, nothing written: {exc}", file=out)
        return EXIT_REFUSED
    for line in summary(report, path):
        print(line, file=out)
    return EXIT_DONE


if __name__ == "__main__":
    sys.exit(main())
