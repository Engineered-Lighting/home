"""Deterministic tests of the host tap's aggregation and attribution, on synthetic event streams. No network."""
import contextlib
import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import body_continuity_tap as tap  # noqa: E402

SERVER = 1000.0   # body_clock server_ts, read by the tap at t = 0


def ready(t, seq, *, lease_left=1.0, track="run-1-e1-7"):
    """A ready the tap read at t (s) with lease_left s of its 2 s lease remaining at the producer."""
    capture = SERVER + t - (2.0 - lease_left)
    return tap.body_row({"status": "ready", "seq": seq, "run_id": "run-1", "track_id": track, "capture_ts": capture,
                         "expires_ts": capture + 2.0, "points": {"count": 5, "position_mm": "AAAA"}}, t, 1e12 + t * 1000)


def clear(t, seq, reason):
    return tap.body_row({"status": "clear", "seq": seq, "run_id": "run-1", "reason": reason}, t, 1e12 + t * 1000)


def person(track="run-1-e1-7", state="confirmed", xy=(1.0, 2.0), obs=("o1",), association="confident", revision=1):
    return {"track_id": track, "state": state, "xy_m": list(xy), "current_obs_ids": list(obs),
            "association_status": association, "association_revision": revision}


def people(t, tracks, status="fresh", qwen="off", diag=None):
    health = {"qwen": {"state": qwen, "asks_per_min": 4}, "fps_in": 2.3}
    if diag is not None:
        health["body"] = diag
    return tap.people_row({"perception_status": status, "presence": "present" if tracks else "empty", "tracks": tracks,
                           "health": health}, t, 1e12 + t * 1000)


def every(start, stop, step, make):
    out, t = [], start
    while t < stop:
        out.append(make(round(t, 3)))
        t += step
    return out


class HashAndParsing(unittest.TestCase):
    def test_hash_matches_the_browser_collector(self):
        # Vectors from body-continuity-core.mjs's h32, so the receipts join on run and track hashes.
        self.assertEqual(tap.h32("run-1"), "3c9d57fc")
        self.assertEqual(tap.h32("2a-e1-7"), "67b0bed1")
        self.assertEqual(tap.h32("ü€"), "a44d0435")
        self.assertIsNone(tap.h32(None))

    def test_sse_parser_skips_comments_and_joins_data_lines(self):
        stream = io.BytesIO(b": heartbeat\n\nevent: body_clock\ndata: {\"server_ts\": 5}\n\nevent: people\ndata: {\"a\":\ndata: 1}\n\n"
                            b"retry: 2000\n\ndata:{\"x\":2}\n\n")
        events = list(tap.sse_events(stream.readline))
        self.assertEqual(events, [("body_clock", '{"server_ts": 5}'), ("people", '{"a":\n1}'), ("message", '{"x":2}')])

    def test_rows_keep_no_points_or_positions(self):
        b = ready(1.0, 1)
        p = people(1.0, [person(xy=(1.25, 2.5)), person("run-1-e1-9", "coasting", xy=(3.25, 2.5), obs=())])
        text = json.dumps([b, {k: v for k, v in p.items() if k != "bands"}, list(p["bands"].values())])
        for forbidden in ("position_mm", "AAAA", "xy_m", "1.25", "3.25", "points"):
            self.assertNotIn(forbidden, text)
        self.assertEqual(set(p["bands"].values()), {">=1.5"})
        self.assertEqual(p["tracks"][1][1:4], ("coasting", "confident", False))

    def test_camera_status_is_kept_only_as_a_short_phrase(self):
        row = tap.people_row({"perception_status": "degraded", "tracks": [], "camera": {"status": "ok", "why": "too dark"}}, 0, 0)
        self.assertEqual(row["camera"], ("ok", "too dark"))
        odd = tap.people_row({"camera": {"status": "ok", "why": "http://10.0.0.5/frame.jpg"}}, 0, 0)
        self.assertEqual(odd["camera"], ("ok", "other"))

    def test_distance_bands(self):
        self.assertEqual([tap.band(d) for d in (0.2, 0.6, 0.99, 1.2, 1.5, 4)],
                         ["<0.6", "0.6-1.0", "0.6-1.0", "1.0-1.5", ">=1.5", ">=1.5"])

    def test_health_body_counters_flatten_without_stamps(self):
        flat = tap.flatten_counts({"run_id": "r", "track_epoch": 2, "seq": 9, "ready_ts": 5.0, "sent": 3, "why": "empty",
                                   "busy": {"stills_first": 1, "slot": 0}, "held": {"pose_unavailable": 2}, "stopped": False})
        self.assertEqual(flat, {"sent": 3, "busy.stills_first": 1, "busy.slot": 0, "held.pose_unavailable": 2})


class Coverage(unittest.TestCase):
    def test_lease_gap_with_processed_frames_is_a_body_skip(self):
        body = [ready(0.0, 1), ready(1.8, 2)]           # the first lease ends at 1.0 s
        rows = every(0.0, 3.0, 0.4, lambda t: people(t, [person()]))
        cov = tap.coverage(body, rows, (SERVER, 0.0), 3.0, lambda t: t * 1000)
        self.assertEqual(len(cov["gaps"]), 2)
        gap = cov["gaps"][0]
        self.assertEqual((gap["cause"], gap["subcause"]), ("lease_elapsed", "body_skip"))
        self.assertAlmostEqual(gap["start_ms"], 1000.0, delta=1)
        self.assertAlmostEqual(gap["end_ms"], 1800.0, delta=1)
        self.assertEqual(cov["gaps"][1]["start_ms"], 2800.0)

    def test_lease_gap_without_frames_is_a_frame_gap(self):
        body = [ready(0.0, 1), ready(2.5, 2)]
        rows = [people(0.0, [person()]), people(0.9, [person()]), people(2.4, [person()])]
        gap = tap.coverage(body, rows, (SERVER, 0.0), 2.6, lambda t: t * 1000)["gaps"][0]
        self.assertEqual(gap["subcause"], "frame_gap")
        self.assertEqual(gap["eligible_frames_without_ready"], 0)   # the 2.4 s frame made the 2.5 s ready

    def test_health_body_counters_name_the_skip(self):
        body = [ready(0.0, 1), ready(1.8, 2)]
        diag = lambda n: {"sent": 1, "busy": {"stills_first": n, "own_job": 0, "slot": 0}, "held": {"pose_unavailable": 0}}
        rows = [people(0.0, [person()], diag=diag(0)), people(0.5, [person()], diag=diag(1)), people(1.2, [person()], diag=diag(1)),
                people(1.6, [person()], qwen="asking", diag=diag(1))]
        gap = tap.coverage(body, rows, (SERVER, 0.0), 1.9, lambda t: t * 1000)["gaps"][0]
        self.assertEqual(gap["subcause"], "stills_precedence")
        self.assertEqual(gap["diag_delta"], {"busy.stills_first": 1})
        self.assertEqual(gap["qwen_asking_events"], 1)

    def test_a_clear_ends_coverage_with_its_reason(self):
        body = [ready(0.0, 1), clear(0.5, 2, "ambiguous"), ready(1.2, 3)]
        rows = every(0.0, 1.5, 0.3, lambda t: people(t, [person()]))
        gaps = tap.coverage(body, rows, (SERVER, 0.0), 1.5, lambda t: t * 1000)["gaps"]
        self.assertEqual(gaps[0]["cause"], "producer_ambiguous")
        self.assertAlmostEqual(gaps[0]["duration_ms"], 700.0, delta=1)

    def test_untracked_time_is_not_a_gap(self):
        body = [ready(0.0, 1)]
        rows = [people(0.0, [person()]), people(0.5, []), people(3.0, [], status="stale")]
        self.assertEqual(tap.coverage(body, rows, (SERVER, 0.0), 3.5, lambda t: t * 1000)["gaps"], [])

    def test_a_perception_stall_keeps_the_person_and_names_its_gap(self):
        # perceive.py idle(): no frame within 1.5 s of the last capture, so a 'stale' clear and a 'stale' people event
        # with no tracks. Frames come back at 2.6 s and the next ready at 2.9 s.
        body = [ready(0.0, 1, lease_left=1.8), clear(1.5, 2, "stale"), ready(2.9, 3)]
        rows = [people(0.0, [person()]), people(0.4, [person()]), people(1.5, [], status="stale"), people(2.0, [], status="stale"),
                people(2.6, [person()]), people(3.0, [person()])]
        cov = tap.coverage(body, rows, (SERVER, 0.0), 3.2, lambda t: t * 1000)
        self.assertEqual([(g["cause"], g["subcause"]) for g in cov["gaps"]], [("perception_stall", "stale")])
        self.assertAlmostEqual(cov["gaps"][0]["start_ms"], 1500.0, delta=1)
        self.assertAlmostEqual(cov["gaps"][0]["end_ms"], 2900.0, delta=1)
        self.assertEqual(cov["blank_ms_by_cause"], {"perception_stall:stale": 1400.0})
        self.assertAlmostEqual(cov["stall_ms"], 1100.0, delta=1)
        self.assertAlmostEqual(cov["tracked_ms"], 3200.0, delta=1)

    def test_a_stall_ends_at_the_epoch_gap_and_silence_counts(self):
        body = [ready(0.0, 1), clear(1.5, 2, "unavailable"), ready(6.4, 3)]
        rows = [people(0.0, [person()]), people(0.4, [person()]), people(1.5, [], status="offline")] + \
            every(2.0, 5.5, 0.5, lambda t: people(t, [], status="offline")) + [people(6.0, [person()])]
        gaps = tap.coverage(body, rows, (SERVER, 0.0), 6.5, lambda t: t * 1000)["gaps"]
        self.assertEqual((gaps[0]["cause"], gaps[0]["subcause"]), ("perception_stall", "offline"))
        self.assertAlmostEqual(gaps[0]["end_ms"], 3400.0, delta=1)   # 3.0 s after the last fresh tracked event
        silent = [people(0.0, [person()]), people(0.4, [person()]), people(4.0, [person()])]
        gaps = tap.coverage([ready(0.0, 1), ready(4.1, 2)], silent, (SERVER, 0.0), 4.2, lambda t: t * 1000)["gaps"]
        self.assertEqual([(g["cause"], g["subcause"]) for g in gaps][0], ("perception_stall", "silent"))
        self.assertAlmostEqual(gaps[0]["start_ms"], 1000.0, delta=1)   # the lease that ran out before it is the stall's too
        self.assertAlmostEqual(gaps[0]["end_ms"], 3400.0, delta=1)

    def test_a_clear_before_the_stall_keeps_its_part(self):
        body = [ready(0.0, 1, lease_left=1.8), clear(0.5, 2, "ambiguous"), ready(2.5, 3)]
        rows = [people(t, [person()]) for t in (0.0, 0.3, 0.6, 0.9)] + \
            [people(1.2, [], status="stale"), people(1.6, [], status="stale"), people(2.0, [], status="stale"), people(2.2, [person()])]
        gaps = tap.coverage(body, rows, (SERVER, 0.0), 2.8, lambda t: t * 1000)["gaps"]
        self.assertEqual([(g["cause"], g["subcause"], g["start_ms"], g["end_ms"]) for g in gaps],
                         [("producer_ambiguous", None, 500.0, 1200.0), ("perception_stall", "stale", 1200.0, 2500.0)])

    def test_subtract_sweeps(self):
        self.assertEqual(tap.subtract([(0, 10), (12, 20)], [(2, 3), (9, 13), (15, 30)]), [(0, 2), (3, 9), (13, 15)])
        self.assertEqual(tap.subtract([(0, 1)], []), [(0, 1)])


class Ambiguity(unittest.TestCase):
    def episode_stream(self, extra):
        body = [ready(0.0, 1), clear(1.0, 2, "ambiguous"), ready(3.0, 3)]
        rows = every(0.0, 3.5, 0.5, lambda t: people(t, [person(xy=(1.0, 1.0))] + ([extra] if 1.0 <= t < 3.0 else [])))
        return body, rows

    def test_far_coasting_track_without_observation(self):
        body, rows = self.episode_stream(person("run-1-e1-9", "coasting", xy=(3.0, 1.0), obs=(), association="ambiguous"))
        out = tap.ambiguous_episodes(body, rows, 3.5, lambda t: t * 1000)
        self.assertEqual(out["count"], 1)
        ep = out["episodes"][0]
        self.assertEqual(ep["duration_ms"], 2000.0)
        self.assertEqual(ep["extra_track"], {"class": "far_coasting_no_obs", "states": ["coasting"], "has_current_obs": False,
                                             "nearest_band": ">=1.5", "max_tracks": 2})
        self.assertEqual(out["rule"]["verdict"], "insufficient_episodes")

    def test_near_or_observed_second_track(self):
        body, rows = self.episode_stream(person("run-1-e1-9", "coasting", xy=(1.3, 1.0), obs=()))
        self.assertEqual(tap.ambiguous_episodes(body, rows, 3.5, lambda t: t)["episodes"][0]["extra_track"]["class"],
                         "near_coasting_no_obs")
        body, rows = self.episode_stream(person("run-1-e1-9", "confirmed", xy=(3.0, 1.0), obs=("o2",)))
        self.assertEqual(tap.ambiguous_episodes(body, rows, 3.5, lambda t: t)["episodes"][0]["extra_track"]["class"],
                         "second_observed_track")

    def test_single_track_ambiguity(self):
        body = [ready(0.0, 1), clear(1.0, 2, "ambiguous"), ready(1.6, 3)]
        rows = every(0.0, 2.0, 0.3, lambda t: people(t, [person(association="ambiguous")]))
        self.assertEqual(tap.ambiguous_episodes(body, rows, 2.0, lambda t: t)["episodes"][0]["extra_track"]["class"], "single_track")

    def test_rule_needs_five_episodes_and_an_eighty_percent_share(self):
        body, rows, seq = [], [], 0
        for k in range(5):
            base = k * 4.0
            seq += 1
            body.append(ready(base, seq))
            seq += 1
            body.append(clear(base + 1.0, seq, "ambiguous"))
            seq += 1
            body.append(ready(base + 3.0, seq))
            rows += [people(base + d, [person(xy=(1, 1)), person("ghost", "coasting", xy=(3, 1), obs=())]) for d in (1.0, 2.0)]
        out = tap.ambiguous_episodes(body, sorted(rows, key=lambda r: r["t"]), 20.0, lambda t: t)
        self.assertEqual(out["rule"]["verdict"], "relax_two_track_rule_candidate")
        self.assertEqual(out["rule"]["far_coasting_no_obs_share"], 1.0)


class Joins(unittest.TestCase):
    def test_frame_rate_binds_when_ready_gaps_follow_frame_gaps(self):
        body = [ready(t, i + 1) for i, t in enumerate(every(0.0, 10.0, 0.8, lambda t: t))]
        rows = every(0.0, 10.0, 0.8, lambda t: people(t, [person()]))
        out = tap.frame_vs_ready(body, rows)
        self.assertEqual(out["verdict"], "frame_rate_binding")
        self.assertEqual(out["frames_per_ready_gap"]["p50"], 0)

    def test_body_skips_dominate_when_frames_arrive_between_readies(self):
        body = [ready(t, i + 1) for i, t in enumerate(every(0.0, 10.0, 1.6, lambda t: t))]
        rows = every(0.0, 10.0, 0.4, lambda t: people(t, [person()], qwen="asking"))
        out = tap.frame_vs_ready(body, rows)
        self.assertEqual(out["verdict"], "body_skips_dominate")
        q = tap.qwen_overlap(body, rows)
        self.assertEqual(q["ready_gaps_over_1s"], q["ready_gaps_over_1s_with_qwen_asking"])
        self.assertEqual(q["asking_fraction_of_frames"], 1.0)


class Analyse(unittest.TestCase):
    def test_full_analysis_is_json_with_join_fields_and_no_media(self):
        body = [ready(0.0, 1), clear(1.0, 2, "scope_changed"), ready(1.02, 3), ready(1.9, 4, track="run-1-e1-7"),
                clear(2.5, 5, "ambiguous"), ready(3.2, 6)]
        rows = every(0.0, 4.0, 0.5, lambda t: people(t, [person(revision=1 if t < 2 else 2)]))
        out = tap.analyse(body, rows, (SERVER, 0.0), 4.0, 1e12, errors=[["stream", "eof", 3.9]], opened={"body-v2": 0.01})
        text = json.dumps(out)
        for forbidden in ("position_mm", "xy_m", "points"):
            self.assertNotIn(forbidden, text)
        self.assertEqual(out["schema"], "den-body-continuity-tap-2")
        self.assertEqual(out["body"]["clear_reasons"], {"ambiguous": 1, "scope_changed": 1})
        self.assertEqual(out["body"]["scope_changed"]["same_track"], 1)
        self.assertEqual(out["people"]["same_track_revision_changes"], 1)
        self.assertEqual(out["people"]["camera"], {"unknown": 8})
        self.assertEqual(out["timeline"]["body"][0], [1e12, tap.h32("run-1"), 1, "ready", None, tap.h32("run-1-e1-7")])
        self.assertEqual(out["window"]["end_wall_ms"], 1e12 + 4000)
        self.assertIn("gaps", out["coverage"])
        self.assertNotIn("gaps", out["coverage_with_0_5s_transport_margin"])

    def test_arguments_are_bounded(self):
        for argv in (["--seconds", "3601"], ["--seconds", "1"], ["--home", "../den"], ["--port", "0"]):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                tap.main(argv)


if __name__ == "__main__":
    unittest.main()
