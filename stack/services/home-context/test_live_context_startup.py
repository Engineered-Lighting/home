"""Bounded synthetic startup/reopen and actual server CLI qualification."""
import copy
import hashlib
import http.client
import json
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path

from journal import Journal, PREFIX
from live_test_helpers import (LiveHttp, MANIFEST, NOW, PROFILE, configuration, event, fact, geometry, grant,
                               independent_digest, receipt_resolver)


HERE = Path(__file__).resolve().parent


class LiveStartupTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix="synthetic-live-startup-", dir=HERE)
        self.addCleanup(self.work.cleanup)
        self.path = Path(self.work.name) / "synthetic.sqlite"
        self.config = configuration()

    def journal(self, *, config=None, grants=None, profile=PROFILE, path=None):
        return Journal(self.path if path is None else path, profile=profile,
                       grants=[grant()] if grants is None else grants, clock=lambda: NOW + .2,
                       live_config=self.config if config is None else config, live_receipt_resolver=receipt_resolver)

    def test_same_frozen_configuration_reopens_original_receipts_and_epoch(self):
        journal = self.journal()
        try:
            server = LiveHttp(journal)
            try:
                status, original = server.request("POST", PREFIX + "/events", event(fact()))
                self.assertEqual(status, 200, original)
            finally:
                server.close()
        finally:
            journal.close()
        reopened = self.journal()
        try:
            server = LiveHttp(reopened)
            try:
                status, retry = server.request("POST", PREFIX + "/events", event(fact()))
                self.assertEqual(status, 200, retry)
                self.assertEqual(retry, original)
                status, snapshot = server.request("GET", PREFIX + "/snapshot?site_id=synthetic-site&room_id=synthetic-room")
                self.assertEqual(status, 200, snapshot)
                self.assertEqual(snapshot["stream_epoch"], original["stream_epoch"])
                self.assertEqual(snapshot["cursor"], 1)
            finally:
                server.close()
        finally:
            reopened.close()

    def test_changed_manifest_refuses_reopen_without_mutating_existing_bytes(self):
        journal = self.journal()
        journal.close()
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        changed = copy.deepcopy(self.config)
        changed["manifest"]["limits"]["max_heads"] = 127
        changed["manifest_ref"]["digest"] = independent_digest(changed["manifest"])
        permissions = grant()
        permissions["live_bindings"][0]["manifest_ref"] = copy.deepcopy(changed["manifest_ref"])
        with self.assertRaises(ValueError):
            self.journal(config=changed, grants=[permissions])
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)

    def test_unmarked_legacy_journal_is_never_migrated_or_mutated(self):
        legacy = Journal(self.path, grants=[], clock=lambda: NOW + .2)
        legacy.close()
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        with self.assertRaises(ValueError):
            self.journal()
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)

    def test_unsupported_profile_and_operation_do_not_initialize_a_journal(self):
        with self.assertRaises(ValueError):
            self.journal(profile="home-live-shadow-unimplemented")
        self.assertFalse(self.path.exists())
        unsupported = grant()
        unsupported["operations"].append("feedback")
        wrong_manifest = grant()
        wrong_manifest["live_bindings"][0]["manifest_ref"]["digest"] = "0" * 64
        mismatched_capability = grant()
        mismatched_capability["operations"] = ["read"]
        for name, permissions in (("unsupported-operation", unsupported), ("wrong-binding-manifest", wrong_manifest),
                                  ("capture-without-event-authority", mismatched_capability)):
            with self.subTest(name=name):
                path = Path(self.work.name) / (name + ".sqlite")
                with self.assertRaises(ValueError):
                    self.journal(grants=[permissions], path=path)
                self.assertFalse(path.exists(), "invalid live authority must reject before database initialization")

    def test_invalid_capability_never_reconfigures_an_existing_wal_journal(self):
        journal = self.journal()
        journal.close()
        # Arrange only this test-created ledger in another SQLite storage mode.
        # Assertions inspect immutable bytes, not rows or private journal tables.
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
        finally:
            connection.close()
        before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in Path(self.work.name).iterdir() if path.is_file()}
        permissions = grant()
        permissions["operations"] = ["read"]
        with self.assertRaises(ValueError):
            self.journal(grants=[permissions])
        after = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in Path(self.work.name).iterdir() if path.is_file()}
        self.assertEqual(after, before)

    def http(self, port, method, path, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=.5)
        try:
            connection.request(method, path, body=None if body is None else json.dumps(body),
                               headers={"Authorization": "Bearer synthetic-live-token", "Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def cli_arguments(self, config_path, *, profile=PROFILE, port=8768, database=None):
        return [sys.executable, str(HERE / "server.py"), "--database", str(database or self.path),
                "--grants", str(config_path), "--profile", profile, "--port", str(port)]

    @contextmanager
    def cli_server(self, config_path):
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        process = subprocess.Popen(self.cli_arguments(config_path, port=port), cwd=HERE,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 5
            while True:
                if process.poll() is not None:
                    _, error = process.communicate()
                    self.fail("synthetic CLI failed before listening: " + error.decode("utf-8", errors="replace"))
                try:
                    status, health = self.http(port, "GET", "/health")
                    if status == 200:
                        self.assertEqual(health["status"], "local_shadow")
                        break
                except (OSError, http.client.HTTPException):
                    pass
                if time.monotonic() >= deadline:
                    self.fail("synthetic CLI did not listen within its bounded startup interval")
                time.sleep(.02)
            yield port
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=3)

    def cli_config(self):
        return {"schema": "home-context-grants-1", "version": 1, "grants": [grant()],
                "live_config": copy.deepcopy(self.config), "live_receipt_catalog": []}

    def test_actual_server_cli_resolves_catalog_and_reopens_same_public_journal(self):
        # Rebase only synthetic observation metadata to the CLI's real UTC clock.
        stamp = int(time.time()) - 1
        delta = stamp - NOW
        def shift(value):
            if isinstance(value, list):
                return [shift(item) for item in value]
            if not isinstance(value, dict):
                return value
            result = {}
            for key, item in value.items():
                if key in {"observed_at", "available_at", "valid_until", "origin_at"}:
                    result[key] = item + delta
                elif key == "observation_root_times":
                    result[key] = {root: observed + delta for root, observed in item.items()}
                else:
                    result[key] = shift(item)
            if result.get("status") == "commissioned" and "original_value" in result:
                result["original_value"] = str(stamp * 1000000000)
            return result
        config = self.cli_config()
        config["live_receipt_catalog"] = [shift(receipt_resolver("synthetic-receipts", "synthetic-capture-1:" + component)) for component in ("pose", "depth")]
        config_path = Path(self.work.name) / "synthetic-cli-grants.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        payloads = [shift(fact()), shift(geometry())]
        original_receipts = []
        with self.cli_server(config_path) as port:
            for expected_cursor, payload in enumerate(payloads, start=1):
                status, saved = self.http(port, "POST", PREFIX + "/events", event(payload))
                self.assertEqual(status, 200, saved)
                self.assertEqual(saved["cursor"], expected_cursor)
                self.assertGreaterEqual(saved["committed_at"], stamp + .1)
                original_receipts.append(saved)
        with self.cli_server(config_path) as port:
            for payload, original in zip(payloads, original_receipts):
                status, retry = self.http(port, "POST", PREFIX + "/events", event(payload))
                self.assertEqual(status, 200, retry)
                self.assertEqual(retry, original)
            status, snapshot = self.http(port, "GET", PREFIX + "/snapshot?site_id=synthetic-site&room_id=synthetic-room")
            self.assertEqual(status, 200, snapshot)
            self.assertEqual(snapshot["stream_epoch"], original_receipts[0]["stream_epoch"])
            self.assertEqual([item["payload"]["observed_at"] for item in snapshot["items"]], [stamp, stamp])
            self.assertEqual(snapshot["cursor"], 2)

    def test_cli_rejects_missing_catalog_duplicate_identity_or_wrong_profile_before_open(self):
        config = self.cli_config()
        cases = [("missing-catalog", {key: value for key, value in config.items() if key != "live_receipt_catalog"}, PROFILE),
                 ("wrong-profile", config, "legacy"), ("unimplemented-profile", config, "home-live-shadow-unimplemented")]
        duplicate = copy.deepcopy(config)
        record = receipt_resolver("synthetic-receipts", "synthetic-capture-1:pose")
        duplicate["live_receipt_catalog"] = [record, copy.deepcopy(record)]
        cases.append(("duplicate-catalog", duplicate, PROFILE))
        for name, body, profile in cases:
            with self.subTest(name=name):
                path = Path(self.work.name) / (name + ".json")
                path.write_text(json.dumps(body), encoding="utf-8")
                database = Path(self.work.name) / (name + ".sqlite")
                result = subprocess.run(self.cli_arguments(path, profile=profile, database=database), cwd=HERE,
                                        stdin=subprocess.DEVNULL, capture_output=True, timeout=5)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(database.exists(), "invalid CLI configuration must not initialize a journal")

    def test_clock_policy_rejects_unagreed_legacy_unit_aliases_before_open(self):
        for clock_name in ("capture_clock", "control_clock"):
            for alias in ("unix_ms", "utc_epoch_s", "utc_epoch_ms", "utc_epoch_ns", "device_relative_s", "session_offset_s"):
                with self.subTest(clock=clock_name, alias=alias):
                    path = Path(self.work.name) / (clock_name + "-" + alias + ".sqlite")
                    config = copy.deepcopy(self.config)
                    config["manifest"]["sources"]["synthetic-source"][clock_name]["original_unit"] = alias
                    config["manifest_ref"]["digest"] = independent_digest(config["manifest"])
                    permissions = grant()
                    permissions["live_bindings"][0]["manifest_ref"] = copy.deepcopy(config["manifest_ref"])
                    with self.assertRaises(ValueError):
                        admitted = self.journal(config=config, grants=[permissions], path=path)
                        admitted.close()
                    self.assertFalse(path.exists(), "invalid clock policy must not initialize a journal")


if __name__ == "__main__":
    unittest.main()
