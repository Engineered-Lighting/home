"""Leave-one-session-out training and evaluation of den V-JEPA probes (AL-T, #327).

Offline research tool, not part of any release. Reads a feature cache
(vjepa-den-cache-1: index.jsonl + tokens.npy, written by
tools/la-vjepa-worker/vjepa_den_encode.py on LA or by vjepa_tune_synth.py) and
writes one report (--out) and optionally one head file (--final). No network,
no ledger, no images. The cache holds biometric features: keep it, the report
and the head under a private 0700 directory on the host that made them.

Protocol (config 'protocol'):
  * folds: hold out one session (mode session) or one calendar day (mode day);
  * train rows: every other session's clips whose first label tier in
    'train_tiers' (e.g. gold, owner, weak) is a den class;
  * test rows: the held-out group's clips with an 'eval_tiers' label (gold,
    owner). Weak labels never score a probe;
  * calibration: weights on the earlier training sessions, one temperature on
    the latest one (by first anchor); with a single training session, in-sample;
  * smoothing: causal, over every clip of the held-out session in time order;
  * buckets: per class one-vs-rest thresholds for 'high' and 'medium' chosen on
    the pooled out-of-fold scores (precision targets, minimum support, Wilson
    lower bound with one independent trial per hold_s of clips).
The baseline is the pinned production readout stored with each clip
(baseline.reading, baseline.zero_shot) scored on the same rows.

Usage:
  python vjepa_tune_eval.py --cache DIR --config vjepa_tune_config.json --out REPORT.json
                            [--labels LABELS.jsonl] [--final HEAD.npz] [--only NAME ...]
  python vjepa_tune_eval.py --synthetic DIR ...   (writes a synthetic cache to DIR first)
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

import numpy as np

# Run with -I on LA (safe_path): the script directory is not on sys.path, so
# its staged siblings are added explicitly and nothing else is.
if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vjepa_tune_probe as probe  # noqa: E402

CACHE_SCHEMA = "vjepa-den-cache-1"
REPORT_SCHEMA = "vjepa-den-tune-report-1"
HEAD_SCHEMA = "vjepa-den-head-1"
CLASSES = probe.CLASSES


# ---- Inputs ------------------------------------------------------------------

def load_cache(directory):
    directory = Path(directory)
    lines = (directory / "index.jsonl").read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    if header.get("schema") != CACHE_SCHEMA:
        raise SystemExit("cache schema %s required" % CACHE_SCHEMA)
    rows = [json.loads(line) for line in lines[1:] if line.strip()]
    tokens = np.load(directory / "tokens.npy", mmap_mode="r")
    if len(rows) != len(tokens) or any(r["row"] != i for i, r in enumerate(rows)):
        raise SystemExit("cache index and tokens disagree")
    if tokens.shape[1] != int(np.prod(header["grid"])) or tokens.shape[2] != header["dim"]:
        raise SystemExit("cache token geometry differs from its header")
    return header, rows, tokens


def apply_labels(rows, file):
    """Replace each row's 'label' with the labels file's entry for its clip_id
    (vjepa_tune_labels output); rows absent from the file get no label."""
    table = {}
    for line in Path(file).read_text(encoding="utf-8").splitlines():
        if line.strip():
            item = json.loads(line)
            table[item["clip_id"]] = item["label"]
    return [dict(r, label=table.get(r["clip_id"], {})) for r in rows]


def label_of(row, tiers):
    label = row.get("label") or {}
    for tier in tiers:
        value = label.get(tier)
        if value in CLASSES:
            return value, tier
    return None, None


def select(rows, config):
    sel = config["select"]
    keep = []
    for r in rows:
        if r.get("crop", "union") != sel.get("crop", "union"):
            continue
        if r.get("span_variant", "3.5s_8f") != sel.get("span_variant", "3.5s_8f"):
            continue
        if sel.get("single_person_only", True) and not r.get("single_person", True):
            continue
        keep.append(r)
    return keep


def groups(rows, mode):
    key = "day" if mode == "day" else "session_id"
    return sorted({r.get(key) or r["session_id"] for r in rows}), key


# ---- Metrics -----------------------------------------------------------------

def per_class(truth, predicted):
    out = {}
    for c in range(len(CLASSES)):
        tp = int(((predicted == c) & (truth == c)).sum())
        fp = int(((predicted == c) & (truth != c)).sum())
        fn = int(((predicted != c) & (truth == c)).sum())
        p = tp / (tp + fp) if tp + fp else None
        r = tp / (tp + fn) if tp + fn else None
        f1 = 2 * p * r / (p + r) if p and r else (0.0 if tp + fn else None)
        out[CLASSES[c]] = {"support": int((truth == c).sum()), "tp": tp, "fp": fp, "fn": fn,
                           "precision": p, "recall": r, "f1": f1}
    return out


def macro_f1(classes, min_support):
    values = [v["f1"] for v in classes.values() if v["support"] >= min_support and v["f1"] is not None]
    return float(np.mean(values)) if values else None


def average_precision(scores, positives):
    positives = np.asarray(positives, dtype=bool)
    if positives.sum() == 0:
        return None
    order = np.argsort(-np.asarray(scores), kind="stable")
    hits = positives[order]
    precision = np.cumsum(hits) / np.arange(1, len(hits) + 1)
    return float((precision * hits).sum() / hits.sum())


def ece(probabilities, truth, bins=5):
    """Top-label ECE over equal-mass bins."""
    if len(truth) == 0:
        return None
    confidence = probabilities.max(axis=1)
    correct = probabilities.argmax(axis=1) == truth
    order = np.argsort(confidence, kind="stable")
    total = 0.0
    for chunk in np.array_split(order, min(bins, len(order))):
        total += len(chunk) * abs(confidence[chunk].mean() - correct[chunk].mean())
    return float(total / len(truth))


def brier(probabilities, truth):
    if len(truth) == 0:
        return None
    onehot = np.eye(len(CLASSES))[truth]
    return float(((probabilities - onehot) ** 2).sum(axis=1).mean())


def coverage_at_precision(probabilities, truth, target):
    """Largest share of rows answered (top class, confidence >= tau) with precision >= target."""
    confidence = probabilities.max(axis=1)
    correct = probabilities.argmax(axis=1) == truth
    best = 0.0
    for tau in np.unique(confidence):
        chosen = confidence >= tau
        if chosen.sum() and correct[chosen].mean() >= target:
            best = max(best, float(chosen.mean()))
    return best


def binary_f1(scores, positives, tau):
    chosen = np.asarray(scores) >= tau
    positives = np.asarray(positives, dtype=bool)
    tp, fp, fn = int((chosen & positives).sum()), int((chosen & ~positives).sum()), int((~chosen & positives).sum())
    return 2 * tp / (2 * tp + fp + fn) if tp else 0.0


def summarize(probabilities, truth, sessions, config):
    pred = probabilities.argmax(axis=1)
    classes = per_class(truth, pred)
    min_support = config["protocol"]["min_class_support"]
    reading = CLASSES.index("reading")
    per_session = {}
    for sid in sorted(set(sessions)):
        mask = np.asarray(sessions) == sid
        per_session[sid[:8]] = {"rows": int(mask.sum()),
                                "macro_f1": macro_f1(per_class(truth[mask], pred[mask]), 1)}
    band = [v["macro_f1"] for v in per_session.values() if v["macro_f1"] is not None]
    return {"rows": int(len(truth)), "classes": classes, "macro_f1": macro_f1(classes, min_support),
            "reading_ap": average_precision(probabilities[:, reading], truth == reading),
            "reading_best_f1": max((binary_f1(probabilities[:, reading], truth == reading, t)
                                    for t in np.linspace(0.05, 0.95, 19)), default=None),
            "ece": ece(probabilities, truth), "brier": brier(probabilities, truth),
            "coverage_at_precision_0_9": coverage_at_precision(probabilities, truth, 0.9),
            "session_macro_f1_band": [min(band), max(band)] if band else None, "per_session": per_session}


# ---- Cross-validation ----------------------------------------------------------

def features_for(tokens, header, kind, rows_idx):
    if kind == "tokens":
        return probe.Subset(tokens, rows_idx)
    return probe.pooled_features(probe.Subset(tokens, rows_idx), header["grid"], kind)


def calibration_split(train_rows):
    """(weight rows, temperature rows, how): the latest training session by first
    anchor is held back for the temperature when there are two or more."""
    first = {}
    for r in train_rows:
        first[r["session_id"]] = min(first.get(r["session_id"], r["anchor_at"]), r["anchor_at"])
    if len(first) < 2:
        return train_rows, train_rows, "in_sample"
    held = max(first, key=lambda s: (first[s], s))
    return ([r for r in train_rows if r["session_id"] != held], [r for r in train_rows if r["session_id"] == held],
            "held_back")


def fit_predict(spec, header, tokens, train_rows, apply_rows, train_tiers):
    """Calibrated probabilities for apply_rows from a probe fitted on train_rows."""
    weight_rows, temp_rows, how = calibration_split(train_rows)
    y_w = np.array([CLASSES.index(label_of(r, train_tiers)[0]) for r in weight_rows])
    y_t = np.array([CLASSES.index(label_of(r, train_tiers)[0]) for r in temp_rows])
    model, kind = probe.build(spec, len(CLASSES))
    model.fit(features_for(tokens, header, kind, [r["row"] for r in weight_rows]), y_w)
    temperature = probe.fit_temperature(model.logits(features_for(tokens, header, kind, [r["row"] for r in temp_rows])), y_t)
    logits = model.logits(features_for(tokens, header, kind, [r["row"] for r in apply_rows]))
    return probe.softmax(logits / temperature), temperature, how, model, kind


def cross_validate(spec, header, rows, tokens, config):
    """Out-of-fold calibrated probabilities for every clip of every held-out group
    that has at least one evaluation label: {row: probabilities}, fold notes."""
    protocol = config["protocol"]
    train_tiers, eval_tiers = protocol["train_tiers"], protocol["eval_tiers"]
    names, key = groups(rows, protocol["fold"])
    oof, notes = {}, []
    for name in names:
        held = [r for r in rows if (r.get(key) or r["session_id"]) == name]
        if not any(label_of(r, eval_tiers)[0] for r in held):
            continue
        train = [r for r in rows if (r.get(key) or r["session_id"]) != name and label_of(r, train_tiers)[0]]
        present = {label_of(r, train_tiers)[0] for r in train}
        if len(train) < protocol["min_train_rows"] or len(present) < 2:
            notes.append({"fold": str(name)[:8], "skipped": "insufficient_training_rows", "train_rows": len(train)})
            continue
        probs, temperature, how, _, _ = fit_predict(spec, header, tokens, train, held, train_tiers)
        for r, p in zip(held, probs):
            oof[r["row"]] = p
        notes.append({"fold": str(name)[:8], "train_rows": len(train), "held_rows": len(held),
                      "temperature": temperature, "temperature_fit": how,
                      "train_classes": sorted(present)})
    return oof, notes


def smoothed(oof, rows, spec):
    """Smooth each session's out-of-fold probabilities in anchor order."""
    by_session = {}
    for r in rows:
        if r["row"] in oof:
            by_session.setdefault(r["session_id"], []).append(r)
    out = {}
    for items in by_session.values():
        p = np.stack([oof[r["row"]] for r in items])
        s = probe.smooth(p, [r["anchor_at"] for r in items], spec)
        out.update({r["row"]: v for r, v in zip(items, s)})
    return out


def scored_rows(rows, probabilities, eval_tiers):
    chosen = [r for r in rows if r["row"] in probabilities and label_of(r, eval_tiers)[0]]
    P = np.stack([probabilities[r["row"]] for r in chosen]) if chosen else np.zeros((0, len(CLASSES)))
    truth = np.array([CLASSES.index(label_of(r, eval_tiers)[0]) for r in chosen], dtype=int)
    return chosen, P, truth


def buckets_for(P, truth, config):
    b = config["buckets"]
    effective = config["protocol"]["stride_s"] / b["hold_s"]
    out = {}
    for c, name in enumerate(CLASSES):
        if (truth == c).sum() < b["min_positive"]:
            out[name] = None
            continue
        out[name] = probe.choose_buckets(P[:, c], truth == c, b["targets"], min_support=b["min_support"],
                                         effective_n=effective)
    return out


def baseline(rows, eval_tiers):
    """The pinned production readout on the same rows (reading head, NTU sums)."""
    chosen = [r for r in rows if label_of(r, eval_tiers)[0] and isinstance((r.get("baseline") or {}).get("reading"), (int, float))]
    if not chosen:
        return None
    truth = np.array([label_of(r, eval_tiers)[0] for r in chosen])
    reading = np.array([r["baseline"]["reading"] for r in chosen])
    out = {"rows": len(chosen), "reading": {"ap": average_precision(reading, truth == "reading"),
                                             "f1_at_0_5": binary_f1(reading, truth == "reading", 0.5),
                                             "f1_at_0_6_high": binary_f1(reading, truth == "reading", 0.6),
                                             "best_f1": max(binary_f1(reading, truth == "reading", t)
                                                            for t in np.linspace(0.05, 0.95, 19))}}
    zero = [r for r in chosen if isinstance(r["baseline"].get("zero_shot"), dict)]
    if zero:
        zt = np.array([label_of(r, eval_tiers)[0] for r in zero])
        out["zero_shot"] = {c: {"ap": average_precision([r["baseline"]["zero_shot"][c] for r in zero], zt == c),
                                "f1_at_0_5": binary_f1([r["baseline"]["zero_shot"][c] for r in zero], zt == c, 0.5)}
                            for c in ("reading", "computer_work", "phone_use")}
    return out


def label_inventory(rows, config):
    out = {}
    for tier in ("gold", "owner", "weak"):
        counts = {}
        for r in rows:
            value = (r.get("label") or {}).get(tier)
            if value in CLASSES:
                counts.setdefault(value, {"clips": 0, "sessions": set()})
                counts[value]["clips"] += 1
                counts[value]["sessions"].add(r["session_id"])
        out[tier] = {k: {"clips": v["clips"], "sessions": len(v["sessions"])} for k, v in sorted(counts.items())}
    return out


def evaluate(header, rows, tokens, config, only=None):
    eval_tiers = config["protocol"]["eval_tiers"]
    rows = select(rows, config)
    results = []
    for spec in config["probes"]:
        if only and spec["name"] not in only:
            continue
        oof, notes = cross_validate(spec, header, rows, tokens, config)
        for smoothing in config["smoothing"]:
            probs = smoothed(oof, rows, smoothing)
            chosen, P, truth = scored_rows(rows, probs, eval_tiers)
            metrics = summarize(P, truth, [r["session_id"] for r in chosen], config) if len(truth) else None
            results.append({"probe": spec["name"], "smoothing": smoothing["name"], "metrics": metrics,
                            "buckets": buckets_for(P, truth, config) if len(truth) else None, "folds": notes})
    ranked = sorted((r for r in results if r["metrics"] and r["metrics"]["macro_f1"] is not None),
                    key=lambda r: (-r["metrics"]["macro_f1"], -(r["metrics"]["reading_ap"] or 0)))
    return {"schema": REPORT_SCHEMA, "synthetic": bool(header.get("synthetic")),
            "cache": {k: header.get(k) for k in ("model_version", "artifact_sha256", "grid", "dim", "count")},
            "config_sha256": hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
            "selected_rows": len(rows), "labels": label_inventory(rows, config),
            "baseline_pinned_readout": baseline(rows, eval_tiers), "results": results,
            "best": {"probe": ranked[0]["probe"], "smoothing": ranked[0]["smoothing"]} if ranked else None,
            "caveats": ["weak labels train but never score",
                        "per-class figures below the sample gate are not passes",
                        "synthetic caches say nothing about den accuracy" if header.get("synthetic") else
                        "den sessions are one person; held-out sessions are not new people"]}


def fit_final(header, rows, tokens, config, probe_name, path):
    """Fit the named probe on every train-labelled row (temperature on the
    latest session) and save the parameters, temperature and identity."""
    spec = next(s for s in config["probes"] if s["name"] == probe_name)
    rows = select(rows, config)
    train = [r for r in rows if label_of(r, config["protocol"]["train_tiers"])[0]]
    _, temperature, how, model, kind = fit_predict(spec, header, tokens, train, train[:1], config["protocol"]["train_tiers"])
    arrays = {}
    for i, member in enumerate(model.members):
        for k, v in member.params.items():
            arrays["m%d_%s" % (i, k)] = v
        arrays["m%d_scale_mean" % i], arrays["m%d_scale_std" % i] = member.scale.mean, member.scale.std
    meta = {"schema": HEAD_SCHEMA, "probe": spec, "features": kind, "classes": list(CLASSES),
            "temperature": temperature, "temperature_fit": how, "train_rows": len(train),
            "cache": {k: header.get(k) for k in ("model_version", "artifact_sha256", "grid", "dim")}}
    np.savez(path, meta=np.array(json.dumps(meta, sort_keys=True)), **arrays)
    return meta


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cache")
    parser.add_argument("--synthetic", help="write a synthetic cache to this directory and use it")
    parser.add_argument("--config", default=str(Path(__file__).with_name("vjepa_tune_config.json")))
    parser.add_argument("--labels")
    parser.add_argument("--out", required=True)
    parser.add_argument("--final", help="also fit the best probe on all rows and save it here (.npz)")
    parser.add_argument("--only", nargs="*")
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if args.synthetic:
        import vjepa_tune_synth as synth
        synth.write(args.synthetic, *synth.make(**config.get("synthetic", {})))
        args.cache = args.synthetic
    if not args.cache:
        raise SystemExit("--cache or --synthetic required")
    header, rows, tokens = load_cache(args.cache)
    if args.labels:
        rows = apply_labels(rows, args.labels)
    report = evaluate(header, rows, tokens, config, only=args.only)
    if args.final and report["best"]:
        report["final_head"] = fit_final(header, rows, tokens, config, report["best"]["probe"], args.final)
    Path(args.out).write_text(json.dumps(report, indent=1, sort_keys=True, default=float) + "\n", encoding="utf-8")
    print("%-26s %-10s %8s %8s %8s %6s" % ("probe", "smoothing", "macroF1", "readAP", "ECE", "rows"))
    for r in report["results"]:
        m = r["metrics"] or {}
        fmt = lambda v: "-" if v is None else "%.3f" % v
        print("%-26s %-10s %8s %8s %8s %6s" % (r["probe"], r["smoothing"], fmt(m.get("macro_f1")),
                                                fmt(m.get("reading_ap")), fmt(m.get("ece")), m.get("rows", 0)))
    if report["baseline_pinned_readout"]:
        b = report["baseline_pinned_readout"]["reading"]
        print("baseline pinned reading head: AP %s, F1@0.5 %.3f, best F1 %.3f" % (
            "-" if b["ap"] is None else "%.3f" % b["ap"], b["f1_at_0_5"], b["best_f1"]))
    print("best:", report["best"])
    return report


if __name__ == "__main__":
    main(sys.argv[1:])
