"""Nonnetwork differential phase-view tests; synthetic contract fixtures only."""
import copy
import json
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

import test_adaptive_feedback_reference_context_service as reference
import test_adaptive_feedback_context_service as legacy
from adaptive_feedback_reference_validation import AdaptiveFeedbackReferenceProfile, REFERENCE_PROFILE
from adaptive_feedback_reference_phase_views import (
    create_fixture_clock, create_phase_view_profile, selection_configuration, phase_view_metrics,
)
from adaptive_feedback_validation import _PHASE_VIEWS_FALLBACK
from journal import Journal, JournalError, canonical, digest

HERE = Path(__file__).resolve().parent


class Host:
    scoped = reference.ReferenceFeedbackServiceTests.scoped
    preference = reference.ReferenceFeedbackServiceTests.preference
    bootstrap = reference.ReferenceFeedbackServiceTests.bootstrap
    semantic_genesis = reference.ReferenceFeedbackServiceTests.semantic_genesis

    def __init__(self, fixture, *, optin):
        self.f = copy.deepcopy(fixture); self.identity = self.f["identity"]
        self.tmp = tempfile.TemporaryDirectory(dir=HERE)
        self.clock = create_fixture_clock()
        config = {k: self.f[k] for k in ("identity", "clock_profile", "materials", "registered_surfaces",
                  "config", "config_digest", "producer_epochs", "actor_bindings", "classifier_policy", "write_authorities")}
        self.j = Journal(Path(self.tmp.name) / "fresh.sqlite", profile=REFERENCE_PROFILE,
                         adaptive_feedback_reference_config=config, grants=legacy.grants(self.f), clock=self.clock.read)
        self.clock.bind(self.j); self.j._set_meta("epoch", "synthetic-phase-views-differential-epoch")
        self.original = self.j.dynamic
        if optin:
            self.j.dynamic = create_phase_view_profile(self.j, selection_configuration(), self.clock)
        self.owner, self.worker, self.issuer = [next(g for g in self.j.grants if g.principal_id == p) for p in ("owner", "worker", "issuer")]

    @property
    def now(self): return self.clock.read()
    @now.setter
    def now(self, t): self.clock.advance(t)
    def close(self): self.j.close(); self.tmp.cleanup()


def state(host):
    return list(host.j.db.iterdump())


def observe(host):
    records = {"current": 0, "prior": 0, "phase": 0, "cuts": [], "pairs": []}
    p = host.j.dynamic
    hydrate, phase, cut, consume = p._hydrate_checkpoint_outputs, p._phase_transition, p._validate_cut, p._consume_phase_checked_views

    def hydration(cp, grant, cutoff=None):
        records["current" if cutoff is not None else "prior"] += 1
        return hydrate(cp, grant, cutoff)
    def transition(commit, old):
        records["phase"] += 1
        before = canonical([commit, old]); result = phase(commit, old)
        if canonical([commit, old]) != before: raise AssertionError("phase mutated proof view")
        return result
    def validate(commit, grant, previous, **kwargs):
        records["cuts"].append(bool(kwargs)); return cut(commit, grant, previous, **kwargs)
    def consumption(*args, **kwargs):
        pair = consume(*args, **kwargs)
        if pair is not _PHASE_VIEWS_FALLBACK: records["pairs"].append((pair, canonical(list(pair))))
        return pair
    p._hydrate_checkpoint_outputs, p._phase_transition, p._validate_cut, p._consume_phase_checked_views = hydration, transition, validate, consumption
    return records


class PhaseViewsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reference.ReferenceFeedbackServiceTests.setUpClass()
        cls.fixture = reference.ReferenceFeedbackServiceTests.f

    def setUp(self):
        self.default = Host(self.fixture, optin=False); self.opt = Host(self.fixture, optin=True)
    def tearDown(self): self.default.close(); self.opt.close()

    def synchronize(self): self.default.j.db.backup(self.opt.j.db)
    def compare_commit(self, commit):
        left = self.default.j.dynamic.worker_commit(copy.deepcopy(commit), self.default.worker, request_canonical_json=canonical(commit))
        right = self.opt.j.dynamic.worker_commit(copy.deepcopy(commit), self.opt.worker, request_canonical_json=canonical(commit))
        self.assertEqual(left, right); self.assertEqual(state(self.default), state(self.opt)); return left
    def compare_refusal(self, commit, expected=None):
        before = [state(h) for h in (self.default, self.opt)]; outcomes = []
        for h in (self.default, self.opt):
            with self.assertRaises(JournalError) as caught: h.j.dynamic.worker_commit(copy.deepcopy(commit), h.worker)
            outcomes.append((caught.exception.code, caught.exception.status, caught.exception.response()))
        self.assertEqual(outcomes[0], outcomes[1]); self.assertEqual(before, [state(h) for h in (self.default, self.opt)])
        if expected: self.assertEqual(outcomes[0][0], expected)

    def test_adoption_reruns_no_constructor_ddl_or_state_change(self):
        host = self.default; before = state(host); old = host.j.dynamic; changes = host.j.db.total_changes
        with patch.object(AdaptiveFeedbackReferenceProfile, "__init__", side_effect=AssertionError("constructor rerun")):
            adopted = create_phase_view_profile(host.j, selection_configuration(), host.clock)
        self.assertIs(host.j.dynamic, old); self.assertIs(adopted.origins, old.origins)
        self.assertIs(adopted.origins.connection, host.j.db); self.assertIs(adopted.j, host.j)
        self.assertIs(type(old), AdaptiveFeedbackReferenceProfile)
        self.assertEqual(before, state(host)); self.assertEqual(changes, host.j.db.total_changes)

    def test_closed_selection_and_nonempty_or_twice_adoption_refuse(self):
        for key in selection_configuration():
            changed = selection_configuration(); changed[key] = "foreign"
            with self.assertRaises(ValueError): create_phase_view_profile(self.default.j, changed, self.default.clock)
        with self.assertRaises(ValueError): create_phase_view_profile(self.opt.j, selection_configuration(), self.opt.clock)
        preference = self.default.preference(); self.default.j.dynamic.declaration(preference, self.default.owner, family=preference["schema"])
        with self.assertRaises(ValueError): create_phase_view_profile(self.default.j, selection_configuration(), self.default.clock)

    def test_clock_stable_callable_exact_bind_and_monotonic_bound(self):
        clock = self.opt.clock; self.assertIs(clock.read, clock.read); self.assertIs(clock.read, self.opt.j.clock)
        for t in (False, -1, 121, float("nan")):
            with self.assertRaises(ValueError): clock.advance(t)
        clock.advance(1)
        with self.assertRaises(ValueError): clock.advance(.5)
        with self.assertRaises(ValueError): clock.bind(self.opt.j)
        with self.assertRaises(ValueError): create_fixture_clock().advance(0)

    def test_bootstrap_exact_bytes_receipt_state_and_one_current_hydration(self):
        commit = self.default.bootstrap(); self.synchronize()
        a, b = observe(self.default), observe(self.opt); receipt = self.compare_commit(commit)
        self.assertEqual((a["current"], a["prior"], a["phase"]), (2, 0, 1))
        self.assertEqual((b["current"], b["prior"], b["phase"]), (1, 0, 1))
        self.assertEqual(a["cuts"], [False]); self.assertEqual(b["cuts"], [True])
        self.assertEqual(receipt["raw_request_digest"], digest(commit)); self.assertEqual(receipt["first_received_at"], 0)
        for pair, before in b["pairs"]: self.assertEqual(canonical(list(pair)), before)

    def test_prior_phase_once_and_all_later_views_stay_unmutated(self):
        commit = self.default.semantic_genesis(); self.synchronize()
        a, b = observe(self.default), observe(self.opt); self.compare_commit(commit)
        self.assertEqual((a["current"], a["prior"], a["phase"]), (2, 3, 1))
        self.assertEqual((b["current"], b["prior"], b["phase"]), (1, 1, 1))
        self.assertEqual(b["cuts"], [True])
        for pair, before in b["pairs"]: self.assertEqual(canonical(list(pair)), before)
        metrics = phase_view_metrics(self.opt.j.dynamic)
        self.assertEqual(metrics["current_hydrations"], 1); self.assertEqual(metrics["prior_hydrations"], 1)
        self.assertEqual(metrics["phase_checks"], 1); self.assertGreaterEqual(metrics["current_hydration_ms"], 0)

    def test_retry_retains_original_outcome_and_has_no_new_hydration_after_clock_change(self):
        commit = self.default.bootstrap(); self.synchronize(); self.compare_commit(commit)
        a, b = observe(self.default), observe(self.opt)
        self.default.now = self.opt.now = 1; self.compare_commit(commit)
        self.assertEqual((a["current"], a["prior"], a["phase"]), (0, 0, 0))
        self.assertEqual((b["current"], b["prior"], b["phase"]), (0, 0, 0))
        changed = copy.deepcopy(commit); changed["checkpoint"]["static_bindings_digest"] = "1" * 64
        self.compare_refusal(changed, "identity_conflict")

    def test_prepare_unknown_clock_adds_zero_callbacks_and_uses_complete_default_path(self):
        commit = self.default.bootstrap(); self.synchronize(); counts = [0, 0]
        for i, h in enumerate((self.default, self.opt)):
            def callback(i=i): counts[i] += 1; return 0
            h.j.clock = callback
        a, b = observe(self.default), observe(self.opt); self.compare_commit(commit)
        self.assertEqual(counts[0], counts[1]); self.assertEqual((a["current"], a["prior"], a["phase"]), (b["current"], b["prior"], b["phase"]))
        self.assertEqual(b["cuts"], [False]); self.assertEqual(phase_view_metrics(self.opt.j.dynamic)["preparation_fallbacks"], 1)

    def test_consume_replaced_clock_no_extra_callback_no_second_phase_and_fresh_cut_prior(self):
        commit = self.default.semantic_genesis(); self.synchronize(); counts = [0, 0]
        a, b = observe(self.default), observe(self.opt)
        p = self.default.j.dynamic; lower = p._hydrate_checkpoint_outputs
        def default_hydrate(cp, grant, cut=None):
            if cut is not None and a["current"] == 1: p.j.clock = replacement0
            return lower(cp, grant, cut)
        def replacement0(): counts[0] += 1; return 0
        def replacement1(): counts[1] += 1; return 0
        p._hydrate_checkpoint_outputs = default_hydrate
        q = self.opt.j.dynamic; consume = q._consume_phase_checked_views
        def switched(*args, **kwargs): q.j.clock = replacement1; return consume(*args, **kwargs)
        q._consume_phase_checked_views = switched
        self.compare_commit(commit)
        self.assertEqual(counts[0], counts[1]); self.assertEqual((b["current"], b["prior"], b["phase"]), (2, 3, 1))
        self.assertEqual(b["cuts"], [False]); self.assertEqual(phase_view_metrics(q)["consume_fallbacks"], 1)

    def test_consume_advanced_actual_clock_falls_back_with_same_receipt_and_phase_count(self):
        commit = self.default.semantic_genesis(); self.synchronize(); a, b = observe(self.default), observe(self.opt)
        p = self.default.j.dynamic; lower = p._hydrate_checkpoint_outputs
        def default_hydrate(cp, grant, cut=None):
            if cut is not None and a["current"] == 1: self.default.clock.advance(.01)
            return lower(cp, grant, cut)
        p._hydrate_checkpoint_outputs = default_hydrate
        q = self.opt.j.dynamic; consume = q._consume_phase_checked_views
        def advanced(*args, **kwargs): self.opt.clock.advance(.01); return consume(*args, **kwargs)
        q._consume_phase_checked_views = advanced
        self.compare_commit(commit); self.assertEqual((b["current"], b["prior"], b["phase"]), (2, 3, 1))
        self.assertEqual(b["cuts"], [False]); self.assertEqual(a["phase"], 1)

    def test_fake_pair_and_prior_cannot_be_public_approval(self):
        commit = self.default.bootstrap(); self.synchronize()
        self.opt.j.dynamic._prepare_phase_checked_views = lambda *a, **k: {"qualified": True, "current": commit["checkpoint"]}
        with self.assertRaises(JournalError) as caught: self.opt.j.dynamic.worker_commit(commit, self.opt.worker)
        self.assertEqual(caught.exception.code, "invalid_phase_views"); self.assertEqual(state(self.default), state(self.opt))

    def test_opaque_token_is_invocation_owner_grant_and_single_use_bound(self):
        commit = self.default.bootstrap(); self.synchronize(); p = self.opt.j.dynamic
        def run():
            invocation = object(); views = p._prepare_phase_checked_views(commit, None, self.opt.worker, invocation=invocation)
            with self.assertRaises(JournalError): p._consume_phase_checked_views(views, commit, None, self.opt.worker, invocation=object())
            with self.assertRaises(JournalError): p._consume_phase_checked_views(views, copy.deepcopy(commit), None, self.opt.worker, invocation=invocation)
            with self.assertRaises(JournalError): p._consume_phase_checked_views(views, commit, None, self.opt.owner, invocation=invocation)
            result = p._consume_phase_checked_views(views, commit, None, self.opt.worker, invocation=invocation)
            self.assertIsNot(result, _PHASE_VIEWS_FALLBACK)
            with self.assertRaises(JournalError): p._consume_phase_checked_views(views, commit, None, self.opt.worker, invocation=invocation)
            return {"done": True}
        self.opt.j._transaction(run)
        self.assertEqual(state(self.default), state(self.opt))

    def test_foreign_owner_and_finished_transaction_refuse_views(self):
        commit = self.default.bootstrap(); self.synchronize(); p = self.opt.j.dynamic; token = object(); saved = []
        def prepare(): saved.append(p._prepare_phase_checked_views(commit, None, self.opt.worker, invocation=token)); return {"done": True}
        self.opt.j._transaction(prepare)
        with self.assertRaises(JournalError): p._consume_phase_checked_views(saved[0], commit, None, self.opt.worker, invocation=token)
        other = Host(self.fixture, optin=True)
        try:
            def foreign():
                with self.assertRaises(JournalError): other.j.dynamic._consume_phase_checked_views(saved[0], commit, None, other.worker, invocation=token)
                return {"done": True}
            other.j._transaction(foreign)
        finally: other.close()

    def test_cut_failure_after_views_matches_and_rolls_back(self):
        commit = self.default.semantic_genesis(); self.synchronize()
        self.default.j.db.execute("UPDATE dynamic_cuts SET delivered_cursor=cut_cursor+1")
        self.synchronize(); a, b = observe(self.default), observe(self.opt)
        self.compare_refusal(commit, "incomplete_cut"); self.assertEqual(b["cuts"], [True])
        for pair, before in b["pairs"]: self.assertEqual(canonical(list(pair)), before)

    def test_storage_failure_after_output_writes_rolls_back_everything(self):
        commit = self.default.semantic_genesis(); self.synchronize(); a, b = observe(self.default), observe(self.opt)
        def refuse(): raise JournalError("capacity_probe", "bounded test-only later storage refusal", 507)
        self.default.j.dynamic._storage_budget = self.opt.j.dynamic._storage_budget = refuse
        self.compare_refusal(commit, "capacity_probe")
        for pair, before in b["pairs"]: self.assertEqual(canonical(list(pair)), before)

    def test_current_worker_revocation_between_calls_fences_new_work_not_retry(self):
        follow = self.default.semantic_genesis(); self.synchronize()
        raw = self.default.j.db.execute("SELECT request_blob FROM feedback_worker_commit_originals WHERE id='bootstrap'").fetchone()[0]
        commit = json.loads(zlib.decompress(raw)); self.compare_commit(commit)
        for h in (self.default, self.opt):
            authority = json.loads(h.j._meta("adaptive-authority"))
            next(p for p in authority["producers"] if p["producer_id"] == "worker")["revoked"] = True
            h.j._set_meta("adaptive-authority", canonical(authority))
        self.compare_commit(commit); self.compare_refusal(follow, "producer_revoked")

    def test_counter_and_static_mutants_keep_errors_and_no_effects(self):
        commit = self.default.semantic_genesis(); self.synchronize()
        for key, value in (("static_bindings_digest", "1" * 64), ("commit_seq", 9), ("runtime_epoch", "foreign")):
            mutant = copy.deepcopy(commit); mutant["checkpoint"][key] = value
            self.compare_refusal(mutant)

    def test_future_original_first_pending40_then80_is_differential(self):
        for available in (40, 80):
            # Each subcase starts a genuinely fresh independent journal pair.
            if available == 80:
                self.default.close(); self.opt.close(); self.default = Host(self.fixture, optin=False); self.opt = Host(self.fixture, optin=True)
            pref = self.default.preference(); pref.update(available_at=available, valid_until=120)
            r = self.default.j.dynamic.declaration(pref, self.default.owner, family=pref["schema"])
            commit = self.default.bootstrap(); publication = json.loads(self.default.j.db.execute("SELECT body FROM publications WHERE cursor=?", (r["cursor"],)).fetchone()[0])
            descriptor = {"source_ref": self.default.j.dynamic._source_ref(publication), "received_at": 0, "eligible_at": available}
            descriptor["digest"] = digest(descriptor); cp, cut = commit["checkpoint"], commit["input_cut"]
            cp.update(input_cursor=r["cursor"], dependency_receipts=[descriptor], pending_inputs=[{"dependency_digest": descriptor["digest"]}],
                      receipts=[{"ref": descriptor["source_ref"]["publication_ref"], "received_at": 0, "eligible_at": available}])
            cp["revisions"]["rows"] = [{"dependency_digest": descriptor["digest"]}]
            cut.update(receipts=copy.deepcopy(cp["receipts"]), eligible_refs=[], buffered_refs=[descriptor["source_ref"]["publication_ref"]])
            cut["digest"] = digest({k: v for k, v in cut.items() if k != "digest"})
            self.synchronize(); self.compare_commit(commit)
            row = self.opt.j.db.execute("SELECT received_at,eligible_at FROM feedback_worker_first_receipts WHERE cursor=?", (r["cursor"],)).fetchone()
            self.assertEqual(tuple(row), (0, available))

    def test_inherited_checkpoint_read_still_hydrates_independently_and_metrics_do_not_claim_it(self):
        commit = self.default.semantic_genesis(); self.synchronize(); self.compare_commit(commit)
        before = phase_view_metrics(self.opt.j.dynamic); observed = observe(self.opt)
        value = self.opt.j.checkpoint("worker", self.opt.worker, self.opt.identity["site_id"], self.opt.identity["room_id"])
        self.assertEqual(value["checkpoint"], commit["checkpoint"])
        self.opt.j.dynamic._stored_checkpoint_view(commit["checkpoint"], self.opt.worker)
        self.assertEqual(observed["prior"], 1); self.assertEqual(before, phase_view_metrics(self.opt.j.dynamic))

    def test_replaced_current_profile_preparation_is_default_without_callback(self):
        commit = self.default.bootstrap(); self.synchronize(); p = self.opt.j.dynamic
        actual = self.opt.clock._read_callable; count = [0]
        def callback(): count[0] += 1; return 0
        self.opt.clock._read_callable = callback; self.opt.j.clock = callback
        self.opt.j.dynamic = self.opt.original
        try:
            self.assertIsNone(p._prepare_phase_checked_views(commit, None, self.opt.worker, invocation=object()))
            self.assertEqual(count[0], 0)
        finally:
            self.opt.clock._read_callable = actual; self.opt.j.clock = actual; self.opt.j.dynamic = p

    def test_replaced_grant_tuple_during_consume_falls_back_once(self):
        commit = self.default.semantic_genesis(); self.synchronize(); a, b = observe(self.default), observe(self.opt)
        p = self.opt.j.dynamic; original = p._consume_phase_checked_views
        def replaced(*args, **kwargs): p.j.grants = tuple(list(p.j.grants)); return original(*args, **kwargs)
        p._consume_phase_checked_views = replaced
        self.compare_commit(commit); self.assertEqual((b["current"], b["prior"], b["phase"]), (2, 3, 1))
        self.assertEqual(b["cuts"], [False]); self.assertEqual(phase_view_metrics(p)["consume_fallbacks"], 1)

    def test_wrong_profile_config_origin_or_grants_are_not_commissioned(self):
        p = self.opt.j.dynamic; self.assertTrue(p._commissioned_clock())
        objects = [(p.j, "dynamic", p.original if hasattr(p, "original") else self.opt.original),
                   (p.j, "grants", tuple(list(p.j.grants))),
                   (p.j, "adaptive_feedback_reference_config", copy.deepcopy(p.j.adaptive_feedback_reference_config)),
                   (p, "config_digest", "f" * 64), (p, "origins", object())]
        for owner, key, replacement in objects:
            original = getattr(owner, key); setattr(owner, key, replacement)
            try: self.assertFalse(p._commissioned_clock(), key)
            finally: setattr(owner, key, original)


if __name__ == "__main__": unittest.main()
