"""What run.py, score.py, check.py and validate.py say about a file they cannot read: one sentence and exit 2, never a
traceback. PowerShell 5.1, the owner's shell, writes `>` as UTF-16LE with a byte-order mark and CRLF, and Out-File -Encoding utf8
writes a UTF-8 byte-order mark and CRLF, so these use those bytes, and real powershell.exe where there is one. From the root:

python -B -m unittest discover -s tools/den-core -p "test_*.py"

Everything is synthetic, and nothing reaches a host or a light.
"""
import json
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

import coordinator  # noqa: E402
import events  # noqa: E402
import fake_light  # noqa: E402
import incumbent  # noqa: E402
import run  # noqa: E402
import score  # noqa: E402
import sources  # noqa: E402
import synthetic  # noqa: E402
import test_live_inputs  # noqa: E402
import test_score  # noqa: E402
import test_spine  # noqa: E402
import validate  # noqa: E402

HAVE_POLICY = incumbent.find_policy() is not None
POWERSHELL = shutil.which("powershell.exe")
T0 = synthetic.T0
COORDINATOR = ["--synthetic", "evening", "--policies", "need", "incumbent", "coordinator"]


def sentence(path):
    return "%s is UTF-16 (PowerShell 5.1's > writes that): save it as UTF-8" % path


def ps51(text):
    """What PowerShell 5.1's `>` writes for this text: a byte-order mark, UTF-16LE and CRLF."""
    return b"\xff\xfe" + text.replace("\n", "\r\n").encode("utf-16-le")


def out_file(text):
    """What its Out-File -Encoding utf8 and Set-Content -Encoding utf8 write: a byte-order mark, UTF-8 and CRLF."""
    return b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode("utf-8")


def tool(name, *argv):
    """A tool as the owner runs it: (exit code, stdout, stderr)."""
    done = subprocess.run([sys.executable, "-B", str(HERE / name), *map(str, argv)], capture_output=True, text=True, timeout=300)
    return done.returncode, done.stdout, done.stderr


class Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def write(self, name, raw):
        path = self.tmp / name
        path.write_bytes(raw if isinstance(raw, bytes) else raw.encode("utf-8"))
        return path

    def refused(self, code, out, err, words):
        """Exit 2, the words on a line, no traceback."""
        self.assertEqual(code, 2, out + err)
        self.assertNotIn("Traceback", out + err)
        self.assertIn(words, out + err)


def good_inputs(tmp):
    """{flag: (the file's text as UTF-8, how the flag takes it)}: one small valid file per input, in the shape each reads."""
    evening_dir = tmp / "evening"
    with mock.patch("builtins.print"):
        run.main(["--synthetic", "evening", "--policies", "incumbent", "--out", str(evening_dir)])
    facts = next(evening_dir.glob("la-fact-log-1-*.jsonl")).read_text("ascii")
    export = test_live_inputs.Export()
    export.row(T0)
    export.row(T0 + 10, occupancy="on")
    status = export.write(tmp)[0]
    correction = test_live_inputs.correction_line(T0, "cc-1", events.LIGHTS[0], test_live_inputs.DIM) + "\n"
    command = test_spine.command(T0 + 1, events.LIGHTS[0], test_spine.SCENE, "ctx-1") + "\n"
    return {
        "facts": facts,
        "poll": json.dumps({"t": "2026-11-04T04:00:00+00:00", "lights": {events.LIGHTS[0]: "on"}, "occ": "on"}) + "\n",
        "mqtt": json.dumps([{"at": 0, "topic": "frigate/available", "payload": "online"},
                            {"at": 1, "topic": "frigate/den_oak/person", "payload": "1"}]) + "\n",
        "journal": test_spine.edge(T0, "unknown", "on") + "\n" + test_spine.edge(T0 + 30, "on", "off") + "\n",
        "status": Path(status).read_bytes().decode("ascii").replace("\r\n", "\n"),
        "corrections": correction,
        "live-commands": command,
        "lights": json.dumps({"last_updated_ts": T0, "entity_id": events.LIGHTS[0], "state": "on", "context_id": "", "context_user_id": "",
                              "context_parent_id": ""}) + "\n",
        "surfaces": json.dumps({"desk": ["counter-1/0"]}) + "\n",
        "light-states": json.dumps({"ts": T0, "camera_id": "den-oak"}) + "\n",
        "light-model": json.dumps(fake_light.build(), indent=1) + "\n",
    }


def argv_for(flag, path, good):
    """run.py's arguments for one input file, every other flag as that input needs."""
    if flag == "status":
        return ["--status", path, "--policies", "incumbent"]
    if flag == "lights":
        return ["--lights", path, "--live-commands", good, "--policies", "incumbent", "--synthetic", "evening"]
    if flag in ("corrections", "live-commands"):
        return ["--" + flag, path, "--synthetic", "evening", "--policies", "incumbent"]
    if flag == "mqtt":
        return ["--mqtt", path, *COORDINATOR]
    return ["--" + flag, path, *COORDINATOR]


class EveryInputOfRunTest(Temp):
    FLAGS = ("facts", "poll", "mqtt", "journal", "status", "corrections", "live-commands", "lights", "surfaces", "light-states", "light-model")

    def setUp(self):
        super().setUp()
        self.good = good_inputs(self.tmp)
        self.assertEqual(sorted(self.good), sorted(self.FLAGS))
        self.command = self.write("commands.jsonl", self.good["live-commands"])

    def test_utf16_is_exit_2_with_the_one_sentence(self):
        for flag in self.FLAGS:
            with self.subTest(flag):
                path = self.write(flag + "-16.jsonl", ps51(self.good[flag]))
                code, out, err = tool("run.py", *argv_for(flag, path, self.command), "--out", self.tmp / ("out-" + flag))
                self.refused(code, out, err, sentence(path))
                self.assertEqual(err.strip(), sentence(path))
                self.assertFalse((self.tmp / ("out-" + flag)).exists())

    def test_a_bom_and_crlf_read_as_the_same_file(self):
        """Out-File -Encoding utf8's bytes. A log is written, so nothing refused it, and its inputs are what the plain file gave."""
        for flag in self.FLAGS:
            if flag == "mqtt":  # read below: its own step list has no inputs alone
                continue
            with self.subTest(flag):
                plain = self.write(flag + ".txt", self.good[flag])
                bom = self.write(flag + "-bom.txt", out_file(self.good[flag]))
                a, b = self.tmp / ("a-" + flag), self.tmp / ("b-" + flag)
                code, out, err = tool("run.py", *argv_for(flag, plain, self.command), "--out", a)
                self.assertEqual(code, 0, out + err)
                code, out, err = tool("run.py", *argv_for(flag, bom, self.command), "--out", b)
                self.assertEqual(code, 0, out + err)
                self.assertEqual(self.decided(a), self.decided(b))

    @staticmethod
    def decided(folder):
        """A log's events. A Decision's config hash is left out, since a config names its model file's path."""
        _, log = events.read(folder / "log.jsonl")
        for event in log:
            if event["kind"] == "decision":
                del event["data"]["inputs"]["config_sha256"]
        return log

    def test_the_mqtt_step_list_reads_with_a_bom_and_crlf(self):
        plain = self.write("mqtt.json", self.good["mqtt"])
        bom = self.write("mqtt-bom.json", out_file(self.good["mqtt"]))
        self.assertEqual(sources.mqtt_timeline(bom, T0), sources.mqtt_timeline(plain, T0))
        self.assertTrue(sources.mqtt_timeline(bom, T0))

    def test_a_folder_of_day_files_names_the_file(self):
        folder = self.tmp / "facts"
        folder.mkdir()
        path = folder / "la-fact-log-1-2026-11-04.jsonl"
        path.write_bytes(ps51(self.good["facts"]))
        code, out, err = tool("run.py", "--facts", folder, *COORDINATOR, "--out", self.tmp / "out")
        self.refused(code, out, err, sentence(path))

    def test_the_adapters_refuse_it_themselves(self):
        calls = {"facts": lambda p: sources.fact_log([p]), "poll": sources.poll_log, "mqtt": sources.mqtt_timeline,
                 "journal": lambda p: sources.occupancy_journal([p]), "status": lambda p: sources.den_lighting_status([p]),
                 "corrections": lambda p: sources.correction_capture([p]), "live-commands": lambda p: sources.recorder_commands([p]),
                 "lights": lambda p: sources.recorder_lights([p]), "light-states": sources.light_states}
        for flag, call in calls.items():
            with self.subTest(flag):
                path = self.write(flag + "-16.jsonl", ps51(self.good[flag]))
                with self.assertRaises(events.BadFile) as refused:
                    call(path)
                self.assertEqual(str(refused.exception), sentence(path))

    def test_utf16_with_no_byte_order_mark_gets_the_same_sentence(self):
        path = self.write("no-bom.jsonl", self.good["journal"].encode("utf-16-le"))
        code, out, err = tool("run.py", "--journal", path, *COORDINATOR, "--out", self.tmp / "out")
        self.refused(code, out, err, sentence(path))


class MalformedInputTest(Temp):
    def refuses(self, words, *argv):
        code, out, err = tool("run.py", *argv, "--out", self.tmp / "out")
        self.refused(code, out, err, words)
        self.assertFalse((self.tmp / "out" / "log.jsonl").exists())

    def test_a_surface_map_that_is_not_json_or_not_a_map(self):
        path = self.write("bad.json", "{not json")
        self.refuses("%s is not JSON" % path, "--surfaces", path, *COORDINATOR)
        for value in (["room"], {"room": "counter-1/0"}, {"room": [1]}):
            path = self.write("shape.json", json.dumps(value))
            with self.subTest(value=value):
                self.refuses("%s is not a surface map" % path, "--surfaces", path, *COORDINATOR)

    def test_a_light_model_that_is_not_json(self):
        path = self.write("bad.json", "{not json")
        self.refuses("%s is not JSON" % path, "--light-model", path, *COORDINATOR)

    def test_a_wrong_shaped_light_model(self):
        whole = fake_light.build()
        no_cct = {**whole, "cct": {}}
        no_rows = {**whole, "basis": {}}
        no_points = {**whole, "sample_points": [{"kind": None}]}
        no_entity = {**whole, "channels": [{"channel_key": "den_front_left.white"}]}
        key = "den_front_left.white"
        first = whole["cct"][key]["basis_keys"][0]
        point = whole["sample_points"][0]["point_id"]
        for name, value in (("kelvin zero", {**whole, "cct": {**whole["cct"], key: {**whole["cct"][key], "kelvin": [0, 2700, 6500]}}}),
                            ("kelvin text", {**whole, "cct": {**whole["cct"], key: {**whole["cct"][key], "kelvin": ["warm", 2700, 6500]}}}),
                            ("dimming text", {**whole, "dimming": {**whole["dimming"], key: {"levels": ["a"], "g": ["b"]}}}),
                            ("basis text", {**whole, "basis": {**whole["basis"], first: {point: "bright"}}}),
                            ("surfaces not a map", {**whole, "surfaces": 5}), ("surfaces of text", {**whole, "surfaces": {"desk": "counter-1/0"}}),
                            ("error text", {**whole, "error": "low"}), ("p90 text", {**whole, "error": {"p90_pct": "low"}}),
                            ("glare text", {**whole, "glare": "none"}), ("spot text", {**whole, "spot_model": "none"}),
                            ("a list", [1, 2]), ("text", "model"), ("no version", {k: v for k, v in whole.items() if k != "version"}),
                            ("channels not a list", {**whole, "channels": {"a": 1}}), ("no channels", {**whole, "channels": []}),
                            ("no cct", no_cct), ("no basis rows", no_rows), ("no point id", no_points), ("no entity", no_entity),
                            ("no dimming", {**whole, "dimming": None})):
            path = self.write("shape.json", json.dumps(value))
            with self.subTest(name):
                self.refuses("%s is not a light model" % path, "--light-model", path, *COORDINATOR)

    def test_a_missing_input_is_a_sentence(self):
        for flag in ("--light-model", "--surfaces", "--light-states", "--poll", "--journal", "--mqtt"):
            with self.subTest(flag):
                self.refuses("absent.json", flag, self.tmp / "absent.json", *COORDINATOR)

    def test_a_step_list_that_is_not_json_or_not_a_list_of_steps(self):
        path = self.write("bad.json", "{not json")
        self.refuses("%s is not JSON" % path, "--mqtt", path, *COORDINATOR)
        for value in ({"at": 1}, [1, 2], [{"topic": "frigate/available"}]):
            path = self.write("steps.json", json.dumps(value))
            with self.subTest(value=value):
                self.refuses("%s is not a Frigate MQTT step list" % path, "--mqtt", path, *COORDINATOR)

    def test_a_bad_line_in_a_jsonl_input_is_exit_2_with_its_number(self):
        path = self.write("poll.jsonl", "not json\n")
        self.refuses("line 1 is not a poll row", "--poll", path, *COORDINATOR)
        path = self.write("frames.jsonl", "{}\n")
        self.refuses("line 1 is not a LightState", "--light-states", path, *COORDINATOR)

    def test_a_file_that_is_not_utf8(self):
        path = self.write("ansi.json", b'{"room": ["caf\xe9"]}')
        self.refuses("%s is not UTF-8" % path, "--surfaces", path, *COORDINATOR)

    def test_a_gate_file_refuses_the_same_way(self):
        whole = coordinator.GATE_FILE.read_bytes().decode("utf-8")
        path = self.write("gate.json", ps51(whole))
        with self.assertRaises(events.BadFile) as refused:
            coordinator.load_gate(path)
        self.assertEqual(str(refused.exception), sentence(path))
        self.assertEqual(coordinator.load_gate(self.write("bom.json", out_file(whole))), coordinator.load_gate())
        for raw, words in (("{not json", "is not JSON"), ("[1]", "is not a gate file"), ('{"slots": {"a": 1}}', "is not a gate file"),
                           ('{"gate": "g", "slots": {"surface_map": {"status": "open", "value": null, "proposed": {}}}}', "is not a gate file")):
            with self.subTest(raw), self.assertRaisesRegex(events.BadFile, words):
                coordinator.load_gate(self.write("bad.json", raw))

    def test_a_gate_with_a_slot_gone_is_a_sentence(self):
        gate = coordinator.load_gate()
        del gate["slots"]["look_brightness"]
        path = self.write("short.json", json.dumps(gate))
        with mock.patch.object(coordinator, "GATE_FILE", path), self.assertRaisesRegex(events.BadFile, "look_brightness"):
            coordinator.make_policy()


@unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
class LogsTest(Temp):
    """check.py, score.py and validate.py on the one file they all read."""

    def setUp(self):
        super().setUp()
        with mock.patch("builtins.print"):
            run.main([*COORDINATOR, "--touch", "--out", str(self.tmp / "run")])
        self.log = self.tmp / "run" / "log.jsonl"
        self.text = self.log.read_text("ascii")

    def test_utf16_is_exit_2_with_the_one_sentence(self):
        path = self.write("log-16.jsonl", ps51(self.text))
        for name, argv in (("check.py", [path]), ("score.py", [path]), ("score.py", [path, "--preview"]), ("validate.py", [path])):
            with self.subTest(name=name, argv=argv):
                code, out, err = tool(name, *argv)
                self.refused(code, out, err, sentence(path))
                self.assertEqual(err.strip(), sentence(path))

    def test_a_bom_and_crlf_read_as_the_same_log(self):
        path = self.write("log-bom.jsonl", out_file(self.text))
        code, out, err = tool("check.py", path)
        self.assertEqual(code, 0, out + err)
        self.assertIn("byte-identically", out)
        code, out, err = tool("score.py", path, "--preview")
        self.assertEqual(code, 0, out + err)
        code, out, err = tool("validate.py", path)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(events.read(path), events.read(self.log))

    def test_a_changed_line_still_fails_through_a_bom_and_crlf(self):
        path = self.write("log-bom.jsonl", out_file(self.text.replace('"status":"applied"', '"status":"partial"', 1)))
        code, out, err = tool("check.py", path)
        self.assertEqual(code, 1, out + err)
        self.assertIn("differs", out)

    def test_a_malformed_or_missing_log_is_exit_2_with_a_sentence(self):
        bad = self.write("bad.jsonl", "not a log\n")
        torn = self.write("torn.jsonl", self.text[:-200])
        no_header = self.write("no-header.jsonl", '{"schema":"den-replay-log-1"}\n')
        for name in ("check.py", "score.py", "validate.py"):
            for path, words in ((bad, "line 1"), (self.tmp / "absent.jsonl", "absent.jsonl"), (torn, "does not end with a newline"),
                                (no_header, "header lacks")):
                with self.subTest(name=name, path=path.name):
                    code, out, err = tool(name, path)
                    self.refused(code, out, err, words)

    def test_a_model_file_gone_bad_since_the_run_is_exit_2(self):
        model = self.write("model.json", json.dumps(fake_light.build()))
        with mock.patch("builtins.print"):
            run.main([*COORDINATOR, "--light-model", str(model), "--out", str(self.tmp / "with-model")])
        log = self.tmp / "with-model" / "log.jsonl"
        self.assertEqual(tool("check.py", log)[0], 0)
        text = model.read_text("utf-8")
        model.write_bytes(out_file(text))  # the same JSON re-saved: the hash is over the parsed model, so this still replays
        self.assertEqual(tool("check.py", log)[0], 0)
        for raw, words in ((ps51(text), "is UTF-16"), (b"{not json", "is not JSON"), (b"[1]", "is not a light model")):
            model.write_bytes(raw)
            for name, argv in (("check.py", [log]), ("score.py", [log]), ("validate.py", [log])):
                with self.subTest(words=words, name=name):
                    code, out, err = tool(name, *argv)
                    self.refused(code, out, err, words)
        model.unlink()
        for name in ("check.py", "score.py", "validate.py"):
            with self.subTest(gone=name):
                code, out, err = tool(name, log)
                self.refused(code, out, err, "light model unreadable")

    def test_marks_utf16_a_bom_and_a_bad_line(self):
        mark = json.dumps(test_score.mark(T0, T0 + 10)) + "\n"
        path = self.write("marks-16.jsonl", ps51(mark))
        code, out, err = tool("score.py", self.log, "--preview", "--marks", path)
        self.refused(code, out, err, sentence(path))
        code, out, err = tool("score.py", self.log, "--preview", "--marks", self.write("marks-bom.jsonl", out_file(mark)))
        self.assertEqual(code, 0, out + err)
        code, out, err = tool("score.py", self.log, "--preview", "--marks", self.write("marks-bad.jsonl", "not json\n"))
        self.refused(code, out, err, "line 1")
        code, out, err = tool("score.py", self.log, "--preview", "--marks", self.tmp / "absent.jsonl")
        self.refused(code, out, err, "absent.jsonl")
        whole = test_score.mark(T0, T0 + 10)
        for bad in ({**whole, "start": "soon", "end": "later"}, {**whole, "R_eval": {**whole["R_eval"], "placement_sd": "3"}}):
            code, out, err = tool("score.py", self.log, "--preview", "--marks", self.write("marks-shape.jsonl", json.dumps(bad) + "\n"))
            self.refused(code, out, err, "line 1")

    def test_truth_utf16_a_bom_and_malformed(self):
        truth = {"schema": "reading-shadow-truth-1", "blocks": []}
        path = self.write("truth-16.json", ps51(json.dumps(truth)))
        code, out, err = tool("validate.py", self.log, "--truth", path)
        self.refused(code, out, err, sentence(path))
        code, out, err = tool("validate.py", self.log, "--truth", self.write("truth-bom.json", out_file(json.dumps(truth))))
        self.assertEqual(code, 0, out + err)
        self.assertIn("no reading blocks", out)
        path = self.write("truth-bad.json", "{not json")
        code, out, err = tool("validate.py", self.log, "--truth", path)
        self.refused(code, out, err, "%s is not JSON" % path)
        for blocks in ([{}], [1], {"a": 1}, [{"start": "soon", "reading": True}]):
            path = self.write("truth-shape.json", json.dumps({"schema": "reading-shadow-truth-1", "blocks": blocks}))
            code, out, err = tool("validate.py", self.log, "--truth", path)
            self.assertEqual(code, 0, out + err)
            self.assertNotIn("Traceback", out + err)
            self.assertIn("is not reading-shadow-truth-1", out)


@unittest.skipUnless(POWERSHELL, "needs real powershell.exe, which is on the owner's PC")
class RealPowerShellTest(Temp):
    """Bytes from the shell itself, and the repair line README.md gives."""

    def powershell(self, command):
        done = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", command], capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_a_redirect_is_refused_and_the_repair_line_reads(self):
        lines = [test_spine.edge(T0, "unknown", "on"), test_spine.edge(T0 + 30, "on", "off")]
        plain = self.write("edges.txt", "\n".join(lines) + "\n")
        redirected, repaired = self.tmp / "redirected.txt", self.tmp / "repaired.txt"
        self.powershell("Get-Content '%s' > '%s'" % (plain, redirected))
        self.assertEqual(redirected.read_bytes()[:2], b"\xff\xfe")
        code, out, err = tool("run.py", "--journal", redirected, *COORDINATOR, "--out", self.tmp / "refused")
        self.refused(code, out, err, sentence(redirected))
        self.powershell("Get-Content '%s' | Set-Content -Encoding utf8 '%s'" % (redirected, repaired))
        self.assertEqual(repaired.read_bytes()[:3], b"\xef\xbb\xbf")
        self.assertEqual(sources.occupancy_journal([repaired]), sources.occupancy_journal([plain]))
        code, out, err = tool("run.py", "--journal", repaired, *COORDINATOR, "--out", self.tmp / "read")
        self.assertEqual(code, 0, out + err)

    def test_the_readme_gives_that_repair_line(self):
        said = " ".join((HERE / "README.md").read_text("utf-8").split())
        self.assertIn("`Get-Content bad.jsonl | Set-Content -Encoding utf8 good.jsonl`", said)

    @unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
    def test_a_log_through_the_shell(self):
        with mock.patch("builtins.print"):
            run.main([*COORDINATOR, "--out", str(self.tmp / "run")])
        log = self.tmp / "run" / "log.jsonl"
        redirected, repaired = self.tmp / "redirected.jsonl", self.tmp / "repaired.jsonl"
        self.powershell("Get-Content '%s' > '%s'" % (log, redirected))
        code, out, err = tool("check.py", redirected)
        self.refused(code, out, err, sentence(redirected))
        self.powershell("Get-Content '%s' | Set-Content -Encoding utf8 '%s'" % (redirected, repaired))
        code, out, err = tool("check.py", repaired)
        self.assertEqual(code, 0, out + err)


class ReadmeLineTest(unittest.TestCase):
    def test_no_sentence_says_the_adapters_read_utf16(self):
        said = " ".join((HERE / "README.md").read_text("utf-8").split())
        self.assertNotIn("which the adapters also read", said)
        self.assertIn("don't redirect with `>`", said)


if __name__ == "__main__":
    unittest.main()
