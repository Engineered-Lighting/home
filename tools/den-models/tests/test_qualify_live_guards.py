"""Guard qualification decisions, offline only: no ssh, systemd, host or model access."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
SPEC = importlib.util.spec_from_file_location(
    "qualify_live_guards", Path(__file__).resolve().parents[1] / "ops" / "qualify_live_guards.py")
guards = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guards)


def control(at=100, *, failing=(), missing=(), blocked=None, dependencies=1, stale_source=None):
    sources = {name: {"ok": name not in failing, "observed_at": at} for name in guards.SOURCES if name not in missing}
    for name in failing:
        sources[name]["reason"] = "dependencies_lineage_invalid" if name == "dependencies" else name + "_unavailable"
    if stale_source is not None:
        sources[stale_source]["observed_at"] = at - 3
    return {"observed_at": at, "admission_blocked": bool(failing) if blocked is None else blocked,
            "blocked_reasons": [name + "_unavailable" for name in failing], "qwen_busy": False,
            "dependencies": [{"transform_id": "den-oak@2026-11-13.1"}] * dependencies, "sources": sources}


def sample(at=100, *, age=.2, **kwargs):
    exact = kwargs.pop("ops_mirror_exact", True)
    return guards.observation(control(at, **kwargs), {"schema": "den.qwen-queue-observation/v1", "observed_at": at},
                              now=at + age, ops_mirror_exact=exact)


def run(count, start=100, **kwargs):
    """Distinct receipts, one published every 0.5 s."""
    return [sample(start + i / 2, **kwargs) for i in range(count)]


class DecisionTests(unittest.TestCase):
    def test_one_ok_then_seven_failures_never_qualifies(self):
        samples = run(1) + run(7, start=100.5, failing=("dependencies",))
        # The local harness predicate was always truthy and accepted this run.
        self.assertTrue(all(x["sources_ok"].values() for x in samples[-8:]))
        self.assertFalse(guards.qualified(samples))
        self.assertFalse(guards.qualified(samples + run(7, start=104)))

    def test_eight_consecutive_fully_ok_receipts_qualify(self):
        self.assertTrue(guards.qualified(run(8)))
        self.assertTrue(guards.qualified([sample(99, failing=("gpu",))] + run(8)))
        self.assertFalse(guards.qualified(run(7)))
        self.assertFalse(guards.qualified(run(8) + [sample(104, failing=("qwen",))]))

    def test_missing_keys_blocked_receipts_and_inexact_mirror_fail(self):
        for bad in (sample(104, missing=("broker",)), sample(104, blocked=True), sample(104, ops_mirror_exact=False),
                    guards.observation({"observed_at": 104}, {}, now=104.2, ops_mirror_exact=True),
                    guards.observation({"sources": {"gpu": "not-a-dict"}}, {}, now=104.2, ops_mirror_exact=True),
                    guards.failed_sample("control_receipt_unreadable", 104.2)):
            with self.subTest(bad=bad):
                self.assertFalse(guards.sample_ok(bad))
                self.assertFalse(guards.qualified(run(7) + [bad]))

    def test_stale_or_repeated_receipts_never_qualify(self):
        queue = {"schema": "den.qwen-queue-observation/v1", "observed_at": 100}
        # One OK receipt re-read eight times long after the producer stopped.
        stale = [guards.observation(control(100), queue, now=700 + i * .4, ops_mirror_exact=True) for i in range(8)]
        self.assertFalse(guards.qualified(stale))
        # Fresh repeat reads of a single receipt count once.
        self.assertFalse(guards.qualified([sample(100, age=i / 10) for i in range(8)]))
        # A repeated receipt in a fresh sequence does not count twice.
        repeated = run(4) + [sample(101.5, age=.6)] + run(3, start=102)
        self.assertEqual(len(guards.receipts(repeated)), 7)
        self.assertFalse(guards.qualified(repeated))
        self.assertTrue(guards.qualified(repeated + [sample(103.5)]))
        # The latest read of a receipt decides, so one that went stale fails.
        self.assertFalse(guards.qualified(run(8) + [sample(103.5, age=1.6)]))
        for bad in (sample(104, age=1.6), sample(104, age=-.1), sample(104, stale_source="dependencies")):
            with self.subTest(bad=bad):
                self.assertFalse(guards.sample_ok(bad))
                self.assertFalse(guards.qualified(run(7) + [bad]))
        # Receipts must advance: an older receipt read later breaks the run.
        self.assertFalse(guards.qualified(run(7) + [sample(100.2)]))

    def test_samples_record_dependency_count_and_reason_codes_only(self):
        ok, failed = sample(dependencies=1), sample(failing=("dependencies",), dependencies=0)
        self.assertEqual((ok["dependency_count"], ok["reasons"]), (1, {}))
        self.assertEqual(failed["dependency_count"], 0)
        self.assertEqual(failed["reasons"], {"dependencies": "dependencies_lineage_invalid"})
        self.assertNotIn("den-oak", json.dumps([ok, failed]))  # Aggregates, never binding values.

    def test_units_needing_reload_fail_closed_and_reload_is_never_run(self):
        units = {"a.service": {"NeedDaemonReload": "no"}, "b.service": {"NeedDaemonReload": "yes"}, "c.service": {}}
        self.assertEqual(guards.units_needing_reload(units), ["b.service", "c.service"])
        self.assertEqual(guards.units_needing_reload({"a.service": {"NeedDaemonReload": "no"}}), [])

    def test_existing_staging_is_verified_by_owner_mode_and_digest(self):
        info = lambda kind, mode, uid=0: SimpleNamespace(st_mode=kind | mode, st_uid=uid)
        self.assertTrue(guards.directory_acceptable(info(stat.S_IFDIR, 0o700)))
        for bad in (info(stat.S_IFDIR, 0o755), info(stat.S_IFDIR, 0o700, 1000), info(stat.S_IFREG, 0o700),
                    info(stat.S_IFLNK, 0o700)):
            self.assertFalse(guards.directory_acceptable(bad))
        data = b"sampler"
        digest = hashlib.sha256(data).hexdigest()
        self.assertTrue(guards.staged_file_acceptable(info(stat.S_IFREG, 0o444), data, digest))
        self.assertFalse(guards.staged_file_acceptable(info(stat.S_IFREG, 0o444), b"changed", digest))
        self.assertFalse(guards.staged_file_acceptable(info(stat.S_IFREG, 0o644), data, digest))
        self.assertFalse(guards.staged_file_acceptable(info(stat.S_IFLNK, 0o444), data, digest))


class CommandTests(unittest.TestCase):
    def arguments(self, sampler, digest):
        return ["--ssh-host", "trial-host", "--scope-unit", "guard-qualification-test-r2.scope",
                "--sampler", str(sampler), "--sampler-sha256", digest, "--canary", "/srv/canary.py",
                "--canary-sha256", "a" * 64, "--failed-units-baseline", "/srv/baseline.json",
                "--health-since", "2026-11-18T00:00:00Z", "--expect-main-pid", "den-live.service=4242",
                "--expect-main-pid", "home-web-gateway.service=4343", "--model-unit", "worker.service",
                "--unit", "mirror.service", "--unit", "observer.service", "--unit", "producer.service",
                "--run-root", "/run/models-test", "--queue-file", "/run/queue/queue.json",
                "--ops-status-file", "/var/lib/ops/status.json", "--ops-mirror-file", "/run/queue/ops-status.json",
                "--observer-dir", "/srv/observer-r2", "--measurements-dir", "/srv/measurements-r2",
                "--worker-source", "/srv/models/tools/den-models", "--operator-config", "/run/models-test/operator.json",
                "--model-user", "models", "--output", str(Path(sampler).with_name("receipt.json"))]

    def test_dry_run_takes_identities_from_arguments_and_contacts_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            sampler = Path(directory) / "measure_trial_scope.py"
            sampler.write_bytes(b"# sampler fixture\n")
            digest = hashlib.sha256(sampler.read_bytes()).hexdigest()
            stdout = io.StringIO()
            with patch.object(subprocess, "run", side_effect=AssertionError("dry run executed a command")), \
                    contextlib.redirect_stdout(stdout):
                self.assertEqual(guards.main(self.arguments(sampler, digest)), 0)
            self.assertFalse((Path(directory) / "receipt.json").exists())
            with self.assertRaisesRegex(guards.QualificationError, "sampler_digest_mismatch"):
                guards.main(self.arguments(sampler, "0" * 64))
            # Sampling slower than the producer's fastest poll could skip a receipt.
            for interval in ("0.2", "0.4", "0"):
                with self.assertRaisesRegex(guards.QualificationError, "invalid_sampling_bounds"):
                    guards.main(self.arguments(sampler, digest) + ["--interval", interval])
        plan = json.loads(stdout.getvalue())
        self.assertLess(plan["interval"], guards.PRODUCER_MIN_POLL)
        self.assertFalse(plan["executes"])
        self.assertEqual(plan["daemon_reload"], "never")
        self.assertEqual(plan["expect_main_pid"], {"den-live.service": 4242, "home-web-gateway.service": 4343})
        self.assertEqual(plan["units"], ["mirror.service", "observer.service", "producer.service"])
        self.assertNotIn("712405", stdout.getvalue())
        self.assertNotIn("sampler_b64", plan)
        # The scope outlives host_main's worst case: every bounded call at its timeout.
        shows = 2 + 1 + 4 + 3  # PID pins, model unit, reload check, final state.
        worst = (guards.HEALTH_ALLOWANCE + guards.SHOW_TIMEOUT * shows + guards.START_TIMEOUT * 3
                 + plan["seconds"] + plan["interval"] + guards.PROBE_TIMEOUT)
        self.assertGreater(plan["scope_runtime_max_sec"], worst)
        self.assertGreater(plan["ssh_timeout_sec"], plan["scope_runtime_max_sec"])
        self.assertGreater(guards.scope_runtime_seconds({**plan, "seconds": 60}), worst + 45)

    def test_execute_always_leaves_a_receipt(self):
        outcomes = ((subprocess.TimeoutExpired(["ssh"], 1, output=b"partial"), None, "ssh_timeout", 1),
                    (OSError("no ssh"), None, "ssh_unavailable", 1),
                    (None, SimpleNamespace(returncode=0, stdout=b"{}", stderr=b""), None, 0))
        for failure, done, code, status in outcomes:
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                sampler = Path(directory) / "measure_trial_scope.py"
                sampler.write_bytes(b"# sampler fixture\n")
                digest = hashlib.sha256(sampler.read_bytes()).hexdigest()
                with patch.object(subprocess, "run", side_effect=failure, return_value=done) as ssh, \
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(guards.main(self.arguments(sampler, digest) + ["--execute"]), status)
                receipt = json.loads((Path(directory) / "receipt.json").read_text())
                self.assertEqual((receipt["returncode"], receipt["error"]), (done.returncode if done else None, code))
                command, timeout = ssh.call_args.args[0], ssh.call_args.kwargs["timeout"]
                self.assertEqual(command[0], "ssh")
                self.assertIn(f"--property=RuntimeMaxSec={receipt['scope_runtime_max_sec']}", command[-1])
                self.assertEqual(timeout, receipt["scope_runtime_max_sec"] + guards.SSH_MARGIN)

    def test_model_uid_probe_imports_load_profile_from_artifacts(self):
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            return SimpleNamespace(returncode=0, stdout="{}", stderr="")
        with patch.object(subprocess, "run", run):
            guards.model_uid_probe({"worker_source": "/srv/models", "operator_config": "/run/operator.json",
                                    "model_user": "models"})
        source = calls[0][-1]
        self.assertIn("from den_models.artifacts import load_profile", source)
        self.assertNotIn("from den_models.contracts import load_profile", source)
        self.assertEqual(calls[0][:4], ["runuser", "-u", "models", "--"])
        from den_models.artifacts import load_profile  # The import the probe relies on resolves.
        self.assertTrue(callable(load_profile))
        compile(source, "probe", "exec")


if __name__ == "__main__":
    unittest.main()
