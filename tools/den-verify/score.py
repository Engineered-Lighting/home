"""Rung 10's scorer: Decision.verify, LightState.residual and the run report.

Pure rules over records already read; no I/O and no network. VERIFY-V1.md
says what is scored and why; this file follows it section by section.
"""
import bisect
import copy
import math
import random
from collections import Counter
from fractions import Fraction

import method as method_mod
import settle
from records import FRAME_APART_S, SAME_EVENING_S, BadFile, is_number, light_state_id, light_state_names, person_state_anchor

TOLERANCE_STATUS = "relative, either way; target, to validate"
SCORED_BASES = ("measured", "fallback_patch")
OWN_ROUNDING = 0.0005 + 1e-9  # how far a Decision's predicted.surfaces, rounded to 3 places, can be from the sum it rounded
MODEL_ERRORS = (LookupError, TypeError, ValueError, AttributeError, ArithmeticError)  # what a prediction can raise on one decision
# Why a decision with a verify frame judged nothing, the first of these that any of its points gives.
NOT_JUDGED = ("read frame stale", "no read frame", "no surface map", "unknown state", "nothing readable", "nothing lit")


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


def changed_light(now, was, tolerance):
    """What one bulb puts under the allowance at a point: its brighter level, once it has really moved.

    A move inside the ladder's tolerance of that level counts in proportion, so a read-back step, a colour
    nudge or a pin's rounding widens a bound by about its own size, never by a whole bulb.
    """
    brighter, moved = max(now, was), abs(now - was)
    return moved / tolerance if moved < tolerance * brighter else brighter


def level_of(scene):
    """The level a record gives a channel: 0 when off, and None, unknown, for a lit bulb with no level or a bulb never seen."""
    if not scene:
        return None
    if scene.get("on") is False:
        return 0.0
    return None if scene.get("level") is None else float(scene["level"])


def model_error(e):
    return "model error: " + str(e.args[0] if isinstance(e, KeyError) and e.args else e)


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
        self.id, self.names = light_state_id(rec, clock), light_state_names(rec, clock)
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


def pin_level(pinned):
    """A pin's level_pct: 0 when it says off, None when it gives none."""
    return 0.0 if pinned.get("level_pct") is None and pinned.get("on") is False else pinned.get("level_pct")


class Hold:
    def __init__(self, entity_id, channels, level, cct_k, since, ended, revision, ref, canon):
        self.names = {canon(entity_id)} | {canon(f"{entity_id}.{c}") for c in channels or []}
        self.level, self.cct_k = level, cct_k
        self.since, self.ended, self.revision, self.ref = since, ended, revision, ref

    def keys(self, state):
        return [k for k in state if k in self.names]

    def active(self, t):
        return self.since <= t and (self.ended is None or self.ended > t)

    def after_copy(self, revision, ts):
        """Past a decision's copy of the hold set, which already says what became of any earlier hold."""
        if self.revision is None or revision is None:
            return self.since > ts
        return self.revision > revision

    def order(self):
        return (self.revision is not None, self.revision or 0, self.since)


class Dec:
    """A Decision with its times resolved and its settle facts."""

    def __init__(self, rec, run):
        self.rec = rec
        self.id = rec.get("decision_id")
        self.canon = run.model.canon
        inputs = rec.get("inputs") or {}
        self.inputs = inputs
        ts, read_ts = rec.get("ts"), inputs.get("light_state_ts")
        anchor = ts if is_number(ts) else read_ts if is_number(read_ts) else run.date_anchor(
            [inputs.get("light_state")], inputs.get("person_state"))
        self.resolve = lambda v: run.clock.resolve(v, anchor)
        try:
            self.ts = self.resolve(ts)
        except ValueError:
            raise BadFile(f"decision {self.id}: its clock time {ts!r} has no date, and nothing in the record or the frames gives one") from None
        self.ref, self.no_ref = run.read_frame(inputs, self.ts)
        self.problem = self.stale_read = self.differs = None
        self.problem_kind = "no settle time"
        try:
            self.steps = settle.decision_steps(rec, self.resolve, run.m)
        except settle.Unsettleable as e:
            self.steps, self.problem = [], str(e)
        self.settled = settle.settled_at(self.steps, self.ts)
        self.first_sent = min((s.sent_at for s in self.steps), default=None)
        self.changed = sorted({s.channel for s in self.steps})
        lm = inputs.get("light_model")
        if not rec.get("applied_scene"):
            self.problem, self.problem_kind = "no applied_scene: nothing says what the bulbs did", "no applied_scene"
        elif lm and lm != run.model.version and not self.problem:
            self.problem, self.problem_kind = f"light model {lm} in the decision, {run.model.version} loaded", "another light model"

    def before_state(self):
        """The state the decision read: a level of None where it read none, for a channel it lists as well as those it read."""
        state = {self.canon(ch): {"level": None, "cct_k": None} for ch in self.rec.get("applied_scene") or {}}
        for ch, a in (self.inputs.get("actuator_state") or {}).items():
            state[self.canon(ch)] = {"level": level_of(a), "cct_k": (a or {}).get("cct_k")}
        return state

    def state_at(self, t, holds):
        """The actual resulting state at t, and the channels held or superseded."""
        state = self.before_state()
        held, read_back = set(), set()
        for ch, a in (self.rec.get("applied_scene") or {}).items():
            ch, a = self.canon(ch), a or {}
            rb = a.get("readback") or {}
            prev = state.get(ch, {})
            level = level_of(rb)
            state[ch] = {"level": level, "cct_k": rb.get("cct_k", prev.get("cct_k"))}
            if level is not None:
                read_back.add(ch)
            if a.get("status") in ("held", "superseded"):
                held.add(ch)
        hold_set = self.inputs.get("hold_set") or {}
        revision = hold_set.get("revision")
        pins = []
        for h in hold_set.get("held") or []:
            p = h.get("pinned") or {}
            pins.append(Hold(h.get("entity_id"), h.get("channels"), pin_level(p), p.get("cct_k"),
                             -math.inf, None, revision, self.id, self.canon))
        pins += sorted((h for h in holds if h.active(t) and h.after_copy(revision, self.ts)), key=Hold.order)
        for h in pins:
            for ch in h.keys(state):
                held.add(ch)
                # this decision's read-back comes after its own copy: a game's vacancy turns a held bulb off and keeps the hold
                own = h.ref == self.id
                if own and ch in read_back:
                    continue
                if h.level is not None:
                    state[ch]["level"] = float(h.level) / 100.0
                elif not own:  # a person set it, after this decision read it, and nothing says where
                    state[ch]["level"] = None
                if h.cct_k is not None:
                    state[ch]["cct_k"] = h.cct_k
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
        recipients = {self.canon(ch): r for ch, r in (self.rec.get("recipients") or {}).items()}
        for ch in self.changed:
            served |= set((recipients.get(self.canon(ch)) or {}).get("served") or [])
        return served


class Run:
    def __init__(self, m, model, decisions, light_states, corrections=(), occupancy=(), gold=(),
                 clock=None, scored=True):
        from records import Clock
        self.m, self.model, self.clock, self.scored = m, model, clock or Clock(), scored
        self.frames = sorted((Frame(r, self.clock) for r in light_states), key=lambda f: f.ts)
        self.frame_times = [f.ts for f in self.frames]
        self.frames_by_id = {}  # two frames on different days may share a derived name
        for f in self.frames:
            for name in f.names:
                self.frames_by_id.setdefault(name, []).append(f)
        self.first_ts = self.frames[0].ts if self.frames else None
        self.decs = sorted((Dec(r, self) for r in decisions), key=lambda d: d.ts)
        self.holds, self.windows = [], settle.Windows()
        for c in corrections:
            self._add_correction(c)
        all_steps = [s for d in self.decs for s in d.steps]
        self.windows.add_steps(all_steps)
        settle.mark_mid_transition(all_steps)
        for d in self.decs:
            if d.ref is not None:
                d.stale_read = settle.stale_read(d.id, d.ts, d.ref.ts, all_steps, self.windows.changes())
        self.occupancy = [(float(o["start"]), float(o["end"]), o.get("people") or []) for o in occupancy]
        self.gold = list(gold)

    def date_anchor(self, names, person_state):
        """A number to date a record's clock strings by: a frame only it names, a person's state id, or the one evening the
        frames span. With frames over several evenings and nothing else, None: nothing says which day a clock time is on."""
        for name in names:
            named = self.frames_by_id.get(name) or ()
            if len(named) == 1:
                return named[0].ts
        anchor = person_state_anchor(person_state)
        if anchor is None and self.frames and self.frame_times[-1] - self.first_ts <= SAME_EVENING_S:
            return self.first_ts
        return anchor

    def frame_at(self, ts):
        """The frame at this time, to within half a millisecond, or None."""
        i = bisect.bisect_left(self.frame_times, ts)
        best = min(self.frames[max(i - 1, 0):i + 1], key=lambda f: abs(f.ts - ts), default=None)
        return best if best is not None and abs(best.ts - ts) <= FRAME_APART_S else None

    def read_frame(self, inputs, ts):
        """(the frame a decision read, None), or (None, why not). Its ts names it, else its id does, within 12 hours: frames
        are keyed by time, and a derived id comes round again the next day."""
        at, name = inputs.get("light_state_ts"), inputs.get("light_state")
        if is_number(at):
            found = self.frame_at(at)
            return found, None if found else f"no LightState at ts {at!r}"
        if name:
            near = [f for f in self.frames_by_id.get(name) or () if abs(f.ts - ts) <= SAME_EVENING_S]
            if near:
                return min(near, key=lambda f: abs(f.ts - ts)), None
            return None, f"{name} names no LightState within 12 hours of this decision"
        return None, inputs.get("light_state_status") or "the decision names none"

    def _add_correction(self, c):
        snap = c.get("snapshot") or {}
        anchor = c["ts"] if is_number(c.get("ts")) else self.date_anchor(snap.get("light_state") or [], snap.get("person_state"))
        resolve = lambda v: self.clock.resolve(v, anchor)
        try:
            ts = resolve(c.get("ts"))
        except ValueError:
            raise BadFile(f"correction {c.get('correction_id')}: its clock time {c.get('ts')!r} has no date, "
                          "and nothing in the record or the frames gives one") from None
        self.windows.add_change(ts, (c.get("action") or {}).get("settle_s"),
                                float(self.m["settle_margin_s"]), c.get("correction_id"))
        h = c.get("hold")
        if h:
            p = h.get("pinned") or {}
            self.holds.append(Hold(h.get("entity_id"), h.get("channels"), pin_level(p), p.get("cct_k"),
                                   resolve(h.get("since") or c.get("ts")), resolve(h.get("ended")),
                                   h.get("hold_set_revision"), c.get("correction_id"), self.model.canon))

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

    def own_prediction_differs(self, d):
        """{surface: why} where a Stage 1 model and map do not give what the Decision says they gave for its own read-backs.

        A Decision does not name its map, and its model's version says nothing of the blocks, so this is what can tell a map
        that is not the one it ran on: a surface read through it would be judged against someone else's numbers. Holds past
        its copy move what is verified, not this. Nothing to compare on den-verify's own shape, whose fixtures hold placeholders.
        """
        if d.differs is None:
            d.differs = {}
            if self.model.stage1:
                logged = (d.rec.get("predicted") or {}).get("surfaces") or {}
                computed = self.model.predict(d.state_at(d.ts, ())[0])
                for sid, value in logged.items():
                    if is_number(value) and is_number(computed.get(sid)) and abs(computed[sid] - value) > OWN_ROUNDING:
                        d.differs[sid] = (f"{sid}: this model and map give {computed[sid]:.3f} for the decision's own read-backs and its "
                                          f"predicted.surfaces says {value}: check --surfaces, and that this is the model it ran on")
        return d.differs

    def no_prediction(self, state, parts, sid, when):
        """Why a surface has no predicted light: a bulb of unknown state there, or no block of it with a value."""
        names = [c for c, s in state.items() if s["level"] is None and parts[c][sid] is None]
        if names:
            return f"unknown state: {', '.join(names)} has no level {when} this decision", "unknown state"
        return f"light model {self.model.version} has no value at any block of {sid}: the fit excluded them", "nothing readable"

    def evaluate(self, d, frame, t):
        """Every surface the model predicts, at frame f, against d's actual resulting state."""
        m, model = self.m, self.model
        state, held, revision = d.state_at(t, self.holds)
        after = model.predict(state)
        parts = model.contributions(state)
        before_state = d.before_state()
        before = model.predict(before_state)
        parts_before = model.contributions(before_state)
        ref = d.ref
        out = {}
        unit_ok = frame.unit == model.basis_unit
        differs = self.own_prediction_differs(d)
        for sid in model.surfaces:
            row = {"point_id": sid, "unit": frame.unit}
            measured, why = frame.usable(sid, m) if unit_ok else (None, "unit mismatch")
            row["basis"] = (frame.estimates.get(sid) or {}).get("basis")
            ref_val, rwhy, rkind = None, "no reference frame in the same unit", "nothing readable"
            if d.stale_read:
                rwhy, rkind = f"reference frame: {d.stale_read}", "read frame stale"
            elif ref is None:
                rwhy, rkind = f"no read frame: {d.no_ref}", "no read frame"
            elif ref.unit == frame.unit:
                ref_val, rwhy = ref.usable(sid, m)
                rwhy = rwhy and f"reference frame: {rwhy}"
            # the state before only matters through the read frame: under zero the level check stands without it
            light, was_light = after[sid], before[sid]
            reads_frame = m["background"] != "zero"
            predicted = None
            if light is None or (was_light is None and reads_frame):
                if ref_val is not None or not reads_frame:
                    rwhy, rkind = (self.no_prediction(before_state, parts_before, sid, "before") if light is not None else
                                   self.no_prediction(state, parts, sid, "after" if was_light is not None else "before or after"))
            elif not reads_frame:
                predicted = light
            elif ref_val is not None:
                predicted = ref_val - was_light + light
            predicted_change = None if light is None or was_light is None else light - was_light
            if sid in differs:
                predicted, predicted_change, rwhy, rkind = None, None, differs[sid], "no surface map"
            row.update(measured=measured, why=why, predicted=predicted, ref_value=ref_val,
                       predicted_change=predicted_change)
            # each channel at its brighter level: one held off gives no light now, and is still why a point moved
            brightest = own = free = held_light = held_moved = 0.0
            for c in parts if predicted is not None else ():
                now, was = parts[c][sid], parts_before[c][sid] if c in parts_before else 0.0
                if was is None:  # under zero, a bulb read nowhere: nothing says it moved, so it widens nothing
                    was = now
                if c in held:
                    held_light += max(now, was)
                    held_moved += abs(now - was)
                else:
                    free += max(now, was)
                    own += abs(now - was)
                brightest += changed_light(now, was, m["tolerance"])
            row["held_share"] = held_light / max(predicted, held_light) if held_light > 0 and predicted is not None else 0.0
            row["held_lit"] = held_light > 0 and row["held_share"] >= m["held_share_min"]
            if measured is None:
                row["status"], row["why_kind"] = "prior_only" if why == "prior_only" else "unverifiable", "nothing readable"
            elif predicted is None:
                row["status"], row["why"], row["why_kind"] = "unverifiable", rwhy, rkind
            else:
                self.judge_level(row, measured, predicted, ref_val, brightest, own, frame.unit)
            # increments: measured change against the model's own change
            if measured is not None and ref_val is not None and predicted_change is not None:
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
            # Out both ways where this decision's bulbs could not show; the reading plays no part.
            row["excluded_held"] = False
            if row["held_lit"] and row["status"] in ("within", "outside"):
                answers_for = own if held_moved > row["level_bound"] else free
                row["excluded_held"] = m["held_rule"] == "exclude_both" or answers_for <= row["level_bound"]
            out[sid] = row
        return out, revision

    def judge_level(self, row, measured, predicted, ref_val, brightest, own, unit):
        """within or outside, to the ladder's tolerance or what the prediction itself inherits if that is wider.

        Whether a point counts, and its bound, come from predictions alone: a reading can only choose
        between hit and miss, never leave a point out.
        """
        m = self.m
        # the read frame's D hides the model's error at the state it read, and the changed bulbs carry theirs;
        # with D taken as zero only an off, with no level to take a share of, needs the same allowance
        noise = self.u(max(predicted, 0.0), unit)
        inherited = 0.0
        if m["background"] != "zero" or predicted <= 0:
            inherited = float(Fraction(str(m["change_allowance"])) * Fraction(brightest))
        if m["background"] != "zero":
            noise = math.hypot(noise, self.u(ref_val, unit))
        wide = max(inherited, noise)
        if predicted <= 0 and not (brightest and wide):
            row["status"], row["why"], row["why_kind"] = "unverifiable", "prediction not positive", "nothing lit"
            return
        level_tolerance = m["tolerance"] * max(predicted, 0.0)
        row["error"] = measured - predicted
        if predicted > 0:
            row["residual"] = row["error"] / predicted
        # the meter cannot tell this change from none, only from a bulb far off: it still counts, to the noise
        row["below_noise"] = level_tolerance < noise and 0 < own <= noise
        ladder = predicted > 0 and within(measured, predicted, m["tolerance"])
        row["status"] = "within" if ladder or within(measured, predicted, 0, wide) else "outside"
        row["level_bound"] = max(level_tolerance, wide)
        row["widened"] = wide > level_tolerance
        row["wide_hit"] = row["status"] == "within" and not ladder

    def explain(self, d, frame, row):
        """One cause for an outside point: held, shadow, daylight, drift or unexplained."""
        if row["excluded_held"]:
            return {"cause": "held", "basis": f"held or superseded share {row['held_share']:.2f}"}
        res = row["error"]
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

    def no_frame(self, d, why):
        """A decision with no verify frame, and no read frame either where the background needs one."""
        if d.ref is None and self.m["background"] != "zero":
            why += f"; no read frame: {d.no_ref}"
        return None, "no frame after settling", why

    def verify_frame(self, d, later):
        """(frame, None, None), or (None, what kind of reason, the reason)."""
        if d.problem:
            return None, d.problem_kind, d.problem
        nxt = min((x for x in later if x.first_sent is not None), key=lambda x: x.first_sent, default=None)
        t_next = nxt.first_sent if nxt else math.inf
        if t_next < d.settled:
            return None, "superseded", f"superseded before it settled: {nxt.id} sent a step first"
        busy_seen = None
        for f in self.frames:
            if f.ts < d.settled:
                continue
            if f.ts >= t_next or f.ts - d.settled > SAME_EVENING_S:
                break
            reason = self.windows.busy(f.ts)
            if reason:
                busy_seen = busy_seen or reason
                continue
            return f, None, None
        if busy_seen:
            return self.no_frame(d, f"no settled frame before the next decision: frames were {busy_seen}")
        return self.no_frame(d, "no frame after the decision settled and before the next one")

    def verify(self, d, frame):
        m = self.m
        rows, _ = self.evaluate(d, frame, frame.ts)
        pts, kinds, eyes = d.verify_points()
        points, increments, explanation, counted, whys = [], [], [], [], []
        for p in pts:
            row = rows.get(p)
            if row is None:
                why = self.model.surface_problem(p)
                points.append({"point_id": p, "kind": kinds.get(p), "status": "unverifiable",
                               "camera_estimate": None, "residual": None, "why": why})
                whys.append(("no surface map", why))
                continue
            kind = kinds.get(p) or self.model.kind(p)
            judged = row["status"] in ("within", "outside")
            points.append({"point_id": p, "kind": kind, "status": row["status"], "camera_estimate": row["measured"],
                           "residual": _r(row.get("residual"), 4) if judged else None})
            if row.get("widened"):
                points[-1]["level_bound"] = _r(row["level_bound"], 3)
            if not judged:
                whys.append((row["why_kind"], row["why"]))
                if row["status"] == "unverifiable":
                    points[-1]["why"] = row["why"]
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
        return verify, whys, counted

    def not_judged(self, verify, whys, counted):
        """(kind, reason) for a decision with a verify frame and no verdict of its own."""
        if counted:
            return "left out", (f"the {len(counted)} point(s) it could judge are left out of the hit rate: "
                                "lit by held or superseded bulbs that its own could not show beside")
        if not verify["points"]:
            return "no surface named", "it names no surface to judge"
        kind = next(k for k in NOT_JUDGED if any(w[0] == k for w in whys))
        texts = list(dict.fromkeys(text for _, text in whys))
        more = f"; and {len(texts) - 3} more" if len(texts) > 3 else ""
        return kind, f"no point judged: {'; '.join(texts[:3])}{more}"

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
        return best if best is not None and t - best.settled <= SAME_EVENING_S else None

    def residual(self, frame):
        reason = self.windows.busy(frame.ts)
        if reason:
            return None, f"not a verify frame: {reason}"
        d = self.governing(frame.ts)
        if d is None:
            return None, "no decision in force"
        if d.problem:
            return None, f"decision {d.id}: {d.problem}"
        if d.stale_read and self.m["background"] != "zero":
            return None, f"decision {d.id}: {d.stale_read}"
        try:
            rows, revision = self.evaluate(d, frame, frame.ts)
        except MODEL_ERRORS as e:
            return None, f"decision {d.id}: {model_error(e)}"
        occ, _ = self.occupied(frame.ts)
        entries = []
        for sid, row in rows.items():
            if row["measured"] is None or row["predicted"] is None:
                continue
            flags = []
            if row["held_lit"]:
                flags.append("held")
            if occ:
                flags.append("occupied")
            if row["basis"] == "fallback_patch":
                flags.append("fallback_patch")
            if row.get("wide_hit"):
                flags.append("wide_tolerance")
            if row.get("below_noise"):
                flags.append("below_noise")
            if row["status"] == "outside":
                flags.append("outside")
                cause = self.explain(d, frame, row)["cause"]
                if cause != "held":
                    flags.append(cause)
            entries.append({"surface_id": sid, "predicted": _r(row["predicted"], 3),
                            "residual": _r(row["measured"] - row["predicted"], 3),
                            "residual_pct": _r(100 * row["residual"], 2) if "residual" in row else None, "unit": frame.unit,
                            "against": {"decision_id": d.id, "hold_set_revision": revision},
                            "flags": flags})
        if not entries:
            return None, f"decision {d.id}: no surface here has both a reading and a prediction"
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

    def rate_and_verdict(self, hits, n):
        gate = Fraction(str(self.m["hit_rate_min"]))
        return (_r(hits / n, 4) if n else None,
                "insufficient" if not n else "pass" if Fraction(hits, n) >= gate else "fail")

    def hit_rate(self, counted_by_day):
        m = self.m
        hits = sum(h for h, _ in counted_by_day.values())
        n = sum(k for _, k in counted_by_day.values())
        value, verdict = Run.rate_and_verdict(self, hits, n)
        out = {"hits": hits, "misses": n - hits, "value": value,
               "gate": m["hit_rate_min"], "orientation": "higher is better",
               "delta": m["delta_hit_rate"], "delta_status": "promotion-margins: no delta set by the owner",
               "verdict": verdict}
        # A day with nothing counted is no evidence: it must not narrow the interval.
        sessions = [day for day in counted_by_day.values() if day[1]]
        out["sessions"] = len(sessions)
        out["days_with_nothing_counted"] = len(counted_by_day) - len(sessions)
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
            out["interval_status"] = f"{'one session' if sessions else 'no session'}: no session-bootstrap interval"
        return out

    def run(self):
        m = self.m
        decisions_out, rows_out = [], []
        counted_by_day = {}
        status_counts = {"within": 0, "outside": 0, "unverifiable": 0, "prior_only": 0}
        per_point = {}
        causes = {"daylight": 0, "shadow": 0, "drift": 0, "unexplained": 0}
        excluded_held = fallback_scored = eyes_total = 0
        below_noise = {"count": 0, "hits": 0}
        wide_by_day = {}
        excluded_by_status = {"within": 0, "outside": 0}
        not_admitted, not_verified, read_frame_stale, unknown_state = [], [], [], []
        spill_counts, inc_counts = {}, {}

        def unjudged(d, rec, kind, why):
            rec["verify_status"] = why
            not_verified.append({"decision_id": d.id, "kind": kind, "reason": why})

        for i, d in enumerate(self.decs):
            rec = copy.deepcopy(d.rec)
            decisions_out.append(rec)
            frame, kind, why = self.verify_frame(d, self.decs[i + 1:])
            if frame is None:
                rec["verify"] = None
                unjudged(d, rec, kind, why)
                continue
            try:
                verify, whys, counted = self.verify(d, frame)
            except MODEL_ERRORS as e:  # one decision's: the run goes on
                rec["verify"] = None
                unjudged(d, rec, "model error", model_error(e))
                continue
            rec["verify"] = verify
            unknown = [text for kind, text in whys if kind == "unknown state"]
            if unknown:
                unknown_state.append({"decision_id": d.id, "points": len(unknown), "why": unknown[0]})
            if verify["within_tolerance"] is None:
                unjudged(d, rec, *self.not_judged(verify, whys, counted))
            else:
                rec.pop("verify_status", None)
            if d.stale_read:
                read_frame_stale.append({"decision_id": d.id, "why": d.stale_read})
            admitted = not any(s.mid_transition for s in d.steps)
            if not admitted:
                not_admitted.append({"decision_id": d.id, "pending": "fixture-capabilities",
                                     "why": "a step went out while the same channel was still fading"})
            occ, _ = self.occupied(frame.ts)
            for p in verify["points"]:
                status_counts[p["status"]] += 1
                per_point.setdefault(p["point_id"], {k: 0 for k in status_counts})[p["status"]] += 1
            eyes_total += len(verify["not_verified"])
            for s in verify["spill"]:
                spill_counts[s["status"]] = spill_counts.get(s["status"], 0) + 1
            for s in verify["increments"]:
                inc_counts[s["status"]] = inc_counts.get(s["status"], 0) + 1
            if admitted:
                hits = n = wide = 0
                for r in counted:
                    if r["excluded_held"]:
                        excluded_held += 1
                        excluded_by_status[r["status"]] += 1
                        continue
                    if r["basis"] == "fallback_patch":
                        fallback_scored += 1
                    n += 1
                    hits += r["status"] == "within"
                    wide += r["wide_hit"]
                    if r["below_noise"]:
                        below_noise["count"] += 1
                        below_noise["hits"] += r["status"] == "within"
                for e in verify["explanation"] or []:
                    if e["cause"] in causes:
                        causes[e["cause"]] += 1
                day = self.clock.day(frame.ts)
                h0, n0 = counted_by_day.get(day, (0, 0))
                counted_by_day[day] = (h0 + hits, n0 + n)
                wide_by_day[day] = wide_by_day.get(day, 0) + wide
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

        wide_hits = sum(wide_by_day.values())
        all_hits = sum(h for h, _ in counted_by_day.values())
        all_counted = sum(k for _, k in counted_by_day.values())
        narrow = self.rate_and_verdict(all_hits - wide_hits, all_counted)
        judged = self.rate_and_verdict(all_hits - below_noise["hits"], all_counted - below_noise["count"])
        misses = sum(causes.values())
        surfaces_total = sum(status_counts.values())
        unver = status_counts["unverifiable"] + status_counts["prior_only"]
        rate = self.hit_rate(counted_by_day)
        # hits the meter could not tell from a bulb that never moved lift nothing: the rest must pass too
        if rate["verdict"] != "fail":
            rate["verdict"] = judged[1]
        report = {
            "tool": "den-verify", "rung": 10, "scored": self.scored,
            "backend": self.model.backend, "predict_sha256": self.model.predict_sha256,
            "model": self.model.describe(),
            "decisions": {"total": len(self.decs), "verified": len(self.decs) - len(not_verified),
                          "not_verified": not_verified,
                          "not_verified_by_kind": dict(Counter(e["kind"] for e in not_verified)),
                          "not_admitted": not_admitted, "read_frame_stale": read_frame_stale,
                          "unknown_state": unknown_state},
            "frames": frame_counts,
            "hit_rate": rate,
            "excluded_held_or_superseded": excluded_held,
            "excluded_held_by_status": excluded_by_status,
            "below_meter_noise": {**below_noise, "misses": below_noise["count"] - below_noise["hits"],
                                  "value_without": judged[0], "verdict_without": judged[1]},
            "hits_beyond_level_tolerance": {"count": wide_hits, "value_if_missed": narrow[0],
                                            "verdict_if_missed": narrow[1]},
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
