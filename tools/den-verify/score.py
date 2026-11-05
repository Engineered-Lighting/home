"""Rung 10's scorer: Decision.verify, LightState.residual and the run report.

Pure rules over records already read; no I/O and no network. VERIFY-V1.md
says what is scored and why; this file follows it section by section.
"""
import copy
import math
import random
from fractions import Fraction

import method as method_mod
import settle
from records import light_state_id, person_state_anchor

TOLERANCE_STATUS = "relative, either way; target, to validate"
SCORED_BASES = ("measured", "fallback_patch")


def within(measured, predicted, tol, extra=0.0):
    """|m - p| <= tol*|p| + extra, in exact rationals.

    The tolerance is the method's decimal, so 0.15 is exactly 3/20; the
    readings are taken exactly as the floats they are.
    """
    tol = Fraction(str(tol))
    m, p = Fraction(measured), Fraction(predicted)
    return abs(m - p) <= tol * abs(p) + Fraction(extra)


def _sign(x):
    return (x > 0) - (x < 0)


def _r(x, n):
    return None if x is None else round(x, n)


def percentile(sorted_values, q):
    """Linear interpolation between closest ranks (q in 0..1)."""
    if not sorted_values:
        return None
    pos = (len(sorted_values) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


class Frame:
    def __init__(self, rec, clock):
        self.rec = rec
        self.id = light_state_id(rec, clock)
        self.ts = float(rec["ts"])
        self.anchored = rec.get("anchored") is True
        self.unit = "lx" if self.anchored else "relative"
        self.estimates = {e["surface_id"]: e for e in rec.get("surface_estimates") or []}
        self.windows = {w["window_id"]: float(w["level"]) for w in rec.get("window_daylight") or []
                        if type(w.get("level")) in (int, float)}

    def usable(self, surface_id, m, bases=SCORED_BASES):
        """The estimate's value if it can be scored, else (None, reason)."""
        e = self.estimates.get(surface_id)
        if e is None:
            return None, "not in frame"
        if e.get("basis") == "prior":
            return None, "prior_only"
        if e.get("basis") not in bases:
            return None, f"basis {e.get('basis')}"
        if 1 - float(e.get("clipped_frac") or 0) < m["v_min"]:
            return None, "clipped"
        if type(e.get("value")) not in (int, float):
            return None, "no value"
        return float(e["value"]), None


class Hold:
    def __init__(self, entity_id, channels, level, cct_k, since, ended, revision, ref):
        self.entity_id, self.channels = entity_id, list(channels or [])
        self.level, self.cct_k = level, cct_k
        self.since, self.ended, self.revision, self.ref = since, ended, revision, ref

    def keys(self, state):
        names = {self.entity_id} | {f"{self.entity_id}.{c}" for c in self.channels}
        return [k for k in state if k in names]

    def active(self, t):
        return self.since <= t and (self.ended is None or self.ended > t)


class Dec:
    """A Decision with its times resolved and its settle facts."""

    def __init__(self, rec, run):
        self.rec = rec
        self.id = rec.get("decision_id")
        inputs = rec.get("inputs") or {}
        self.inputs = inputs
        ref_id = inputs.get("light_state")
        self.ref = run.frames_by_id.get(ref_id) if ref_id else None
        anchor = (self.ref.ts if self.ref else None) or person_state_anchor(inputs.get("person_state")) \
            or run.first_ts
        self.resolve = lambda v: run.clock.resolve(v, anchor)
        self.ts = self.resolve(rec.get("ts"))
        self.problem = None
        try:
            self.steps = settle.decision_steps(rec, self.resolve, run.m)
        except settle.Unsettleable as e:
            self.steps, self.problem = [], str(e)
        self.settled = settle.settled_at(self.steps, self.ts)
        self.first_sent = min((s.sent_at for s in self.steps), default=None)
        self.changed = sorted({s.channel for s in self.steps})
        lm = inputs.get("light_model")
        if lm and lm != run.model.version and not self.problem:
            self.problem = f"light model {lm} in the decision, {run.model.version} loaded"

    def before_state(self):
        state = {}
        for ch, a in (self.inputs.get("actuator_state") or {}).items():
            on = a.get("on", True) is not False
            state[ch] = {"level": float(a.get("level") or 0) if on else 0.0, "cct_k": a.get("cct_k")}
        return state

    def state_at(self, t, holds):
        """The actual resulting state at t, and the channels held or superseded."""
        state = self.before_state()
        held = set()
        for ch, a in (self.rec.get("applied_scene") or {}).items():
            rb = a.get("readback") or {}
            prev = state.get(ch, {})
            state[ch] = {"level": float(rb.get("level") or 0),
                         "cct_k": rb.get("cct_k", prev.get("cct_k"))}
            if a.get("status") in ("held", "superseded"):
                held.add(ch)
        hold_set = self.inputs.get("hold_set") or {}
        revision = hold_set.get("revision")
        pins = []
        for h in hold_set.get("held") or []:
            p = h.get("pinned") or {}
            pins.append(Hold(h.get("entity_id"), h.get("channels"), p.get("level_pct"), p.get("cct_k"),
                             -math.inf, None, revision, self.id))
        pins += [h for h in holds if h.active(t)]
        for h in pins:
            for ch in h.keys(state):
                if h.level is not None:
                    state[ch]["level"] = float(h.level) / 100.0
                if h.cct_k is not None:
                    state[ch]["cct_k"] = h.cct_k
                held.add(ch)
            if h.revision is not None and (revision is None or h.revision > revision):
                revision = h.revision
        return state, held, revision

    def verify_points(self):
        rec = self.rec
        pts, kinds = [], {}

        def add(p, kind=None):
            if p and p not in pts:
                pts.append(p)
            if p and kind and p not in kinds:
                kinds[p] = kind

        for p in (rec.get("predicted") or {}).get("surfaces") or {}:
            if p != "unit":
                add(p)
        for t in (rec.get("targets") or {}).values():
            if isinstance(t, dict):
                add(t.get("surface_id"), "task")
        eyes = []
        for e in (rec.get("predicted") or {}).get("eyes") or []:
            if e.get("track_id"):
                eyes.append(f"eye-{e['track_id']}")
        for rc in (rec.get("recipients") or {}).values():
            for tg in (rc or {}).get("targets") or []:
                if tg.get("kind") in ("task", "layer"):
                    add(tg.get("target_id"), tg.get("kind"))
                elif tg.get("kind") == "eye":
                    eyes.append(tg.get("target_id"))
        return pts, kinds, list(dict.fromkeys(e for e in eyes if e))

    def tracks(self):
        out = []
        for rc in (self.rec.get("recipients") or {}).values():
            out += list((rc or {}).get("served") or []) + list((rc or {}).get("exposed") or [])
            for tg in (rc or {}).get("targets") or []:
                if tg.get("kind") == "task" and tg.get("track_id"):
                    out.append(tg["track_id"])
        for sid in self.inputs.get("person_state") or []:
            out.append(str(sid).split("@")[0])
        return list(dict.fromkeys(out))

    def served_by_changed(self):
        served = set()
        recipients = self.rec.get("recipients") or {}
        for ch in self.changed:
            served |= set((recipients.get(ch) or {}).get("served") or [])
        return served


class Run:
    def __init__(self, m, model, decisions, light_states, corrections=(), occupancy=(), gold=(),
                 clock=None, scored=True):
        from records import Clock
        self.m, self.model, self.clock, self.scored = m, model, clock or Clock(), scored
        self.frames = sorted((Frame(r, self.clock) for r in light_states), key=lambda f: f.ts)
        self.frames_by_id = {}
        for f in self.frames:
            if f.id in self.frames_by_id:
                raise ValueError(f"two LightStates share the id {f.id}")
            self.frames_by_id[f.id] = f
        self.first_ts = self.frames[0].ts if self.frames else None
        self.decs = sorted((Dec(r, self) for r in decisions), key=lambda d: d.ts)
        self.holds, self.windows = [], settle.Windows()
        for c in corrections:
            self._add_correction(c)
        all_steps = [s for d in self.decs for s in d.steps]
        self.windows.add_steps(all_steps)
        settle.mark_mid_transition(all_steps)
        self.occupancy = [(float(o["start"]), float(o["end"]), o.get("people") or []) for o in occupancy]
        self.gold = list(gold)

    def _add_correction(self, c):
        snap = c.get("snapshot") or {}
        anchor = next((self.frames_by_id[i].ts for i in snap.get("light_state") or []
                       if i in self.frames_by_id), None) \
            or person_state_anchor(snap.get("person_state")) or self.first_ts
        resolve = lambda v: self.clock.resolve(v, anchor)
        ts = resolve(c.get("ts"))
        self.windows.add_change(ts, (c.get("action") or {}).get("settle_s"),
                                float(self.m["settle_margin_s"]), c.get("correction_id"))
        h = c.get("hold")
        if h:
            p = h.get("pinned") or {}
            self.holds.append(Hold(h.get("entity_id"), h.get("channels"), p.get("level_pct"), p.get("cct_k"),
                                   resolve(h.get("since") or c.get("ts")), resolve(h.get("ended")),
                                   h.get("hold_set_revision"), c.get("correction_id")))

    # -- shared pieces --------------------------------------------------------

    def occupied(self, t):
        people = []
        hit = False
        for start, end, ppl in self.occupancy:
            if start <= t < end:
                hit = True
                people += ppl
        return hit, people

    def u(self, value, unit):
        """The fixed measurement-uncertainty bound for one estimate."""
        extra = self.m["uncertainty_abs_lx"] if unit == "lx" else 0.0
        return self.m["uncertainty_rel"] * abs(value) + extra

    def evaluate(self, d, frame, t):
        """Every surface the model predicts, at frame f, against d's actual resulting state."""
        m, model = self.m, self.model
        state, held, revision = d.state_at(t, self.holds)
        after = model.predict(state)
        parts = model.contributions(state)
        before_state = d.before_state()
        before = model.predict(before_state)
        ref = d.ref
        out = {}
        unit_ok = frame.unit == model.basis_unit
        for sid in model.surfaces:
            row = {"point_id": sid, "unit": frame.unit}
            measured, why = frame.usable(sid, m) if unit_ok else (None, "unit mismatch")
            row["basis"] = (frame.estimates.get(sid) or {}).get("basis")
            ref_val, rwhy = None, "no reference frame in the same unit"
            if ref is not None and ref.unit == frame.unit:
                ref_val, rwhy = ref.usable(sid, m)
                rwhy = rwhy and f"reference frame: {rwhy}"
            if m["background"] == "zero":
                background = 0.0
            else:
                background = None if ref_val is None else ref_val - before[sid]
            predicted = None if background is None else background + after[sid]
            row.update(measured=measured, why=why, predicted=predicted, ref_value=ref_val,
                       predicted_change=after[sid] - before[sid])
            held_light = sum(parts[c][sid] for c in held if c in parts)
            if predicted is not None and predicted > 0:
                row["held_share"] = held_light / predicted
            else:
                row["held_share"] = 0.0
            row["held_lit"] = held_light > 0 and row["held_share"] >= m["held_share_min"]
            if measured is None:
                row["status"] = "prior_only" if why == "prior_only" else "unverifiable"
            elif predicted is None:
                row["status"], row["why"] = "unverifiable", rwhy
            elif predicted <= 0:
                row["status"], row["why"] = "unverifiable", "prediction not positive"
            else:
                row["residual"] = (measured - predicted) / predicted
                row["status"] = "within" if within(measured, predicted, m["tolerance"]) else "outside"
            # increments: measured change against the model's own change
            if measured is not None and ref_val is not None:
                mc = measured - ref_val
                unc = self.u(measured, frame.unit) + self.u(ref_val, frame.unit)
                row["increment"] = {
                    "point_id": sid, "measured_change": mc, "predicted_change": row["predicted_change"],
                    "status": "within" if within(mc, row["predicted_change"], m["tolerance"], unc) else "outside",
                    "uncertainty": unc}
            else:
                row["increment"] = {"point_id": sid, "measured_change": None,
                                    "predicted_change": row["predicted_change"],
                                    "status": "unverifiable", "uncertainty": None}
            # The read-frame prediction carries the model's error on the change. Where the fall is larger than
            # the level left, the level's tolerance is narrower than that error: it proves neither a hit nor
            # a miss, and only a miss on the increment counts.
            if (row["status"] in ("within", "outside") and m["background"] != "zero"
                    and -row["predicted_change"] > predicted and row["increment"]["status"] == "within"):
                row["status"], row["why"], row["dim_unresolved"] = "unverifiable", "a deep dim whose change landed", True
                del row["residual"]
            # A held-lit point is never a hit; it is a miss only beyond what the held light could account for.
            row["excluded_held"] = (row["held_lit"] and row["status"] in ("within", "outside")
                                    and (m["held_rule"] == "exclude_both"
                                         or within(measured, predicted, m["tolerance"], held_light)))
            out[sid] = row
        return out, revision

    def explain(self, d, frame, row):
        """One cause for an outside point: held, shadow, daylight, drift or unexplained."""
        if row["excluded_held"]:
            return {"cause": "held", "basis": f"held or superseded share {row['held_share']:.2f}"}
        res = row["residual"]
        occ, people = self.occupied(frame.ts)
        xy = self.model.xy(row["point_id"])
        if occ and res < 0 and xy:
            for p in people:
                pxy = p.get("xy")
                if pxy and math.hypot(pxy[0] - xy[0], pxy[1] - xy[1]) <= self.m["shadow_radius_m"]:
                    return {"cause": "shadow", "admitted": False, "pending": "envelope-parameters",
                            "basis": f"{p.get('track_id')} within {self.m['shadow_radius_m']} m, occupied"}
        if d.ref is not None:
            common = set(frame.windows) & set(d.ref.windows)
            change = sum(frame.windows[w] - d.ref.windows[w] for w in common)
            if common and abs(change) >= self.m["daylight_change_min"] and _sign(change) == _sign(res):
                return {"cause": "daylight", "basis": f"window daylight changed by {change:+.3f}"}
        inc = row["increment"]
        if (inc["status"] == "outside" and not occ and row["held_share"] == 0
                and _sign(inc["measured_change"] - inc["predicted_change"]) == _sign(res)):
            return {"cause": "drift", "basis": "the change itself landed off in the same direction"}
        return {"cause": "unexplained", "basis": "no daylight change, shadow or drift"}

    # -- Decision.verify ------------------------------------------------------

    def verify_frame(self, d, later):
        if d.problem:
            return None, d.problem
        nxt = min((x for x in later if x.first_sent is not None), key=lambda x: x.first_sent, default=None)
        t_next = nxt.first_sent if nxt else math.inf
        if t_next < d.settled:
            return None, f"superseded before it settled: {nxt.id} sent a step first"
        busy_seen = None
        for f in self.frames:
            if f.ts < d.settled:
                continue
            if f.ts >= t_next:
                break
            reason = self.windows.busy(f.ts)
            if reason:
                busy_seen = busy_seen or reason
                continue
            return f, None
        if busy_seen:
            return None, f"no settled frame before the next decision: frames were {busy_seen}"
        return None, "no frame after the decision settled and before the next one"

    def verify(self, d, frame):
        m = self.m
        rows, _ = self.evaluate(d, frame, frame.ts)
        pts, kinds, eyes = d.verify_points()
        points, increments, explanation, counted = [], [], [], []
        for p in pts:
            row = rows.get(p)
            if row is None:
                points.append({"point_id": p, "kind": kinds.get(p), "status": "unverifiable",
                               "camera_estimate": None, "residual": None})
                continue
            kind = kinds.get(p) or self.model.kind(p)
            points.append({"point_id": p, "kind": kind, "status": row["status"],
                           "camera_estimate": row["measured"], "residual": _r(row.get("residual"), 4)})
            inc = dict(row["increment"])
            for k in ("measured_change", "predicted_change", "uncertainty"):
                inc[k] = _r(inc[k], 3)
            increments.append(inc)
            if row["status"] == "outside":
                explanation.append({"point_id": p, **self.explain(d, frame, row)})
            if row["status"] in ("within", "outside"):
                counted.append(row)
        spill = self.spill(d, frame)
        scored = [r for r in counted if not r["excluded_held"]]
        verify = {
            "ts": frame.ts, "light_state": frame.id, "anchored": frame.anchored, "unit": frame.unit,
            "points": points, "spill": spill, "increments": increments, "not_verified": eyes,
            "tolerance": m["tolerance"], "tolerance_status": TOLERANCE_STATUS,
            "within_tolerance": (all(r["status"] == "within" for r in scored) if scored else None),
            "explanation": explanation or None,
        }
        return verify, rows, counted

    def spill(self, d, frame):
        m, out = self.m, []
        served = d.served_by_changed()
        targets = d.rec.get("targets") or {}
        for track in d.tracks():
            if track in served:
                continue
            t = targets.get(track) if isinstance(targets.get(track), dict) else {}
            region = t.get("surface_id")
            if not region:
                continue
            entry = {"track_id": track, "region": region, "measured_after": None, "spill": None,
                     "status": "unverifiable", "uncertainty": None}
            value, _ = frame.usable(region, m)
            if value is not None:
                entry["measured_after"] = value
                band = t.get("lx") if m["spill_band_top"] == "target_lx" else None
                if frame.unit == "lx" and type(band) in (int, float):
                    top = float(band) + m["spill_allowance_lx"]
                    unc = self.u(value, frame.unit)
                    entry["spill"] = _r(max(0.0, value - top), 3)
                    entry["uncertainty"] = _r(unc, 3)
                    entry["status"] = ("within" if value + unc <= top else
                                       "outside" if value - unc > top else "unverifiable")
            out.append(entry)
        return out

    # -- LightState.residual --------------------------------------------------

    def governing(self, t):
        best = None
        for d in self.decs:
            if d.ts <= t and (d.first_sent is None or d.first_sent <= t):
                best = d
        return best

    def residual(self, frame):
        reason = self.windows.busy(frame.ts)
        if reason:
            return None, f"not a verify frame: {reason}"
        d = self.governing(frame.ts)
        if d is None:
            return None, "no decision in force"
        if d.problem:
            return None, f"decision {d.id}: {d.problem}"
        try:
            rows, revision = self.evaluate(d, frame, frame.ts)
        except KeyError as e:
            return None, f"decision {d.id}: {e.args[0]}"
        occ, _ = self.occupied(frame.ts)
        entries = []
        for sid, row in rows.items():
            if row["measured"] is None or row.get("residual") is None:
                continue
            flags = []
            if row["held_lit"]:
                flags.append("held")
            if occ:
                flags.append("occupied")
            if row["basis"] == "fallback_patch":
                flags.append("fallback_patch")
            if row["status"] == "outside":
                flags.append("outside")
                cause = self.explain(d, frame, row)["cause"]
                if cause != "held":
                    flags.append(cause)
            entries.append({"surface_id": sid, "predicted": _r(row["predicted"], 3),
                            "residual": _r(row["measured"] - row["predicted"], 3),
                            "residual_pct": _r(100 * row["residual"], 2), "unit": frame.unit,
                            "against": {"decision_id": d.id, "hold_set_revision": revision},
                            "flags": flags})
        return entries, None

    # -- gold need time -------------------------------------------------------

    def gold_share(self):
        m = self.m
        totals = {"need_s": 0.0, "verifiable_s": 0.0,
                  "unknown_s": {"geometry": 0.0, "no_frame": 0.0, "mid_fade": 0.0, "unverifiable": 0.0}}
        frames = self.frames
        for g in self.gold:
            start, end = float(g["start"]), float(g["end"])
            span = max(0.0, end - start)
            totals["need_s"] += span
            mark = g.get("R_eval") or {}
            sd = mark.get("placement_sd")
            if not mark.get("surface_id") or type(sd) not in (int, float) or sd > m["tau_r_cm"]:
                totals["unknown_s"]["geometry"] += span
                continue
            covered = 0.0
            for i, f in enumerate(frames):
                nxt = frames[i + 1].ts if i + 1 < len(frames) else math.inf
                lo, hi = max(start, f.ts), min(end, nxt, f.ts + m["max_frame_gap_s"])
                if hi <= lo:
                    continue
                covered += hi - lo
                if self.windows.busy(f.ts):
                    totals["unknown_s"]["mid_fade"] += hi - lo
                elif f.usable(mark["surface_id"], m, bases=("measured",))[0] is not None:
                    totals["verifiable_s"] += hi - lo
                else:
                    totals["unknown_s"]["unverifiable"] += hi - lo
            totals["unknown_s"]["no_frame"] += span - covered
        need = totals["need_s"]
        totals["share"] = (totals["verifiable_s"] / need) if need else None
        totals["note"] = ("bounds delivered-service evidence at rung 11; fallback patches and priors "
                          "never count; marks beyond tau_R are geometry unknowns (strict)")
        for k in ("need_s", "verifiable_s"):
            totals[k] = round(totals[k], 3)
        totals["unknown_s"] = {k: round(v, 3) for k, v in totals["unknown_s"].items()}
        totals["share"] = _r(totals["share"], 4)
        return totals

    # -- the whole run --------------------------------------------------------

    def hit_rate(self, counted_by_day):
        m = self.m
        hits = sum(h for h, _ in counted_by_day.values())
        n = sum(k for _, k in counted_by_day.values())
        gate = Fraction(str(m["hit_rate_min"]))
        out = {"hits": hits, "misses": n - hits, "value": _r(hits / n, 4) if n else None,
               "gate": m["hit_rate_min"], "orientation": "higher is better",
               "delta": m["delta_hit_rate"], "delta_status": "promotion-margins: no delta set by the owner",
               "verdict": ("insufficient" if not n else "pass" if Fraction(hits, n) >= gate else "fail")}
        sessions = list(counted_by_day.values())
        out["sessions"] = len(sessions)
        if len(sessions) >= 2:
            rng = random.Random(0)
            rates = []
            for _ in range(int(m["bootstrap_rounds"])):
                pick = [sessions[rng.randrange(len(sessions))] for _ in sessions]
                tot = sum(k for _, k in pick)
                if tot:
                    rates.append(sum(h for h, _ in pick) / tot)
            rates.sort()
            a = (1 - m["interval_level"]) / 2
            out["interval"] = [_r(percentile(rates, a), 4), _r(percentile(rates, 1 - a), 4)]
            out["interval_status"] = (f"two-sided {m['interval_level']:.0%}, {int(m['bootstrap_rounds'])} "
                                      "session-bootstrap rounds (owner choice 6)")
        else:
            out["interval"] = None
            out["interval_status"] = "one session: no session-bootstrap interval"
        return out

    def run(self):
        m = self.m
        decisions_out, rows_out = [], []
        counted_by_day = {}
        status_counts = {"within": 0, "outside": 0, "unverifiable": 0, "prior_only": 0}
        per_point = {}
        causes = {"daylight": 0, "shadow": 0, "drift": 0, "unexplained": 0}
        excluded_held = fallback_scored = eyes_total = dims_unverifiable = 0
        excluded_by_status = {"within": 0, "outside": 0}
        not_admitted, not_verified = [], []
        spill_counts, inc_counts = {}, {}
        for i, d in enumerate(self.decs):
            rec = copy.deepcopy(d.rec)
            frame, why = self.verify_frame(d, self.decs[i + 1:])
            if frame is None:
                rec["verify"], rec["verify_status"] = None, why
                not_verified.append({"decision_id": d.id, "reason": why})
                decisions_out.append(rec)
                continue
            try:
                verify, rows, counted = self.verify(d, frame)
            except KeyError as e:
                why = str(e.args[0])
                rec["verify"], rec["verify_status"] = None, why
                not_verified.append({"decision_id": d.id, "reason": why})
                decisions_out.append(rec)
                continue
            rec["verify"] = verify
            rec.pop("verify_status", None)
            decisions_out.append(rec)
            admitted = not any(s.mid_transition for s in d.steps)
            if not admitted:
                not_admitted.append({"decision_id": d.id, "pending": "fixture-capabilities",
                                     "why": "a step went out while the same channel was still fading"})
            occ, _ = self.occupied(frame.ts)
            for p in verify["points"]:
                status_counts[p["status"]] += 1
                per_point.setdefault(p["point_id"], {k: 0 for k in status_counts})[p["status"]] += 1
                dims_unverifiable += "dim_unresolved" in rows.get(p["point_id"], {})
            eyes_total += len(verify["not_verified"])
            for s in verify["spill"]:
                spill_counts[s["status"]] = spill_counts.get(s["status"], 0) + 1
            for s in verify["increments"]:
                inc_counts[s["status"]] = inc_counts.get(s["status"], 0) + 1
            if admitted:
                hits = n = 0
                for r in counted:
                    if r["excluded_held"]:
                        excluded_held += 1
                        excluded_by_status[r["status"]] += 1
                        continue
                    if r["basis"] == "fallback_patch":
                        fallback_scored += 1
                    n += 1
                    hits += r["status"] == "within"
                for e in verify["explanation"] or []:
                    if e["cause"] in causes:
                        causes[e["cause"]] += 1
                day = self.clock.day(frame.ts)
                h0, n0 = counted_by_day.get(day, (0, 0))
                counted_by_day[day] = (h0 + hits, n0 + n)
            rows_out.append({"decision_id": d.id, "light_state": frame.id, "ts": frame.ts,
                             "settled_at": d.settled, "occupied": occ, "admitted": admitted,
                             "within_tolerance": verify["within_tolerance"],
                             "points": {p["point_id"]: p["status"] for p in verify["points"]}})

        states_out, frame_counts = [], {"total": len(self.frames), "residual_written": 0,
                                        "occupied": 0, "excluded": {}}
        for f in self.frames:
            rec = copy.deepcopy(f.rec)
            entries, why = self.residual(f)
            if self.occupied(f.ts)[0]:
                frame_counts["occupied"] += 1
            if entries is None:
                rec.setdefault("residual", [])
                rec["residual_status"] = why
                if why.startswith("not a verify frame"):
                    key = why.split(": ")[1]
                    frame_counts["excluded"][key] = frame_counts["excluded"].get(key, 0) + 1
            else:
                rec["residual"] = entries
                rec.pop("residual_status", None)
                frame_counts["residual_written"] += 1
            states_out.append(rec)

        misses = sum(causes.values())
        surfaces_total = sum(status_counts.values())
        unver = status_counts["unverifiable"] + status_counts["prior_only"]
        report = {
            "tool": "den-verify", "rung": 10, "scored": self.scored,
            "model": {"version": self.model.version, "basis_unit": self.model.basis_unit,
                      "backend": self.model.backend},
            "decisions": {"total": len(self.decs), "verified": len(rows_out),
                          "not_verified": not_verified, "not_admitted": not_admitted},
            "frames": frame_counts,
            "hit_rate": self.hit_rate(counted_by_day),
            "excluded_held_or_superseded": excluded_held,
            "excluded_held_by_status": excluded_by_status,
            "dim_points_unverifiable": dims_unverifiable,
            "fallback_patch_points_scored": fallback_scored,
            "misses_explained": {
                **causes, "misses": misses,
                "share_explained": _r((misses - causes["unexplained"]) / misses, 4) if misses else None,
                "share_explained_admitted": (_r((causes["daylight"] + causes["drift"]) / misses, 4)
                                             if misses else None),
                "shadow_status": "not admitted: pending envelope-parameters (nominal body envelope)"},
            "points": status_counts, "per_point": per_point,
            "eyes": {"not_verified": eyes_total, "note": "no eye point is ever written as verified"},
            "unverifiable_coverage": {
                "surfaces": _r(unver / surfaces_total, 4) if surfaces_total else None,
                "all_targets": (_r((unver + eyes_total) / (surfaces_total + eyes_total), 4)
                                if surfaces_total + eyes_total else None)},
            "spill": spill_counts, "increments": inc_counts,
            "gold_need_time": self.gold_share(),
            "pending": {k: {"options": list(v["options"]), "selected": v["selected"], "affects": v["affects"]}
                        for k, v in method_mod.PENDING.items()},
            "rows": rows_out,
        }
        return decisions_out, states_out, report
