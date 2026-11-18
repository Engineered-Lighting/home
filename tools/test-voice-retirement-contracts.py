#!/usr/bin/env python3
"""Offline voice retirement contracts; all Docker/HTTP/host probes are fakes.

Git Bash on Windows (or bash on Linux) is needed for the startup contracts.
No real Docker CLI, cameras, HA, audio, model or GPU calls are made.
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "stack/services/video-labeler"))
from videolabeler import db, gpuexclusive


class LegacyRecoveryTests(unittest.TestCase):
    def test_persisted_pre_retirement_recovery_retains_history_without_voice_start(self):
        for entrypoint in ("restore", "startup_recover", "supervisor_tick"):
            with self.subTest(entrypoint=entrypoint), tempfile.TemporaryDirectory() as temp:
                path = str(Path(temp) / "ledger.db")
                units = [{"kind": "container", "name": name}
                         for name in sorted(gpuexclusive.RETIRED_VOICE_CONTAINERS)]
                units.append({"kind": "container", "name": "hav-vllm"})
                expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
                with db.open_db(path) as conn:
                    db.apply_migrations(conn)
                    ledger_id = conn.execute(
                        "INSERT INTO exclusive_state (units_json, deadline_at, created_at) VALUES (?, ?, ?)",
                        (json.dumps(units), expired, expired)).lastrowid
                started = []
                gx = gpuexclusive.GpuExclusive(path, SimpleNamespace(),
                                              docker=SimpleNamespace(start=started.append))
                with patch.dict(os.environ):
                    os.environ.pop("VOICE_ENABLED", None)
                    result = gx.restore(ledger_id) if entrypoint == "restore" else getattr(gx, entrypoint)()
                self.assertTrue(result)
                self.assertEqual(started, ["hav-vllm"])
                with db.open_db(path) as conn:
                    row = conn.execute("SELECT * FROM exclusive_state WHERE id = ?", (ledger_id,)).fetchone()
                restored = json.loads(row["units_json"])
                self.assertEqual([u["name"] for u in restored], [u["name"] for u in units])
                self.assertTrue(all(u["restore_result"] == "skipped_retired" for u in restored[:-1]))
                self.assertEqual(row["state"], "restored")
                self.assertTrue(row["restored_at"])
                self.assertEqual(gx.startup_recover(), [])
                self.assertEqual(started, ["hav-vllm"])

    def test_explicit_opt_in_allows_recorded_voice_restore(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "ledger.db")
            units = [{"kind": "container", "name": name} for name in gpuexclusive.RETIRED_VOICE_CONTAINERS]
            now = datetime.now(timezone.utc).isoformat()
            with db.open_db(path) as conn:
                db.apply_migrations(conn)
                ledger_id = conn.execute(
                    "INSERT INTO exclusive_state (units_json, deadline_at, created_at) VALUES (?, ?, ?)",
                    (json.dumps(units), now, now)).lastrowid
            started = []
            gx = gpuexclusive.GpuExclusive(path, SimpleNamespace(), docker=SimpleNamespace(start=started.append))
            with patch.dict(os.environ, {"VOICE_ENABLED": "1"}):
                self.assertTrue(gx.restore(ledger_id))
            self.assertEqual(set(started), gpuexclusive.RETIRED_VOICE_CONTAINERS)


class ConfigurationTests(unittest.TestCase):
    def test_only_four_voice_services_gain_voice_profile_and_s2s_remains_opt_in(self):
        text = (REPO / "stack/docker-compose.yml").read_text(encoding="utf-8")
        blocks = dict(re.findall(r"^  ([\w-]+):\s*\n(.*?)(?=^  [\w-]+:|\Z)", text, re.M | re.S))
        voice = {name for name, block in blocks.items() if re.search(r'^    profiles: \["voice"\]', block, re.M)}
        self.assertEqual(voice, {"wyoming-parakeet", "kokoro-tts", "chatterbox-tts", "wyoming-kokoro"})
        for name in ("s2s-model", "personaplex-bridge"):
            self.assertIn('profiles: ["s2s"]', blocks[name])
        for name in ("vllm", "vision-sidecar", "metrics-sidecar"):
            self.assertNotRegex(blocks[name], r"^    profiles:")

    def test_morning_speech_guard_does_not_gate_lighting_branch(self):
        text = (REPO / "ha-config/packages/homeai_good_morning.yaml").read_text(encoding="utf-8")
        self.assertRegex(text, r"homeai_voice_enabled:\n(?:[^\n]*\n){2}    initial: false")
        energize = text.split("# 1) Energize", 1)[1].split("# 2) Speak", 1)[0]
        speech = text.split("# 2) Speak", 1)[1].split("# 3) Fire", 1)[0]
        self.assertIn("action: input_text.set_value", energize)
        self.assertIn("input_boolean.living_lights_morning_energize_enabled", energize)
        self.assertNotIn("homeai_voice_enabled", energize)
        self.assertRegex(speech, r'entity_id: input_boolean.homeai_voice_enabled\s+state: "on"')
        self.assertIn("action: assist_satellite.announce", speech)


class StartupTests(unittest.TestCase):
    def run_stack(self, verb="up", voice="0"):
        bash = os.environ.get("VOICE_TEST_BASH") or (r"C:\Program Files\Git\bin\bash.exe" if os.name == "nt" else shutil.which("bash"))
        self.assertTrue(bash and Path(bash).is_file(), "bash is required; VOICE_TEST_BASH can specify its exact path")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            log = root / "calls.txt"
            wrapper = root / "fake-host.sh"
            wrapper.write_text('''#!/usr/bin/env bash
set -eu
docker() {
  printf 'docker profiles=%s args=%s\\n' "${COMPOSE_PROFILES-unset}" "$*" >> "$VOICE_TEST_LOG"
  case "$1" in inspect) echo healthy ;; compose) echo 'mock compose accepted' ;; *) return 99 ;; esac
}
curl() { printf 'curl %s\\n' "$*" >> "$VOICE_TEST_LOG"; }
ip() { echo '100: from all to 192.168.0.0/24 lookup main'; }
nvidia-smi() { echo 'mock GPU'; }
export -f docker curl ip nvidia-smi
exec bash "$VOICE_TEST_SCRIPT" "$@"
''', encoding="utf-8", newline="\n")
            env = {**os.environ, "VOICE_ENABLED": voice, "COMPOSE_PROFILES": "voice,s2s",
                   "VOICE_TEST_LOG": log.as_posix(), "VOICE_TEST_SCRIPT": (REPO / "stack/scripts/stack.sh").as_posix(),
                   "STACK_DIR": (REPO / "stack").as_posix()}
            result = subprocess.run([str(bash), str(wrapper), verb, "vllm"], env=env,
                                    capture_output=True, text=True, timeout=15)
            return result, log.read_text() if log.exists() else ""

    def test_default_up_passes_core_smoke_without_voice_and_ignores_inherited_profiles(self):
        result, calls = self.run_stack()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("docker profiles= args=compose up -d", calls)
        self.assertNotIn("--profile", calls)
        for name in gpuexclusive.RETIRED_VOICE_CONTAINERS:
            self.assertNotIn(name, calls)
        for port in (8000, 8091, 8092):
            self.assertIn(f"localhost:{port}/", calls)
        for port in (8880, 8881, 10300, 10301):
            self.assertNotIn(f":{port}", calls)
        self.assertIn("All smoke tests passed.", result.stdout)

    def test_opt_in_enables_only_voice_profile(self):
        result, calls = self.run_stack("logs", "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("docker profiles= args=compose --profile voice logs", calls)
        self.assertNotIn("s2s", calls)

    def test_invalid_opt_in_refuses_before_docker(self):
        result, calls = self.run_stack("up", "true")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(calls, "")


if __name__ == "__main__":
    unittest.main()
