"""Tests for the ask check (inference plan, step 4), on made-up stream events: no camera, no Home, no LA.

    python -B -m unittest discover -s tools\\den-live -p "test_ask_check.py"
"""
import io
import json
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ask_check  # noqa: E402


def event(state="off", why="Frigate sees nobody", answer=None, reply_s=None):
    tracks = [{"track_id": "e1-trk-1", "state": "confirmed", "xy_m": [1, 1]}]
    if answer:
        word, at = answer
        tracks[0]["activity"] = {"word": word, "close_up": word in ("reading", "hobby"), "at": at}
    return {"schema": "den-live-people-1", "tracks": tracks,
            "health": {"qwen": {"state": state, "why": why, "reply_s_median": reply_s}}}


class PhaseTest(unittest.TestCase):
    def test_the_hops_and_the_answers_after_a_cue(self):
        samples = [(100.5, event("off", "Frigate sees nobody")),
                   (101.0, event("off", "nobody seated")),
                   (103.0, event("asking", None)),
                   (103.8, event("asking", None, ("reading", 5003.7))),
                   (104.5, event("asking", None, ("reading", 5003.7))),   # the same answer, shown again
                   (105.8, event("asking", None, ("unclear", 5005.7))),
                   (107.8, event("asking", None, ("reading", 5007.7), 0.61))]
        got = ask_check.phase(samples, 100.0)
        self.assertEqual((got["frigate_s"], got["asking_s"], got["first_answer_s"], got["first_close_up_s"]), (1.0, 3.0, 3.8, 3.8))
        self.assertEqual((got["answers"], got["close_up"]), (3, 2))
        self.assertEqual(ask_check.words(got["words"]), "reading 2, unclear 1")

    def test_an_answer_shown_before_the_cue_isnt_counted_again(self):
        earlier = [(99.0, event("asking", None, ("reading", 4999.0)))]
        samples = [(100.5, event("asking", None, ("reading", 4999.0))), (102.5, event("asking", None, ("phone_use", 5002.0)))]
        got = ask_check.phase(samples, 100.0, earlier=earlier)
        self.assertEqual((got["answers"], got["close_up"]), (1, 0))

    def test_when_the_asks_stopped(self):
        samples = [(200.2, event("asking", None)), (200.6, event("off", "nobody seated"))]
        self.assertEqual(ask_check.stopped_s(samples, 200.0), 0.6)
        self.assertIsNone(ask_check.stopped_s([(200.2, event("asking", None))], 200.0))
        flicker = [(200.2, event("off", "nobody seated")), (200.4, event("asking", None)), (201.4, event("off", "nobody seated"))]
        self.assertEqual(ask_check.stopped_s(flicker, 200.0), 1.4, "one quiet frame before more asking isn't the stop")
        self.assertEqual(ask_check.stopped_s([(200.2, event("off", "nobody seated"))], 200.0), 0.0)

    def test_an_empty_leave_window_says_the_stream_stopped(self):
        read = ask_check.phase([], 100.0)
        out = ask_check.lines(read, read, None, None, False, 0.0, {"read": 3, "phone": 2, "leave": 0, "reconnects": 1})
        self.assertIn("no events came after the cue", out[4])
        self.assertIn("3, 2 and 0 events in the three parts, 1 reconnects", out[-1])

    def test_the_lines_are_words_and_numbers_only(self):
        read = ask_check.phase([(103.8, event("asking", None, ("reading", 1.0)))], 100.0)
        phone = ask_check.phase([(203.8, event("asking", None, ("phone_use", 2.0)))], 200.0)
        out = ask_check.lines(read, phone, 0.6, 0.62, True, 0.0)
        self.assertIn("Frigate on before the cue", out[1])
        self.assertIn("close-up on 1 of 1 answers (reading 1)", out[2])
        self.assertIn("close-up on 0 of 1 answers (phone_use 1)", out[3])
        self.assertIn("asks stopped 0.6 s after the cue", out[4])
        self.assertIn("median 0.62 s", out[5])


class MainTest(unittest.TestCase):
    def test_refused_without_a_console_or_an_https_home(self):
        out = io.StringIO()
        sys.stdout, saved = out, sys.stdout
        try:
            self.assertEqual(ask_check.main(["--home", "https://x"], console=lambda: False), 2)
            self.assertEqual(ask_check.main(["--home", "http://x"], console=lambda: True, speak=lambda s: None), 2)
        finally:
            sys.stdout = saved
        self.assertIn("refused, no console", out.getvalue())
        self.assertIn("give your Home's address", out.getvalue())


if __name__ == "__main__":
    unittest.main()
