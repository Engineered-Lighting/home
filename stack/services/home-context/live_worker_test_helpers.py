"""Fictional process/HTTP fixtures for the supported live worker boundary."""
import copy
import hashlib
import json
import queue
import subprocess
import threading
from pathlib import Path

from journal import digest
from live_test_helpers import MANIFEST, METHOD, PROFILE, SCOPE, configuration, fact, grant, receipt_resolver


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PROBE = ROOT / "tools" / "fixtures" / "home-live-worker-public-probe.cjs"
WORKER_TOKEN = "synthetic-engagement-worker-token"
WORKER_ID = "engagement-shadow"


def describe(seed):
    run = subprocess.run(["node", str(PROBE)], input=json.dumps({"op": "describe", "config": seed}) + "\n",
                         cwd=ROOT, text=True, capture_output=True, timeout=10)
    if run.returncode:
        raise AssertionError(run.stderr or run.stdout)
    result = json.loads(run.stdout)
    if not result.get("ok"):
        raise AssertionError(result)
    return result["value"]


def worker_fixture(*, second_source=False):
    base = configuration()
    if second_source:
        source = copy.deepcopy(base["manifest"]["sources"]["synthetic-source"])
        source["sensor_roots"] = ["camera:synthetic-second"]
        source["receipt_store_ids"] = ["synthetic-second-receipts"]
        base["manifest"]["sources"]["synthetic-second-source"] = source
    seed = {"profile": PROFILE, "identity": copy.deepcopy(SCOPE),
            "source_pins": {name: digest(source) for name, source in base["manifest"]["sources"].items()},
            "engagement_policy": {"support_methods": [copy.deepcopy(METHOD)], "refinement_methods": [],
                                  "negative_methods": [copy.deepcopy(METHOD)], "allow_experimental": True},
            "worker_id": WORKER_ID, "producer_id": WORKER_ID, "producer_epoch": "worker-epoch",
            "revision": "synthetic-r1", "max_output_age_s": 3, "semantic_interval_s": 1}
    description = describe(seed)
    worker = {key: seed[key] for key in ("worker_id", "producer_id", "producer_epoch", "revision", "source_pins",
                                       "max_output_age_s", "semantic_interval_s", "engagement_policy")}
    worker.update(config_digest=description["config_digest"], schema_pins=description["schema_pins"])
    manifest = copy.deepcopy(base["manifest"])
    manifest.update(schema="home-live-ingress-manifest-2", version=2,
                    worker_interface_digest=description["worker_interface_digest"], worker=worker)
    manifest_ref = {"id": "synthetic-worker-manifest", "digest": digest(manifest)}
    MANIFEST.update(manifest_ref)
    service = {"schema": "home-live-ingress-config-2", "version": 2,
               "manifest_ref": manifest_ref, "manifest": manifest}
    producer = grant()
    source_ids = list(seed["source_pins"])
    sensors = sorted({root for source in manifest["sources"].values() for root in source["sensor_roots"]})
    producer["source_ids"] = source_ids
    producer["sensor_roots"] = sensors
    producer["live_bindings"] = [{"manifest_ref": copy.deepcopy(manifest_ref), "source_id": source_id,
                                  "capabilities": ["capture", "control", "read", "revoke"]} for source_id in source_ids]
    worker_types = ["home-live-fact-1", "home-live-geometry-1", "home-live-engagement-state-1",
                    "home-live-shadow-decision-1", "home-live-worker-checkpoint-1", "home-live-worker-commit-1",
                    "home-live-input-cut-1"]
    worker_grant = {"principal_id": "synthetic-engagement-principal", "producer_id": WORKER_ID,
                    "producer_epoch": "worker-epoch", "expected_consumer_id": WORKER_ID,
                    "expected_worker_id": WORKER_ID, "token_sha256": hashlib.sha256(WORKER_TOKEN.encode()).hexdigest(),
                    "scopes": [{"site_id": SCOPE["site_id"], "room_id": SCOPE["room_id"]}],
                    "operations": ["read", "worker_commit"], "payload_types": worker_types,
                    "revisions": ["synthetic-r1"], "source_ids": list(seed["source_pins"]),
                    "sensor_roots": sensors, "schema_pins": {},
                    "live_bindings": [{"manifest_ref": copy.deepcopy(manifest_ref), "source_id": name,
                                       "capabilities": ["read", "worker_commit"]} for name in seed["source_pins"]]}
    from schema_registry import schema_digest
    worker_grant["schema_pins"] = {name: schema_digest(name, profile=PROFILE) for name in worker_types}
    config = {**seed, "manifest_ref": copy.deepcopy(manifest_ref), "live_manifest": copy.deepcopy(manifest),
              "token": WORKER_TOKEN}
    return service, [producer, worker_grant], config


def second_source_fact(seq):
    payload = fact("second-source-fact-" + str(seq), seq=seq)
    payload["source_id"] = "synthetic-second-source"
    payload["association"]["association_id"] = "synthetic-second-association"
    payload["person_ref"].update(pid=2, track_epoch="second-person-track")
    payload["subject_ref"].update(subject_id="second-person-work", subject_epoch="second-person-subject")
    payload["head"]["key"].update(source_id=payload["source_id"], person_ref=copy.deepcopy(payload["person_ref"]),
                                    subject_ref=copy.deepcopy(payload["subject_ref"]))
    payload["sensor_roots"] = ["camera:synthetic-second"]
    payload["observation_roots"] = ["second-" + root for root in payload["observation_roots"]]
    payload["observation_root_times"] = {"second-" + root: at for root, at in payload["observation_root_times"].items()}
    payload["sensor_root_bindings"] = {root: ["camera:synthetic-second"] for root in payload["observation_roots"]}
    for original in payload["original_receipt_refs"]:
        original["store_id"] = "synthetic-second-receipts"
    return payload


def worker_receipt_resolver(store_id, receipt_id):
    if store_id != "synthetic-second-receipts":
        return receipt_resolver(store_id, receipt_id)
    record = copy.deepcopy(receipt_resolver("synthetic-receipts", receipt_id))
    record["ref"]["store_id"] = store_id
    record["observation_root_times"] = {"second-" + root: at for root, at in record["observation_root_times"].items()}
    record["sensor_root_bindings"] = {root: ["camera:synthetic-second"] for root in record["observation_root_times"]}
    return record


class WorkerProbe:
    def __init__(self):
        self.process = subprocess.Popen(["node", str(PROBE)], cwd=ROOT, text=True,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.lines = queue.Queue()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        for line in self.process.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def command(self, op, **fields):
        self.process.stdin.write(json.dumps({"op": op, **fields}) + "\n")
        self.process.stdin.flush()
        try:
            line = self.lines.get(timeout=10)
        except queue.Empty:
            self.process.kill()
            self.process.wait(5)
            raise AssertionError("synthetic worker exceeded command deadline") from None
        if not line:
            raise AssertionError("worker probe exited: " + self.process.stderr.read())
        result = json.loads(line)
        if not result.get("ok"):
            raise AssertionError(result)
        return result["value"]

    def close(self):
        if not self.process.stdin.closed:
            try:
                self.process.stdin.close()
            except OSError:
                pass
        if self.process.poll() is None:
            try:
                self.process.wait(5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(5)
        self.reader.join(2)
        self.process.stdout.close()
        self.process.stderr.close()
