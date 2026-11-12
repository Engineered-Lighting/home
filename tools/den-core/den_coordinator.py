"""The den lighting coordinator, v1 (coordinator plan, review/coordinator-20261111/plan.md).

One program decides every den light. This module is its pure core: events in, records out. den-live drives it
live on LA; report.py and the tests drive it offline. It never sends anything itself.

Events (kind, t, data):
- people: {"tracks": [{track_id, confirmed, joints3d, capture_ts}]}, den-live's tracks with the 3D skeleton.
- person_state: a need_v1 PersonState (track_id, focused_engagement, field_support, sources).
- ha: any of {on, occupied, game_running, qwen_ok, last_correction}: the coordinator switch, occupancy,
  HASS.Agent's game flag, whether Qwen is answering, and den_lighting 1.1's latest correction record.
- fixture: {fixture_id, ...} status or profile for one fixture (real fixtures: status, hand, joints_now).
- labels: a den-room-labels-1 document. settings: owner settings from Home. stop / resume: Home's Stop spot.
- tick: every second.

Records out (kind, data): decision (decision/v3 on den-spots-1), intent (aim_move, keep_lit, stop, resume, for
den-live's sender), bulb_plan (a den_lighting.coordinator_plan call), learned (a learning update), kept.

Rules, each from the owner's decisions (goals.md, codex-review/DECISIONS.md):
- spots: one job per fixture (share.py): the reader's task, else pre-aim, else art, else rest. Never in front of
  the person lit; the most over the shoulder wins; the light model only sets the level.
- a task needs, now: seated, exactly one person, need_v1 engaged on a close-up task with observed support, and a
  pose under 3 s old. Standing up or a second person ends it on the next sample.
- moves follow the owner's move_style setting (lit, dim or dark); a fixture whose profile can't move lit moves
  dark; a path whose beam centre sweeps through the lit person's own head moves dark.
- bulbs: plans through den_lighting only (bulbs.py); learning from the owner's corrections (learn.py).
- Off: nothing is sent (den_lighting drops the plan and Home Assistant presses every fixture's Stop), and
  Decisions carry on as shadow. Stop latches the spots until Resume.

Standard library only. Pure: reads no clock, file or network.
"""
import copy
import hashlib
import json
import math

import body
import bulbs
import eye
import joints
import learn
import scurve
import share
import situation
import spot_light

POLICY = "den-coordinator@v1"
DECISION_VERSION = "decision/v3"
DOMAIN = "den-spots-1"
SETTINGS = {"move_style": "lit", "move_dim_level": 0.3, "fade_in_s": 1.0, "fade_out_s": 0.5, "shift_m": 0.10,
            "shift_hold_s": 1.5, "phone_patience_s": 30, "pre_aim": True, "accent_level": 0.4, "follow_walkers": False}
MOVE_STYLES = ("lit", "dim", "dark")
TASK_KINDS = ("reading", "hobby")
SPOT_TARGET = 300.0
CCT_K = 2700
POSE_MAX_AGE_S = 3.0
SUPPORT_TTL_S = 45.0
TRACK_GONE_S = 6.0
STATUS_STALE_S = 10.0
COMMAND_VALID_MS = 1500


def _usable(row, t, desk_until=None):
    """Virtual fixtures always; a real one when installed (or `desk` while the owner's desk test is on), with a
    fresh status, not in fault, not held by hand, and not refused in the last 60 s."""
    if not row.get("real"):
        return True
    fresh = row.get("status_at") is not None and t - row["status_at"] <= STATUS_STALE_S
    status_ok = row.get("status") == "installed" or (row.get("status") == "desk" and desk_until is not None and t < desk_until)
    return (status_ok and fresh and not row.get("hand") and not row.get("fault")
            and t >= row.get("refused_until", -math.inf))


def profile_sha256(profile):
    return hashlib.sha256(json.dumps(profile, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


class Coordinator:
    def __init__(self, config):
        self.settings = dict(SETTINGS, **config.get("settings", {}))
        self.fixtures = {}
        for fid, row in config.get("fixtures", {}).items():
            self.fixtures[fid] = dict(row, profile=row.get("profile") or copy.deepcopy(scurve.FICTIONAL_PROFILE))
        self.utc_offset_h = config.get("utc_offset_h", 0.0)
        self.seat_mark = config.get("seat_mark")
        self.tracks, self.states = {}, {}
        self.ha = {"on": False, "occupied": None, "game_running": False, "qwen_ok": True}
        self.labels = None
        self.learner = config.get("learner") or learn.Learner()
        self.planner = bulbs.BulbPlanner(config.get("lights", []))
        self.spots = {fid: {"job": None, "on": False, "level": 0.0, "joints": None, "target": None} for fid in self.fixtures}
        self.stopped, self.made, self.last_key, self.last_seq, self.decision_id = False, 0, None, None, None
        self.shadow_sig = None  # while Off or stopped, the last "would do" written, so it's written once
        self.desk_until = None  # the owner's signed-in desk-test switch: `desk` fixtures count until then

    # ---- inputs -----------------------------------------------------------------------------------------------

    def handle(self, event):
        kind, t, data = event["kind"], event["t"], event.get("data") or {}
        out = []
        if kind == "people":
            self._people(t, data.get("tracks", []))
        elif kind == "person_state":
            self.states[data["track_id"]] = data
        elif kind == "ha":
            was_on = self.ha["on"]
            self.ha.update({k: data[k] for k in ("on", "occupied", "game_running", "qwen_ok") if k in data})
            if was_on and not self.ha["on"]:
                self._all_off()  # Home Assistant pressed every Stop; nothing is lit now
            if not was_on and self.ha["on"]:
                out += [self._intent("resume", fid) for fid, row in self.fixtures.items() if row.get("real")]
            if data.get("last_correction"):
                out += self._learn(t, data["last_correction"])
        elif kind == "fixture":
            row = self.fixtures.setdefault(data["fixture_id"], {"profile": copy.deepcopy(scurve.FICTIONAL_PROFILE)})
            row.update({k: v for k, v in data.items() if k != "fixture_id"}, status_at=t)
            self.spots.setdefault(data["fixture_id"], {"job": None, "on": False, "level": 0.0, "joints": None, "target": None})
            if data.get("joints_now"):
                self.spots[data["fixture_id"]]["joints"] = tuple(data["joints_now"])
        elif kind == "labels":
            self.labels = data
        elif kind == "settings":
            self.settings.update({k: v for k, v in data.items() if k in SETTINGS})
            if self.settings["move_style"] not in MOVE_STYLES:
                raise ValueError("move_style must be one of %s" % (MOVE_STYLES,))
        elif kind == "stop":
            self.stopped = True
            out += [self._intent("stop", fid) for fid in self.fixtures]
            self._all_off()
        elif kind == "desk":
            self.desk_until = data.get("until") if data.get("on") else None
        elif kind == "resume":
            self.stopped = False
            out += [self._intent("resume", fid) for fid, row in self.fixtures.items() if row.get("real")]
        return out + self._evaluate(t, tick=(kind == "tick"))

    def _people(self, t, tracks):
        seen = set()
        for track in tracks:
            if not track.get("confirmed"):
                continue
            tid = track["track_id"]
            seen.add(tid)
            row = self.tracks.setdefault(tid, {"samples": [], "filter": body.ShiftFilter(self.settings["shift_m"],
                                                                                         self.settings["shift_hold_s"])})
            row["samples"] = [s for s in row["samples"] if s[0] >= t - 3.0] + [(t, track.get("joints3d") or {})]
            row["capture_ts"], row["last_t"] = track.get("capture_ts", t), t
        for tid in [tid for tid, row in self.tracks.items() if tid not in seen and t - row["last_t"] > TRACK_GONE_S]:
            del self.tracks[tid]  # a track that ends: its final state is "gone", so its spot goes off
        for tid in [tid for tid in self.tracks if tid not in seen]:
            self.tracks[tid]["samples"] = [s for s in self.tracks[tid]["samples"] if s[0] >= t - 3.0]

    # ---- the situation and the people ---------------------------------------------------------------------------

    def _person_rows(self, t):
        rows = []
        for tid, row in sorted(self.tracks.items()):
            if not row["samples"]:
                continue
            j = row["samples"][-1][1]
            face, head, mid = body.facing(j), body.head(j), body.shoulder_mid(j)
            sat = row["seated"] = body.seated(row["samples"], t, was_seated=row.get("seated", False))
            row["filter"].shift_m, row["filter"].hold_s = self.settings["shift_m"], self.settings["shift_hold_s"]
            target, source = (None, None)
            if sat:
                point, source = body.task_point(j, face, self.seat_mark)
                row["filter"].update(t, point)
                target = row["filter"].aim
            else:
                row["filter"].aim = None
                target, source = (mid or head), "torso"
            rows.append({"track_id": tid, "head": head, "shoulder_mid": mid or head, "facing": face, "seated": sat,
                         "target": target, "source": source, "pose_age_s": t - row["capture_ts"]})
        alone = len(rows) == 1
        for person in rows:
            # need's room-level record (track "room") stands for the one person when they're alone
            state = self.states.get(person["track_id"]) or (self.states.get("room") if alone else None) or {}
            engagement = state.get("focused_engagement") or {}
            support = ((state.get("field_support") or {}).get("focused_engagement") or {})
            person["task"] = bool(alone and person["seated"] and engagement.get("state") in ("engaged", "ending")
                                  and engagement.get("kind") in TASK_KINDS and support.get("support_status") == "observed"
                                  and person["pose_age_s"] <= POSE_MAX_AGE_S and person["target"])
            person["evidence"] = {"person_state_id": state.get("state_id"), "sources": state.get("sources"),
                                  "pose_capture_ts": self.tracks[person["track_id"]]["capture_ts"],
                                  "activity_valid_until": None if support.get("last_support_capture_ts") is None
                                  else support["last_support_capture_ts"] + SUPPORT_TTL_S}
        return rows

    def _key(self, t, people):
        act = situation.activity(self.ha["occupied"], self.ha["game_running"], sum(p["task"] for p in people),
                                 sum(p["seated"] for p in people), self.ha["qwen_ok"])
        return act, situation.key(act, situation.part_of_day(t, self.utc_offset_h))

    # ---- learning and bulbs -------------------------------------------------------------------------------------

    def _learn(self, t, record):
        if record.get("seq") == self.last_seq:
            return []
        self.last_seq = record.get("seq")
        people = self._person_rows(t)
        _, key = self._key(t, people)
        row, why = self.learner.apply(record, key, len(people), self.ha["on"], bool(self.ha["occupied"]),
                                      own_fade=bool(record.get("own_fade")))
        return [{"kind": "learned", "data": {"situation": key, "light": record.get("entity_id"), "seq": record.get("seq"),
                                             "learned": row, "skipped": why}}]

    # ---- spots ------------------------------------------------------------------------------------------------

    def _all_off(self):
        for spot in self.spots.values():
            spot.update(job=None, on=False, level=0.0)

    def _intent(self, action, fid, **fields):
        return {"kind": "intent", "data": dict({"action": action, "fixture_id": fid, "decision_id": self.decision_id,
                                                "source": "coordinator"}, **fields)}

    def _level(self, fid, job):
        if job["kind"] == "accent":
            return self.settings["accent_level"], None
        if job["kind"] != "task":
            return 0.0, None
        profile = self.fixtures[fid]["profile"]
        full = spot_light.spot_value(self.fixtures[fid]["aim_origin_m"], job["target"],
                                     lumens=profile.get("lumens", spot_light.ASSUMED_SPOT_LUMENS),
                                     fwhm_deg=profile.get("beam_fwhm_deg", 20.0))
        level, value, short = spot_light.level_for(full, SPOT_TARGET)
        return level, {"value": round(value, 1), "shortfall": round(short, 1), "note": "modeled, assumed lumens"}

    def _evaluate(self, t, tick):
        people = self._person_rows(t)
        act, key = self._key(t, people)
        out = []
        plan = self.planner.plan(t, key, self.learner, self.ha["on"])
        if plan:
            out.append({"kind": "bulb_plan", "data": plan})

        fixtures = {fid: dict(row, range=row["profile"]["range"], usable=_usable(row, t, self.desk_until))
                    for fid, row in self.fixtures.items()}
        current = {fid: s["job"] for fid, s in self.spots.items() if s["job"]}
        art = share.art_from_labels(self.labels) if self.ha["occupied"] else []
        seated_heads = [p["head"] for p in people if p["seated"] and p["head"]]
        visible = [p for p in people if p["seated"] or self.settings["pre_aim"]]
        jobs, notes = share.allocate(fixtures, visible, art, current, self.settings["pre_aim"], seated_heads)
        live = self.ha["on"] and not self.stopped
        changes = {}
        for fid in self.fixtures:
            change = self._spot_change(fid, jobs.get(fid), people, live)
            if change:
                changes[fid] = change
        sig = None if live else repr(sorted((fid, c["job"], c["level"], c["joints"]) for fid, c in changes.items()))
        if live:
            self.shadow_sig = None
        if (changes or key != self.last_key) and (live or sig != self.shadow_sig or key != self.last_key):
            self.last_key, self.shadow_sig = key, sig
            out.append(self._decision(t, act, key, people, jobs, notes, changes, live))
            if live:
                out += [i for change in changes.values() for i in change.pop("intents")]
        if tick and live:
            for fid, spot in self.spots.items():
                if spot["on"]:
                    out.append(self._intent("keep_lit", fid))
            if any(s["on"] for s in self.spots.values()):
                out.append({"kind": "kept", "data": {"decision_id": self.decision_id, "at": t}})
        return out

    def _spot_change(self, fid, job, people, live):
        """The change to one fixture, with the intents that make it, or None. Updates self.spots when live."""
        spot = self.spots[fid]
        same = (job or None) and spot["job"] and job["kind"] == spot["job"]["kind"] and job["who"] == spot["job"]["who"]
        new_joints = tuple(job["joints"]) if job else spot["joints"]
        level, predicted = self._level(fid, job) if job else (0.0, None)
        on = level > 0
        aim_moved = job is not None and (spot["joints"] is None or max(abs(a - b) for a, b in zip(new_joints, spot["joints"])) > 0.05)
        if same and not aim_moved and abs(level - spot["level"]) < 1e-6:
            return None
        if not job and not spot["on"] and spot["job"] is None:
            return None
        profile, row = self.fixtures[fid]["profile"], self.fixtures[fid]
        move = {"kind": "none", "predicted_s": 0.0}
        intents = []
        person = next((p for p in people if job and p["track_id"] == job["who"]), None)
        if aim_moved and spot["joints"] is not None:
            delta = joints.route(spot["joints"], new_joints)
            move["predicted_s"] = round(scurve.move_time(delta, profile["axes"]), 3)
            lit_both = spot["on"] and on
            style = self.settings["move_style"] if profile.get("lit_motion") else "dark"
            if lit_both and style != "dark" and person and person["head"]:
                ok, closest = eye.path_clear(row["aim_origin_m"], row["horizontal_world_reference"], spot["joints"],
                                             new_joints, math.dist(row["aim_origin_m"], job["target"]), person["head"])
                move["path_check"] = {"ok": ok, "min_clear_m": round(closest, 3)}
                if not ok:
                    style = "dark"
            move["kind"] = style if lit_both else "dark"
        elif aim_moved:
            move["kind"] = "dark" if not on else "first"
        if job is not None or spot["on"]:
            level_now = level if on else 0.0
            lit_during = move["kind"] in ("lit", "dim")
            fields = {"pan_cdeg": joints.to_cdeg(new_joints[0]), "tilt_cdeg": joints.to_cdeg(new_joints[1]),
                      "velocity_cdeg_s": [0, 0], "level": round(level_now, 4), "cct_k": CCT_K,
                      "lit_during_move": lit_during, "valid_for_ms": COMMAND_VALID_MS,
                      "fade_s": self.settings["fade_in_s"] if on else self.settings["fade_out_s"]}
            if move["kind"] == "dim":
                fields["level_during_move"] = self.settings["move_dim_level"]
            evidence = person.get("evidence") if (person and job and job["kind"] == "task") else None
            fields.update(predicted_s=move["predicted_s"], profile_sha256=profile_sha256(profile),
                          pose_age_s=round(person["pose_age_s"], 3) if evidence else None,
                          activity_valid_until=evidence and evidence["activity_valid_until"])  # for fixture_calls.jsonl
            intents.append(self._intent("aim_move", fid, **fields))
        record = {"job": job and {"kind": job["kind"], "who": job["who"]}, "on": on, "level": round(level, 4),
                  "cct_k": CCT_K if on else None, "target_m": job and [round(c, 3) for c in job["target"]],
                  "joints": {"frame": joints.FRAME, "pan_cdeg": joints.to_cdeg(new_joints[0]),
                             "tilt_cdeg": joints.to_cdeg(new_joints[1])} if new_joints else None,
                  "change": "aim" if aim_moved else ("off" if not on else "level"), "move": move,
                  "predicted": predicted, "score": job and job.get("score"), "real": bool(row.get("real")),
                  "profile": profile.get("label"), "intents": intents}
        if live:
            spot.update(job=job and {"kind": job["kind"], "who": job["who"]}, on=on, level=level,
                        joints=new_joints, target=job and tuple(job["target"]))
        return record

    def _decision(self, t, act, key, people, jobs, notes, changes, live):
        self.made += 1
        self.decision_id = "spot-%d-%d" % (round(t * 1000), self.made)
        for change in changes.values():
            for intent in change["intents"]:
                intent["data"]["decision_id"] = self.decision_id
        spots = {fid: {k: v for k, v in change.items() if k != "intents"} for fid, change in changes.items()}
        why = []
        for fid, change in sorted(spots.items()):
            job = change["job"]
            why.append("%s %s" % (fid, "rests" if not job else "%s for %s, level %.2f" % (job["kind"], job["who"], change["level"])))
        refused = sorted({"%s %s" % (n["fixture"], n["reason"]) for n in notes if n.get("fixture")})
        return {"kind": "decision", "data": {
            "decision_id": self.decision_id, "version": DECISION_VERSION, "ts": t, "policy": POLICY,
            "actuator_domain": DOMAIN, "actuation": "live" if live else "shadow",
            "situation": {"activity": act, "key": key, "people": len(people)},
            "jobs": {fid: {"kind": j["kind"], "who": j["who"], "score": j["score"]} for fid, j in sorted(jobs.items())},
            "spots": spots, "notes": notes,
            "evidence": {p["track_id"]: p["evidence"] for p in people if p["task"]},
            "why": "%s: %s%s" % (key, "; ".join(why) or "no change to the spots",
                                 ("; refused: " + ", ".join(refused)) if refused else "")}}


def make_policy(config=None):
    return Coordinator(config or {})
