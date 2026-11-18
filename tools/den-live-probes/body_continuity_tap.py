"""Read-only loopback tap of den-live's body and people streams, for the body continuity probe.

Run on the den-live host from the workstation through stdin, so nothing is copied to or written on the host:

    ssh -o BatchMode=yes -o ConnectTimeout=15 home-app python3 -I - --seconds 120 \
        < tools/den-live-probes/body_continuity_tap.py > <out-dir>/tap-<utc>.json

It holds two GET subscriptions on 127.0.0.1:8111 (body-v2 and stream) for a bounded window, then prints one JSON
document. Household media rule: counts, percentiles, timestamps, 32-bit hashes of ids and distance bands only. It
never keeps or prints a point, a position, an image or camera data: track positions become pairwise distance bands
as each event is read, and body packets keep only their status, reason, ids and lease times.

Attribution, from the producer's side:
- coverage gaps: tracked time with no ready under its lease, by the packet that ended coverage, and for lease ends
  whether processed frames arrived (body_skip) or not (frame_gap). With den-live's health.body counters (the
  producer diagnostics branch) the split names stills precedence, hold reasons, rate or busy skips. A perception
  stall (no fresh frame: a stale, degraded or starting people event, or silence) keeps the person tracked for up to
  perceive.py's EPOCH_GAP_S, and its gap, with the hidden tail after it, is perception_stall by people status;
- frame_vs_ready: ready inter-arrival against processed-frame inter-arrival (ratio of p95s, 1.2 decides), with Qwen
  ask overlap;
- ambiguous episodes: each ambiguous clear until the next ready, with the extra track's state, current observation
  and distance band from the body's track. The G12-B rule needs 5 or more episodes."""
import argparse
import bisect
import http.client
import json
import math
import socket
import struct
import sys
import threading
import time

SCHEMA = "den-body-continuity-tap-2"
FRESH_S = 1.5
STALL_S = 3.0      # perceive.py EPOCH_GAP_S: it keeps its tracks this long without a fresh frame
# What a perception stall explains when it follows in the same gap: perceive.py's idle clears and the last lease.
STALL_ABSORBS = ("lease_elapsed", "producer_unavailable", "no_ready_yet")
BAND_ORDER = ("<0.6", "0.6-1.0", "1.0-1.5", ">=1.5")
FAR_BANDS = ("1.0-1.5", ">=1.5")
REASONS = ("stale", "scope_changed", "unavailable", "insufficient_depth", "ambiguous", "empty", "pose_unavailable",
           "warming_background", "head_mask_unavailable")
SKIP_ORDER = (("busy.stills_first", "stills_precedence"), ("held.", "hold_reason"), ("skipped_rate", "rate_skip"),
              ("busy.own_job", "busy_skip"), ("busy.slot", "busy_skip"), ("skipped_busy", "busy_skip"))


def h32(value):
    """FNV-1a over UTF-16 code units, as body-continuity-core.mjs hashes, so the two receipts join."""
    if value is None:
        return None
    text = str(value).encode("utf-16-le")
    h = 0x811C9DC5
    for (unit,) in struct.iter_unpack("<H", text):
        h = ((h ^ unit) * 16777619) & 0xFFFFFFFF
    return "%08x" % h


def word(value):
    if value is None:
        return None
    if isinstance(value, str) and 0 < len(value) <= 32 and all(c.islower() or c == "_" for c in value):
        return value
    return "other"


def phrase(value):
    """A producer status phrase such as 'too dark', or 'other'."""
    if value is None or value == "":
        return None
    if isinstance(value, str) and len(value) <= 64 and all(c.islower() or c in " ,'-" for c in value):
        return value
    return "other"


def num(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def band(distance):
    for limit, name in zip((0.6, 1.0, 1.5), BAND_ORDER):
        if distance < limit:
            return name
    return BAND_ORDER[-1]


def pct(values):
    a = sorted(v for v in values if v is not None and math.isfinite(v))
    if not a:
        return None
    q = lambda p: round(a[max(0, math.ceil(len(a) * p) - 1)], 3)
    return {"n": len(a), "p50": q(0.5), "p95": q(0.95), "max": round(a[-1], 3)}


def sse_events(readline, stop=None):
    """(event name, data) per server-sent event; comments and heartbeats are skipped."""
    name, data = None, []
    while stop is None or not stop.is_set():
        line = readline()
        if not line:
            break
        line = line.decode("utf-8", "replace").rstrip("\r\n")
        if not line:
            if data:
                yield name or "message", "\n".join(data)
            name, data = None, []
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[6:] if line.startswith("data: ") else line[5:])


def flatten_counts(record, prefix=""):
    """Numeric counters of den-live's health.body, as dotted keys; stamps and ids are left out."""
    out = {}
    if not isinstance(record, dict):
        return out
    for key, value in record.items():
        if key in ("run_id", "track_epoch", "seq", "why", "stopped") or key.endswith("_ts"):
            continue
        if isinstance(value, dict):
            out.update(flatten_counts(value, prefix + key + "."))
        elif isinstance(value, int) and not isinstance(value, bool):
            out[prefix + key] = value
    return out


def body_row(event, t, wall):
    status = event.get("status")
    status = status if status in ("ready", "clear") else "other"
    reason = event.get("reason")
    return {"t": t, "wall": wall, "seq": event.get("seq") if isinstance(event.get("seq"), int) else None,
            "run_h": h32(event.get("run_id")), "status": status,
            "reason": (reason if reason in REASONS else "other") if status == "clear" else None,
            "track_h": h32(event.get("track_id")), "cap": num(event.get("capture_ts")), "exp": num(event.get("expires_ts")),
            "object": "object" in event}


def people_row(event, t, wall):
    tracks, places = [], []
    for track in event.get("tracks") or ():
        if not isinstance(track, dict):
            continue
        obs = track.get("current_obs_ids")
        tracks.append((h32(track.get("track_id")), word(track.get("state")), word(track.get("association_status")),
                       None if "current_obs_ids" not in track else bool(obs), track.get("association_revision")))
        xy = track.get("xy_m")
        places.append((xy[0], xy[1]) if isinstance(xy, (list, tuple)) and len(xy) >= 2 and num(xy[0]) is not None
                      and num(xy[1]) is not None else None)
    bands = {}
    for i in range(len(tracks)):
        for j in range(i + 1, len(tracks)):
            if places[i] is not None and places[j] is not None:
                name = band(math.hypot(places[i][0] - places[j][0], places[i][1] - places[j][1]))
                bands[(tracks[i][0], tracks[j][0])] = bands[(tracks[j][0], tracks[i][0])] = name
    del places
    health = event.get("health") if isinstance(event.get("health"), dict) else {}
    qwen = health.get("qwen") if isinstance(health.get("qwen"), dict) else {}
    diag = health.get("body") if isinstance(health.get("body"), dict) else None
    camera = event.get("camera") if isinstance(event.get("camera"), dict) else {}
    return {"t": t, "wall": wall, "seq": event.get("seq") if isinstance(event.get("seq"), int) else None,
            "status": word(event.get("perception_status")), "presence": word(event.get("presence")),
            "camera": (word(camera.get("status")), phrase(camera.get("why"))),
            "tracks": tracks, "bands": bands, "qwen": word(qwen.get("state")), "asks_per_min": num(qwen.get("asks_per_min")),
            "fps_in": num(health.get("fps_in")), "diag": flatten_counts(diag) if diag is not None else None}


def tracked(row):
    return row["status"] == "fresh" and bool(row["tracks"])


def eligible(row):
    return row["status"] == "fresh" and len(row["tracks"]) == 1 and row["tracks"][0][1] == "confirmed"


def _merge(spans):
    merged = []
    for s, e in sorted(spans):
        if e - s <= 1e-9:
            continue
        if merged and s <= merged[-1][1] + 1e-9:
            merged[-1] = (merged[-1][0], max(e, merged[-1][1]))
        else:
            merged.append((s, e))
    return merged


def subtract(spans, holes):
    """spans minus holes, both lists of (start, end), in one sweep; the result is sorted and merged."""
    holes, out, j = _merge(holes), [], 0
    for s, e in _merge(spans):
        while j < len(holes) and holes[j][1] <= s:
            j += 1
        cur, k = s, j
        while k < len(holes) and holes[k][0] < e:
            if holes[k][0] > cur:
                out.append((cur, holes[k][0]))
            cur = max(cur, holes[k][1])
            k += 1
        if cur < e:
            out.append((cur, e))
    return _merge(out)


def covered_spans(body, clock, t_end, margin_s=0.0):
    """From each ready's arrival to its lease end (less a transport margin) or the next body packet."""
    spans = []
    if clock is None:
        return spans
    server, at = clock
    for i, row in enumerate(body):
        if row["status"] != "ready" or row["exp"] is None:
            continue
        nxt = body[i + 1]["t"] if i + 1 < len(body) else t_end
        end = min(at + (row["exp"] - server) - margin_s, nxt, t_end)
        if end > row["t"]:
            spans.append((row["t"], end))
    return spans


def presence_spans(people, t_end):
    """Fresh tracked spans, and perception-stall spans: after fresh tracked time, a non-fresh event or silence keeps
    the person for up to STALL_S from the last fresh tracked event, as perceive.py keeps its tracks. A fresh event
    with no tracks ends it at once."""
    fresh, stall, last = [], [], None
    for i, row in enumerate(people):
        nxt = min(people[i + 1]["t"] if i + 1 < len(people) else t_end, t_end)
        if row["status"] == "fresh":
            last = row["t"] if row["tracks"] else None
        start = row["t"]
        if tracked(row):
            start = min(nxt, row["t"] + FRESH_S)
            fresh.append((row["t"], start))
        if last is not None and min(nxt, last + STALL_S) > start:
            stall.append((start, min(nxt, last + STALL_S)))
    return _merge(fresh), _merge(stall)


def tracked_spans(people, t_end):
    fresh, stall = presence_spans(people, t_end)
    return _merge(fresh + stall)


def stall_status(people, people_t, s, e):
    """The first non-fresh people status in [s, e], or 'silent' when none came."""
    for row in people[bisect.bisect_left(people_t, s - 1e-9):bisect.bisect_right(people_t, e)]:
        if row["status"] != "fresh":
            return row["status"] or "other"
    return "silent"


def diag_delta(diags, s, e):
    """Counter increases between the last health.body record at or before s and the last at or before e."""
    times = [t for t, _ in diags]
    i, j = bisect.bisect_right(times, s) - 1, bisect.bisect_right(times, e) - 1
    if i < 0 or j < 0:
        return None
    a, b = diags[i][1], diags[j][1]
    return {k: b[k] - a.get(k, 0) for k in sorted(b) if b[k] - a.get(k, 0) > 0}


def coverage(body, people, clock, t_end, wall_of, margin_s=0.0):
    gaps, pieces = [], []
    body_t, people_t = [b["t"] for b in body], [r["t"] for r in people]
    diags = [(r["t"], r["diag"]) for r in people if r["diag"] is not None]
    _fresh, stalls = presence_spans(people, t_end)
    stall_t = [s for s, _ in stalls]
    for s, e in subtract(tracked_spans(people, t_end), covered_spans(body, clock, t_end, margin_s)):
        i = bisect.bisect_right(body_t, s + 1e-9) - 1
        last = body[i] if i >= 0 else None
        if last is None:
            cause = "no_ready_yet"
        elif last["status"] == "clear":
            cause = "lease_elapsed" if last["reason"] == "stale" else "producer_" + str(last["reason"])
        else:
            cause = "lease_elapsed"
        # Split where a perception stall began, as the browser attribution does.
        k = bisect.bisect_right(stall_t, s) - 1
        if k < 0 or stalls[k][1] <= s:
            k += 1
        at = max(s, stalls[k][0]) if k < len(stalls) and stalls[k][0] < e else None
        since = stalls[k][0] if at is not None else None
        if at is None:
            pieces.append((s, e, cause, last, None))
        elif at <= s or cause in STALL_ABSORBS:
            pieces.append((s, e, "perception_stall", last, since))
        else:
            pieces += [(s, at, cause, last, None), (at, e, "perception_stall", last, since)]
    for s, e, cause, last, since in pieces:
        rows = people[bisect.bisect_right(people_t, s):bisect.bisect_right(people_t, e)]
        frames = [r for r in rows if eligible(r)]
        j = bisect.bisect_left(body_t, e - 1e-9)
        if frames and j < len(body) and body[j]["status"] == "ready" and abs(body[j]["t"] - e) < 1e-6:
            frames = frames[:-1]   # the gap's last frame is the one whose ready ended it
        gap = {"start_ms": round(s * 1000, 1), "end_ms": round(e * 1000, 1), "duration_ms": round((e - s) * 1000, 1),
               "start_wall_ms": round(wall_of(s)), "end_wall_ms": round(wall_of(e)), "cause": cause, "subcause": None,
               "people_events": len(rows), "eligible_frames_without_ready": len(frames),
               "qwen_asking_events": sum(1 for r in rows if r["qwen"] == "asking")}
        if cause == "perception_stall":
            gap["subcause"] = stall_status(people, people_t, since, e)
        elif cause == "lease_elapsed":
            gap["subcause"] = "body_skip" if frames else "frame_gap"
            # From the last body packet, so a skip just before the lease ran out counts too.
            delta = diag_delta(diags, last["t"] if last else s, e)
            if delta is not None:
                gap["diag_delta"] = delta
                for prefix, name in SKIP_ORDER:
                    if any(k.startswith(prefix) for k in delta):
                        gap["subcause"] = name
                        break
        gaps.append(gap)
    totals = {}
    for gap in gaps:
        key = gap["cause"] + (":" + gap["subcause"] if gap["subcause"] else "")
        totals[key] = round(totals.get(key, 0) + gap["duration_ms"], 1)
    return {"margin_s": margin_s, "gaps": gaps, "blank_ms_by_cause": totals,
            "tracked_ms": round(sum(e - s for s, e in tracked_spans(people, t_end)) * 1000, 1),
            "stall_ms": round(sum(e - s for s, e in stalls) * 1000, 1)}


def _body_track(body, people, i):
    prior = [b for b in body[:i] if b["status"] == "ready" and b["track_h"]]
    if prior:
        return prior[-1]["track_h"]
    single = [r["tracks"][0][0] for r in people if eligible(r)]
    return single[-1] if single else None


def ambiguous_episodes(body, people, t_end, wall_of, minimum=5):
    episodes = []
    for i, clear in enumerate(body):
        if clear["status"] != "clear" or clear["reason"] != "ambiguous":
            continue
        ends = [b["t"] for b in body[i + 1:] if b["status"] == "ready"]
        end = ends[0] if ends else t_end
        own = _body_track(body, people, i)
        rows = [r for r in people if clear["t"] - 1e-9 <= r["t"] <= end]
        extras = [(h, state, obs, r["bands"].get((own, h))) for r in rows for (h, state, _a, obs, _rev) in r["tracks"] if h != own]
        states = sorted({x[1] for x in extras if x[1]})
        observed = any(x[2] for x in extras)
        known = [x[3] for x in extras if x[3] is not None]
        nearest = min(known, key=BAND_ORDER.index) if known else None
        if not rows:
            kind = "no_people_events"
        elif not extras:
            kind = "single_track"
        elif states == ["coasting"] and not observed:
            kind = "far_coasting_no_obs" if known and all(b in FAR_BANDS for b in known) else "near_coasting_no_obs"
        elif observed:
            kind = "second_observed_track"
        else:
            kind = "other"
        episodes.append({"start_ms": round(clear["t"] * 1000, 1), "end_ms": round(end * 1000, 1),
                         "duration_ms": round((end - clear["t"]) * 1000, 1), "start_wall_ms": round(wall_of(clear["t"])),
                         "end_wall_ms": round(wall_of(end)), "open_at_end": not ends,
                         "extra_track": {"class": kind, "states": states, "has_current_obs": observed, "nearest_band": nearest,
                                         "max_tracks": max((len(r["tracks"]) for r in rows), default=0)}})
    total = sum(ep["duration_ms"] for ep in episodes)
    far = sum(ep["duration_ms"] for ep in episodes if ep["extra_track"]["class"] == "far_coasting_no_obs")
    share = round(far / total, 3) if total else None
    verdict = ("insufficient_episodes" if len(episodes) < minimum else
               "relax_two_track_rule_candidate" if share is not None and share >= 0.8 else "keep_two_track_rule")
    return {"episodes": episodes, "count": len(episodes), "blank_ms": round(total, 1),
            "rule": {"verdict": verdict, "far_coasting_no_obs_share": share, "minimum_episodes": minimum,
                     "statement": "relax body_rgb's two-track rule only if at least 0.8 of ambiguous blank ms comes from "
                                  "coasting tracks with no current observation 1.0 m or more from the body's track, "
                                  "over at least %d episodes" % minimum}}


def frame_vs_ready(body, people):
    ready = [b["t"] for b in body if b["status"] == "ready"]
    frames = [r["t"] for r in people if r["status"] == "fresh"]
    ready_gaps = [b - a for a, b in zip(ready, ready[1:])]
    frame_gaps = [b - a for a, b in zip(frames, frames[1:])]
    per_gap = [bisect.bisect_left(frames, b) - bisect.bisect_right(frames, a) for a, b in zip(ready, ready[1:])]
    rg, fg = pct(ready_gaps), pct(frame_gaps)
    ratio = round(rg["p95"] / fg["p95"], 3) if rg and fg and fg["p95"] > 0 else None
    return {"ready_gap_s": rg, "frame_gap_s": fg, "frames_per_ready_gap": pct(per_gap), "p95_ratio": ratio,
            "verdict": None if ratio is None else "frame_rate_binding" if ratio <= 1.2 else "body_skips_dominate",
            "rule": "ready-gap p95 at most 1.2 times processed-frame-gap p95 means the frame rate binds the cadence"}


def qwen_overlap(body, people):
    fresh = [r for r in people if r["status"] == "fresh"]
    fresh_t = [r["t"] for r in fresh]
    ready = [b["t"] for b in body if b["status"] == "ready"]
    long_gaps = [(a, b) for a, b in zip(ready, ready[1:]) if b - a > 1.0]
    asking = [any(r["qwen"] == "asking" for r in fresh[bisect.bisect_right(fresh_t, a):bisect.bisect_left(fresh_t, b)])
              for a, b in long_gaps]
    return {"asking_fraction_of_frames": round(sum(1 for r in fresh if r["qwen"] == "asking") / len(fresh), 3) if fresh else None,
            "ready_gaps_over_1s": len(long_gaps), "ready_gaps_over_1s_with_qwen_asking": sum(asking),
            "asks_per_min": pct([r["asks_per_min"] for r in fresh])}


def analyse(body, people, clock, t_end, wall0, errors=(), opened=None):
    wall_of = lambda t: wall0 + t * 1000.0
    ready = [b for b in body if b["status"] == "ready"]
    clears = [b for b in body if b["status"] == "clear"]
    count = lambda values: {str(k): values.count(k) for k in sorted(set(values), key=str)}
    prod_now = (lambda t: clock[0] + (t - clock[1])) if clock else None
    scope = []
    for i, b in enumerate(body):
        if b["status"] == "clear" and b["reason"] == "scope_changed":
            before = [x for x in body[:i] if x["status"] == "ready"]
            after = [x for x in body[i + 1:] if x["status"] == "ready"]
            scope.append({"recovered_ms": round((after[0]["t"] - b["t"]) * 1000, 1) if after else None,
                          "same_track": bool(before and after and before[-1]["track_h"] == after[0]["track_h"])})
    revisions, last = 0, {}
    for row in people:
        current = {h: rev for (h, _s, _a, _o, rev) in row["tracks"]}
        revisions += sum(1 for h, rev in current.items() if h in last and last[h] != rev)
        last = current
    seqs = [b["seq"] for b in body if b["seq"] is not None]
    segments = []
    for row in people:
        key = (len(row["tracks"]), tuple(x[1] for x in row["tracks"]), tuple(x[2] for x in row["tracks"]),
               tuple(x[3] for x in row["tracks"]))
        if segments and segments[-1][0] == key:
            segments[-1][2] = row["wall"]
            segments[-1][3] += 1
        else:
            segments.append([key, row["wall"], row["wall"], 1])
    return {
        "schema": SCHEMA,
        "window": {"seconds": round(t_end, 3), "start_wall_ms": round(wall0), "end_wall_ms": round(wall_of(t_end))},
        "connections": {"opened_s": opened or {}, "errors": list(errors)},
        "clock": clock is not None,
        "body": {"events": len(body), "ready": len(ready), "ready_rate_hz": round(len(ready) / t_end, 3) if t_end else None,
                 "clear_reasons": count([c["reason"] for c in clears]), "seq_gaps": sum(max(0, b - a - 1) for a, b in zip(seqs, seqs[1:])),
                 "ready_with_object_key": sum(1 for r in ready if r["object"]),
                 "ready_age_at_producer_s": pct([prod_now(r["t"]) - r["cap"] for r in ready if prod_now and r["cap"] is not None]),
                 "lease_remaining_at_producer_s": pct([r["exp"] - prod_now(r["t"]) for r in ready if prod_now and r["exp"] is not None]),
                 "scope_changed": {"clears": len(scope), "same_track": sum(1 for s in scope if s["same_track"]),
                                   "recovered_ms": pct([s["recovered_ms"] for s in scope])}},
        "people": {"events": len(people), "rate_hz": round(len(people) / t_end, 3) if t_end else None,
                   "track_counts": count([len(r["tracks"]) for r in people]),
                   "track_states": count([x[1] for r in people for x in r["tracks"]]),
                   "association_status": count([x[2] for r in people for x in r["tracks"]]),
                   "without_current_obs": sum(1 for r in people for x in r["tracks"] if x[3] is False),
                   "presence": count([r["presence"] for r in people]), "status": count([r["status"] for r in people]),
                   "camera": count(["%s: %s" % r["camera"] if r["camera"][1] else r["camera"][0] or "unknown" for r in people]),
                   "same_track_revision_changes": revisions, "fps_in_last": people[-1]["fps_in"] if people else None,
                   "single_confirmed_s": round(sum(min(b["t"], a["t"] + FRESH_S) - a["t"] for a, b in zip(people, people[1:] + [{"t": t_end}])
                                                   if eligible(a)), 3),
                   "health_body_counters": any(r["diag"] is not None for r in people)},
        "coverage": coverage(body, people, clock, t_end, wall_of),
        "coverage_with_0_5s_transport_margin": {k: v for k, v in coverage(body, people, clock, t_end, wall_of, 0.5).items() if k != "gaps"},
        "ambiguous": ambiguous_episodes(body, people, t_end, wall_of),
        "join": {"frame_vs_ready": frame_vs_ready(body, people), "qwen": qwen_overlap(body, people)},
        "timeline": {"body": [[round(b["wall"]), b["run_h"], b["seq"], b["status"], b["reason"], b["track_h"]] for b in body[:20000]],
                     "people_segments": [[round(s[1]), round(s[2]), s[3], s[0][0], list(s[0][1]), list(s[0][2]), list(s[0][3])]
                                         for s in segments[:20000]]},
    }


def tap(seconds, port=8111, home="den"):
    base = "/api/den-live/v1/homes/%s/" % home
    stop, errors, opened = threading.Event(), [], {}
    body, people, clock = [], [], []
    t0, wall0 = time.monotonic(), time.time() * 1000.0
    conns = []

    def reader(route):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conns.append(conn)
        try:
            conn.request("GET", base + route, headers={"Host": "127.0.0.1:%d" % port, "Accept": "text/event-stream",
                                                        "Accept-Encoding": "identity"})
            resp = conn.getresponse()
            if resp.status != 200:
                errors.append([route, "status_%d" % resp.status, round(time.monotonic() - t0, 3)])
                return
            opened[route] = round(time.monotonic() - t0, 3)
            for name, data in sse_events(lambda: resp.readline(1 << 22), stop):
                t, wall = time.monotonic() - t0, time.time() * 1000.0
                try:
                    event = json.loads(data)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                if name == "body_clock" and not clock and num(event.get("server_ts")) is not None:
                    clock.append((event["server_ts"], t))
                elif name == "body":
                    body.append(body_row(event, t, wall))
                elif name == "people":
                    people.append(people_row(event, t, wall))
            if not stop.is_set():
                errors.append([route, "eof", round(time.monotonic() - t0, 3)])
        except (OSError, http.client.HTTPException) as exc:
            if not stop.is_set():
                errors.append([route, type(exc).__name__, round(time.monotonic() - t0, 3)])
        finally:
            conn.close()

    threads = [threading.Thread(target=reader, args=(route,), daemon=True) for route in ("body-v2", "stream")]
    for thread in threads:
        thread.start()
    time.sleep(seconds)
    stop.set()
    t_end = time.monotonic() - t0
    for thread in threads:
        thread.join(6)
    for conn in conns:
        try:
            conn.sock and conn.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
    body = sorted((b for b in list(body) if b["t"] <= t_end), key=lambda b: b["t"])
    people = sorted((p for p in list(people) if p["t"] <= t_end), key=lambda p: p["t"])
    return analyse(body, people, clock[0] if clock else None, t_end, wall0, errors, opened)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only den-live body and people tap (aggregates only).")
    parser.add_argument("--seconds", type=float, default=120.0)
    parser.add_argument("--port", type=int, default=8111)
    parser.add_argument("--home", default="den")
    args = parser.parse_args(argv)
    if not 5 <= args.seconds <= 3600:
        parser.error("--seconds must be from 5 to 3600")
    if not 1 <= args.port <= 65535 or not args.home.isalnum():
        parser.error("bad --port or --home")
    result = tap(args.seconds, args.port, args.home)
    result["started_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(result["window"]["start_wall_ms"] / 1000.0))
    sys.stdout.write(json.dumps(result, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
