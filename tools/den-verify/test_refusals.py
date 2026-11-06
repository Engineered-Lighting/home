"""What verify.py says about a file it cannot read: one sentence and exit 2, never a traceback.

PowerShell 5.1, the owner's shell, writes `>` as UTF-16LE with a byte-order mark and CRLF, and its Out-File -Encoding utf8 writes
a UTF-8 byte-order mark and CRLF, so these use those bytes, and real powershell.exe where there is one. From the worktree root:

    python -B -m unittest discover -s tools/den-verify -p "test_*.py"

Everything is synthetic. Nothing reaches a host or a light.
"""
import copy
import json
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import method  # noqa: E402
import synthetic as syn  # noqa: E402
import verify as cli  # noqa: E402
from records import write_jsonl  # noqa: E402
from test_verify import Quiet  # noqa: E402

POWERSHELL = shutil.which("powershell.exe")
T = syn.T0
GOLD = [{"episode_id": "g1", "track_id": "A", "start": T + 4, "end": T + 20,
         "R_eval": {"surface_id": "counter-1", "placement_sd": 2.0, "source": "gold_mark"}}]
OCCUPANCY = [{"start": T + 1, "end": T + 3, "people": [{"track_id": "A", "xy": [1.0, 1.0]}]}]


def sentence(path):
    return "%s is UTF-16 (PowerShell 5.1's > writes that): save it as UTF-8" % path


def ps51(text):
    """What PowerShell 5.1's `>` writes for this text: a byte-order mark, UTF-16LE and CRLF."""
    return b"\xff\xfe" + text.replace("\n", "\r\n").encode("utf-16-le")


def out_file(text):
    """What its Out-File -Encoding utf8 and Set-Content -Encoding utf8 write: a byte-order mark, UTF-8 and CRLF."""
    return b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode("utf-8")


def tool(*argv, cwd=None):
    """verify.py as the owner runs it: (exit code, what it printed)."""
    done = subprocess.run([sys.executable, "-B", str(HERE / "verify.py"), *map(str, argv)], capture_output=True, text=True, cwd=cwd, timeout=120)
    return done.returncode, done.stdout + done.stderr


def lines(rows):
    return "".join(json.dumps(row) + "\n" for row in rows)


class Case(unittest.TestCase):
    """The walkthrough's evening with every optional input given, each file in plain UTF-8, one file changed at a time."""
    FLAGS = ("decisions", "light-states", "corrections", "occupancy", "gold", "model", "surfaces")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        decisions, frames, corrections = syn.v11_evening()
        self.good = {"decisions": lines(decisions), "light-states": lines(frames), "corrections": lines(corrections),
                     "occupancy": lines(OCCUPANCY), "gold": lines(GOLD), "model": json.dumps(syn.v11_model()),
                     "surfaces": json.dumps({"counter-1": ["counter-1"], "armchair-book": ["armchair-book"]})}
        self.records = {"decisions": decisions, "light-states": frames, "corrections": corrections, "occupancy": OCCUPANCY, "gold": GOLD}
        self.paths = {flag: self.write(flag + ".txt", text) for flag, text in self.good.items()}

    def write(self, name, raw):
        path = self.tmp / name
        path.write_bytes(raw if isinstance(raw, bytes) else raw.encode("utf-8"))
        return path

    def argv(self, out="out", **given):
        """A preview run, with some flags pointing at other files: given is {flag with _ for -: path}."""
        paths = {**self.paths, **{flag.replace("_", "-"): path for flag, path in given.items()}}
        return ["run", *[word for flag, path in paths.items() for word in ("--" + flag, str(path))],
                "--utc-offset-h", "-7", "--preview", "--out", str(self.tmp / out)]

    def main(self, argv):
        """(exit code, what it printed), in this process, where a traceback would be this test's error."""
        with Quiet() as q:
            code = cli.main(argv)
        return code, q.buf.getvalue()

    def assert_refused(self, code, printed, *words):
        self.assertEqual(code, 2, printed)
        self.assertNotIn("Traceback", printed)
        self.assertNotIn("stopped on an input", printed, "the last resort: a check should have named this")
        for word in words:
            self.assertIn(word, printed)
        self.assertLessEqual(len(printed.strip().splitlines()), 2, printed)

    def assert_nothing_written(self, out="out"):
        self.assertFalse((self.tmp / out).exists())


class Utf16Test(Case):
    def test_every_input_is_exit_2_with_the_one_sentence(self):
        for flag in self.FLAGS:
            with self.subTest(flag):
                path = self.write(flag + "-16.txt", ps51(self.good[flag]))
                code, printed = tool(*self.argv(**{flag.replace("-", "_"): path}))
                self.assertEqual(code, 2, printed)
                self.assertEqual(printed.strip(), sentence(path))
                self.assert_nothing_written()

    def test_the_sentence_names_the_path_as_it_was_typed(self):
        (self.tmp / "in").mkdir()
        for flag in self.FLAGS:
            typed = "in/%s-16.txt" % flag
            (self.tmp / typed).write_bytes(ps51(self.good[flag]))
            with self.subTest(flag):
                code, printed = tool(*self.argv(**{flag.replace("-", "_"): typed}), cwd=self.tmp)
                self.assertEqual(code, 2, printed)
                self.assertEqual(printed.strip(), sentence(typed))

    def test_no_byte_order_mark_gets_the_same_sentence(self):
        for flag in self.FLAGS:
            with self.subTest(flag):
                path = self.write(flag + "-le.txt", self.good[flag].encode("utf-16-le"))
                self.assertEqual(self.main(self.argv(**{flag.replace("-", "_"): path})), (2, sentence(path) + "\n"))

    def test_big_endian_gets_the_same_sentence(self):
        path = self.write("be.txt", self.good["decisions"].encode("utf-16"))
        self.assertEqual(self.main(self.argv(decisions=path))[1], sentence(path) + "\n")
        path = self.write("be2.txt", b"\xfe\xff" + self.good["decisions"].encode("utf-16-be"))
        self.assertEqual(self.main(self.argv(decisions=path))[1], sentence(path) + "\n")

    def test_the_method_file_too(self):
        text = method.lf_text(HERE / "VERIFY-V1.md")
        path = self.write("VERIFY-V1.md", ps51(text))
        for argv in (["--method", str(path), "slots"], ["--method", str(path), *self.argv()]):
            code, printed = tool(*argv)
            self.assertEqual(code, 2, printed)
            self.assertEqual(printed.strip(), sentence(path))

    def test_a_bom_and_crlf_read_as_the_same_file(self):
        code, _ = self.main(self.argv("plain"))
        self.assertEqual(code, 0)
        plain = json.loads((self.tmp / "plain" / "report-preview.json").read_text("utf-8"))
        for flag in self.FLAGS:
            with self.subTest(flag):
                path = self.write(flag + "-bom.txt", out_file(self.good[flag]))
                code, printed = self.main(self.argv(flag, **{flag.replace("-", "_"): path}))
                self.assertEqual(code, 0, printed)
                report = json.loads((self.tmp / flag / "report-preview.json").read_text("utf-8"))
                for key in ("hit_rate", "decisions", "points", "frames", "gold_need_time"):
                    self.assertEqual(report[key], plain[key], key)

    def test_the_method_file_reads_with_a_bom_and_crlf(self):
        path = self.write("VERIFY-V1.md", out_file(method.lf_text(HERE / "VERIFY-V1.md")))
        plain, bom = method.load(), method.load(path)
        self.assertEqual((plain.values, plain.open, plain.sha256), (bom.values, bom.open, bom.sha256))

    @unittest.skipUnless(POWERSHELL, "no powershell.exe here: the bytes above are what it writes")
    def test_real_powershell_output_and_the_readme_repair_line(self):
        """Real `>` output is refused by name, and the repair README gives is read."""
        script = self.tmp / "try.ps1"
        lines_of = ["Set-Location -LiteralPath '%s'" % self.tmp]
        for flag in self.FLAGS:
            lines_of.append("Get-Content -LiteralPath '%s' > '%s-16.txt'" % (self.paths[flag], flag))
            lines_of.append("Get-Content '%s-16.txt' | Set-Content -Encoding utf8 '%s-8.txt'" % (flag, flag))
        script.write_text("\r\n".join(lines_of) + "\r\n", encoding="utf-8")
        subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)], check=True, timeout=120,
                       capture_output=True)
        for flag in self.FLAGS:
            with self.subTest(flag):
                wide = self.tmp / (flag + "-16.txt")
                code, printed = tool(*self.argv(**{flag.replace("-", "_"): wide}))
                self.assertEqual(code, 2, printed)
                self.assertEqual(printed.strip(), sentence(wide))
                repaired = self.tmp / (flag + "-8.txt")
                code, printed = tool(*self.argv(flag, **{flag.replace("-", "_"): repaired}))
                self.assertEqual(code, 0, printed)
        self.assertIn("Get-Content bad.jsonl | Set-Content -Encoding utf8 good.jsonl", (HERE / "README.md").read_text("utf-8"))


class UnreadableTest(Case):
    def test_a_missing_input_is_a_sentence_naming_it(self):
        for flag in self.FLAGS:
            with self.subTest(flag):
                path = self.tmp / ("no-such-" + flag)
                self.assert_refused(*self.main(self.argv(**{flag.replace("-", "_"): path})), str(path))
                self.assert_nothing_written()

    def test_a_folder_is_a_sentence_naming_it(self):
        folder = self.tmp / "a-folder"
        folder.mkdir()
        for flag in self.FLAGS:
            with self.subTest(flag):
                self.assert_refused(*self.main(self.argv(**{flag.replace("-", "_"): folder})), str(folder))

    def test_a_missing_method_file(self):
        path = self.tmp / "no-method.md"
        for argv in (["--method", str(path), "slots"], ["--method", str(path), *self.argv()]):
            self.assert_refused(*self.main(argv), str(path))

    def test_a_method_file_with_no_block_or_a_bad_line(self):
        for name, text, words in (("none", "no block here\n", "must hold exactly one"), ("bad", "```verify-v1\ntolerance = high\n```\n", "bad method line")):
            path = self.write(name + ".md", text)
            self.assert_refused(*self.main(["--method", str(path), "slots"]), words)

    def test_a_file_that_is_not_utf8(self):
        for flag in self.FLAGS:
            with self.subTest(flag):
                path = self.write(flag + "-ansi.txt", self.good[flag].encode("utf-8") + "\n{\"caf\xe9\": 1}\n".encode("cp1252"))
                self.assert_refused(*self.main(self.argv(**{flag.replace("-", "_"): path})), str(path), "not UTF-8")

    def test_an_output_folder_that_cannot_be_made(self):
        blocker = self.write("a-file", "x")
        argv = self.argv()
        argv[argv.index("--out") + 1] = str(blocker)
        self.assert_refused(*self.main(argv), str(blocker))

    def test_a_utc_offset_no_clock_has(self):
        for value in ("nan", "inf", "1e300", "25"):
            argv = self.argv()
            argv[argv.index("--utc-offset-h") + 1] = value
            self.assert_refused(*self.main(argv), "--utc-offset-h")
            self.assert_nothing_written()


class MalformedTest(Case):
    JSONL = ("decisions", "light-states", "corrections", "occupancy", "gold")

    def with_line(self, flag, line):
        return self.write(flag + "-bad.txt", self.good[flag] + line + "\n")

    def test_a_line_that_is_not_json_names_its_number(self):
        for flag in self.JSONL:
            with self.subTest(flag):
                number = len(self.records[flag]) + 1
                self.assert_refused(*self.main(self.argv(**{flag.replace("-", "_"): self.with_line(flag, "{")})), "line %d" % number)

    def test_a_truncated_file_is_a_sentence(self):
        for flag in self.JSONL:
            with self.subTest(flag):
                path = self.write(flag + "-cut.txt", self.good[flag][:-5])
                self.assert_refused(*self.main(self.argv(**{flag.replace("-", "_"): path})), str(path), "line %d" % len(self.records[flag]))

    def test_a_line_that_is_no_object(self):
        for flag in self.JSONL:
            for line in ("[1]", "5", '"x"', "null", "true"):
                with self.subTest(flag=flag, line=line):
                    self.assert_refused(*self.main(self.argv(**{flag.replace("-", "_"): self.with_line(flag, line)})),
                                        "line %d" % (len(self.records[flag]) + 1))

    def test_numbers_json_has_no_form_for_and_numbers_no_float_holds(self):
        for flag in self.JSONL:
            for value in ("NaN", "Infinity", "-Infinity", "1e999", "-1e999", "1" + "0" * 400):
                with self.subTest(flag=flag, value=value[:12]):
                    row = '{"decision_id": "x", "ts": %s, "start": 1, "end": 1}' % value
                    self.assert_refused(*self.main(self.argv(**{flag.replace("-", "_"): self.with_line(flag, row)})),
                                        "line %d" % (len(self.records[flag]) + 1))

    def test_nested_too_deep_to_read(self):
        deep = "[" * 200000
        for flag in self.JSONL:
            with self.subTest(flag):
                self.assert_refused(*self.main(self.argv(**{flag.replace("-", "_"): self.with_line(flag, deep)})),
                                    "line %d" % (len(self.records[flag]) + 1))
        self.assert_refused(*self.main(self.argv(model=self.write("deep.json", deep))))

    def test_blank_lines_read(self):
        path = self.write("blank.txt", "\n" + self.good["decisions"].replace("\n", "\n\n") + "   \n")
        self.assertEqual(self.main(self.argv(decisions=path))[0], 0)


class DecisionLinesTest(Case):
    def test_a_line_with_no_decision_id_or_ts_names_the_line(self):
        for key in ("decision_id", "ts"):
            gone = {k: v for k, v in self.records["decisions"][0].items() if k != key}
            for name, row in (("absent", gone), ("null", {**gone, key: None})):
                path = self.write("no-%s.txt" % key, self.good["decisions"] + json.dumps(row) + "\n")
                with self.subTest(key=key, name=name):
                    self.assert_refused(*self.main(self.argv(decisions=path)), "line 2", key)
                    self.assert_nothing_written()

    def test_a_den_event_envelope_says_to_pass_decisions_jsonl(self):
        envelope = {"schema": "den-event-1", "t": T, "seq": 0, "kind": "decision", "source": "decide", "data": self.records["decisions"][0]}
        path = self.write("log.jsonl", json.dumps(envelope) + "\n")
        self.assert_refused(*self.main(self.argv(decisions=path)), "line 1", "den-event-1", "decisions.jsonl")

    def test_den_cores_log_header_says_the_same(self):
        header = {"schema": "den-replay-log-1", "sources": [], "policies": []}
        envelope = {"schema": "den-event-1", "t": T, "seq": 0, "kind": "tick", "source": "runner", "data": {}}
        path = self.write("log.jsonl", json.dumps(header) + "\n" + json.dumps(envelope) + "\n")
        self.assert_refused(*self.main(self.argv(decisions=path)), "line 1", "decisions.jsonl")
        self.assert_nothing_written()

    def test_an_envelope_on_a_later_line_is_named_there(self):
        envelope = {"schema": "den-event-1", "t": T, "seq": 0, "kind": "tick", "source": "runner", "data": {}}
        path = self.write("mixed.jsonl", self.good["decisions"] + json.dumps(envelope) + "\n")
        self.assert_refused(*self.main(self.argv(decisions=path)), "line 2", "decisions.jsonl")

    def test_a_clock_time_with_nothing_to_date_it_is_a_sentence(self):
        row = copy.deepcopy(self.records["decisions"][0])
        row["inputs"].update(person_state=[], light_state=None)
        path = self.write("undated.txt", json.dumps(row) + "\n")
        empty = self.write("empty.txt", "")
        self.assert_refused(*self.main(self.argv(decisions=path, light_states=empty, corrections=empty)), row["decision_id"], "date")


def at(record, *keys):
    """The object holding the last key, walking from record."""
    for key in keys[:-1]:
        record = record[key]
    return record


class WrongInsideTest(Case):
    """Every field the tool reads, wrong: a sentence with the line's number, in the file that holds it."""
    BULB = "F1.spot"
    DECISION = (
        (("decision_id",), 5), (("decision_id",), ""), (("ts",), True), (("ts",), "soon"), (("ts",), -5), (("ts",), 1e300), (("ts",), []),
        (("inputs",), 5), (("inputs",), []), (("inputs", "light_state"), 5), (("inputs", "light_state_ts"), "x"), (("inputs", "light_state_ts"), True),
        (("inputs", "person_state"), 5), (("inputs", "actuator_state"), []), (("inputs", "actuator_state", BULB), 5),
        (("inputs", "actuator_state", BULB, "on"), "yes"), (("inputs", "actuator_state", BULB, "level"), "high"),
        (("inputs", "actuator_state", BULB, "level"), True), (("inputs", "actuator_state", BULB, "cct_k"), "warm"),
        (("inputs", "hold_set"), 5), (("inputs", "hold_set", "revision"), "x"), (("inputs", "hold_set", "held"), 5), (("inputs", "hold_set", "held"), [5]),
        (("inputs", "hold_set", "held"), [{"entity_id": 5}]), (("inputs", "hold_set", "held"), [{"entity_id": "a", "channels": 5}]),
        (("inputs", "hold_set", "held"), [{"entity_id": "a", "pinned": 5}]), (("inputs", "hold_set", "held"), [{"entity_id": "a", "pinned": {"level_pct": "x"}}]),
        (("inputs", "hold_set", "held"), [{"entity_id": "a", "pinned": {"cct_k": "x"}}]), (("inputs", "hold_set", "held"), [{"entity_id": "a", "pinned": {"on": "x"}}]),
        (("targets",), 5), (("targets", "A", "surface_id"), 5),
        (("applied_scene",), []), (("applied_scene", BULB), 5), (("applied_scene", BULB, "status"), 5), (("applied_scene", BULB, "readback"), 5),
        (("applied_scene", BULB, "readback", "level"), "high"), (("applied_scene", BULB, "readback", "cct_k"), "warm"), (("applied_scene", BULB, "readback", "on"), 1),
        (("applied_scene", BULB, "steps"), 5), (("applied_scene", BULB, "steps"), [5]), (("applied_scene", BULB, "steps", 0, "sent_at"), "soon"),
        (("applied_scene", BULB, "steps", 0, "sent_at"), True), (("applied_scene", BULB, "steps", 0, "transition_s"), "x"), (("applied_scene", BULB, "steps", 0, "kind"), 5),
        (("applied_scene", BULB, "steps", 0, "result"), 5), (("applied_scene", BULB, "steps", 0, "result", "at"), "soon"),
        (("recipients",), 5), (("recipients", BULB), 5), (("recipients", BULB, "served"), 5), (("recipients", BULB, "exposed"), 5), (("recipients", BULB, "targets"), 5),
        (("recipients", BULB, "targets"), [5]), (("recipients", BULB, "targets"), [{"kind": 5}]), (("recipients", BULB, "targets"), [{"kind": "task", "target_id": 5}]),
        (("predicted",), 5), (("predicted", "surfaces"), 5), (("predicted", "surfaces", "counter-1"), "x"), (("predicted", "eyes"), 5), (("predicted", "eyes"), [5]),
        (("predicted", "surfaces_not_logged"), "x"), (("predicted", "surfaces_not_logged"), -1), (("predicted", "surfaces_not_logged"), True), (("predicted", "surfaces_not_logged"), 1.5),
    )
    FRAME = (
        (("ts",), None), (("ts",), True), (("ts",), "19:03:29"), (("ts",), -5), (("ts",), 1e300), (("id",), 5), (("surface_estimates",), 5), (("surface_estimates",), [5]),
        (("surface_estimates",), [{"value": 1}]), (("surface_estimates",), [{"surface_id": 5, "value": 1}]), (("surface_estimates",), [{"surface_id": "a", "value": "x"}]),
        (("surface_estimates",), [{"surface_id": "a", "value": True}]), (("surface_estimates",), [{"surface_id": "a", "value": 1, "clipped_frac": "x"}]),
        (("surface_estimates",), [{"surface_id": "a", "value": 1, "basis": 5}]), (("window_daylight",), 5), (("window_daylight",), [5]),
        (("window_daylight",), [{"level": 1}]), (("window_daylight",), [{"window_id": 5, "level": 1}]), (("window_daylight",), [{"window_id": "w", "level": "x"}]),
    )
    CORRECTION = (
        (("ts",), None), (("ts",), True), (("ts",), "soon"), (("action",), 5), (("action", "settle_s"), "x"), (("snapshot",), 5), (("snapshot", "light_state"), 5), (("snapshot", "person_state"), 5),
        (("hold",), 5), (("hold", "entity_id"), 5), (("hold", "channels"), 5), (("hold", "pinned"), 5), (("hold", "pinned", "level_pct"), "x"), (("hold", "pinned", "cct_k"), "x"),
        (("hold", "since"), "soon"), (("hold", "ended"), True), (("hold", "hold_set_revision"), "x"),
    )
    OCCUPANCY_LINE = (
        (("start",), None), (("start",), "x"), (("end",), None), (("end",), True), (("people",), 5), (("people",), [5]), (("people", 0, "xy"), "x"), (("people", 0, "xy"), [1.0]), (("people", 0, "xy"), [1.0, "x"]),
    )
    GOLD_LINE = (
        (("start",), None), (("start",), "x"), (("end",), None), (("end",), True), (("R_eval",), 5), (("R_eval", "surface_id"), 5), (("R_eval", "placement_sd"), "x"),
    )

    def table(self, flag, cases, index=0):
        for keys, value in cases:
            rows = copy.deepcopy(self.records[flag])
            at(rows[index], *keys)[keys[-1]] = value
            path = self.tmp / (flag + "-wrong.txt")
            write_jsonl(path, rows)
            with self.subTest(flag=flag, keys=keys, value=value):
                self.assert_refused(*self.main(self.argv(**{flag.replace("-", "_"): path})), str(path), "line %d" % (index + 1))
                self.assert_nothing_written()

    def test_inside_a_decision(self):
        self.table("decisions", self.DECISION)

    def test_inside_a_frame(self):
        self.table("light-states", self.FRAME, 1)

    def test_inside_a_correction(self):
        self.table("corrections", self.CORRECTION)

    def test_inside_an_occupancy_line(self):
        self.table("occupancy", self.OCCUPANCY_LINE)

    def test_inside_a_gold_line(self):
        self.table("gold", self.GOLD_LINE)

    def test_a_den_event_envelope_as_corrections_says_what_it_is(self):
        envelope = {"schema": "den-event-1", "t": T, "seq": 0, "kind": "correction", "source": "correction_capture", "data": {"correction_id": "c"}}
        path = self.write("corrections.jsonl", json.dumps(envelope) + "\n")
        self.assert_refused(*self.main(self.argv(corrections=path)), "line 1", "den-event-1", "--corrections")

    def test_two_frames_at_one_time_are_a_sentence(self):
        rows = copy.deepcopy(self.records["light-states"])
        rows.append(copy.deepcopy(rows[1]))
        path = self.tmp / "twice.txt"
        write_jsonl(path, rows)
        self.assert_refused(*self.main(self.argv(light_states=path)), str(path), "lines 2 and %d" % len(rows))
        rows[-1]["ts"] += 0.0004  # section 4's 0.0005 s could not tell them apart
        write_jsonl(path, rows)
        self.assert_refused(*self.main(self.argv(light_states=path)), "lines 2 and %d" % len(rows))


def channel_table(name, value, key="F1.spot"):
    """The walkthrough's model with one of a channel's tables changed: cct's kelvin or basis_keys, dimming's levels or g."""
    model = syn.v11_model()
    model["cct" if name in ("kelvin", "basis_keys") else "dimming"][key][name] = value
    return model


def basis_row(value, key="F1.spot@3500K"):
    """The walkthrough's model with one basis row replaced, or taken away when value is None."""
    model = syn.v11_model()
    if value is None:
        del model["basis"][key]
    else:
        model["basis"][key] = value
    return model



def kelvin_twice():
    """F1.spot captured at 3500 K twice, each with its own key to its one row: a temperature that is no step."""
    model = channel_table("kelvin", [3500, 3500])
    model["cct"]["F1.spot"]["basis_keys"] = ["F1.spot@3500K", "F1.spot@3500K"]
    return model

class WrongModelTest(Case):
    """rung 8 Stage 1's light-model.json, the one shape den-verify reads."""
    CASES = (
        ("a list", []), ("a number", 5), ("null", None), ("no version", {"version": None}),
        ("version not text", {"version": 5}), ("no unit", {"basis_unit": None}), ("unit not text", {"basis_unit": 5}), ("fake not a flag", {"fake": "no"}),
        ("no points", {"sample_points": None}), ("points not a list", {"sample_points": 5}), ("empty points", {"sample_points": []}), ("a point not an object", {"sample_points": [5]}),
        ("a point with no id", {"sample_points": [{"kind": "task"}]}), ("two points one id", {"sample_points": [{"point_id": "a"}, {"point_id": "a"}]}), ("a point id not text", {"sample_points": [{"point_id": 5}]}),
        ("no channels", {"channels": None}), ("channels not a list", {"channels": 5}), ("empty channels", {"channels": []}), ("a channel not an object", {"channels": [5]}),
        ("a channel with no key", {"channels": [{"entity_id": None}]}), ("a key not text", {"channels": [{"channel_key": 5, "entity_id": None}]}),
        ("a channel with no entity", {"channels": [{"channel_key": "F1.spot"}]}), ("an entity not text", {"channels": [{"channel_key": "F1.spot", "entity_id": 5}]}),
        ("two channels one key", {"channels": [{"channel_key": "F1.spot", "entity_id": None}, {"channel_key": "F1.spot", "entity_id": None}]}),
        ("two channels one entity", {"channels": [{"channel_key": "F1.spot", "entity_id": "light.x"}, {"channel_key": "F2.spot", "entity_id": "light.x"}]}),
        ("no cct", {"cct": None}), ("cct not an object", {"cct": 5}), ("a channel with no cct", {"cct": {}}),
        ("no dimming", {"dimming": None}), ("dimming not an object", {"dimming": 5}), ("a channel with no dimming", {"dimming": {}}),
        ("no basis", {"basis": None}), ("basis not an object", {"basis": 5}), ("the canon example's own basis", {"basis": {"F1.spot": {"counter-1": None}}}),
        ("a spot model", {"spot_model": {"F1.spot": {"counter-1": {}}}}),
        ("surfaces not a map", {"surfaces": 5}), ("a surface not a list", {"surfaces": {"a": 5}}), ("a block not text", {"surfaces": {"a": [5]}}),
    )
    INSIDE = (
        ("kelvin text", channel_table("kelvin", ["warm"])), ("a kelvin of 0", channel_table("kelvin", [0])), ("no kelvin", channel_table("kelvin", [])),
        ("a kelvin twice", kelvin_twice()),
        ("keys not a list", channel_table("basis_keys", 5)), ("keys too few", channel_table("basis_keys", [])),
        ("levels not rising", channel_table("levels", [254, 177.8, 127, 0])), ("levels text", channel_table("levels", ["a", "b", "c", "d"])), ("no levels", channel_table("levels", [])),
        ("a level twice", channel_table("levels", [0, 127, 127, 254])), ("g text", channel_table("g", ["a", "b", "c", "d"])), ("g too short", channel_table("g", [0.0])),
        ("a row not an object", basis_row(5)), ("a value not a number", basis_row({"counter-1": "x", "armchair-book": 1.0})),
        ("a block left out", basis_row({"counter-1": 686.0})), ("a basis key that names nothing", basis_row(None)),
    )

    def test_each_is_a_sentence_saying_it_is_not_a_light_model(self):
        for name, change in self.CASES:
            model = change if not isinstance(change, dict) else {**syn.v11_model(), **change}
            if isinstance(change, dict):
                model = {k: v for k, v in model.items() if not (k in change and change[k] is None)}
            path = self.write("wrong.json", json.dumps(model))
            with self.subTest(name):
                self.assert_refused(*self.main(self.argv(model=path)), str(path), "not a light model")
                self.assert_nothing_written()

    def test_inside_a_channels_tables_is_a_sentence_too(self):
        for name, model in self.INSIDE:
            path = self.write("wrong.json", json.dumps(model))
            with self.subTest(name):
                self.assert_refused(*self.main(self.argv(model=path)), str(path), "not a light model")
                self.assert_nothing_written()

    def test_a_null_block_is_how_stage_1_writes_an_excluded_one_and_reads(self):
        model = syn.v11_model()
        model["sample_points"][1]["excluded"] = "mask"
        for row in model["basis"].values():
            row["armchair-book"] = None
        self.assertEqual(self.main(self.argv(model=self.write("excluded.json", json.dumps(model))))[0], 0)

    def test_not_json_is_a_sentence(self):
        for text in ("", "{", "not json at all", '{"version": NaN}', '{"version": 1e999}'):
            path = self.write("bad.json", text)
            with self.subTest(text):
                self.assert_refused(*self.main(self.argv(model=path)), str(path))

    def test_a_right_model_still_reads(self):
        self.assertEqual(self.main(self.argv())[0], 0)


class LastResortTest(Case):
    def test_what_no_check_named_is_one_sentence_with_where_it_happened(self):
        def broken(*_args, **_kwargs):
            raise TypeError("a bug")

        with mock.patch.object(cli, "Run", broken):
            code, printed = self.main(self.argv())
        self.assertEqual(code, 2)
        self.assertEqual(len(printed.strip().splitlines()), 1, printed)
        self.assertIn("verify.py stopped on an input of the wrong shape, or a bug of its own: TypeError: a bug", printed)
        self.assertIn("test_refusals.py line", printed)
        self.assertNotIn("Traceback", printed)


class FuzzTest(Case):
    """Any value anywhere: never a traceback, and no check left for the last resort to name."""
    VALUES = (None, 5, -1, 0, 1e300, 1.5, "x", "", "19:03", True, False, [], [5], [None], {}, {"a": 1}, {"level": "x"})

    def leaves(self, value, path=()):
        yield path
        if isinstance(value, dict):
            for key, item in value.items():
                yield from self.leaves(item, (*path, key))
        elif isinstance(value, list):
            for n, item in enumerate(value):
                yield from self.leaves(item, (*path, n))

    def mutate(self, rng, value):
        out = copy.deepcopy(value)
        spots = [p for p in self.leaves(out) if p]
        path = rng.choice(spots)
        at(out, *path)[path[-1]] = rng.choice(self.VALUES)
        return out

    def test_random_mutations_of_each_file(self):
        rng = random.Random(20261106)
        model = syn.v11_model()
        for n in range(400):
            flag = rng.choice(self.FLAGS)
            if flag == "model":
                path = self.write("fuzz.json", json.dumps(self.mutate(rng, model)))
            elif flag == "surfaces":
                path = self.write("fuzz.json", json.dumps(self.mutate(rng, json.loads(self.good["surfaces"]))))
            else:
                rows = copy.deepcopy(self.records[flag])
                rows[rng.randrange(len(rows))] = self.mutate(rng, rng.choice(rows))
                path = self.write("fuzz.txt", lines(rows))
            shutil.rmtree(self.tmp / "out", ignore_errors=True)
            with self.subTest(n=n, flag=flag):
                code, printed = self.main(self.argv(**{flag.replace("-", "_"): path}))
                self.assertIn(code, (0, 2), printed)
                self.assertNotIn("stopped on an input", printed, path.read_text("utf-8")[:2000])


if __name__ == "__main__":
    unittest.main()
