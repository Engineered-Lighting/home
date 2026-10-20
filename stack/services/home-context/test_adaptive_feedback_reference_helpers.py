"""Reference representation regressions; no devices, APIs or authority claims."""
import copy
import json
import unittest
from pathlib import Path

from adaptive_feedback_reference_validation import (
    ROLE_FIELDS, PROJECTION_FIELDS, dependency_pool, expand_exposure_projection,
    hydrate_reference_checkpoint,
)
from journal import JournalError, digest
from adaptive_feedback_validation import component_id

ROOT = Path(__file__).resolve().parents[3]


def reference_fixture(count=2):
    body = json.loads((ROOT / "contracts/home-adaptive-feedback/examples/home-geometry-4.json").read_text())
    identity = {key: body[key] for key in ("site_id", "room_id", "session_id", "domain_epoch")}
    body["objects"] = [{"object_ref": {**identity, "object_epoch": "book-epoch", "oid": "book1"},
                        "person_ref": None, "association_generation": 0, "state": "closed", "patches": [],
                        "uncertainty": {"translation_radius_m": 0, "rotation_radius_rad": 0, "extent_error_m": 0,
                                        "profile_ref": "synthetic-error", "profile_digest": "0" * 64}}]
    checkpoint = {"schema": "home-context-worker-checkpoint-8", "version": 8, **identity,
                  "journal_epoch": "reference-journal", "input_cursor": count, "last_t": 1,
                  **{role: [] for role in ROLE_FIELDS}, "referenced_outputs": [],
                  "dependency_receipts": [], "revisions": {"rows": []}, "pipeline": None}
    originals = []
    for cursor in range(1, count + 1):
        payload = copy.deepcopy(body); payload["id"] = "original-" + str(cursor)
        payload["observed_at"] = 0; payload["available_at"] = 0
        publication = {"schema": "home-context-publication-2", "id": payload["id"],
                       "producer_id": "synthetic-camera", "producer_epoch": "camera-1",
                       "stream_epoch": checkpoint["journal_epoch"], "journal_epoch": checkpoint["journal_epoch"],
                       "cursor": cursor, "context_seq": 0, "committed_at": 0,
                       "payload_type": payload["schema"], "payload_version": "4.0", "payload_digest": digest(payload), "payload": payload}
        source = {"publication_ref": {k: publication[k] for k in ("journal_epoch", "cursor", "payload_digest")},
                  **{k: publication[k] for k in ("producer_id", "producer_epoch", "payload_type", "payload_version")},
                  "publication_id": publication["id"], "component": None}
        descriptor = {"source_ref": source, "received_at": 1, "eligible_at": 1}
        descriptor["digest"] = digest(descriptor)
        checkpoint["dependency_receipts"].append(descriptor)
        checkpoint["geometry_inputs"].append({"dependency_digest": descriptor["digest"]})
        originals.append({"publication": publication, "first_received_at": 1})
    return checkpoint, originals


class ReferenceCarrierHelperTests(unittest.TestCase):
    def refuses(self, fn):
        with self.assertRaises(JournalError) as caught: fn()
        self.assertEqual(caught.exception.code, "invalid_reference")

    def test_exact_ordered_originals_and_no_input_mutation(self):
        cp, originals = reference_fixture(); before = copy.deepcopy((cp, originals))
        cp["geometry_inputs"].reverse()
        view = hydrate_reference_checkpoint(cp, originals)
        self.assertEqual([row["publication"]["cursor"] for row in view["geometry_inputs"]], [2, 1])
        self.assertEqual(view["schema"], "home-context-worker-checkpoint-8")
        self.assertEqual(view["referenced_outputs"], cp["referenced_outputs"])
        view["geometry_inputs"][0]["publication"]["payload"]["id"] = "mutated-private-copy"
        self.assertEqual(originals, before[1])
        self.assertTrue(all(set(row) == {"dependency_digest"} for row in cp["geometry_inputs"]))

    def test_unknown_duplicate_extra_and_rebound_descriptors_refuse(self):
        cp, originals = reference_fixture()
        for change in (lambda c: c["geometry_inputs"][0].update(dependency_digest="0" * 64),
                       lambda c: c["geometry_inputs"].append(copy.deepcopy(c["geometry_inputs"][0])),
                       lambda c: c["geometry_inputs"].pop(),
                       lambda c: c["dependency_receipts"][0]["source_ref"].update(producer_epoch="other")):
            mutant = copy.deepcopy(cp); change(mutant)
            self.refuses(lambda: hydrate_reference_checkpoint(mutant, originals))

    def test_role_specific_mask_presence_order_and_revisions_mask_free(self):
        cp, originals = reference_fixture(1)
        valid_id = component_id(originals[0]["publication"]["payload"]["objects"][0], True)
        cp["geometry_inputs"][0]["component_ids"] = [valid_id]
        view = hydrate_reference_checkpoint(cp, originals)
        self.assertEqual(view["geometry_inputs"][0]["component_ids"], [valid_id])
        for ids in ([], ["b", "a"], ["a", "a"]):
            mutant = copy.deepcopy(cp); mutant["geometry_inputs"][0]["component_ids"] = ids
            self.refuses(lambda: dependency_pool(mutant))
        mutant = copy.deepcopy(cp); mutant["committed_output_inputs"] = mutant["geometry_inputs"]; mutant["geometry_inputs"] = []
        self.refuses(lambda: dependency_pool(mutant))
        mutant = copy.deepcopy(cp); mutant["revisions"]["rows"] = [copy.deepcopy(mutant["geometry_inputs"][0])]
        self.refuses(lambda: dependency_pool(mutant))

    def test_first_scope_bytes_publisher_and_clock_rebinding_refuse(self):
        cp, originals = reference_fixture(1)
        for mutate in (lambda r: r.update(first_received_at=0),
                       lambda r: r["publication"].update(producer_id="foreign"),
                       lambda r: r["publication"]["payload"].update(domain_epoch="other"),
                       lambda r: r["publication"]["payload"].update(id="changed-body"),
                       lambda r: r["publication"].update(committed_at=2)):
            candidate = copy.deepcopy(originals); mutate(candidate[0])
            self.refuses(lambda: hydrate_reference_checkpoint(cp, candidate))

    def test_future_availability_is_not_refreshed_first_receipt(self):
        cp, originals = reference_fixture(1)
        p = originals[0]["publication"]; p["payload"]["available_at"] = 40; p["payload_digest"] = digest(p["payload"])
        descriptor = cp["dependency_receipts"][0]; descriptor["source_ref"]["publication_ref"]["payload_digest"] = p["payload_digest"]
        descriptor["received_at"] = 0; descriptor["eligible_at"] = 40; descriptor["digest"] = digest({k: v for k, v in descriptor.items() if k != "digest"})
        cp["geometry_inputs"] = []; cp["pending_inputs"] = [{"dependency_digest": descriptor["digest"]}]
        originals[0]["first_received_at"] = 0
        view = hydrate_reference_checkpoint(cp, originals)
        self.assertEqual(view["pending_inputs"][0]["received_at"], 0)
        self.assertEqual(descriptor["eligible_at"], 40)

    def test_projection_is_exact_closed_and_retains_full_or_null(self):
        held = {"installed_at": 1, "requests": [], "targets": [], "environment_heads": [], "support_bounds": [],
                "qualification_rows": [], "installation_rows": []}
        projection = {k: held[k] for k in PROJECTION_FIELDS}
        pipeline = {"exposure_context": {"representation": "held_context_projection_1", "digest": digest(projection)},
                    "feedback_exposure": {"held_context": held}}
        expand_exposure_projection(pipeline)
        self.assertEqual(pipeline["exposure_context"], projection)
        for context, parent in (({"representation": "held_context_projection_1", "digest": "0" * 64}, held),
                                ({"representation": "held_context_projection_1", "digest": digest(projection)}, None),
                                ({"representation": "held_context_projection_1", "digest": digest(projection), "path": "arbitrary"}, held)):
            candidate = {"exposure_context": context, "feedback_exposure": {"held_context": parent}}
            self.refuses(lambda: expand_exposure_projection(candidate))
        self.assertIsNone(expand_exposure_projection(None))


if __name__ == "__main__": unittest.main()
