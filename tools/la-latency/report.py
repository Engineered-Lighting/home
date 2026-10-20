#!/usr/bin/env python3
"""Baseline table and S2.1 decision record from harvest.py samples (plan S0.2, S2.1).

Workstation only: Python 3.10+, standard library only. Reads one or more
harvest JSONL files plus slo.json and writes Markdown:

- the baseline table: n, p50, p95, dates and method for every pipeline in
  plan section 4, then supporting measurements and LA pilot event counts;
- when the window holds at least 7 days and 500 occupied Qwen requests, the
  S2.1 D1-D3 decision record: every criterion with its value, or
  "insufficient data". A short window writes no record: extend it rather than
  decide early.

Percentiles are nearest-rank. A statistic needs at least
min_samples_per_statistic samples (slo.json), otherwise it is
"insufficient data". Flag metrics report their rate (mean of 1/0 values).

--since and --until bound the decision window: the baseline table, the
7-day/500-request gate, D1, D3 and the live side of D2. The D2 no-live
baseline is every sample before --live-since (from --baseline-since if
given), whatever --since says: live Qwen starts at S1.3, before the S2.1
window, and the plan's baseline is the S0.2 one.
"""
import argparse
import bisect
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import harvest

HOLDS, FAILS, NO_DATA = "holds", "does not hold", "insufficient data"
DAY = 86400.0

# D2 guard series (pipeline, metric, label). Voice TTFT comes only from the
# S1.4 synthetic voice probe, in exact seconds. vLLM's TTFT histogram counts
# every client, live Qwen included, at bucket upper bounds that step 1.33-2.5x
# above 0.1 s, so it cannot resolve 1.10x; it stays a supporting measurement.
D2_GUARDS = (("voice_probe", "ttft", "voice TTFT p95"),
             ("camera_chat_la", "backend", "camera-chat backend p95"))

# LA pilot events that need the owner (metric suffix, meaning).
ALARMS = (
    ("_unresolved", "an unresolved execution closes LA admission until reconciled"),
    ("_ledger_unavailable", "the LA ledger failed and admission closed"),
    ("_consumer_disabled", "the live consumer did not start"),
    ("event:live_admission_closed", "live admission closed; after a re-pin it stays closed until live-scene.json "
                                    "and Victoria's runtime_revision name the new engine"),
    ("event:vllm_started_at_changed", "vLLM restarted and the pilot refuses until it is re-pinned (S0.4(d))"),
    ("event:pilot_refused_start", "the pilot refused to start: camera chat and LA facts are down (S0.4(d))"),
    ("event:live_admission_stays_closed", "an S1.8 reopen was refused (hourly limit or another close reason); "
                                          "live stays closed until the pilot restarts"),
)


class ReportError(ValueError):
    pass


def load_samples(paths):
    """Validated samples from harvest JSONL files (any malformed line refuses)."""
    rows = []
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                where = "%s:%d" % (Path(path).name, number)
                try:
                    row = json.loads(line)
                except ValueError:
                    raise ReportError(where + ": not JSON") from None
                if not isinstance(row, dict) or set(row) != set(harvest.SAMPLE_KEYS):
                    raise ReportError(where + ": sample keys must be exactly " + ", ".join(harvest.SAMPLE_KEYS))
                if (not all(isinstance(row[k], str) and row[k] for k in ("pipeline", "metric", "source", "method"))
                        or not harvest.finite(row["value_s"])):
                    raise ReportError(where + ": invalid sample")
                try:
                    epoch = harvest.parse_time(row["t"])
                except harvest.InputError:
                    raise ReportError(where + ": invalid t") from None
                rows.append({**row, "_t": epoch})
    return rows


def percentile(values, q):
    """Nearest-rank percentile, q in (0, 1]."""
    ordered = sorted(values)
    return ordered[max(1, math.ceil(q * len(ordered))) - 1]


def day(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%d")


def seconds(value):
    return "%.3f s" % value


def millis(value):
    return "%.0f ms" % (value * 1000)


class Index:
    def __init__(self, rows, minimum):
        self.minimum = minimum
        self.series = {}
        for row in rows:
            self.series.setdefault((row["pipeline"], row["metric"]), []).append(row)
        for series in self.series.values():
            series.sort(key=lambda r: r["_t"])

    def rows(self, pipeline, metric, after=None, before=None):
        return [r for r in self.series.get((pipeline, metric), [])
                if (after is None or r["_t"] >= after) and (before is None or r["_t"] < before)]

    def values(self, pipeline, metric, **window):
        return [r["value_s"] for r in self.rows(pipeline, metric, **window)]

    def count(self, pipeline, metric, **window):
        return len(self.rows(pipeline, metric, **window))

    def stat(self, pipeline, metric, q, **window):
        values = self.values(pipeline, metric, **window)
        return percentile(values, q) if len(values) >= self.minimum else None

    def rate(self, pipeline, metric, **window):
        values = self.values(pipeline, metric, **window)
        return sum(values) / len(values) if len(values) >= self.minimum else None


def target_text(entry):
    parts = []
    for target in entry.get("targets", []):
        bounds = ["%s ≤ %s" % (key[:-2], seconds(target[key])) for key in ("p50_s", "p95_s", "p99_s") if key in target]
        if "rate_min" in target:
            bounds.append("rate ≥ %.0f%%" % (target["rate_min"] * 100))
        parts.append("%s: %s" % (target["stage"], ", ".join(bounds)))
    if entry.get("relative_target"):
        parts.append(entry["relative_target"])
    if entry.get("guard_ratio_max"):
        parts.append("p95 with live Qwen ≤ %.2f × the no-live baseline" % entry["guard_ratio_max"])
    return "; ".join(parts) or "set after S0.2"


def table_row(index, entry, with_target):
    rows = index.rows(entry["pipeline"], entry["metric"])
    values = [r["value_s"] for r in rows]
    if not rows:
        p50 = p95 = dates = "no data"
        method = entry.get("missing", "no data")
    else:
        first, last = day(rows[0]["_t"]), day(rows[-1]["_t"])
        dates = first if first == last else "%s to %s" % (first, last)
        method = "; ".join(sorted({r["method"] for r in rows}))
        if entry.get("kind") == "rate":
            p50, p95 = "rate %.1f%%" % (100 * sum(values) / len(values)), "n/a"
        elif entry.get("kind") == "count":
            p50, p95 = "%g" % percentile(values, .5), "%g" % percentile(values, .95)
        else:
            p50, p95 = seconds(percentile(values, .5)), seconds(percentile(values, .95))
    cells = [entry["label"], str(len(rows)), p50, p95, dates, method]
    if with_target:
        cells += [entry.get("today", ""), target_text(entry)]
    return "| " + " | ".join(c.replace("|", "/") for c in cells) + " |"


def la_calls(index):
    """Completed LA live calls with sub-second journal times (total once the
    S0.4 suffix is logged, else backend). A whole-second time can be up to
    1 s early, which is wider than the pairing tolerance."""
    la = index.rows("qwen_live_la", "total") or index.rows("qwen_live_la", "backend")
    return [r for r in la if not r["method"].endswith(harvest.WHOLE_SECOND)]


def paired_overhead(index, tolerance):
    """Qwen transport overhead per request: Jetson request_to_fact minus the LA
    total for the same request. A request spans [sent, available] with sent =
    available - request_to_fact; an LA call spans [t - total, t], t being its
    journal time. A call pairs with a request when the whole call lies inside
    [sent - tolerance, available + tolerance]. One request is in flight at a
    time, so back to back the previous call starts a full request before this
    send and never fits. A request that two calls fit, or a call that two
    requests claim, is skipped."""
    la = la_calls(index)
    ends = [r["_t"] for r in la]
    facts = index.rows("qwen_live_victoria", "request_to_fact")
    fits, claims = [], {}
    for fact in facts:
        sent = fact["_t"] - fact["value_s"]
        first = bisect.bisect_left(ends, sent - tolerance)
        last = bisect.bisect_right(ends, fact["_t"] + tolerance)
        inside = [i for i in range(first, last) if ends[i] - la[i]["value_s"] >= sent - tolerance]
        fits.append(inside)
        for i in inside:
            claims[i] = claims.get(i, 0) + 1
    return [fact["value_s"] - la[inside[0]]["value_s"]
            for fact, inside in zip(facts, fits) if len(inside) == 1 and claims[inside[0]] == 1]


def decision_rows(index, slo, live_since, baseline=None):
    """[(gate, criterion, value, result, gap)]. gap is None unless the result
    is insufficient data; then (kind, detail): kind "few" when every input has
    samples but fewer than the minimum, so a longer window helps, and "none"
    when an input has no samples or is missing, so it cannot. baseline holds
    the D2 no-live samples (default: this index)."""
    decision, minimum = slo["decision"], index.minimum
    d1, d2, d3 = decision["D1"], decision["D2"], decision["D3"]
    baseline = index if baseline is None else baseline
    overhead = paired_overhead(index, decision["pairing_tolerance_s"])
    o50 = percentile(overhead, .5) if len(overhead) >= minimum else None
    o95 = percentile(overhead, .95) if len(overhead) >= minimum else None
    rows = []

    def add(group, criterion, value, ok, text, counts, detail=None, kind=None):
        if value is not None:
            rows.append((group, criterion, text, HOLDS if ok(value) else FAILS, None))
            return
        n = min(counts)
        if detail is None:
            detail = "no samples" if n == 0 else ("n=%d, needs %d" % (n, minimum) if n < minimum else "unusable values")
        kind = kind or ("few" if 0 < n < minimum else "none")
        rows.append((group, criterion, "%s (%s)" % (NO_DATA, detail), NO_DATA, (kind, detail)))

    paired = "%d of %d requests paired" % (len(overhead), index.count("qwen_live_victoria", "request_to_fact"))
    pairs = [len(overhead)]
    stay, build = d1["stay"], d1["build"]
    add("D1 stay", "Qwen overhead p50 ≤ %s" % millis(stay["qwen_overhead_p50_max_s"]), o50,
        lambda v: v <= stay["qwen_overhead_p50_max_s"], None if o50 is None else "%s (%s)" % (millis(o50), paired),
        pairs, paired)
    add("D1 stay", "Qwen overhead p95 ≤ %s" % millis(stay["qwen_overhead_p95_max_s"]), o95,
        lambda v: v <= stay["qwen_overhead_p95_max_s"], None if o95 is None else "%s (%s)" % (millis(o95), paired),
        pairs, paired)
    vjepa = index.stat("vjepa_live", "transport_overhead", .5)
    limit = stay["vjepa_overhead_p50_max_s"]
    criterion = "V-JEPA overhead p50 ≤ %s" % millis(limit)
    if vjepa is not None:
        add("D1 stay", criterion, vjepa, lambda v: v <= limit, millis(vjepa), [])
    else:
        bound = index.stat("vjepa_live", "overhead_upper_bound", .5)
        measured = index.count("vjepa_live", "transport_overhead")
        if bound is not None and bound <= limit:
            add("D1 stay", criterion, bound, lambda v: True, millis(bound) + " (upper bound)", [])
        elif bound is not None:
            # The bound includes frame age at send, so more of it cannot decide.
            add("D1 stay", criterion, None, None, None, [measured],
                "upper bound %s only; transport_overhead %s" % (
                    millis(bound), "n=%d, needs %d" % (measured, minimum) if measured else "has no samples"))
        else:
            add("D1 stay", criterion, None, None, None,
                [max(measured, index.count("vjepa_live", "overhead_upper_bound"))])
    gate = index.stat("occupancy_gate", "edge_to_first_send", .95)
    add("D1 stay", "gate-to-send p95 ≤ %s" % seconds(stay["gate_to_send_p95_max_s"]), gate,
        lambda v: v <= stay["gate_to_send_p95_max_s"], None if gate is None else seconds(gate),
        [index.count("occupancy_gate", "edge_to_first_send")])
    supplied = index.rate("home_context", "la_supplied_flag")
    add("D1 stay", "LA sources supplied on ≥ %.0f%% of occupied ticks" % (100 * stay["la_supplied_min_rate"]), supplied,
        lambda v: v >= stay["la_supplied_min_rate"], None if supplied is None else "%.1f%%" % (100 * supplied),
        [index.count("home_context", "la_supplied_flag")])

    add("D1 build", "Qwen overhead p50 > %s" % millis(build["qwen_overhead_p50_over_s"]), o50,
        lambda v: v > build["qwen_overhead_p50_over_s"], None if o50 is None else "%s (%s)" % (millis(o50), paired),
        pairs, paired)
    add("D1 build", "Qwen overhead p95 > %s" % millis(build["qwen_overhead_p95_over_s"]), o95,
        lambda v: v > build["qwen_overhead_p95_over_s"], None if o95 is None else "%s (%s)" % (millis(o95), paired),
        pairs, paired)
    upload = index.stat("qwen_live_victoria", "upload", .5)
    share = upload / o50 if upload is not None and o50 is not None and o50 > 0 else None
    add("D1 build", "upload > %.0f%% of overhead" % (100 * build["upload_share_over"]), share,
        lambda v: v > build["upload_share_over"], None if share is None else "%.0f%%" % (100 * share),
        [index.count("qwen_live_victoria", "upload"), len(overhead)])
    body = index.stat("qwen_live_victoria", "body_bytes", .5)
    upstream = index.stat("link", "upstream_mbps", .5)
    used = (body * 8 * d1["approved_cadence_hz"] / 1e6 / upstream
            if body is not None and upstream is not None and upstream > 0 else None)
    add("D1 build", "approved cadence (%g Hz) uses > %.0f%% of Victoria upstream" % (
        d1["approved_cadence_hz"], 100 * build["upstream_share_over"]), used,
        lambda v: v > build["upstream_share_over"], None if used is None else "%.1f%%" % (100 * used),
        [index.count("qwen_live_victoria", "body_bytes"), index.count("link", "upstream_mbps")])
    resend = index.rate("vjepa_live", "resent_frame_fraction")
    add("D1 build", "V-JEPA resends > %.0f%% of its bytes (frames as proxy)" % (100 * build["vjepa_resend_share_over"]),
        resend, lambda v: v > build["vjepa_resend_share_over"], None if resend is None else "%.0f%%" % (100 * resend),
        [index.count("vjepa_live", "resent_frame_fraction")])

    for pipeline, metric, label in D2_GUARDS:
        criterion = "%s with live ≤ %.2f × no-live" % (label, d2["guard_ratio_max"])
        if live_since is None:
            add("D2 guard", criterion, None, None, None, [0], "no --live-since split")
            continue
        before = baseline.stat(pipeline, metric, .95, before=live_since)
        after = index.stat(pipeline, metric, .95, after=live_since)
        ratio = after / before if before and after is not None else None
        counts = [baseline.count(pipeline, metric, before=live_since), index.count(pipeline, metric, after=live_since)]
        # The no-live side is in the past: a longer window adds only live samples.
        add("D2 guard", criterion, ratio, lambda v: v <= d2["guard_ratio_max"],
            None if ratio is None else "%.2f× (%s against %s)" % (ratio, seconds(after), seconds(before)), counts,
            "%d no-live and %d live samples, needs %d each" % (counts[0], counts[1], minimum),
            None if counts[0] >= minimum else "none")
    window = {"after": live_since} if live_since is not None else {}
    gpu = index.stat("la_gpu", "gpu_temp_c", .95, **window)
    add("D2 guard", "GPU temperature p95 ≤ %g °C" % d2["gpu_temp_p95_max_c"], gpu,
        lambda v: v <= d2["gpu_temp_p95_max_c"], None if gpu is None else "%.1f °C" % gpu,
        [index.count("la_gpu", "gpu_temp_c", **window)])
    busy = index.rate("qwen_live_victoria", "queue_busy_flag", **window)
    add("D2 guard", "queue_busy < %.0f%% of ticks" % (100 * d2["queue_busy_share_max"]), busy,
        lambda v: v < d2["queue_busy_share_max"], None if busy is None else "%.1f%%" % (100 * busy),
        [index.count("qwen_live_victoria", "queue_busy_flag", **window)])

    la95 = index.stat("la_detector", "capture_to_fact", .95)
    jetson50 = index.stat("jetson_detector", "capture_to_fact", .5)
    gap = la95 - jetson50 if la95 is not None and jetson50 is not None else None
    add("D3", "LA capture-to-fact p95 < Jetson p50", gap, lambda v: v < 0,
        None if gap is None else "%s against %s" % (seconds(la95), seconds(jetson50)),
        [index.count("la_detector", "capture_to_fact"), index.count("jetson_detector", "capture_to_fact")])
    stale = index.rate("la_detector", "stale_flag")
    add("D3", "LA stale rate < %.0f%%" % (100 * d3["stale_rate_max"]), stale,
        lambda v: v < d3["stale_rate_max"], None if stale is None else "%.1f%%" % (100 * stale),
        [index.count("la_detector", "stale_flag")])
    return rows


def _undecided(open_rows):
    """Undecided, naming what blocks it and whether a longer window can help."""
    def named(kind):
        return "; ".join("%s (%s)" % (row[1], row[4][1]) for row in open_rows if (row[4][0] == "few") == (kind == "few"))
    text = "Undecided: insufficient data."
    if named("few"):
        text += " A longer window helps: " + named("few") + "."
    if named("none"):
        text += " A longer window cannot help until a source or input exists: " + named("none") + "."
    return text


def verdicts(rows):
    def pick(group, status=None):
        return [row for row in rows if row[0] == group and (status is None or row[3] == status)]

    def criteria(found):
        return "; ".join(row[1] for row in found)
    stay = pick("D1 stay")
    if pick("D1 build", HOLDS):
        d1 = "Build S2.2 (a build criterion holds: %s)." % criteria(pick("D1 build", HOLDS))
    elif stay and all(row[3] == HOLDS for row in stay):
        d1 = "Stay with request/response."
    elif pick("D1 stay", FAILS) and not pick("D1 build", NO_DATA):
        d1 = ("Neither stay nor build: stay fails on %s, and no build criterion holds. The plan has no rule "
              "for this gap; an owner decision is needed." % criteria(pick("D1 stay", FAILS)))
    elif pick("D1 stay", FAILS):
        d1 = "Not stay (fails on %s); build is still open. %s" % (
            criteria(pick("D1 stay", FAILS)), _undecided(pick("D1 build", NO_DATA)))
    else:
        d1 = _undecided(pick("D1 stay", NO_DATA) + pick("D1 build", NO_DATA))
    guard = pick("D2 guard")
    if pick("D2 guard", FAILS):
        d2 = "Do not raise the cadence: the S1.4 guard does not hold (%s)." % criteria(pick("D2 guard", FAILS))
    elif guard and all(row[3] == HOLDS for row in guard):
        d2 = "Keep or raise the cadence: the S1.4 guard holds."
    else:
        d2 = _undecided(pick("D2 guard", NO_DATA))
    d3s = pick("D3")
    if pick("D3", FAILS):
        d3 = "Retire the LA detector and pose (S2.3)."
    elif d3s and all(row[3] == HOLDS for row in d3s):
        d3 = "Keep detector and pose on LA (S2.3 JPEG passthrough)."
    else:
        d3 = _undecided(pick("D3", NO_DATA))
    return {"D1": d1, "D2": d2, "D3": d3}


def render(rows, slo, *, sources=(), since=None, until=None, live_since=None, baseline_since=None, generated=None):
    minimum = slo["min_samples_per_statistic"]
    baseline = Index([r for r in rows if baseline_since is None or r["_t"] >= baseline_since], minimum)
    rows = [r for r in rows if (since is None or r["_t"] >= since) and (until is None or r["_t"] < until)]
    index = Index(rows, minimum)
    generated = generated or day(datetime.now(timezone.utc).timestamp())
    out = ["# Victoria → LA latency baseline", "",
           "Generated %s by tools/la-latency/report.py from %d samples%s." % (
               generated, len(rows), (" (" + ", ".join(sources) + ")") if sources else ""),
           "Window: %s to %s (UTC). Percentiles are nearest-rank; a decision statistic needs at least %d samples." % (
               day(since) if since is not None else "start", day(until) if until is not None else "end",
               index.minimum), "",
           "## Baseline (plan section 4)", "",
           "| Pipeline | n | p50 | p95 | Dates | Method | Today (plan) | Target |",
           "|---|---|---|---|---|---|---|---|"]
    out += [table_row(index, entry, True) for entry in slo["pipelines"]]
    out += ["", "## Supporting measurements", "", "| Measurement | n | p50 | p95 | Dates | Method |", "|---|---|---|---|---|---|"]
    out += [table_row(index, entry, False) for entry in slo["supporting"]]
    counts = sorted(key for key in index.series if key[1].startswith(("event:", "outcome:")))
    out += ["", "## LA pilot events and outcomes", ""]
    if counts:
        out += ["| Pipeline | Event or outcome | Count | Last seen (UTC) |", "|---|---|---|---|"]
        for pipeline, metric in counts:
            series = index.series[(pipeline, metric)]
            out.append("| %s | %s | %d | %s |" % (pipeline, metric, len(series), series[-1]["t"]))
        alarms = ["%s: %s" % (m, meaning) for p, m in counts for suffix, meaning in ALARMS if m.endswith(suffix)]
        if alarms:
            out += ["", "**Alarm:** " + "; ".join(alarms) + "."]
    else:
        out.append("No LA pilot events or outcomes in the samples.")

    window = slo["decision"]["window"]
    facts = index.rows(window["pipeline"], window["metric"])
    span = (facts[-1]["_t"] - facts[0]["_t"]) / DAY if facts else 0.0
    out += ["", "## S2.1 decision record", "",
            "Window: %.1f days and %d occupied Qwen requests (%s/%s); S2.1 needs ≥ %d days and ≥ %d." % (
                span, len(facts), window["pipeline"], window["metric"], window["min_days"],
                window["min_occupied_qwen_requests"])]
    if span < window["min_days"] or len(facts) < window["min_occupied_qwen_requests"]:
        out += ["", "No decision record: too little occupied data. Extend the window rather than decide early."]
        return "\n".join(out) + "\n"
    if live_since is not None:
        out.append("D2 guard: the no-live baseline is every sample before %s%s, whatever --since says; "
                   "the live side is the window from %s." % (
                       harvest.format_time(live_since),
                       "" if baseline_since is None else " from " + harvest.format_time(baseline_since),
                       harvest.format_time(live_since if since is None else max(since, live_since))))
    coarse = (len(index.rows("qwen_live_la", "total") or index.rows("qwen_live_la", "backend"))
              - len(la_calls(index)))
    if coarse:
        out.append("%d LA timing lines have whole-second journal times and are left out of the Qwen overhead "
                   "pairing; export the LA journal with -o short-iso-precise or -o json." % coarse)
    decisions = decision_rows(index, slo, live_since, baseline)
    out += ["", "| Gate | Criterion | Value | Result |", "|---|---|---|---|"]
    out += ["| %s | %s | %s | %s |" % row[:4] for row in decisions]
    out += [""] + ["- **%s:** %s" % item for item in verdicts(decisions).items()]
    return "\n".join(out) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("samples", nargs="+", help="harvest.py JSONL files")
    parser.add_argument("--slo", default=str(Path(__file__).with_name("slo.json")))
    parser.add_argument("--since", help="only samples at or after this ISO-8601 time (e.g. after S1.1-S1.5)")
    parser.add_argument("--until", help="only samples before this ISO-8601 time")
    parser.add_argument("--live-since", help="D2 guard: samples before this time are the no-live baseline, "
                                             "whatever --since says")
    parser.add_argument("--baseline-since", help="D2 guard: drop no-live baseline samples before this time")
    parser.add_argument("--date", help="report date (default today, UTC)")
    parser.add_argument("-o", "--output", default="-")
    args = parser.parse_args(argv)
    try:
        with open(args.slo, encoding="utf-8") as handle:
            slo = json.load(handle)
        times = {k: harvest.parse_time(getattr(args, k)) if getattr(args, k) else None
                 for k in ("since", "until", "live_since", "baseline_since")}
        rows = load_samples(args.samples)
    except (ReportError, harvest.InputError, OSError, ValueError) as error:
        print("report: " + str(error), file=sys.stderr)
        return 2
    text = render(rows, slo, sources=[Path(p).name for p in args.samples], generated=args.date, **times)
    if args.output == "-":
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write(text)
    else:
        with open(args.output, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
