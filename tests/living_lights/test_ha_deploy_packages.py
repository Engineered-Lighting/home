"""Tests for tools/ha-deploy-packages.py, the offline deploy planner for
Home Assistant package files.

Run: python3 -m unittest tests/living_lights/test_ha_deploy_packages.py

Deterministic and offline: it checks the alias slugify against Home
Assistant's rule (and, when the ha-sim venv is present, against
homeassistant.util.slugify itself, still without any network), helper
extraction from a small package, the core-restart flag, host-path mapping,
and an end-to-end --plan run against two real package files in a temporary
output directory. It never contacts Home Assistant.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "tools" / "ha-deploy-packages.py"
GOOD_MORNING = "ha-config/packages/homeai_good_morning.yaml"
GRADIENT = "ha-config/packages/living_lights_gradient.yaml"
HA_SIM_PYTHON = Path("/home/marcelo-lima/.venvs/ha-sim/bin/python")


def _load_tool():
    spec = importlib.util.spec_from_file_location("ha_deploy_packages", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tool = _load_tool()

SLUG_CASES = [
    ("Living Lights \u2014 asleep ON (house quiet overnight)",
     "living_lights_asleep_on_house_quiet_overnight"),
    ("HomeAI \u2014 good morning (greeting + energize)",
     "homeai_good_morning_greeting_energize"),
    ("Living Lights - sofa gradient actuator", "living_lights_sofa_gradient_actuator"),
    ("Living Lights \u2014 asleep OFF \U0001f305", "living_lights_asleep_off"),
    ("Lights\u2014asleep", "lights_asleep"),
    ("Marcelo's 1,000 lights \u2728 caf\u00e9", "marcelo_s_1000_lights_cafe"),
    ("  a--b__c  d  ", "a_b_c_d"),
    ("\u2600\ufe0f", "unknown"),
    ("\u00dcn\u00efcode \u00df \u00e6", "unicode_ss_ae"),
    # cp1252 mojibake of an em-dash, as emitted by build-living-lights-actuators.py:
    # Home Assistant transliterates it to "aEUR" + quote, so the live id has aeur.
    ("Living Lights \u00e2\u20ac\u201d lighting activity ledger (light.sink)",
     "living_lights_aeur_lighting_activity_ledger_light_sink"),
    # Latin-1 / Latin Extended glyphs text_unidecode maps that NFKD alone does not.
    ("\u00d0\u00f0 \u1e9e", "dd_ss"),
    # Vulgar fractions: the translit table is consulted before NFKD would split
    # U+00BD into "1" + U+2044 + "2" (which HA turns into 1_2, not 12).
    ("a\u00bdb \u00bc \u00be", "a1_2b_1_4_3_4"),
]

# Strings whose characters the stdlib slugify drops where the host would
# transliterate them; dropped_chars() must expose exactly those characters.
DROPPED_CASES = [
    ("Living Lights \u2014 asleep OFF \U0001f305", "\U0001f305"),
    ("\u4e2d\u6587 lights", "\u4e2d\u6587"),
    ("\u0420\u0443\u0441\u0441\u043a\u0438\u0439", "\u0420\u0443\u0441\u0441\u043a\u0438\u0439"),
    ("caf\u00e9 \u2014 Marcelo's \u00bd", ""),
    ("", ""),
    (None, ""),
]

SMALL_PACKAGE = """
input_boolean:
  living_lights_asleep:
    name: "Living Lights \u2014 asleep"
    initial: off
  living_lights_enabled:
    name: enabled
input_number:
  living_lights_asleep_cap_pct:
    min: 0
    max: 100
    initial: 30
  living_lights_no_initial:
    min: 0
    max: 1
input_text:
  living_lights_asleep_writer:
    max: 255
    initial: ""
input_datetime:
  living_lights_last_latch:
    has_date: true
    has_time: true
input_select:
  living_lights_house_mode:
    name: "Living Lights \u2014 house mode"
    options: [normal, focus, away]
    initial: normal
template:
  - binary_sensor:
      - name: x
        state: "{{ true }}"
automation:
  - id: some_id
    alias: "Living Lights \u2014 asleep ON (house quiet overnight)"
    triggers: []
    actions: []
  - alias: "Living Lights \u2014 asleep OFF"
    id: other_id
    triggers: []
    actions: []
script:
  homeai_return_home:
    alias: "HomeAI \u2014 return-home scene"
    sequence: []
secret_thing: !secret api_token
included: !include_dir_named packages
"""

# The same package at "base": one automation keeps its id but changes alias.
SMALL_PACKAGE_AT_BASE = """
automation:
  - id: some_id
    alias: "Living Lights \u2014 asleep ON (old wording)"
    triggers: []
    actions: []
  - alias: "Living Lights \u2014 asleep OFF"
    id: other_id
    triggers: []
    actions: []
"""

# A package with domains the planner does not handle and an alias whose
# characters the stdlib slugify drops; used through --repo on a temp tree.
UNHANDLED_PACKAGE = """
mqtt:
  sensor:
    - name: "Dining Left Avg Speed"
      state_topic: "frigate/events"
shell_command:
  ping_host: "true"
input_select:
  house_mode:
    options: [normal, away]
    initial: normal
automation:
  - id: cjk_id
    alias: "\u4e2d\u6587 lights"
    triggers: []
    actions: []
"""


def _walk_strings(node, keys=("alias", "name")):
    """Every string under an `alias` or `name` key anywhere in a document."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k in keys and isinstance(v, str):
                yield v
            yield from _walk_strings(v, keys)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_strings(item, keys)


class SlugifyTests(unittest.TestCase):
    def test_cases(self):
        for alias, expected in SLUG_CASES:
            with self.subTest(alias=alias):
                self.assertEqual(tool.slugify(alias), expected)

    def test_empty(self):
        self.assertEqual(tool.slugify(""), "")
        self.assertEqual(tool.slugify(None), "")

    def test_dropped_chars(self):
        for text, expected in DROPPED_CASES:
            with self.subTest(text=text):
                self.assertEqual(tool.dropped_chars(text), expected)
        # Every repository alias/name must slug without dropping anything.
        for path in _ha_config_yaml_files():
            doc = tool.load_ha_yaml(path.read_text(encoding="utf-8"))
            for s in _walk_strings(doc):
                with self.subTest(path=path.name, text=s):
                    self.assertEqual(tool.dropped_chars(s), "")

    @unittest.skipUnless(HA_SIM_PYTHON.exists(), "ha-sim venv not present")
    def test_matches_home_assistant_slugify(self):
        """Cross-check against homeassistant.util.slugify (offline import) for
        the fixed cases and every `alias` and `name` string (automations,
        scripts, helpers, template entities, mqtt entities, ...) in
        ha-config/*.yaml and ha-config/packages/*.yaml."""
        strings = [a for a, _ in SLUG_CASES] + [t for t, _ in DROPPED_CASES if t]
        seen = set(strings)
        files = _ha_config_yaml_files()
        self.assertTrue(any(p.name == "homeai_proactive.yaml" for p in files))
        for path in files:
            doc = tool.load_ha_yaml(path.read_text(encoding="utf-8"))
            for s in _walk_strings(doc):
                if s not in seen:
                    seen.add(s)
                    strings.append(s)
        self.assertGreater(len(strings), 200)
        script = ("import json,sys\nfrom homeassistant.util import slugify\n"
                  "print(json.dumps([slugify(a) for a in json.load(sys.stdin)]))\n")
        proc = subprocess.run([str(HA_SIM_PYTHON), "-c", script], input=json.dumps(strings),
                              capture_output=True, text=True, check=True)
        expected = json.loads(proc.stdout)
        for text, want in zip(strings, expected):
            with self.subTest(text=text):
                if tool.dropped_chars(text):
                    # Known divergence: the stdlib path drops non-Latin scripts
                    # that the host transliterates, and says so.
                    continue
                self.assertEqual(tool.slugify(text), want)


def _ha_config_yaml_files() -> list[Path]:
    cfg = REPO / "ha-config"
    return sorted(cfg.glob("*.yaml")) + sorted((cfg / "packages").glob("*.yaml"))


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.doc = tool.load_ha_yaml(SMALL_PACKAGE)

    def test_custom_tags_do_not_break_parsing(self):
        self.assertEqual(self.doc["secret_thing"], "<secret api_token>")
        self.assertEqual(self.doc["included"], "<include_dir_named packages>")

    def test_helpers(self):
        helpers = {h["entity_id"]: h for h in tool.extract_helpers(self.doc)}
        self.assertEqual(set(helpers), {
            "input_boolean.living_lights_asleep",
            "input_boolean.living_lights_enabled",
            "input_number.living_lights_asleep_cap_pct",
            "input_number.living_lights_no_initial",
            "input_text.living_lights_asleep_writer",
            "input_datetime.living_lights_last_latch",
            "input_select.living_lights_house_mode",
        })
        self.assertEqual(helpers["input_select.living_lights_house_mode"]["initial"], "normal")
        self.assertTrue(helpers["input_select.living_lights_house_mode"]["has_initial"])
        self.assertIs(helpers["input_boolean.living_lights_asleep"]["initial"], False)
        self.assertTrue(helpers["input_boolean.living_lights_asleep"]["has_initial"])
        self.assertFalse(helpers["input_boolean.living_lights_enabled"]["has_initial"])
        self.assertIsNone(helpers["input_boolean.living_lights_enabled"]["initial"])
        self.assertEqual(helpers["input_number.living_lights_asleep_cap_pct"]["initial"], 30)
        self.assertFalse(helpers["input_number.living_lights_no_initial"]["has_initial"])
        self.assertEqual(helpers["input_text.living_lights_asleep_writer"]["initial"], "")
        self.assertTrue(helpers["input_text.living_lights_asleep_writer"]["has_initial"])
        self.assertFalse(helpers["input_datetime.living_lights_last_latch"]["has_initial"])

    def test_format_initial(self):
        helpers = {h["entity_id"]: h for h in tool.extract_helpers(self.doc)}
        meta = lambda e: {"has_initial": helpers[e]["has_initial"],
                          "declared_initial": helpers[e]["initial"]}
        self.assertEqual(tool.format_initial("input_boolean.living_lights_asleep",
                                             meta("input_boolean.living_lights_asleep")), "off")
        self.assertEqual(tool.format_initial("input_number.living_lights_asleep_cap_pct",
                                             meta("input_number.living_lights_asleep_cap_pct")), "30")
        self.assertEqual(tool.format_initial("input_boolean.living_lights_enabled",
                                             meta("input_boolean.living_lights_enabled")),
                         "(none declared)")

    def test_automations_use_alias_not_id(self):
        autos = tool.extract_automations(self.doc)
        self.assertEqual([a["entity_id"] for a in autos], [
            "automation.living_lights_asleep_on_house_quiet_overnight",
            "automation.living_lights_asleep_off",
        ])
        self.assertEqual([a["id"] for a in autos], ["some_id", "other_id"])
        self.assertEqual([a["dropped_chars"] for a in autos], ["", ""])
        self.assertEqual([a["renamed"] for a in autos], [False, False])

    def test_renamed_automations_flagged_by_id(self):
        autos = tool.extract_automations(self.doc)
        base = tool.extract_automations(tool.load_ha_yaml(SMALL_PACKAGE_AT_BASE))
        renamed = tool.flag_renamed_automations(autos, base)
        self.assertEqual([a["id"] for a in renamed], ["some_id"])
        self.assertEqual(autos[0]["entity_id_at_base"],
                         "automation.living_lights_asleep_on_old_wording")
        self.assertTrue(autos[0]["renamed"])
        self.assertEqual(autos[1]["entity_id_at_base"], "automation.living_lights_asleep_off")
        self.assertFalse(autos[1]["renamed"])
        # No base document (new file at base): nothing is flagged.
        autos = tool.extract_automations(self.doc)
        self.assertEqual(tool.flag_renamed_automations(autos, tool.extract_automations(None)), [])
        self.assertEqual([a["entity_id_at_base"] for a in autos], [None, None])

    def test_unhandled_domains(self):
        self.assertEqual(tool.unhandled_domains(self.doc), ["included", "secret_thing"])
        self.assertEqual(tool.unhandled_domains(tool.load_ha_yaml(UNHANDLED_PACKAGE)),
                         ["mqtt", "shell_command"])
        self.assertEqual(tool.unhandled_domains({"automation": [], "input_select": {}}), [])
        self.assertEqual(tool.unhandled_domains(None), [])

    def test_scripts(self):
        scripts = tool.extract_scripts(self.doc)
        self.assertEqual(scripts, [{"key": "homeai_return_home",
                                    "alias": "HomeAI \u2014 return-home scene",
                                    "entity_id": "script.homeai_return_home"}])

    def test_reload_domains_in_protocol_order(self):
        self.assertEqual(tool.reload_domains(self.doc), [
            "template", "automation", "script",
            "input_boolean", "input_number", "input_text", "input_datetime", "input_select"])
        self.assertEqual(tool.reload_domains(tool.load_ha_yaml(UNHANDLED_PACKAGE)),
                         ["automation", "input_select"])
        self.assertEqual(tool.reload_domains({"automation": []}), ["automation"])
        self.assertEqual(tool.reload_domains(None), [])


class RestartFlagTests(unittest.TestCase):
    def test_custom_component_python_requires_restart(self):
        self.assertTrue(tool.requires_core_restart(
            "ha-config/extended_openai_conversation/functions/living_lights.py"))
        self.assertTrue(tool.requires_core_restart(
            "./ha-config/extended_openai_conversation/__init__.py"))

    def test_other_files_do_not(self):
        self.assertFalse(tool.requires_core_restart(GOOD_MORNING))
        self.assertFalse(tool.requires_core_restart("ha-config/homeai_proactive.yaml"))
        self.assertFalse(tool.requires_core_restart(
            "ha-config/extended_openai_conversation/functions/living_lights.yaml"))
        self.assertFalse(tool.requires_core_restart("tools/living_lights_tv_states.py"))
        self.assertFalse(tool.requires_core_restart(
            "ha-config/extended_openai_conversation_other/x.py"))


class HostMapTests(unittest.TestCase):
    def test_defaults(self):
        m = tool.parse_host_map(None)
        self.assertEqual(tool.host_target(GOOD_MORNING, m),
                         "/config/packages/homeai_good_morning.yaml")
        self.assertEqual(tool.host_target("ha-config/homeai_proactive.yaml", m),
                         "/config/packages/homeai_proactive.yaml")
        self.assertEqual(tool.host_target(
            "ha-config/extended_openai_conversation/functions/living_lights.py", m),
            "/config/custom_components/extended_openai_conversation/functions/living_lights.py")
        self.assertIsNone(tool.host_target("tools/x.py", m))

    def test_explicit_map_and_longest_prefix(self):
        m = tool.parse_host_map(
            "ha-config/packages=/cfg/pk, ha-config/packages/sub=/elsewhere,"
            "ha-config/homeai_proactive.yaml=/cfg/pk/homeai_proactive.yaml")
        self.assertEqual(tool.host_target("ha-config/packages/a.yaml", m), "/cfg/pk/a.yaml")
        self.assertEqual(tool.host_target("ha-config/packages/sub/b.yaml", m), "/elsewhere/b.yaml")
        self.assertEqual(tool.host_target("ha-config/homeai_proactive.yaml", m),
                         "/cfg/pk/homeai_proactive.yaml")

    def test_bad_entry(self):
        with self.assertRaises(SystemExit):
            tool.parse_host_map("no-equals-sign")


class RepoPathTests(unittest.TestCase):
    def test_relative_paths_are_normalised(self):
        self.assertEqual(tool.resolve_repo_path(REPO, "./" + GOOD_MORNING), GOOD_MORNING)
        self.assertEqual(tool.resolve_repo_path(REPO, GOOD_MORNING + "/"), GOOD_MORNING)

    def test_absolute_path_inside_repo_becomes_relative(self):
        self.assertEqual(tool.resolve_repo_path(REPO, str(REPO / GOOD_MORNING)), GOOD_MORNING)
        self.assertEqual(tool.resolve_repo_path(REPO, str(REPO / "ha-config" / ".." / GOOD_MORNING)),
                         GOOD_MORNING)

    def test_absolute_path_outside_repo_is_refused(self):
        with self.assertRaises(SystemExit) as ctx:
            tool.resolve_repo_path(REPO, "/etc/hostname")
        self.assertIn("outside the repository", str(ctx.exception))
        self.assertIn("/etc/hostname", str(ctx.exception))


def _git_show_sha(path: str, base: str = "origin/main") -> str | None:
    proc = subprocess.run(["git", "-C", str(REPO), "show", f"{base}:{path}"],
                          capture_output=True, check=False)
    return hashlib.sha256(proc.stdout).hexdigest() if proc.returncode == 0 else None


class PlanEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name) / "plan"
        env = dict(os.environ, HA_TOKEN="not-a-real-token-value")
        cls.proc = subprocess.run(
            [sys.executable, str(TOOL), "--plan", "--files", GOOD_MORNING, GRADIENT,
             "--base", "origin/main", "--out", str(cls.out), "--host", "ha.example"],
            cwd=str(REPO), capture_output=True, text=True, env=env, check=False)
        cls.receipt = json.loads((cls.out / "receipt.json").read_text())
        cls.checklist = (cls.out / "deploy-checklist.md").read_text()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_exit_and_outputs(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr)
        self.assertTrue((self.out / "receipt.json").is_file())
        self.assertTrue((self.out / "deploy-checklist.md").is_file())

    def test_receipt_header(self):
        r = self.receipt
        self.assertEqual(r["schema"], "home-ha-package-deploy/v1")
        self.assertEqual(r["status"], "PLANNED (not run)")
        self.assertEqual(r["base"], "origin/main")
        self.assertEqual(r["host"], "ha.example")
        self.assertIsInstance(r["prepared_at"], float)
        self.assertEqual(r["deploy_log"], [])
        self.assertFalse(r["core_restart_required"])
        self.assertEqual(list(r["files"]), [GOOD_MORNING, GRADIENT])

    def test_file_hashes_and_targets(self):
        for path in (GOOD_MORNING, GRADIENT):
            with self.subTest(path=path):
                entry = self.receipt["files"][path]
                data = (REPO / path).read_bytes()
                self.assertEqual(entry["sha256_after"], hashlib.sha256(data).hexdigest())
                self.assertEqual(entry["bytes_after"], len(data))
                self.assertEqual(entry["target"], "/config/packages/" + Path(path).name)
                self.assertEqual(entry["sha256_expected_on_host_before"], _git_show_sha(path))
                self.assertIsNone(entry["sha256_observed_on_host"])
                self.assertFalse(entry["core_restart_required"])
                staged = Path(entry["staged"])
                self.assertEqual(staged.parent, self.out)
                self.assertEqual(hashlib.sha256(staged.read_bytes()).hexdigest(),
                                 entry["sha256_after"])

    def test_receipt_automations_and_helpers(self):
        r = self.receipt
        self.assertIn("automation.homeai_good_morning_greeting_energize", r["automation_entities"])
        self.assertIn("automation.living_lights_sofa_gradient_actuator", r["automation_entities"])
        gm = r["files"][GOOD_MORNING]
        self.assertEqual(gm["automations"][0]["alias"],
                         "HomeAI \u2014 good morning (greeting + energize)")
        self.assertEqual(gm["automations"][0]["entity_id"],
                         "automation.homeai_good_morning_greeting_energize")
        self.assertEqual(gm["reloads"], ["automation"])
        self.assertEqual(gm["unhandled_domains"], [])
        self.assertEqual(gm["renamed_automations"], [])
        self.assertEqual(r["renamed_automations"], [])
        self.assertEqual(r["alias_dropped_chars"], [])
        gr = r["files"][GRADIENT]
        self.assertEqual(gr["reloads"], ["template", "automation", "input_boolean", "input_number"])
        # The gradient package carries an mqtt block the planner cannot reload:
        # recorded per file and at the top level, warned about in the header
        # and on stderr, never turned into an mqtt/reload call.
        self.assertEqual(gr["unhandled_domains"], ["mqtt"])
        self.assertEqual(r["unhandled_domains"], {GRADIENT: ["mqtt"]})
        header = self.checklist[:self.checklist.index("## 1.")]
        self.assertIn(f"WARNING: unhandled top-level domain(s) in `{GRADIENT}`: `mqtt`.", header)
        self.assertEqual(self.checklist.count("WARNING"), 1)
        self.assertIn(f"WARNING: unhandled domain(s) in {GRADIENT}: mqtt", self.proc.stderr)
        self.assertNotIn("/api/services/mqtt/reload", self.checklist)
        lam = r["helper_values"]["input_number.living_lights_gradient_lambda"]
        self.assertEqual(lam, {"declared_initial": 0.6, "has_initial": True,
                               "before": None, "after": None})
        en = r["helper_values"]["input_boolean.living_lights_gradient_enabled"]
        self.assertFalse(en["has_initial"])
        self.assertEqual(r["reloads"], ["template", "automation", "input_boolean", "input_number"])

    def test_checklist_commands_in_protocol_order(self):
        c = self.checklist
        markers = [
            "sha256sum /config/packages/homeai_good_morning.yaml",
            "ha backups new --help",
            "ha backups new --homeassistant",
            ".bak.\\$ts",
            "scp -P 22222 ",
            "root@ha.example:/config/packages/living_lights_gradient.yaml",
            "ha core check",
            "/api/services/template/reload",
            "/api/services/automation/reload",
            "/api/services/input_boolean/reload",
            "/api/services/input_number/reload",
            "## 8. Core restart",
            "Not required",
            "/api/states/automation.homeai_good_morning_greeting_energize",
            "/api/states/automation.living_lights_sofa_gradient_actuator",
            "## 11. Receipt",
            "Rollback:",
        ]
        positions = [c.index(m) for m in markers]
        self.assertEqual(positions, sorted(positions), markers)
        self.assertNotIn("/api/services/script/reload", c)
        self.assertNotIn("ha core restart", c)
        self.assertIn(self.receipt["files"][GRADIENT]["sha256_after"], c)
        self.assertIn(self.receipt["files"][GRADIENT]["sha256_expected_on_host_before"] or "ABSENT", c)
        self.assertIn("/api/states/input_number.living_lights_gradient_lambda", c)
        self.assertIn("declared initial = 0.6", c)

    def test_checklist_helper_initial_semantics(self):
        """`initial` applies to a new helper or on a core restart, not on reload."""
        c = self.checklist
        step3 = c[c.index("## 3."):c.index("## 4.")]
        step10 = c[c.index("## 10."):c.index("## 11.")]
        self.assertIn("applied only when the helper is new on the host", step3)
        self.assertIn("core restart (step 8)", step3)
        self.assertIn("keeps the live value of an existing helper", step3)
        self.assertNotIn("revert", c)
        self.assertIn("A reload of an existing helper keeps its value", step10)
        self.assertIn("input_select/select_option", step10)

    def test_checklist_post_check_registry_fallback(self):
        c = self.checklist
        step9 = c[c.index("## 9."):c.index("## 10.")]
        self.assertIn("may differ when the automation was renamed", step9)
        self.assertIn("`GET /api/states` filtered by `attributes.id`", step9)
        gm_id = self.receipt["files"][GOOD_MORNING]["automations"][0]["id"]
        self.assertIsNotNone(gm_id)
        self.assertIn(f'select(.attributes.id == "{gm_id}")', step9)
        self.assertIn("http://ha.example:8123/api/states | jq", step9)
        self.assertNotIn("run it first for the renamed", step9)

    def test_checklist_flags_renamed_automation(self):
        """A base/after alias slug change shows in the header and in step 9."""
        receipt = json.loads(json.dumps(self.receipt))
        auto = receipt["files"][GOOD_MORNING]["automations"][0]
        auto["entity_id_at_base"] = "automation.homeai_good_morning_old"
        auto["renamed"] = True
        entry = {"id": auto["id"], "entity_id_at_base": auto["entity_id_at_base"],
                 "entity_id": auto["entity_id"]}
        receipt["files"][GOOD_MORNING]["renamed_automations"] = [entry]
        receipt["renamed_automations"] = [dict(entry, file=GOOD_MORNING)]
        staged = {p: e["staged"] for p, e in receipt["files"].items()}
        c = tool.render_checklist(receipt, staged)
        header = c[:c.index("## 1.")]
        self.assertIn(f"WARNING: automation id `{auto['id']}` in `{GOOD_MORNING}` changed its "
                      "alias slug: `automation.homeai_good_morning_old` at base -> "
                      f"`{auto['entity_id']}` after", header)
        step9 = c[c.index("## 9."):c.index("## 10.")]
        self.assertIn(f"run it first for the renamed automation id(s) `{auto['id']}`", step9)

    def test_no_secret_material(self):
        for text in (self.checklist, json.dumps(self.receipt), self.proc.stdout):
            self.assertNotIn("not-a-real-token-value", text)
            self.assertNotIn("rtsp://", text)
        self.assertIn("Bearer $HA_TOKEN", self.checklist)

    def test_restart_flag_end_to_end(self):
        """A custom-component .py in the set flips the restart flag and step 8."""
        py = sorted((REPO / "ha-config" / "extended_openai_conversation").glob("*.py"))
        if not py:
            self.skipTest("no custom-component python file in the repo")
        rel = py[0].relative_to(REPO).as_posix()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "plan2"
            proc = subprocess.run(
                [sys.executable, str(TOOL), "--plan", "--files", GOOD_MORNING, rel,
                 "--base", "origin/main", "--out", str(out)],
                cwd=str(REPO), capture_output=True, text=True, check=False)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            receipt = json.loads((out / "receipt.json").read_text())
            checklist = (out / "deploy-checklist.md").read_text()
        self.assertTrue(receipt["core_restart_required"])
        self.assertTrue(receipt["files"][rel]["core_restart_required"])
        self.assertEqual(receipt["files"][rel]["reloads"], [])
        self.assertIn("REQUIRED: a custom-component Python file", checklist)
        self.assertIn('"ha core restart"', checklist)
        self.assertIn("/config/custom_components/extended_openai_conversation/", checklist)

    def test_absolute_files_path_inside_repo(self):
        """An absolute path under the repo is recorded under its relative path."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "abs"
            proc = subprocess.run(
                [sys.executable, str(TOOL), "--plan", "--files", str(REPO / GOOD_MORNING),
                 "--base", "origin/main", "--out", str(out)],
                cwd=str(tmp), capture_output=True, text=True, check=False)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            receipt = json.loads((out / "receipt.json").read_text())
        self.assertEqual(list(receipt["files"]), [GOOD_MORNING])
        self.assertEqual(receipt["files"][GOOD_MORNING]["target"],
                         "/config/packages/homeai_good_morning.yaml")

    def test_absolute_files_path_outside_repo_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / "homeai_good_morning.yaml"
            outside.write_text("automation: []\n")
            proc = subprocess.run(
                [sys.executable, str(TOOL), "--plan", "--files", str(outside),
                 "--base", "origin/main", "--out", str(Path(tmp) / "p")],
                cwd=str(REPO), capture_output=True, text=True, check=False)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("outside the repository", proc.stderr)
            self.assertIn(str(outside), proc.stderr)
            self.assertNotIn("not a file in the worktree", proc.stderr)
            self.assertFalse((Path(tmp) / "p").exists())

    def test_unhandled_domains_and_dropped_chars_warn(self):
        """A package with mqtt/shell_command and a CJK alias: the receipt
        records the unhandled domains, input_select is reloaded and recorded,
        and the checklist header plus stderr carry WARNING lines."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            (repo / "ha-config" / "packages").mkdir(parents=True)
            rel = "ha-config/packages/odd.yaml"
            (repo / rel).write_text(UNHANDLED_PACKAGE, encoding="utf-8")
            out = Path(tmp) / "plan3"
            proc = subprocess.run(
                [sys.executable, str(TOOL), "--plan", "--files", rel, "--repo", str(repo),
                 "--base", "origin/main", "--out", str(out)],
                cwd=str(tmp), capture_output=True, text=True, check=False)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            receipt = json.loads((out / "receipt.json").read_text())
            checklist = (out / "deploy-checklist.md").read_text()
        entry = receipt["files"][rel]
        self.assertTrue(entry["new_at_base"])
        self.assertEqual(entry["unhandled_domains"], ["mqtt", "shell_command"])
        self.assertEqual(receipt["unhandled_domains"], {rel: ["mqtt", "shell_command"]})
        self.assertEqual(entry["reloads"], ["automation", "input_select"])
        self.assertEqual(receipt["reloads"], ["automation", "input_select"])
        self.assertEqual(receipt["helper_values"]["input_select.house_mode"]["declared_initial"],
                         "normal")
        self.assertEqual(entry["automations"][0]["entity_id"], "automation.lights")
        self.assertEqual(entry["automations"][0]["dropped_chars"], "\u4e2d\u6587")
        self.assertEqual(receipt["alias_dropped_chars"], [{
            "file": rel, "alias": "\u4e2d\u6587 lights", "dropped_chars": "\u4e2d\u6587",
            "entity_id": "automation.lights"}])
        header = checklist[:checklist.index("## 1.")]
        self.assertIn(f"WARNING: unhandled top-level domain(s) in `{rel}`: `mqtt`, `shell_command`",
                      header)
        self.assertIn("WARNING: characters dropped from alias `\u4e2d\u6587 lights`", header)
        self.assertIn("U+4E2D U+6587", header)
        self.assertIn("/api/services/input_select/reload", checklist)
        self.assertNotIn("/api/services/mqtt/reload", checklist)
        self.assertIn(f"WARNING: unhandled domain(s) in {rel}: mqtt, shell_command", proc.stderr)
        self.assertIn("WARNING: characters dropped from alias", proc.stderr)
        self.assertIn("U+4E2D U+6587", proc.stderr)

    def test_unmapped_file_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [sys.executable, str(TOOL), "--plan", "--files", "AGENTS.md",
                 "--base", "origin/main", "--out", str(Path(tmp) / "p")],
                cwd=str(REPO), capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("UNMAPPED", proc.stderr)


if __name__ == "__main__":
    unittest.main()
