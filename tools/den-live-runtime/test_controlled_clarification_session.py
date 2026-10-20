"""Opt-in connected clarification through the actual local runtime and TLS edge."""
from contextlib import ExitStack
from copy import deepcopy
import http.client
import json
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from controlled_external_io import build_base_configuration, assemble_journal_grants, SCENARIOS
from controlled_session import ControlledSession, load_prepared_room
from room_configuration import upgrade_configuration


def bundle_for_clarification():
    root = Path(__file__).resolve().parents[2]
    bundle = build_base_configuration(session_ref={"site_id": "authored-site", "room_id": "authored-room",
        "session_id": "authored-clarification-session", "domain_epoch": "authored-clarification-domain"},
        node_runtime=shutil.which("node"), clarification=True)
    room = json.loads(subprocess.check_output([shutil.which("node"), "-e",
        "process.stdout.write(JSON.stringify(require('./tools/fixtures/home-live-room.cjs').policy()))"], cwd=root, text=True))
    room["registration"] = deepcopy(bundle["worker_config"]["delivery_policy"]["registration"])
    def current(value):
        if isinstance(value, dict):
            for key in value:
                if key == "valid_until": value[key] = time.time() + 180
                else: current(value[key])
        elif isinstance(value, list):
            for row in value: current(row)
    current(room)
    method = next(row for row in bundle["configuration"]["manifest"]["sources"].values())["method_refs"]
    method = next(row for row in method if row["id"] == "controlled-authored-holding-method")
    policy = {"schema": "home-live-clarification-policy-1", "version": 1, "proposal_ttl_s": 30,
        "max_pending": 1, "max_history": 128, "repeat_policy": "one_per_scope",
        "material_methods": [deepcopy(method)], "interaction_methods": [deepcopy(method)]}
    bundle["configuration"], bundle["worker_config"] = upgrade_configuration(
        bundle["configuration"], bundle["worker_config"], room, clarification_policy=policy)
    return bundle


class ControlledClarificationTests(unittest.TestCase):
    def test_prepared_successor_is_closed_and_scenario_roster_requires_matching_profile(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            bundle = bundle_for_clarification()
            prepared = {"schema": "home-den-controlled-room-2", "version": 2, "pack_directory": str(Path(directory).resolve()),
                "asset_selection": {}, "resident_binding": {"schema": "home-live-resident-binding-9", "version": 9},
                "room_policy": bundle["worker_config"]["room_policy"], "source_bundle_digest": "c"*64,
                "acquisition_binding": {}, "resident_assets": {}, "resident_source_paths": [],
                "clarification_policy": bundle["worker_config"]["clarification_policy"]}
            target = Path(directory).resolve() / "prepared.json"
            target.write_text(json.dumps(prepared))
            self.assertEqual(load_prepared_room(target), prepared)
            for mutate in (lambda p: p.update(schema="home-den-controlled-room-1",version=1),
                           lambda p: p.pop("clarification_policy"),
                           lambda p: p.update(resident_binding=None),
                           lambda p: p["resident_binding"].update(version=8)):
                wrong = deepcopy(prepared); mutate(wrong); target.write_text(json.dumps(wrong))
                with self.assertRaisesRegex(ValueError, "prepared_room_refused"): load_prepared_room(target)
            ordinary = build_base_configuration(session_ref=bundle["worker_config"]["identity"], node_runtime=shutil.which("node"))
            ordinary["external_io"] = bundle["external_io"]
            with self.assertRaisesRegex(ValueError, "controlled_profile_scenarios_mismatch"):
                ControlledSession(bundle=ordinary, output_directory=directory, node_runtime=shutil.which("node"), source_bundle_digest="c"*64)

    def test_default_grants_and_six_scenarios_do_not_gain_reply_authority(self):
        bundle = build_base_configuration(session_ref={"site_id": "authored-site", "room_id": "authored-room",
            "session_id": "ordinary-session", "domain_epoch": "ordinary-domain"}, node_runtime=shutil.which("node"))
        self.assertEqual(bundle["external_io"].status()["scenarios"], list(SCENARIOS))
        grants = assemble_journal_grants(bundle["configuration"], bundle["worker_config"],
            publication_token="producer", worker_token="worker", human_token="human")
        self.assertFalse(any("home-live-intent-event-3" in grant["payload_types"] for grant in grants))

    def test_real_factory_question_and_tls_reply_are_causally_replayed_without_task_authority(self):
        from live_context_edge import binding_digest
        with ExitStack() as cleanup:
            directory = Path(__file__).resolve().parents[2] / "artifacts" / ("controlled-clarification10-" + uuid.uuid4().hex)
            directory.mkdir(parents=True)
            print("authored clarification10 evidence:", directory, flush=True)
            bundle = bundle_for_clarification()
            bundle["external_io"].set_scenario("material_uncertain")
            session = ControlledSession(bundle=bundle, output_directory=directory, node_runtime=shutil.which("node"), source_bundle_digest="c" * 64)
            cleanup.callback(session.close)
            self.assertEqual(session.supervisor.config["interval_s"], 1.5)
            self.assertEqual(session.status()["scenarios"], list(SCENARIOS) + ["material_uncertain"])
            edge = session.start_edge()
            context = ssl.create_default_context(cafile=edge["ca_file"])
            headers = {"Authorization": "Bearer " + Path(edge["token_file"]).read_text(), "X-Home-Context-Binding": binding_digest(edge["binding"])}
            def request(method, route, body=None):
                connection = http.client.HTTPSConnection("127.0.0.1", int(edge["url"].rsplit(":", 1)[1]), context=context, timeout=3)
                try:
                    connection.request(method, route, json.dumps(body) if body is not None else None,
                        headers={**headers, **({"Content-Type": "application/json"} if body is not None else {})})
                    response = connection.getresponse()
                    return response.status, json.loads(response.read())
                finally: connection.close()
            query = "?site_id=authored-site&room_id=authored-room"
            def latest():
                status, snapshot = request("GET", "/api/home-context/v1/snapshot" + query)
                self.assertEqual(status, 200, snapshot)
                return next((row["payload"] for row in snapshot["items"] if row["payload_type"] == "home-live-shadow-decision-9"), None)
            session.control({"id": "clarification-start", "operation": "start"})
            deadline, decision = time.monotonic() + 12, None
            while time.monotonic() < deadline:
                if session.status()["status"] != "running": time.sleep(.02); continue
                decision = latest()
                if decision and decision["pending"]: break
                time.sleep(.05)
            self.assertTrue(decision and decision["pending"], session.status())
            proposal = decision["pending"][0]
            self.assertIsNone(proposal["episode_id"]); self.assertIsNone(proposal["scene_id"])
            self.assertEqual(proposal["deadline"] - proposal["created_at"], 30)
            self.assertIsNone(decision["room_output"]["task"]["fixture_id"])
            now = time.time()
            reply = {"schema": "home-live-intent-event-3", "version": 3, "id": "intent:controlled-material-reply", **session.identity,
                "manifest_ref": deepcopy(bundle["configuration"]["manifest_ref"]), "actor_ref": "controlled-owner",
                "origin": "human_declaration", "truth_role": "runtime_intent", "kind": "activity_reply",
                "target": {key: deepcopy(proposal[key]) for key in ("person_ref", "subject_ref", "episode_id", "scene_id")}
                    | {"pending_ref": proposal["proposal_id"]},
                "set_at": now, "available_at": now, "valid_until": now+30, "value": {"answer": "using_material"}, "reply_to": proposal["proposal_id"]}
            status, accepted = request("POST", "/api/home-context/v1/intent-events", reply)
            self.assertEqual(status, 200, accepted)
            deadline, result = time.monotonic()+8, None
            while time.monotonic() < deadline:
                status, result = request("GET", "/api/home-context/v1/intent-events/" + reply["id"] + query)
                if status == 200 and result.get("application_result"): break
                time.sleep(.05)
            self.assertEqual(result["application_result"]["reason"], "activity_declaration_recorded", result)
            decision = latest()
            self.assertEqual(decision["pending"], [])
            self.assertIsNone(decision["room_output"]["task"]["fixture_id"])
            self.assertNotEqual(decision["scene"]["status"], "selected")
            session.control({"id": "clarification-stop", "operation": "stop"})
            final = session.status(results=True)
            self.assertEqual(final["status"], "stopped", final)
            self.assertEqual(final["ordinary_replay"]["status"], "reproduced", final)


if __name__ == "__main__": unittest.main()
