"""What run.py, score.py, check.py and validate.py say about a file they cannot read: one sentence and exit 2, never a
traceback. PowerShell 5.1, the owner's shell, writes `>` as UTF-16LE with a byte-order mark and CRLF, and Out-File -Encoding utf8
writes a UTF-8 byte-order mark and CRLF, so these use those bytes, and real powershell.exe where there is one. From the root:

python -B -m unittest discover -s tools/den-core -p "test_*.py"

Everything is synthetic, and nothing reaches a host or a light.
"""
import copy
import io
import json
import os
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

import check  # noqa: E402
import coordinator  # noqa: E402
import events  # noqa: E402
import fake_light  # noqa: E402
import incumbent  # noqa: E402
import light_adapter  # noqa: E402
import run  # noqa: E402
import runner  # noqa: E402
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


def tool(name, *argv, timeout=300, cwd=None, env=None):
    """A tool as the owner runs it: (exit code, stdout, stderr)."""
    done = subprocess.run([sys.executable, "-B", str(HERE / name), *map(str, argv)], capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env)
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

    def test_the_sentence_names_the_path_as_it_was_typed(self):
        """A relative path with a forward slash, as the owner types it: Path() and resolve() would each say something else."""
        (self.tmp / "in").mkdir()
        for flag in self.FLAGS:
            typed = "in/%s-16.jsonl" % flag
            (self.tmp / typed).write_bytes(ps51(self.good[flag]))
            with self.subTest(flag):
                code, out, err = tool("run.py", *argv_for(flag, typed, self.command), "--out", self.tmp / ("out-" + flag), cwd=self.tmp)
                self.assertEqual(code, 2, out + err)
                self.assertEqual(err.strip(), sentence(typed))

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

    def test_a_fact_line_with_its_keys_right_and_its_values_wrong_has_its_number(self):
        lines = good_inputs(self.tmp)["facts"].splitlines()
        row = json.loads(lines[2])
        for key, value in (("observed_at", "soon"), ("observed_at", []), ("observed_at", None), ("observed_at", 5), ("request_id", []), ("request_id", {"a": 1}), ("request_id", 5)):
            lines[2] = json.dumps({**row, key: value}, separators=(",", ":"))
            path = self.write("la-fact-log-1-2026-11-04.jsonl", "\n".join(lines) + "\n")
            with self.subTest(key=key, value=value):
                self.refuses("%s line 3 is not a la-fact-log-1 line" % path, "--facts", path, "--assume-occupied", "--policies", "need", "incumbent")

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


class WrongInsideAModelTest(Temp):
    """CONTRACT section 7's wrong-shaped model, wrong one level further in than a missing key: an id that can't key a dict, a
    glare or spot table a solve would only trip on at the eye or aim it names, and a value that used to run on with no word."""

    KEY = "den_front_left.white"
    ROW = {"G": 0.1, "sd": 0.01, "model_status": "unvalidated"}

    def stage1_shaped(self):
        model = fake_light.build(surfaces=False)
        del model["fake"]
        return model

    def cases(self, whole):
        """(name, words the sentence must have, the model). Each is wrong whichever backend would read it."""
        def edited(path, value):
            model = copy.deepcopy(whole)
            at = model
            for key in path[:-1]:
                at = at[key]
            at[path[-1]] = value
            return model
        first = whole["sample_points"][0]["point_id"]
        basis_key = whole["cct"][self.KEY]["basis_keys"][0]
        return (
            ("version a list", "version", edited(("version",), [])), ("version a number", "version", edited(("version",), 5)),
            ("basis_unit a number", "basis_unit", edited(("basis_unit",), 5)), ("basis_unit null", "basis_unit", edited(("basis_unit",), None)),
            ("fake as text", "fake", edited(("fake",), "false")), ("fake a number", "fake", edited(("fake",), 1)),
            ("no sample points", "sample_points", edited(("sample_points",), [])), ("sample_points an object", "sample_points", edited(("sample_points",), {})),
            ("a sample point a number", "sample_points", edited(("sample_points", 0), 5)),
            ("point_id a list", "point_id", edited(("sample_points", 0, "point_id"), [first])),
            ("point_id an object", "point_id", edited(("sample_points", 0, "point_id"), {"a": 1})),
            ("point_id a number", "point_id", edited(("sample_points", 0, "point_id"), 5)), ("point_id null", "point_id", edited(("sample_points", 0, "point_id"), None)),
            ("a point twice", "point_id", edited(("sample_points",), whole["sample_points"] + whole["sample_points"][:1])),
            ("a channel a number", "channels", edited(("channels", 0), 5)),
            ("entity_id a list", "entity_id", edited(("channels", 0, "entity_id"), ["light.den_front_left"])),
            ("entity_id an object", "entity_id", edited(("channels", 0, "entity_id"), {"a": 1})),
            ("entity_id a number", "entity_id", edited(("channels", 0, "entity_id"), 5)), ("entity_id null", "entity_id", edited(("channels", 0, "entity_id"), None)),
            ("channel_key a list", "channel_key", edited(("channels", 0, "channel_key"), [self.KEY])),
            ("a channel twice", "channel_key", edited(("channels",), whole["channels"] + whole["channels"][:1])),
            ("a bulb on two channels", "entity_id", edited(("channels",), whole["channels"] + [{**whole["channels"][0], "channel_key": "other.white"}])),
            ("cct a list", "cct", edited(("cct",), [])), ("basis a list", "basis", edited(("basis",), [])),
            ("kelvin twice", "temperature", edited(("cct", self.KEY, "kelvin"), [2700, 2700, 2700])),
            ("levels falling", "dimming", edited(("dimming", self.KEY), {"levels": [5, 3, 1], "g": [1, 2, 3]})),
            ("levels repeated", "dimming", edited(("dimming", self.KEY), {"levels": [5, 5], "g": [1, 2]})),
            ("a basis row empty", "sample point", edited(("basis", basis_key), {})),
            ("a basis row missing a block", "sample point", edited(("basis", basis_key), {"a": 1.0})),
            ("a 401-digit basis value", "numbers", edited(("basis", basis_key, first), 10 ** 400)),
            ("NaN in a basis row", "numbers", edited(("basis", basis_key, first), float("nan"))),
            ("error zero", "error", edited(("error",), 0)), ("error a list", "error", edited(("error",), [])),
            ("glare zero", "glare", edited(("glare",), 0)), ("glare a list", "glare", edited(("glare",), [])),
            ("a glare channel a number", "glare", edited(("glare",), {self.KEY: 5})),
            ("a glare eye a number", "glare", edited(("glare",), {self.KEY: {"room": 5}})),
            ("a glare row empty", "glare", edited(("glare",), {self.KEY: {"room": {}}})),
            ("a glare row of text", "glare", edited(("glare",), {self.KEY: {"room": {**self.ROW, "G": "x"}}})),
            ("a glare row of nulls", "glare", edited(("glare",), {self.KEY: {"room": {**self.ROW, "G": None, "sd": None}}})),
            ("a glare row with no status", "glare", edited(("glare",), {self.KEY: {"room": {"G": 0.1, "sd": 0.01}}})),
            ("spot_model zero", "spot_model", edited(("spot_model",), 0)), ("spot_model a list", "spot_model", edited(("spot_model",), [])),
            ("a spot channel a number", "spot_model", edited(("spot_model",), {self.KEY: 5})),
            ("a spot aim a number", "spot_model", edited(("spot_model",), {self.KEY: {"a": 5}})),
            ("a spot aim of text", "spot_model", edited(("spot_model",), {self.KEY: {"a": {first: "x"}}})))

    def test_each_is_refused_at_the_file_by_what_is_wrong(self):
        for label, whole in (("fake", fake_light.build()), ("Stage 1's shape", self.stage1_shaped())):
            for name, words, model in self.cases(whole):
                with self.subTest(label, name=name):
                    with self.assertRaises(events.BadFile) as refused:
                        light_adapter.check_shape(model, "model.json")
                    said = str(refused.exception)
                    self.assertTrue(said.startswith("model.json is not a light model: "), said)
                    self.assertIn(words, said)

    def test_what_the_coordinator_reads_stops_at_the_command_line_as_a_sentence(self):
        for name, _, model in self.cases(self.stage1_shaped()):
            if name in ("entity_id a list", "point_id a list", "version a list", "basis_unit a number", "no sample points", "a glare eye a number"):
                path = self.write("model.json", json.dumps(model))
                with self.subTest(name):
                    code, out, err = tool("run.py", *COORDINATOR, "--touch", "--light-model", path, "--out", self.tmp / "out")
                    self.refused(code, out, err, "%s is not a light model: " % path)
                    self.assertFalse((self.tmp / "out" / "log.jsonl").exists())

    def test_a_fake_marked_file_with_a_broken_spot_table_stops_too(self):
        for table in ({self.KEY: 5}, {self.KEY: {"a": 5}}):
            path = self.write("model.json", json.dumps({**fake_light.build(), "spot_model": table}))
            code, out, err = tool("run.py", *COORDINATOR, "--touch", "--light-model", path, "--out", self.tmp / "out")
            self.refused(code, out, err, "%s is not a light model: " % path)

    def test_nan_where_nothing_reads_it_is_a_sentence_and_not_a_codec_message(self):
        path = self.write("model.json", json.dumps({**self.stage1_shaped(), "note": float("nan")}))
        code, out, err = tool("run.py", *COORDINATOR, "--light-model", path, "--out", self.tmp / "out")
        self.refused(code, out, err, "%s is not a light model: " % path)

    def test_a_right_model_still_passes_with_every_table_it_may_carry(self):
        glare = {events.LIGHTS[0]: {"room": {"G": 0.3, "sd": 0.01}}}
        spot = {events.LIGHTS[0]: {"a": dict.fromkeys(("counter-1", "armchair-book", "wall-west"), 100.0)}}
        for model in (fake_light.build(), fake_light.build(glare=glare), fake_light.build(spot=spot), self.stage1_shaped(),
                      {**self.stage1_shaped(), "fake": False}, {**self.stage1_shaped(), "fake": None}, {**self.stage1_shaped(), "error": None},
                      {**self.stage1_shaped(), "error": {"median_pct": 1.5, "p90_pct": 9.0}}):
            light_adapter.check_shape(model, "model.json")

    def test_a_real_run_on_a_glare_table_still_logs(self):
        path = self.write("model.json", json.dumps(fake_light.build(glare={events.LIGHTS[0]: {"room": {"G": 0.3, "sd": 0.01}}})))
        code, out, err = tool("run.py", *COORDINATOR, "--touch", "--light-model", path, "--out", self.tmp / "out")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(tool("check.py", self.tmp / "out" / "log.jsonl")[0], 0)


HUGE = "1" + "0" * 400  # what json.loads reads as an int that float() and math.isfinite answer with OverflowError
ENDLESS_S = 20  # a run that loops over ticks that never end is killed here; a refusal takes under a second
ON = {"automatic": True, "gaming_session": False, "occupancy": "on", "revision": 1}


def edge_at(text):
    return "occupancy edge: entity=%s from=unknown to=on observed_at=%s\n" % (sources.DEN_OCCUPANCY, text)


def status_at(text):
    return '{"last_updated_ts":%s,"entity_id":"sensor.x","state":"on","attributes":%s}\n' % (text, json.dumps(ON))


def steps_at(*times):
    """A Frigate step list whose person count goes 1, 0, 1, so each step is an occupancy edge."""
    return "[%s]\n" % ",".join('{"at":%s,"topic":"frigate/den_oak/person","payload":"%d"}' % (t, (n + 1) % 2) for n, t in enumerate(times))


class NumbersNoFloatHoldsTest(Temp):
    """A ts of 401 digits is good JSON. It used to be an OverflowError traceback wherever a time is read."""

    def refuses(self, words, *argv):
        code, out, err = tool("run.py", *argv, "--out", self.tmp / "out")
        self.refused(code, out, err, words)
        self.assertFalse((self.tmp / "out" / "log.jsonl").exists())

    def test_a_frame_correction_command_or_bulb_row(self):
        correction = {"schema": "den-event-1", "t": 0, "seq": 0, "kind": "correction", "source": "correction_capture",
                      "data": {"correction_id": "c", "entity_id": events.LIGHTS[0], "after": {"on": True, "brightness": 100, "kelvin": 2700}, "source": "t"}}
        command = {"entity_id": events.LIGHTS[0], "revision": 1, "target": {"on": True, "brightness": 100, "kelvin": 2700}, "context_id": "c"}
        good = self.write("commands.jsonl", test_spine.command(T0 + 1, events.LIGHTS[0], test_spine.SCENE, "ctx-1") + "\n")
        for flag, line, words, argv in (
                ("--light-states", '{"ts":%s}\n' % HUGE, "line 1 is not a LightState", COORDINATOR),
                ("--corrections", json.dumps(correction).replace('"t": 0', '"t": %s' % HUGE) + "\n", "line 1 is not a correction-capture correction line",
                 ["--synthetic", "evening", "--policies", "incumbent"]),
                ("--live-commands", '{"time_fired_ts":%s,"event_data":%s}\n' % (HUGE, json.dumps(command)), "line 1 is not a den_lighting_command export row",
                 ["--synthetic", "evening", "--policies", "incumbent"]),
                ("--lights", '{"last_updated_ts":%s,"entity_id":"%s","state":"on","context_id":"","context_user_id":"","context_parent_id":""}\n' % (HUGE, events.LIGHTS[0]),
                 "line 1 is not a den bulb state export row", ["--live-commands", good, "--synthetic", "evening", "--policies", "incumbent"])):
            path = self.write("huge.jsonl", line)
            with self.subTest(flag):
                self.refuses(words, flag, path, *argv)

    def test_a_status_row_and_a_step_list(self):
        self.refuses("line 1 is not a den_lighting status sensor export row", "--status", self.write("status.jsonl", status_at(HUGE)), "--policies", "incumbent")
        for text in (HUGE, "NaN", "Infinity", "1e999"):
            with self.subTest(text):
                self.refuses("is not a Frigate MQTT step list", "--mqtt", self.write("steps.json", steps_at(text)), "--policies", "incumbent")

    def test_a_frame_whose_own_id_would_be_longer_than_a_line_keeps_room_for(self):
        self.refuses("line 1 is not a LightState", "--light-states", self.write("far.jsonl", '{"ts":-1e300}\n'), *COORDINATOR)

    def test_a_poll_row_at_a_time_no_clock_has(self):
        # On Windows the date itself raises; elsewhere it is a large negative epoch, which the runner refuses.
        path = self.write("poll.jsonl", '{"t":"0001-01-01T00:00:00","lights":{},"occ":"on"}\n')
        code, out, err = tool("run.py", "--poll", path, "--policies", "incumbent", "--out", self.tmp / "out")
        self.refused(code, out, err, "line 1 is not a poll row" if sys.platform == "win32" else "a time in one of them is wrong")

    def test_a_journal_edge_that_is_no_number_names_its_file(self):
        path = self.write("edges.txt", edge_at("1.2.3"))
        self.refuses("%s: an occupancy edge's observed_at is not a number" % path, "--journal", path, "--policies", "incumbent")

    def test_nested_too_deep_to_read_is_not_json(self):
        path = self.write("deep.json", "[" * 200000)
        self.refuses("%s is not JSON" % path, "--surfaces", path, *COORDINATOR)


class TimesNoRunCanReachTest(Temp):
    """A time a float holds can still be one no run ends at: first + k * tick never passes it, and run.py grew a heap of ticks
    until the machine ran out of memory. These are killed at ENDLESS_S if that comes back."""

    def refuses(self, words, *argv):
        code, out, err = tool("run.py", *argv, "--out", self.tmp / "out", timeout=ENDLESS_S)
        self.refused(code, out, err, words)
        self.assertFalse((self.tmp / "out" / "log.jsonl").exists())

    def test_a_journal_edge_past_what_a_float_holds(self):
        path = self.write("edges.txt", edge_at(HUGE))
        self.refuses("%s: an occupancy edge's observed_at is not a number" % path, "--journal", path, "--policies", "incumbent")

    def test_a_time_far_past_any_tick(self):
        for flag, name, text in (("--mqtt", "steps.json", steps_at("1e300")), ("--status", "status.jsonl", status_at("1e300")),
                                 ("--journal", "edges.txt", edge_at("9" * 300))):
            with self.subTest(flag):
                self.refuses("a time in one of them is wrong", flag, self.write(name, text), "--policies", "incumbent")

    def test_a_time_no_clock_here_can_print_leaves_no_log_behind(self):
        # Past the year 3000 or before 1970 Windows' gmtime stops, and run.py used to write the log and then stop on [Errno 22].
        for text in ("1e11", "-5"):
            with self.subTest(text):
                self.refuses("a time in one of them is wrong", "--status", self.write("status.jsonl", status_at(text)), "--policies", "incumbent")

    def test_good_times_too_far_apart_to_tick_through(self):
        # --mqtt with no --mqtt-t0 sits in 1970: beside real facts that is 56 years of 2 s ticks.
        self.refuses("a time in one of them is wrong", "--mqtt", self.write("steps.json", steps_at("0", "1793764800")), "--policies", "incumbent")
        poll = '{"t":"%s","lights":{},"occ":"on"}\n{"t":"%s","lights":{},"occ":"off"}\n' % ("1000-01-01T00:00:00+00:00", "9000-01-01T00:00:00+00:00")
        self.refuses("a time in one of them is wrong", "--poll", self.write("poll.jsonl", poll), "--policies", "incumbent")

    def test_the_runner_itself_refuses_them(self):
        one = (T0, "occupancy", {"value": "on", "via": "t"}, "synthetic")
        far = (T0 + 1e12, "occupancy", {"value": "off", "via": "t"}, "synthetic")
        for inputs, kwargs in (([one], {"tick_s": 0}), ([one], {"tick_s": -1}), ([one], {"tick_s": 1e-300}), ([one], {"tail_s": 1e300}), ([one, far], {}),
                               ([(float("inf"), *one[1:])], {}), ([(float("nan"), *one[1:]), one], {}), ([one], {"tick_s": float("nan")})):
            with self.subTest(kwargs=kwargs, times=[item[0] for item in inputs]), self.assertRaisesRegex(ValueError, "a time in one of them is wrong"):
                run_cut_short(inputs, **kwargs)

    def test_a_long_evening_still_runs(self):
        # A week of ticks. The refusal is about times no run reaches, and a real replay never comes near it.
        one = (T0, "occupancy", {"value": "on", "via": "t"}, "synthetic")
        self.assertEqual(runner.check_span([one, (T0 + 7 * 86400, "occupancy", {"value": "off", "via": "t"}, "synthetic")], 2.0, runner.TAIL_S), None)

    def test_about_a_year_of_ticks_is_where_it_stops_as_readme_says(self):
        one = (T0, "occupancy", {"value": "on", "via": "t"}, "synthetic")
        self.assertEqual(runner.check_span([one, (T0 + 360 * 86400, "occupancy", {"value": "off", "via": "t"}, "synthetic")], 2.0, runner.TAIL_S), None)
        with self.assertRaisesRegex(ValueError, "a time in one of them is wrong"):
            runner.check_span([one, (T0 + 380 * 86400, "occupancy", {"value": "off", "via": "t"}, "synthetic")], 2.0, runner.TAIL_S)


def run_cut_short(inputs, **kwargs):
    """runner.check_span's view of runner.run's arguments, so a guard that is gone fails here and doesn't loop in this process."""
    runner.check_span(inputs, kwargs.get("tick_s", 2.0), kwargs.get("tail_s", runner.TAIL_S))


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

    def test_a_line_at_a_time_no_float_holds_is_exit_2_with_its_number(self):
        head, first, *rest = self.text.split("\n")
        event = json.loads(first)
        event["t"] = 10 ** 400
        path = self.write("huge.jsonl", "\n".join([head, events.dumps(event), *rest]))
        for name in ("check.py", "score.py", "validate.py"):
            with self.subTest(name):
                code, out, err = tool(name, path)
                self.refused(code, out, err, "line 2")

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

    def test_a_mark_wrong_inside_is_its_line_and_not_a_traceback(self):
        whole = test_score.mark(T0, T0 + 10)
        region = whole["R_eval"]
        text = json.dumps(whole)
        for name, line in (("surface_id a list", json.dumps({**whole, "R_eval": {**region, "surface_id": []}})),
                           ("surface_id an object", json.dumps({**whole, "R_eval": {**region, "surface_id": {"a": 1}}})),
                           ("surface_id a number", json.dumps({**whole, "R_eval": {**region, "surface_id": 5}})),
                           ("surface_id null", json.dumps({**whole, "R_eval": {**region, "surface_id": None}})),
                           ("R_eval text", json.dumps({**whole, "R_eval": "surface_id"})),
                           ("end of 401 digits", text.replace('"end": %s' % (T0 + 10), '"end": %s' % HUGE)),
                           ("start of 401 digits", text.replace('"start": %s' % T0, '"start": %s' % HUGE)),
                           ("placement_sd of 401 digits", text.replace('"placement_sd": 3', '"placement_sd": %s' % HUGE)),
                           ("start NaN", text.replace('"start": %s' % T0, '"start": NaN')),
                           ("placement_sd Infinity", text.replace('"placement_sd": 3', '"placement_sd": Infinity'))):
            self.assertNotEqual(line, text, name)
            path = self.write("marks-shape.jsonl", line + "\n")
            with self.subTest(name), self.assertRaisesRegex(ValueError, "%s line 1" % path.name):
                score.read_marks(path)
        path = self.write("marks-shape.jsonl", json.dumps({**whole, "R_eval": {**region, "surface_id": []}}) + "\n")
        code, out, err = tool("score.py", self.log, "--preview", "--marks", path)
        self.refused(code, out, err, "line 1")
        self.assertEqual(len(score.read_marks(self.write("good.jsonl", text + "\n"))), 1)

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

    def test_truth_of_the_wrong_shape_is_refused_as_any_other_file_is(self):
        good = {"schema": "reading-shadow-truth-1", "blocks": [{"block": "b1", "reading": True, "start": T0, "end": T0 + 60}]}
        for name, value in (("a list", []), ("a number", 5), ("null", None), ("another schema", {**good, "schema": "other"}), ("no schema", {"blocks": []}),
                            ("no blocks", {"schema": good["schema"]}), ("blocks a number", {**good, "blocks": 5}), ("blocks an object", {**good, "blocks": {"a": 1}}),
                            ("a block a number", {**good, "blocks": [1]}), ("a block with nothing", {**good, "blocks": [{}]}),
                            ("start as text", {**good, "blocks": [{"start": "soon", "end": T0, "reading": True}]}),
                            ("no end", {**good, "blocks": [{"start": T0, "reading": True}]}), ("end null", {**good, "blocks": [{"start": T0, "end": None, "reading": True}]}),
                            ("a start past any clock", {**good, "blocks": [{"block": "b1", "reading": True, "start": 1e300, "end": 2e300}]})):
            path = self.write("truth-shape.json", json.dumps(value))
            with self.subTest(name):
                code, out, err = tool("validate.py", self.log, "--truth", path)
                self.refused(code, out, err, "%s is not reading-shadow-truth-1" % path)
                self.assertEqual(out, "")  # no check line and no overall line: it stops as it does on a log it can't read

    def test_truth_is_read_before_any_check_runs(self):
        with mock.patch.object(check, "check", side_effect=AssertionError("a check ran first")), self.assertRaises(events.BadFile):
            validate.report(self.log, self.tmp / "absent.json")

    def test_truth_that_is_missing_or_a_folder_is_refused_too(self):
        for path in (self.tmp / "absent.json", self.tmp):
            with self.subTest(path.name):
                code, out, err = tool("validate.py", self.log, "--truth", path)
                self.refused(code, out, err, "cannot read %s" % path)
                self.assertEqual(out, "")

    def test_truth_of_the_right_shape_with_nothing_to_say_still_reads_n_a(self):
        path = self.write("truth.json", json.dumps({"schema": "reading-shadow-truth-1", "blocks": [{"block": "b1", "reading": False, "start": T0, "end": T0 + 60}]}))
        code, out, err = tool("validate.py", self.log, "--truth", path)
        self.assertEqual(code, 0, out + err)
        self.assertIn("n/a: no reading blocks", out)
        with mock.patch("builtins.print"):
            run.main(["--synthetic", "evening", "--policies", "incumbent", "--out", str(self.tmp / "no-need")])
        code, out, err = tool("validate.py", self.tmp / "no-need" / "log.jsonl", "--truth", path)
        self.assertEqual(code, 0, out + err)
        self.assertIn("n/a: need is not in this log", out)

    def test_a_truth_file_of_good_blocks_still_gives_its_lines_on_this_log(self):
        code, out, err = tool("validate.py", self.log, "--truth", self.tmp / "run" / "truth.json")
        self.assertEqual(code, 0, out + err)
        self.assertRegex(out, r"  b2 \(cue ")


@unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
class WrongInsideALogTest(Temp):
    """A log can be good JSON, canonical and in order, and the wrong shape inside. check.py, score.py and validate.py replay it
    and read its lines, so a wrong value used to meet them as a TypeError or KeyError traceback, exit 1."""

    TOOLS = (("check.py",), ("score.py",), ("score.py", "--preview"), ("validate.py",))

    def setUp(self):
        super().setUp()
        with mock.patch("builtins.print"):
            run.main([*COORDINATOR, "--touch", "--out", str(self.tmp / "run")])
        self.lines = (self.tmp / "run" / "log.jsonl").read_text("ascii").split("\n")[:-1]
        self.header = json.loads(self.lines[0])
        self.at = next(n for n, p in enumerate(self.header["policies"]) if p["module"] == "coordinator")

    def with_header(self, change, name="log.jsonl"):
        header = copy.deepcopy(self.header)
        change(header)
        return self.write(name, "\n".join([events.dumps(header), *self.lines[1:]]) + "\n")

    def with_config(self, key, value, name="log.jsonl"):
        def change(header):
            if value is KeyError:
                del header["policies"][self.at]["config"][key]
            else:
                header["policies"][self.at]["config"][key] = value
        return self.with_header(change, name)

    def with_event(self, wanted, change, name="log.jsonl"):
        """The log with the first event wanted() picks changed; the line stays canonical, so only what reads it can tell."""
        lines = list(self.lines)
        for n, line in enumerate(lines[1:], 1):
            event = json.loads(line)
            if wanted(event):
                change(event)
                lines[n] = events.dumps(event)
                return self.write(name, "\n".join(lines) + "\n")
        self.fail("no such event in the log")

    def test_a_header_of_the_wrong_shape_is_refused_where_it_is_read(self):
        entry = self.header["policies"][self.at]
        cases = [("policies", value) for value in (None, 5, "x", {}, [5], ["x"], [None], [{}], [{"module": 5}], [{"module": "coordinator"}],
                                                   [{**entry, "config": 5}], [{**entry, "config": None}], [{**entry, "code_sha256": None}],
                                                   [{**entry, "module": ["coordinator"]}], [{**entry, "id": 5}], [{**entry, "config_sha256": []}])]
        cases += [("sources", value) for value in (None, 5, "x", {})]
        cases += [(key, value) for key in ("tick_s", "tail_s") for value in (None, "x", [], {}, True, -1, 10 ** 400)]
        cases += [("tick_s", 0)]
        cases += [(key, value) for key in ("plant", "synthetic", "assume_occupied") for value in (None, 5, "x", [])]
        cases += [(key, value) for key in ("shown", "actuation") for value in (None, 5, [], {})]
        for key, value in cases:
            path = self.write("header.jsonl", "\n".join([json.dumps({**self.header, key: value}, sort_keys=True, separators=(",", ":")), *self.lines[1:]]) + "\n")
            with self.subTest(key=key, value=str(value)[:60]), self.assertRaisesRegex(ValueError, "its header") as refused:
                events.read_run(path)
            self.assertIn(key if key != "policies" else "polic", str(refused.exception))

    def test_a_right_header_of_any_run_still_reads(self):
        self.assertEqual(events.read_run(self.tmp / "run" / "log.jsonl")[0], self.header)

    def test_each_tool_says_so_in_one_sentence(self):
        for label, path in (("policies 5", self.with_header(lambda h: h.update(policies=5), "a.jsonl")),
                            ("tick_s text", self.with_header(lambda h: h.update(tick_s="x"), "b.jsonl")),
                            ("a policy of no shape", self.with_header(lambda h: h.update(policies=[5]), "c.jsonl"))):
            for tool_name, *extra in self.TOOLS:
                with self.subTest(label, tool=tool_name, extra=extra):
                    code, out, err = tool(tool_name, path, *extra)
                    self.refused(code, out, err, "its header")

    def test_a_logged_config_of_the_wrong_shape_is_a_sentence(self):
        cases = {"compensation": 5, "gate": 5, "light_model": 5, "code": 5, "looks": 5, "preference": None, "light_states": [{}]}
        for key, value in cases.items():
            path = self.with_config(key, value)
            with self.subTest(key):
                result = check.check(path)
                self.assertFalse(result["ok"])
                self.assertTrue(result.get("unreadable"), result)
                self.assertIn("coordinator", result["reason"])
                lines, code = score.report(path, preview=True)
                self.assertEqual(code, 2, lines)
                self.assertIn("refused", lines[0])
        for name, value, words in (("compensation", 5, "wrong shape"), ("light_states", [{}], "wrong shape"), ("light_states", 5, "wrong shape")):
            path = self.with_config(name, value)
            for tool_name, *extra in self.TOOLS:
                with self.subTest(name, value=value, tool=tool_name, extra=extra):
                    code, out, err = tool(tool_name, path, *extra)
                    self.refused(code, out, err, words)

    def test_a_config_with_a_key_gone_is_refused_by_score_as_it_is_by_check(self):
        for key in ("compensation", "gate", "light_model"):
            path = self.with_config(key, KeyError)
            with self.subTest(key):
                code, out, err = tool("check.py", path)
                self.assertNotIn("Traceback", out + err)
                self.assertIn(code, (1, 2), out + err)
                code, out, err = tool("score.py", path, "--preview")
                self.refused(code, out, err, "config changed")

    def test_a_policy_this_tool_does_not_have(self):
        code, out, err = tool("check.py", self.with_header(lambda h: h["policies"][self.at].update(module="nope")))
        self.refused(code, out, err, "nope")
        real = {"module": "events", "code_sha256": events.sha256_file(HERE / "events.py")}
        path = self.with_header(lambda h: h["policies"][self.at].update(real))
        code, out, err = tool("check.py", path)
        self.refused(code, out, err, "events")

    def test_a_tick_that_never_passes_the_tail_is_not_an_endless_replay(self):
        code, out, err = tool("check.py", self.with_header(lambda h: h.update(tick_s=1e-300)), timeout=ENDLESS_S)
        self.assertEqual(code, 1, out + err)
        self.assertIn("a time in one of them is wrong", out)

    def test_a_source_that_is_not_text_is_its_line(self):
        path = self.with_event(lambda e: True, lambda e: e.update(source=[]))
        for tool_name, *extra in self.TOOLS:
            with self.subTest(tool=tool_name, extra=extra):
                code, out, err = tool(tool_name, path, *extra)
                self.refused(code, out, err, "line 2")

    def test_an_input_line_of_the_wrong_shape_has_no_traceback(self):
        for label, wanted, change in (("occupancy value a list", lambda e: e["kind"] == "occupancy", lambda e: e["data"].update(value=[])),
                                      ("light on as text", lambda e: e["kind"] == "light_observed", lambda e: e["data"].update(on="x", brightness="y", kelvin=[])),
                                      ("entity_id a list", lambda e: e["kind"] == "light_observed", lambda e: e["data"].update(entity_id=["light.den_front_left"])),
                                      ("a fact's inside", lambda e: e["kind"] == "facts", lambda e: e["data"].update(observation=5, observed_at=[]))):
            path = self.with_event(wanted, change)
            for tool_name, *extra in self.TOOLS:
                with self.subTest(label, tool=tool_name, extra=extra):
                    code, out, err = tool(tool_name, path, *extra)
                    self.assertNotIn("Traceback", out + err)
                    self.assertIn(code, (1, 2), out + err)
                    self.assertTrue((out + err).strip())

    def test_a_derived_line_wrong_inside_fails_replay_with_no_traceback(self):
        def decision(e):
            return e["kind"] == "decision" and e["source"] == "decide"

        def mode(e):
            return e["kind"] == "mode" and e["data"]["holds"]

        for label, wanted, change in (("no skipped", decision, lambda e: e["data"].pop("skipped")),
                                      ("no applied_scene", decision, lambda e: e["data"].pop("applied_scene")),
                                      ("no requested_scene", decision, lambda e: e["data"].pop("requested_scene")),
                                      ("no predicted", decision, lambda e: e["data"].pop("predicted")),
                                      ("no inputs", decision, lambda e: e["data"].pop("inputs")),
                                      ("a mode's holds a number", mode, lambda e: e["data"].update(holds=5)),
                                      ("a hold with no since", mode, lambda e: e["data"]["holds"][0].pop("since")),
                                      ("no person fields", lambda e: e["kind"] == "person_state", lambda e: e["data"].pop("focused_engagement"))):
            path = self.with_event(wanted, change)
            for tool_name, *extra in self.TOOLS:
                with self.subTest(label, tool=tool_name, extra=extra):
                    code, out, err = tool(tool_name, path, *extra)
                    self.assertNotIn("Traceback", out + err)
                    self.assertIn(code, (1, 2), out + err)
                    self.assertTrue((out + err).strip())
            code, out, _ = tool("check.py", path)
            self.assertEqual(code, 1, label)
            self.assertIn("differs", out)

    def test_score_fails_its_replay_line_on_such_a_log_and_says_why_it_stops(self):
        def decision(e):
            return e["kind"] == "decision" and e["source"] == "decide"

        def steps(e):
            for entry in e["data"]["applied_scene"].values():
                entry["steps"] = 1.5

        for label, wanted, change in (("no applied_scene", decision, lambda e: e["data"].pop("applied_scene")),
                                      ("no inputs", decision, lambda e: e["data"].pop("inputs")),
                                      ("no predicted", decision, lambda e: e["data"].pop("predicted")),
                                      ("steps a number", decision, steps),
                                      ("a mode's holds a number", lambda e: e["kind"] == "mode", lambda e: e["data"].update(holds=1e300)),
                                      ("no person fields", lambda e: e["kind"] == "person_state", lambda e: e["data"].pop("focused_engagement"))):
            path = self.with_event(wanted, change)
            with self.subTest(label):
                code, out, err = tool("score.py", path, "--preview")
                self.assertEqual((code, err), (1, ""), out + err)
                self.assertRegex(out, r"(?m)^replay: FAIL \(line \d+ differs\)$")
                self.assertRegex(out, r"(?m)^verdict: none \(preview\): replay failed, and a line of this log is the wrong shape for what comes after it: \w+Error: ")

    def test_a_scored_run_on_such_a_log_fails_with_a_verdict(self):
        gate = test_score.closed_gate(self.tmp)
        with test_score.real_backend():
            log = test_score.replay_log(self.tmp, gate, test_score.real_model(self.tmp), surfaces=test_score.fake_map())[0]
            self.lines = Path(log).read_text("ascii").split("\n")[:-1]
            path = self.with_event(lambda e: e["kind"] == "decision" and e["source"] == "decide", lambda e: e["data"].pop("applied_scene"), "scored.jsonl")
            lines, code = score.report(path, gate_path=gate)
        self.assertEqual(code, 1, lines)
        self.assertIn("replay: FAIL", "\n".join(lines))
        self.assertTrue(lines[-1].startswith("verdict: FAIL: replay failed, and a line of this log is the wrong shape"), lines[-1])

    def test_score_refuses_an_input_line_no_replay_can_run(self):
        # The incumbent reads a bulb's kelvin as a number, so this one can't replay at all: check.py's exit 2, in score.py's words too.
        path = self.with_event(lambda e: e["kind"] == "light_observed", lambda e: e["data"].update(on=True, brightness=[], kelvin=[]))
        code, out, err = tool("check.py", path)
        self.refused(code, out, err, "the log does not replay")
        code, out, err = tool("score.py", path, "--preview")
        self.refused(code, out, err, "the log does not replay")
        self.assertRegex(err.strip(), r"TypeError: .* \(\S+\.py line \d+\)$")
        self.assertEqual(out, "")

    def test_a_log_that_replays_is_this_tools_own_if_score_trips_on_it(self):
        # What replays byte for byte is what this code wrote, so a TypeError after that is a bug, for the last resort to say where.
        with mock.patch.object(score, "structural", side_effect=TypeError("surprise")), self.assertRaisesRegex(TypeError, "surprise"):
            score.report(self.tmp / "run" / "log.jsonl", preview=True)

    def test_validate_reports_such_a_line_check_by_check(self):
        # The review's probe: every check prints a line and the overall line follows, on a decide log and a coordinator log.
        with mock.patch("builtins.print"):
            run.main(["--synthetic", "evening", "--touch", "--policies", "need", "incumbent", "decide", "--out", str(self.tmp / "decide")])
        decide_lines = (self.tmp / "decide" / "log.jsonl").read_text("ascii").split("\n")[:-1]
        cases = []
        for lines in (decide_lines, self.lines):
            self.lines = lines
            cases += [self.with_event(lambda e: e["kind"] == "decision" and e["source"] == "decide", lambda e: e["data"].update(requested=e["data"].pop("requested_scene")), "a%d.jsonl" % len(cases)),
                      self.with_event(lambda e: e["kind"] == "decision" and e["source"] == "decide" and e["data"]["skipped"],
                                      lambda e: [s.update(entity_id=s.pop("channel")) for s in e["data"]["skipped"]], "b%d.jsonl" % len(cases)),
                      self.with_event(lambda e: e["kind"] == "mode" and e["data"]["holds"], lambda e: [h.update(opened=h.pop("since")) for h in e["data"]["holds"]], "c%d.jsonl" % len(cases)),
                      self.with_event(lambda e: e["kind"] == "decision" and e["source"] == "decide", lambda e: e["data"].pop("skipped"), "d%d.jsonl" % len(cases))]
        for path in cases:
            with self.subTest(path.name):
                code, out, err = tool("validate.py", path)
                self.assertEqual(code, 1, out + err)
                self.assertNotIn("Traceback", out + err)
                said = out.splitlines()
                self.assertEqual([line.split(":")[0] for line in said[:6]], [*validate.NAMES, "overall"], out)
                self.assertTrue(said[5].startswith("overall: FAIL"), out)

    def test_a_checkout_with_no_den_lighting_is_not_called_a_wrong_shape(self):
        log = self.tmp / "run" / "log.jsonl"
        err = io.StringIO()
        with mock.patch.object(incumbent, "find_policy", return_value=None):
            with mock.patch("sys.stderr", err):
                self.assertEqual(check.main([str(log)]), 2)
            lines, passed = validate.report(log)
        self.assertEqual(err.getvalue().count("\n"), 1)
        self.assertIn("den_lighting policy.py not found", err.getvalue())
        self.assertNotIn("wrong shape", err.getvalue())
        self.assertFalse(passed)
        self.assertTrue(lines[0].startswith("replay: FAIL: replay refused: den_lighting policy.py not found"), lines[0])

    def test_a_coordinator_log_with_no_incumbent_is_refused_by_name(self):
        with mock.patch("builtins.print"):
            run.main(["--synthetic", "evening", "--touch", "--policies", "need", "coordinator", "--out", str(self.tmp / "alone")])
        for extra in ((), ("--preview",)):
            code, out, err = tool("score.py", self.tmp / "alone" / "log.jsonl", *extra)
            self.refused(code, out, err, "no incumbent in this log")
        self.assertEqual(tool("check.py", self.tmp / "alone" / "log.jsonl")[0], 0)


@unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
class NamesACodePageCantWriteTest(Temp):
    """PowerShell 5.1's | Set-Content makes stdout a pipe in the console's code page, strict, and a surface or block can be named
    anything. score.py stopped on a traceback at its last print, and run.py with the log written and neither of the files after it."""

    ENV = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    NAME = "\u043a\u0440\u0435\u0441\u043b\u043e"

    def test_each_tool_writes_such_a_name_as_an_escape_and_goes_on(self):
        surfaces = self.write("surfaces.json", json.dumps({self.NAME: ["counter-1/0"], self.NAME + "-gone": [], "room": ["counter-1/0", "counter-1/1"]}))
        out = self.tmp / "run"
        code, said, err = tool("run.py", *COORDINATOR, "--touch", "--surfaces", surfaces, "--out", out, env=self.ENV)
        self.assertEqual((code, err), (0, ""), said + err)
        self.assertIn("\\u043a", said)  # a surface with no block is named in the summary
        self.assertTrue((out / "decisions.jsonl").exists() and (out / "surfaces.json").exists())
        code, said, err = tool("score.py", out / "log.jsonl", "--preview", env=self.ENV)
        self.assertNotIn("Traceback", said + err)
        self.assertEqual(err, "")
        self.assertIn("\\u043a", said)  # every surface is listed
        code, said, err = tool("check.py", out / "log.jsonl", env=self.ENV)
        self.assertEqual((code, err), (0, ""), said + err)
        truth = {"schema": "reading-shadow-truth-1", "blocks": [{"block": self.NAME, "reading": True, "start": T0, "end": T0 + 60}]}
        code, said, err = tool("validate.py", out / "log.jsonl", "--truth", self.write("truth.json", json.dumps(truth)), env=self.ENV)
        self.assertEqual((code, err), (0, ""), said + err)
        self.assertIn("\\u043a", said)  # a block is named on its line


class LastResortTest(Temp):
    """What no check before it named. A tool still says one sentence and exits 2, and the sentence says where, so a bug in the
    tool itself isn't lost for want of a traceback."""

    def said(self, call):
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            code = call()
        return code, err.getvalue()

    def stops(self, tool_name, call):
        code, err = self.said(call)
        self.assertEqual(code, 2)
        self.assertEqual(len(err.strip().splitlines()), 1, err)
        self.assertTrue(err.startswith("%s stopped on an input of the wrong shape, or a bug of its own: KeyError: 'surprise'" % tool_name), err)
        self.assertRegex(err.strip(), r"\(\S+\.py line \d+\)$")

    def test_each_tool_on_what_it_did_not_expect(self):
        surprise = KeyError("surprise")
        with mock.patch.object(run, "main", side_effect=surprise):
            self.stops("run.py", run.cli)
        with mock.patch.object(check, "check", side_effect=surprise):
            self.stops("check.py", lambda: check.main(["log"]))
        with mock.patch.object(score, "report", side_effect=surprise):
            self.stops("score.py", lambda: score.main(["log"]))
        with mock.patch.object(coordinator, "load_gate", side_effect=surprise):
            self.stops("score.py", lambda: score.main(["--slots"]))
        with mock.patch.object(events, "read_run", side_effect=surprise):
            self.stops("validate.py", lambda: validate.main(["log"]))

    def test_it_gets_there_from_the_command_line(self):
        code, out, err = tool("run.py", "--synthetic", "evening", "--policies", "events", "--out", self.tmp / "out")
        self.refused(code, out, err, "run.py stopped on an input of the wrong shape, or a bug of its own: AttributeError")

    def test_a_keyboard_interrupt_is_not_caught(self):
        with mock.patch.object(run, "main", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            run.cli()


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

    def test_it_says_what_is_refused_and_what_only_fails(self):
        # Each clause is run above: WrongInsideAModelTest, WrongInsideALogTest, LogsTest's marks and truth, TimesNoRunCanReachTest
        # and LastResortTest. The sentence once said never a traceback and they did not all hold.
        said = " ".join((HERE / "README.md").read_text("utf-8").split())
        for words in ("Any input that is missing, not JSON or the wrong shape is one sentence and exit 2, never a traceback",
                      "what is inside a light model, a mark or `--truth`", "a log's header, its logged config and its input lines",
                      "a time past any clock", "`--mqtt` with no `--mqtt-t0`", "a failed check, not a refusal, with exit 1", "`validate.py` goes on check by check",
                      "`score.py` fails its replay line",
                      "stopped on an input of the wrong shape, or a bug of its own", "a name it can't write comes out as a `\\uXXXX` escape"):
            self.assertIn(words, said)
        check_says = said.split("`check.py LOG` re-imports")[1].split("```")[0]
        self.assertIn("wrong shape to replay", check_says)
        truth_says = said.split("`--truth` adds, as information only")[1].split("Tests:")[0]
        self.assertIn("missing or the wrong shape is refused like any other file", truth_says)
        self.assertIn("A log with no `incumbent` in it", said.split("### What a scored run needs")[1].split("### What it gates")[0])

    def test_it_says_nothing_has_run_the_verify_line_here(self):
        said = " ".join((HERE / "README.md").read_text("utf-8").split())
        self.assertIn("den-core's tests don't run that line: they check only that it names these files, and it needs a `verify.py` with `--surfaces`", said)

    def test_the_release_fragment_says_the_same(self):
        fragment = HERE.parents[1] / "changes" / "unreleased" / "den-core-rung-9-coordinator.md"
        if not fragment.exists():  # a release moves it to the archive
            return
        said = " ".join(fragment.read_text("utf-8").split())
        for words in ("is refused with one sentence and exit 2 when it is UTF-16 (what PowerShell 5.1's `>` writes), missing, malformed or the wrong shape",
                      "`--truth`", "A log of the right shape with a line changed or wrong inside fails its replay with exit 1", "`validate.py` says so check by check",
                      "a time no run could reach is refused"):
            self.assertIn(words, said)


if __name__ == "__main__":
    unittest.main()
