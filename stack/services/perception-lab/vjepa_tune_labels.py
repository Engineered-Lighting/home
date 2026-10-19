"""Clip labels for den V-JEPA probe tuning: owner tiers plus weak labels (AL-T, #327).

Offline operator tool, run where the ledger lives (the Jetson controller). The
ledger is opened read-only (lab_regression.open_ro) and never written; the
only output is --out. No network, no images.

Each clip is a window [window_start, window_end] in epoch seconds of one
session (vjepa_tune_export writes them). Its label has three tiers:

  gold   owner_reviewed annotation intervals (lab_regression provenance)
  owner  owner_statement manifest labels, bookmarks, unreviewed owner labels
  weak   a code rule over signals recorded in the same window (below), or a
         qwen_proposed annotation when no rule fires

A tier's value is the den class whose intervals cover at least label_fraction
of the window, else None (no label, or a conflict). empty_room and 'other'
are never a probe class (V-JEPA only runs on occupied clips).

Weak rule (config 'weak'), signals within the window padded by window_pad_s:
  * Qwen: the valid qwen.observation activity_hypothesis values;
  * JEV: live reasoning.output answers covering the window's midpoint with
    confidence >= jev_min_confidence (every arm present must agree);
  * PC: derived.computer_input_recency states from ha.observation.
  computer_work : PC input active, and Qwen or JEV says computer_work.
                  Independent of the camera (weak_independent true).
  reading, phone_use, resting, transition :
                  Qwen and JEV agree on the class and the PC is not active.
                  Camera-correlated (weak_independent false).
  Anything else, or any disagreement: no weak label.
Circularity is recorded, never hidden: qwen_consumed_by_jev is always true on
the den wires, and vjepa_in_session marks sessions where JEV could also have
seen V-JEPA. Weak labels may train a probe; they never score one.

Usage:
  python vjepa_tune_labels.py --ledger DB --clips CLIPS.jsonl --out LABELS.jsonl [--manifest FILE]
                              [--config vjepa_tune_config.json]
"""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys

import lab_regression as regression

CLASSES = ("reading", "computer_work", "phone_use", "resting", "transition")
GOLD = frozenset({"owner_reviewed"})
OWNER = frozenset({"owner_statement", "bookmark", "owner_unreviewed"})
WEAK_ANNOTATION = frozenset({"qwen_proposed"})
PC_FACT = "derived.computer_input_recency"


def finite(value):
    return regression.scoring.finite(value)


# ---- Pure rules ----------------------------------------------------------------

def covering(intervals, start, end, fraction):
    """The den class covering >= fraction of [start, end] (session seconds), else None."""
    width = end - start
    if width <= 0:
        return None
    cover = defaultdict(float)
    for item in intervals:
        overlap = min(end, item["end"]) - max(start, item["start"])
        if overlap > 0:
            cover[item["activity"]] += overlap
    winners = [a for a, s in cover.items() if s >= fraction * width]
    return winners[0] if len(winners) == 1 and winners[0] in CLASSES else None


def weak_label(qwen, jev, pc, config):
    """(class or None, basis, independent). qwen: activity_hypothesis values;
    jev: [(activity or None, confidence)] answers covering the midpoint;
    pc: derived input-recency states in the window."""
    q = Counter(a for a in qwen if a in CLASSES)
    q_top = q.most_common(1)[0][0] if q and len(q) == 1 and sum(q.values()) >= config["qwen_min_agree"] else None
    confident = [a for a, p in jev if finite(p) and p >= config["jev_min_confidence"]]
    j_top = confident[0] if confident and len(set(confident)) == 1 and confident[0] in CLASSES else None
    states = set(s for s in pc if s)
    active = bool(states & set(config["pc_active_states"]))
    inactive = bool(states) and states <= set(config["pc_inactive_states"])
    if active and "computer_work" in (q_top, j_top) and not ({q_top, j_top} - {None, "computer_work"}):
        return "computer_work", "pc_input_active+" + ("qwen" if q_top else "jev"), True
    if q_top and q_top == j_top and q_top != "computer_work" and not active:
        return q_top, "qwen_jev_agree" + ("+pc_inactive" if inactive else ""), False
    return None, None, False


def label_clip(context, window_start, window_end, config):
    """The tiered label of one clip window (epoch seconds) of one session."""
    started = context["started_at"]
    start, end = window_start - started, window_end - started
    fraction = config["label_fraction"]
    tiers = {}
    for tier, kinds in (("gold", GOLD), ("owner", OWNER), ("weak_annotation", WEAK_ANNOTATION)):
        tiers[tier] = covering([i for i in context["intervals"] if i["provenance"] in kinds], start, end, fraction)
    pad = config["window_pad_s"]
    mid = (start + end) / 2
    qwen = [a for t, a in context["qwen"] if window_start - pad <= t <= window_end + pad]
    jev = [(a, p) for s, until, a, p in context["jev"] if s <= mid < until]
    pc = [state for t, state in context["pc"] if window_start - pad <= t <= window_end + pad]
    weak, basis, independent = weak_label(qwen, jev, pc, config)
    if weak is None and tiers["weak_annotation"]:
        weak, basis = tiers["weak_annotation"], "qwen_proposed_annotation"
    return {"gold": tiers["gold"], "owner": tiers["owner"], "weak": weak, "weak_basis": basis,
            "weak_independent": independent, "qwen_consumed_by_jev": True,
            "vjepa_in_session": context["vjepa_in_session"]}


# ---- Ledger reads ----------------------------------------------------------------

def session_contexts(db, manifest):
    """{session_id: context} with intervals (session seconds) and the weak signals."""
    sessions, intervals, _ = regression.gather(db, manifest)
    contexts = {sid: {"started_at": s["started_at"] or 0.0, "intervals": [], "qwen": [], "jev": [], "pc": [],
                      "vjepa_in_session": False} for sid, s in sessions.items()}
    for item in intervals:
        contexts[item["session_id"]]["intervals"].append(item)
    for sid, kind, raw in db.execute("""SELECT session_id,kind,payload FROM events WHERE kind IN
            ('qwen.observation','reasoning.output','ha.observation','vjepa.observation') ORDER BY id"""):
        if sid not in contexts:
            continue
        value = json.loads(raw)
        ctx = contexts[sid]
        if kind == "vjepa.observation":
            ctx["vjepa_in_session"] = True
        elif kind == "qwen.observation":
            facts = value.get("facts") or {}
            if value.get("status") == "valid" and finite(value.get("observed_at")):
                ctx["qwen"].append((value["observed_at"], facts.get("activity_hypothesis")))
        elif kind == "reasoning.output":
            p = value.get("prediction") or {}
            if (value.get("mode", p.get("mode")) == "live" and finite(p.get("start"))
                    and finite(p.get("valid_until"))):
                acts = p.get("activities") or [None]
                ctx["jev"].append((p["start"], p["valid_until"], acts[0] if len(acts) == 1 else None,
                                   p.get("confidence")))
        elif kind == "ha.observation":
            fact = (value.get("ha") or {}).get(PC_FACT) or {}
            if finite(value.get("observed_at")) and fact.get("status") == "known":
                ctx["pc"].append((value["observed_at"], fact.get("state")))
    return contexts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ledger", default=regression.LEDGER)
    parser.add_argument("--manifest", default=regression.MANIFEST)
    parser.add_argument("--clips", required=True, help="clips.jsonl from vjepa_tune_export")
    parser.add_argument("--config", default=str(Path(__file__).with_name("vjepa_tune_config.json")))
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))["weak"]
    db = regression.open_ro(args.ledger)
    contexts = session_contexts(db, regression.load_manifest(args.manifest))
    counts = Counter()
    with open(args.out, "w", encoding="utf-8") as out:
        for line in Path(args.clips).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            clip = json.loads(line)
            if clip.get("schema"):
                continue
            label = label_clip(contexts[clip["session_id"]], clip["window_start"], clip["window_end"], config)
            for tier in ("gold", "owner", "weak"):
                counts[(tier, label[tier])] += 1
            out.write(json.dumps({"clip_id": clip["clip_id"], "label": label}, sort_keys=True) + "\n")
    for (tier, value), n in sorted(counts.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
        print("%-6s %-14s %6d" % (tier, value, n))


if __name__ == "__main__":
    main(sys.argv[1:])
