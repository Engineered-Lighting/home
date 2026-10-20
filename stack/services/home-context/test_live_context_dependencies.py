"""Independent public-path regressions for scoped and transitive live support."""
import copy
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from journal import Journal, PREFIX
from live_test_helpers import (LiveHttp, MANIFEST, NOW, PROFILE, SCOPE, barrier, configuration,
                               event, fact, geometry, grant, independent_digest,
                               publication_ref, receipt_resolver)


@contextmanager
def session(*, dependent_sources=False, limited_revision=False):
    """Only generated synthetic configuration; no household stores or files."""
    config = configuration()
    if dependent_sources:
        for name in ("dependent-source", "transitive-source"):
            config["manifest"]["sources"][name] = copy.deepcopy(config["manifest"]["sources"]["synthetic-source"])
        config["manifest_ref"]["digest"] = independent_digest(config["manifest"])
        MANIFEST.update(config["manifest_ref"])
    authority = grant()
    if dependent_sources:
        for name in ("dependent-source", "transitive-source"):
            authority["source_ids"].append(name)
            binding = copy.deepcopy(authority["live_bindings"][0])
            binding["source_id"] = name
            authority["live_bindings"].append(binding)
    grants = [authority]
    if limited_revision:
        limited = grant(token="synthetic-limited-token")
        limited["revisions"] = ["synthetic-r2"]
        grants.append(limited)
    with tempfile.TemporaryDirectory(prefix="synthetic-live-dependency-", dir=Path(__file__).parent) as work:
        state = SimpleNamespace(now=NOW + .2, path=Path(work) / "synthetic.sqlite", config=config, grants=grants)

        def reopen():
            state.journal = Journal(state.path, grants=state.grants, profile=PROFILE, clock=lambda: state.now,
                                    live_config=state.config, live_receipt_resolver=receipt_resolver)
            state.server = LiveHttp(state.journal)

        reopen()
        state.reopen = reopen
        try:
            yield state
        finally:
            state.server.close()
            state.journal.close()


class LiveDependencyTests(unittest.TestCase):
    def accept(self, state, payload):
        status, receipt = state.server.request("POST", PREFIX + "/events", event(payload))
        self.assertEqual(status, 200, receipt)
        return receipt

    def snapshot(self, state, token="synthetic-live-token"):
        status, result = state.server.request("GET", PREFIX + "/snapshot?site_id=synthetic-site&room_id=synthetic-room", token=token)
        self.assertEqual(status, 200, result)
        return result

    def test_parent_requires_its_own_revision_read_authority(self):
        with session(limited_revision=True) as state:
            original = geometry()
            saved = self.accept(state, original)
            self.assertEqual(self.snapshot(state, "synthetic-limited-token")["items"], [])
            state.now = NOW + 1.2
            derived = fact(seq=2)
            derived["parent_refs"] = [publication_ref(saved)]
            derived["observation_roots"] += original["observation_roots"]
            for field in ("observation_root_times", "sensor_root_bindings"):
                derived[field].update(copy.deepcopy(original[field]))
            proposed = event(derived)
            proposed["revision"] = "synthetic-r2"
            status, result = state.server.request("POST", PREFIX + "/events", proposed, token="synthetic-limited-token")
            self.assertEqual(status, 403, result)
            self.assertEqual(result["error"]["code"], "forbidden")
            self.assertEqual(self.snapshot(state)["cursor"], 1)
            self.assertEqual(self.snapshot(state, "synthetic-limited-token")["items"], [])

    def test_transitive_support_loss_withdraws_current_claims_and_survives_restart(self):
        for cause in ("barrier", "revocation", "supersession", "expiry"):
            with self.subTest(cause=cause), session(dependent_sources=True) as state:
                original = geometry()
                if cause == "expiry":
                    original["valid_until"] = NOW + 2
                    for part in original["parts"].values():
                        part["valid_until"] = NOW + 2
                original_receipt = self.accept(state, original)
                state.now = NOW + .4
                derived = fact(event_id="derived-claim")
                derived["source_id"] = derived["head"]["key"]["source_id"] = "dependent-source"
                derived["parent_refs"] = [publication_ref(original_receipt)]
                derived["available_at"] = NOW + .3
                derived_receipt = self.accept(state, derived)
                state.now = NOW + .6
                transitive = fact(event_id="transitive-claim")
                transitive["source_id"] = transitive["head"]["key"]["source_id"] = "transitive-source"
                transitive["parent_refs"] = [publication_ref(derived_receipt)]
                transitive["available_at"] = NOW + .5
                self.accept(state, transitive)
                self.assertEqual({item["id"] for item in self.snapshot(state)["items"]},
                                 {original["id"], "derived-claim", "transitive-claim"})
                state.now = NOW + 1.2
                expected_ids = set()
                if cause == "barrier":
                    self.accept(state, barrier([original_receipt]))
                elif cause == "revocation":
                    payload = {"schema": "home-context-revocation-1", "version": 1, "id": "revoke-original-source", **SCOPE,
                               "source_id": "synthetic-source", "revision": "*", "reason": "synthetic-authority-ended",
                               "available_at": NOW + 1}
                    envelope = event(original)
                    envelope.update(id=payload["id"], payload_type=payload["schema"], payload=payload,
                                    observed_at=NOW + 1, available_at=NOW + 1, valid_until=NOW + 2)
                    status, result = state.server.request("POST", PREFIX + "/revocations", envelope)
                    self.assertEqual(status, 200, result)
                elif cause == "supersession":
                    replacement = geometry(event_id="replacement-geometry", seq=2)
                    replacement["head"]["supersedes"] = publication_ref(original_receipt)
                    self.accept(state, replacement)
                    expected_ids = {"replacement-geometry"}
                else:
                    state.now = NOW + 2
                self.assertEqual({item["id"] for item in self.snapshot(state)["items"]}, expected_ids)
                state.server.close()
                state.journal.close()
                state.reopen()
                self.assertEqual({item["id"] for item in self.snapshot(state)["items"]}, expected_ids)
                # Receipt reconciliation is historical, and must not rematerialize
                # a withdrawn dependent even when its own TTL has not expired.
                self.assertEqual(self.accept(state, derived), derived_receipt)
                self.assertEqual({item["id"] for item in self.snapshot(state)["items"]}, expected_ids)


if __name__ == "__main__":
    unittest.main()
