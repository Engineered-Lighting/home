"""Independent causal/association regressions through real loopback HTTP.

All measurements, identities and commissioning policy are synthetic. No camera,
household artifact, external service or production manifest is used.
"""
import copy
import tempfile
import unittest
from pathlib import Path

from journal import Journal, PREFIX
from live_test_helpers import (LiveHttp, NOW, PROFILE, configuration, event, geometry,
                               grant, independent_digest, receipt, receipt_resolver)


class LiveCausalAdversaryTests(unittest.TestCase):
    def _with_server(self, callback, *, config=None, now=NOW + .2):
        config = configuration() if config is None else config
        writer = grant()
        writer["live_bindings"][0]["manifest_ref"] = copy.deepcopy(config["manifest_ref"])
        with tempfile.TemporaryDirectory(prefix="synthetic-live-adversary-", dir=Path(__file__).parent) as work:
            journal = Journal(Path(work) / "synthetic.sqlite", grants=[writer], profile=PROFILE,
                              clock=lambda: now, live_config=config, live_receipt_resolver=receipt_resolver)
            try:
                server = LiveHttp(journal)
                try:
                    callback(server, config)
                finally:
                    server.close()
            finally:
                journal.close()

    def _post(self, server, config, payload):
        payload["manifest_ref"] = copy.deepcopy(config["manifest_ref"])
        return server.request("POST", PREFIX + "/events", event(payload))

    def test_fresh_capture_cannot_refresh_part_supported_only_by_old_receipts(self):
        payload = geometry(seq=2)
        payload["original_receipt_refs"] += [receipt(1, "pose"), receipt(1, "depth")]
        for suffix in ("rgb", "depth"):
            root = "synthetic-frame-1:" + suffix
            payload["observation_roots"].append(root)
            payload["observation_root_times"][root] = NOW
            payload["sensor_root_bindings"][root] = ["camera:synthetic-oak"]
        payload["parts"]["work"]["support_refs"] = [receipt(1, "pose"), receipt(1, "depth")]

        def verify(server, config):
            status, body = self._post(server, config, payload)
            self.assertEqual(status, 409, body)
            self.assertEqual(body["error"]["code"], "invalid_clock")
        self._with_server(verify, now=NOW + 1.2)

    def test_selected_parent_part_cannot_extend_its_support_expiry(self):
        parent = geometry("synthetic-parent-geometry")
        parent["parts"]["work"]["valid_until"] = NOW + .6

        def verify(server, config):
            status, accepted = self._post(server, config, parent)
            self.assertEqual(status, 200, accepted)
            whole_ref = {key: accepted[key] for key in ("stream_epoch", "cursor", "producer_id", "id", "payload_digest")}
            whole_ref["component_id"] = None
            support_ref = {**whole_ref, "component_id": "parts.work"}
            child = geometry("synthetic-child-geometry")
            child["available_at"] = NOW + .4
            child["head"]["position"]["revision"] = 1
            child["head"]["supersedes"] = whole_ref
            child["parent_refs"] = [support_ref]
            child["parts"]["work"]["support_refs"] = [support_ref]
            status, body = self._post(server, config, child)
            self.assertEqual(status, 409, body)
            self.assertEqual(body["error"]["code"], "invalid_clock")
        self._with_server(verify, now=NOW + .4)

    def test_supplied_work_requires_qualified_subject(self):
        payload = geometry()
        payload["subject_ref"] = None
        payload["head"]["key"]["subject_ref"] = None
        payload["association"]["subject_status"] = "not_applicable"

        def verify(server, config):
            status, body = self._post(server, config, payload)
            self.assertEqual(status, 400, body)
            self.assertEqual(body["error"]["code"], "invalid_association")
        self._with_server(verify)

    def _body_only(self):
        config = configuration()
        source = config["manifest"]["sources"]["synthetic-source"]
        source["part_policies"]["body"] = copy.deepcopy(source["part_policies"]["work"])
        source["primitive_ids"]["body"] = ["torso"]
        config["manifest_ref"]["digest"] = independent_digest(config["manifest"])
        payload = geometry()
        payload["subject_ref"] = None
        payload["head"]["key"]["subject_ref"] = None
        payload["association"]["subject_status"] = "not_applicable"
        body = copy.deepcopy(payload["parts"]["work"])
        body["shape"] = {"kind": "opaque_primitives", "items": [{"part_id": "torso", **body["shape"]}]}
        payload["parts"]["work"] = copy.deepcopy(payload["parts"]["head"])
        payload["parts"]["body"] = body
        return config, payload

    def test_supplied_body_requires_qualified_person(self):
        config, payload = self._body_only()
        person = payload["person_ref"]
        payload["person_ref"] = None
        payload["head"]["key"]["person_ref"] = None
        payload["association"]["person_status"] = "unresolved"
        payload["association"]["candidate_person_refs"] = [person]

        def verify(server, config):
            status, body = self._post(server, config, payload)
            self.assertEqual(status, 400, body)
            self.assertEqual(body["error"]["code"], "invalid_association")
        self._with_server(verify, config=config)

    def test_qualified_body_without_working_subject_remains_admissible(self):
        config, payload = self._body_only()

        def verify(server, config):
            status, body = self._post(server, config, payload)
            self.assertEqual(status, 200, body)
            self.assertEqual(body["id"], payload["id"])
        self._with_server(verify, config=config)


if __name__ == "__main__":
    unittest.main()
