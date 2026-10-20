"""Retained aiming through the public journal and actual persistent worker.

All geometry, optics, timings and references are explicitly fictional.
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from journal import Journal, PREFIX, digest
from live_delivery_test_helpers import delivery_worker_fixture, delivery_geometry
from live_test_helpers import MANIFEST, LiveHttp, NOW, PROFILE, event, fact, publication_ref
from live_worker_test_helpers import WorkerProbe, describe, worker_receipt_resolver, WORKER_TOKEN


def stable_fixture():
    service, grants, config = delivery_worker_fixture()
    config["delivery_policy"].update(schema="home-live-task-delivery-policy-2", version=2)
    description = describe(config)
    manifest = service["manifest"]
    manifest.update(schema="home-live-ingress-manifest-5", version=5,
                    delivery_interface_digest=description["delivery_interface_digest"])
    manifest["worker"].update(config_digest=description["config_digest"], delivery_policy=copy.deepcopy(config["delivery_policy"]),
                              delivery_schema_pins=description["delivery_schema_pins"])
    service.update(schema="home-live-ingress-config-5", version=5)
    service["manifest_ref"] = {"id": "synthetic-retained-delivery-manifest", "digest": digest(manifest)}
    MANIFEST.update(service["manifest_ref"])
    config.update(manifest_ref=copy.deepcopy(service["manifest_ref"]), live_manifest=copy.deepcopy(manifest))
    from schema_registry import LIVE_STABLE_DELIVERY_VERSIONS, LIVE_DELIVERY_VERSIONS, schema_digest
    for grant in grants:
        grant["payload_types"] = [name for name in grant["payload_types"] if name not in LIVE_DELIVERY_VERSIONS]
        for binding in grant["live_bindings"]:
            binding["manifest_ref"] = copy.deepcopy(service["manifest_ref"])
        if "worker_commit" in grant["operations"]:
            grant["payload_types"] += list(LIVE_STABLE_DELIVERY_VERSIONS)
        elif "intent" in grant["operations"]:
            grant["payload_types"].append("home-live-shadow-decision-4")
        grant["schema_pins"] = {name: schema_digest(name, profile=PROFILE) for name in grant["payload_types"]}
    return service, grants, config


class StableDeliveryTests(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory(prefix="synthetic-stable-delivery-", dir=Path(__file__).parent)
        self.addCleanup(work.cleanup)
        self.now, self.seq, self.started, self.heads = NOW + .2, 0, False, {}
        self.service, self.grants, self.config = stable_fixture()
        self.journal = Journal(Path(work.name) / "synthetic.sqlite", grants=self.grants, profile=PROFILE,
                               clock=lambda: self.now, live_config=self.service,
                               live_receipt_resolver=worker_receipt_resolver)
        self.addCleanup(self.journal.close)
        self.http = LiveHttp(self.journal)
        self.addCleanup(self.http.close)
        self.config["service_url"] = f"http://127.0.0.1:{self.http.server.server_port}"
        self.probe = WorkerProbe()
        self.addCleanup(self.probe.close)

    def publish(self, payload):
        key = str(payload["head"]["key"])
        if key in self.heads:
            payload["head"]["supersedes"] = publication_ref(self.heads[key])
        status, receipt = self.http.request("POST", PREFIX + "/events", event(payload))
        self.assertEqual(status, 200, receipt)
        self.heads[key] = receipt

    def advance(self, y=0, mode="supplied", prepare=False):
        self.seq += 1
        self.now = NOW + self.seq - 1 + .2
        self.publish(fact("synthetic-stable-fact-" + str(self.seq), seq=self.seq))
        geometry = delivery_geometry(self.seq, mode)
        geometry["parts"]["work"]["shape"]["center_m"][1] = y
        self.publish(geometry)
        if not self.started:
            self.probe.command("start", config=self.config, t=self.now)
            self.started = True
        if prepare:
            self.probe.command("fault", mode="before_commit")
        result = self.probe.command("tick", t=self.now)
        if prepare:
            self.assertFalse(result["decisions"])
            return json.loads(result["commit_posts"][-1])
        self.assertEqual(len(result["decisions"]), 1, result)
        return result["decisions"][0]

    def establish(self):
        for _ in range(6):
            first = self.advance()
        self.assertEqual(first["execution"]["status"], "virtual", first)
        return first

    def test_noise_retains_original_aim_and_scene_with_current_geometry_references(self):
        first = self.establish()
        for n in range(12):
            row = self.advance(.005 if n % 2 else -.005)
            self.assertEqual(row["schema"], "home-live-shadow-decision-4")
            self.assertEqual(row["scene"]["scene_id"], first["scene"]["scene_id"])
            self.assertEqual(row["allocation"]["assignments"], first["allocation"]["assignments"])
            self.assertEqual(row["display_lease"]["geometry_refs"], row["input_cut"]["geometry_refs"])
        self.assertGreater(row["execution"]["readback"][0]["brightness_fraction"], 0)

    def test_changed_region_withdraws_retained_aim_then_bounded_dark_restart(self):
        self.establish()
        for _ in range(6):
            self.advance()
        withdrawn = self.advance(1.5)
        self.assertEqual(withdrawn["execution"]["status"], "unavailable")
        resumed = self.advance(1.5)
        self.assertEqual(resumed["execution"]["continuity"], "discontinuous_dark_reset")
        self.assertEqual(resumed["execution"]["readback"][0]["brightness_fraction"], 0)

    def test_current_protection_loss_withdraws_even_when_the_prior_endpoint_was_clear(self):
        self.establish()
        for _ in range(6):
            self.advance()
        row = self.advance(mode="head_in_beam")
        self.assertEqual(row["execution"]["status"], "unavailable")
        self.assertEqual(row["allocation"]["reason"], "protected_volume_intersection")

    def test_restart_and_lost_ack_preserve_the_exact_prior_motion_plan(self):
        first = self.establish()
        self.probe.command("fault", mode="lost_ack")
        self.advance(.005)
        self.probe.command("stop")
        self.started = False
        resumed = self.advance(-.005)
        self.assertEqual(resumed["execution"]["continuity"], "continuous")
        self.assertEqual(resumed["allocation"]["assignments"], first["allocation"]["assignments"])

    def test_independent_journal_rejects_rewritten_retained_endpoint(self):
        self.establish()
        commit = self.advance(.005, prepare=True)
        decision = commit["publications"][-1]["payload"]
        for section in (decision["allocation"], decision["execution"]):
            section["assignments"][0]["pan_deg"] += 1
        commit["checkpoint"]["state"]["virtual_execution"]["state"]["plan"]["to_pan_deg"] += 1
        status, result = self.http.request("POST", PREFIX + "/worker-commits", commit, token=WORKER_TOKEN)
        self.assertEqual(status, 409, result)
        self.assertEqual(result["error"]["code"], "invalid_delivery_assignment", result)
