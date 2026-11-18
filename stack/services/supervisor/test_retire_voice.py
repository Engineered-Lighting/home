"""No Docker, network, services, models or credentials: metadata-only fakes."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("retire_voice", Path(__file__).with_name("retire_voice.py"))
retire = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(retire)


class FakeDocker:
    def __init__(self):
        self.rows = {name: {"id": f"{i + 1:064x}", "name": name, "running": i != 1,
                           "restart": {"Name": "unless-stopped", "MaximumRetryCount": 0}}
                     for i, name in enumerate(retire.TARGETS)}
        self.calls = []
        self.fail_stop = False

    def daemon_id(self):
        return "reviewed-local-daemon"

    def inspect(self, identity):
        return copy.deepcopy(next((r for r in self.rows.values()
                                   if identity in (r["name"], r["id"])), None))

    def mutate(self, args):
        row = next(r for r in self.rows.values() if r["id"] == args[-1])
        self.calls.append(list(args))
        if args[0] == "update":
            name, _, retries = args[1].split("=", 1)[1].partition(":")
            row["restart"] = {"Name": name, "MaximumRetryCount": int(retries or 0)}
        elif args[0] == "stop":
            if self.fail_stop:
                raise retire.RetirementError("simulated stop failure")
            row["running"] = False
        elif args[0] == "start":
            row["running"] = True
        else:
            raise AssertionError("Unexpected Docker operation")


class VoiceRetirementTests(unittest.TestCase):
    def test_default_is_read_only_with_no_receipt(self):
        docker = FakeDocker()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "baseline.json"
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(retire.main(["--receipt", str(path)], client=docker), 0)
            self.assertTrue(json.loads(output.getvalue())["dry_run"])
            self.assertFalse(path.exists())
            self.assertEqual(docker.calls, [])

    def test_apply_stops_only_fixed_ids_and_keeps_rollback_baseline(self):
        docker = FakeDocker()
        baseline = retire.plan(docker)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "baseline.json"
            retire.execute(docker, copy.deepcopy(baseline), path, docker.daemon_id())
            receipt = json.loads(path.read_text())
            self.assertEqual(receipt["state"], "retired")
            self.assertEqual(receipt["targets"], baseline["targets"])
            self.assertEqual({c[-1] for c in docker.calls}, {r["id"] for r in baseline["targets"]})
            self.assertEqual({c[0] for c in docker.calls}, {"update", "stop"})
            self.assertEqual(sum(c[0] == "stop" for c in docker.calls), 3)
            self.assertTrue(all(not r["running"] and r["restart"]["Name"] == "no" for r in docker.rows.values()))
            docker.calls.clear()
            retire.execute(docker, receipt, path, docker.daemon_id(), rollback=True)
            self.assertEqual(docker.rows, {r["name"]: r for r in baseline["targets"]})
            self.assertEqual(sum(c[0] == "start" for c in docker.calls), 3)

    def test_partial_failure_keeps_backup_without_automatic_start(self):
        docker = FakeDocker()
        docker.fail_stop = True
        baseline = retire.plan(docker)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "baseline.json"
            with self.assertRaises(retire.RetirementError):
                retire.execute(docker, copy.deepcopy(baseline), path, docker.daemon_id())
            receipt = json.loads(path.read_text())
            self.assertEqual(receipt["state"], "partial_retirement")
            self.assertEqual(receipt["targets"], baseline["targets"])
            self.assertFalse(any(c[0] == "start" for c in docker.calls))

    def test_wrong_daemon_replaced_target_or_foreign_name_fails_before_mutation(self):
        for failure in ("daemon", "replacement", "foreign"):
            docker = FakeDocker()
            receipt = retire.plan(docker)
            if failure == "replacement":
                docker.rows[retire.TARGETS[-1]]["id"] = "f" * 64
            if failure == "foreign":
                receipt["targets"][-1]["name"] = "hav-vllm"
            with tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "baseline.json"
                with self.assertRaises(retire.RetirementError):
                    retire.execute(docker, receipt, path, "wrong-daemon" if failure == "daemon" else docker.daemon_id())
                self.assertEqual(docker.calls, [])
                self.assertFalse(path.exists())

    def test_existing_backup_is_never_overwritten_by_new_retirement(self):
        docker = FakeDocker()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "baseline.json"
            path.write_text("original backup")
            with self.assertRaises(FileExistsError):
                retire.execute(docker, retire.plan(docker), path, docker.daemon_id())
            self.assertEqual(path.read_text(), "original backup")
            self.assertEqual(docker.calls, [])


if __name__ == "__main__":
    unittest.main()
