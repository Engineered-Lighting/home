"""Stage C pure preparation; no adaptive journal, routes or runtime are installed."""
import copy
import json
import subprocess
import unittest
from pathlib import Path

from adaptive_validation import (CLOCK_PROFILE, assert_clock_profile, assert_relative_time,
                                 RevisionStore, assert_authority_cas, create_clock_profile,
                                 eligible_at, filter_subject_publications, following_allowed,
                                 normalize_time, server_eligible_at, subject_chain_allowed,
                                 validate_authority, validate_command_clocks, validate_subject_authorization)
from journal import JournalError, canonical, digest


ROOT = Path(__file__).resolve().parents[3]


class AdaptiveClockPreparationTests(unittest.TestCase):
    def setUp(self):
        self.profile = create_clock_profile(session_id="fictional-session-1")

    def row(self, effective=5, available=5, committed=5, received=5):
        return {"publication": {"payload": {"session_id": self.profile["session_id"],
                "clock_profile_digest": self.profile["digest"], "effective_at": effective,
                "available_at": available}, "committed_at": committed}, "received_at": received}

    def error(self, code, function):
        with self.assertRaises(JournalError) as raised:
            function()
        self.assertEqual(raised.exception.code, code)

    @staticmethod
    def python_case(case):
        op, args = case["op"], case["args"]
        try:
            if op == "createProfile":
                value = create_clock_profile(**args)
            elif op == "assertProfile":
                value = assert_clock_profile(args)
            elif op == "assertTime":
                value = assert_relative_time(args["value"], args["profile"])
            elif op == "normalizeTime":
                value = normalize_time(**args)
            elif op == "eligibleAt":
                value = eligible_at(args["row"], args["profile"])
            else:
                raise AssertionError("test operation")
            return {"ok": True, "value": value}
        except JournalError as error:
            return {"ok": False, "error": error.code}

    def test_python_clock_boundary_matches_actual_javascript(self):
        q = create_clock_profile(session_id="fictional-session-2", origin_utc_s=1800000000,
                                 utc_source_clocks=["z-clock", "a-clock"])
        cases = [{"op": "createProfile", "args": {"session_id": self.profile["session_id"]}},
                 {"op": "createProfile", "args": {"session_id": q["session_id"], "origin_utc_s": q["origin_utc_s"], "utc_source_clocks": ["z-clock", "a-clock"]}}]
        for config in ({"session_id": "bad space"}, {"session_id": "a", "utc_source_clocks": ["clock"]},
                       {"session_id": "a", "origin_utc_s": -1}, {"session_id": "a", "utc_source_clocks": None},
                       {"session_id": "a", "origin_utc_s": 1, "utc_source_clocks": ["clock", "clock"]}):
            cases.append({"op": "createProfile", "args": config})
        cases.append({"op": "assertProfile", "args": self.profile})
        for mutation in ({"maximum_t": 121}, {"digest": "0" * 64}, {"kind": "utc_epoch_seconds"}, {"extra": 0}):
            cases.append({"op": "assertProfile", "args": {**self.profile, **mutation}})
        for value in (0, 120, -.001, 120.001, 1800000000, True, None):
            cases.append({"op": "assertTime", "args": {"value": value, "profile": self.profile}})
        for value, units, clock, profile in (
                (5.4, CLOCK_PROFILE, self.profile["id"], self.profile),
                (5.4, CLOCK_PROFILE, "wrong-clock", self.profile),
                (1800000005.25, "utc_epoch_seconds", "a-clock", q),
                (1800000005.25, "utc_epoch_seconds", "wrong-clock", q),
                (1800000005.25, "utc_epoch_seconds", "a-clock", self.profile),
                (1800000005.25, "milliseconds", "a-clock", q),
                (1800000005.25, CLOCK_PROFILE, self.profile["id"], self.profile)):
            cases.append({"op": "normalizeTime", "args": {"value": value, "units": units,
                         "source_clock_id": clock, "profile": profile}})
        for times in ((9, 5, 5, 5), (5, 9, 5, 5), (5, 5, 9, 9), (5, 5, 5, 9), (5, 5, 6, 5)):
            cases.append({"op": "eligibleAt", "args": {"row": self.row(*times), "profile": self.profile}})
        for key in ("effective_at", "available_at", "clock_profile_digest", "session_id"):
            row = self.row(); del row["publication"]["payload"][key]
            cases.append({"op": "eligibleAt", "args": {"row": row, "profile": self.profile}})
        fallback = self.row(); fallback["publication"]["payload"].update(effective_at=None, observed_at=5)
        cases.append({"op": "eligibleAt", "args": {"row": fallback, "profile": self.profile}})
        missing = self.row(); del missing["received_at"]
        cases.append({"op": "eligibleAt", "args": {"row": missing, "profile": self.profile}})
        script = r"""
const K=require('./app/src/home-adaptive-experience-clock.js');let input='';process.stdin.on('data',x=>input+=x);process.stdin.on('end',()=>{
process.stdout.write(JSON.stringify(JSON.parse(input).map(c=>{try{let value;if(c.op==='createProfile')value=K.createProfile(c.args);else if(c.op==='assertProfile')value=K.assertProfile(c.args);else if(c.op==='assertTime')value=K.assertTime(c.args.value,c.args.profile);else if(c.op==='normalizeTime')value=K.normalizeTime(c.args);else value=K.eligibleAt(c.args.row,c.args.profile);return {ok:true,value};}catch(e){return {ok:false,error:e.message};}})));});
"""
        run = subprocess.run(["node", "-e", script], input=json.dumps(cases), cwd=ROOT,
                             text=True, capture_output=True, timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual([self.python_case(case) for case in cases], json.loads(run.stdout))

    def test_server_eligibility_never_manufactures_original_consumer_receipt(self):
        row = self.row(6, 4, 2, 8)
        self.assertEqual(server_eligible_at(row["publication"], self.profile), 6)
        self.assertEqual(eligible_at(row, self.profile), 8)
        del row["received_at"]
        self.error("adaptive_clock_missing", lambda: eligible_at(row, self.profile))

    def test_future_command_effect_and_availability_are_pending_without_clock_rewrite(self):
        payload = self.row()["publication"]["payload"]
        payload.update(issued_at=.2, effective_at=.6, available_at=.4, valid_until=1)
        before = copy.deepcopy(payload)
        clocks = validate_command_clocks(payload, self.profile, .2)
        self.assertEqual(clocks, {"observed_at": .2, "available_at": .4, "valid_until": 1})
        publication = {"payload": payload, "committed_at": .2}
        self.assertEqual(server_eligible_at(publication, self.profile), .6)
        self.assertEqual(eligible_at({"publication": publication, "received_at": .8}, self.profile), .8)
        self.assertEqual(payload, before)

    def test_issued_metadata_cannot_be_future_or_later_than_availability(self):
        payload = self.row()["publication"]["payload"]
        payload.update(issued_at=1, effective_at=2, available_at=2, valid_until=3)
        self.error("adaptive_issued_clock", lambda: validate_command_clocks(payload, self.profile, .5))
        payload.update(issued_at=2, available_at=1)
        self.error("adaptive_issued_clock", lambda: validate_command_clocks(payload, self.profile, 2))
        del payload["issued_at"]
        self.error("adaptive_clock_missing", lambda: validate_command_clocks(payload, self.profile, 2))

    def test_expired_at_admission_command_stays_expired_without_ttl_refresh(self):
        payload = self.row()["publication"]["payload"]
        payload.update(issued_at=0, effective_at=0, available_at=0, valid_until=.1)
        clocks = validate_command_clocks(payload, self.profile, .2)
        self.assertEqual(clocks["valid_until"], .1)
        row = {"publication": {"payload": payload, "committed_at": .2}, "received_at": .3}
        self.assertEqual(eligible_at(row, self.profile), .3)
        self.assertLess(payload["valid_until"], eligible_at(row, self.profile))

    def test_command_profile_binding_interval_and_utc_range_fail_closed(self):
        good = self.row()["publication"]["payload"]
        good.update(issued_at=0, effective_at=5, available_at=5, valid_until=6)
        for key, value, code in (("session_id", "other-session", "adaptive_clock_binding"),
                                ("clock_profile_digest", "0" * 64, "adaptive_clock_binding"),
                                ("issued_at", 1800000000, "adaptive_clock_range"),
                                ("effective_at", True, "adaptive_clock_range"),
                                ("valid_until", 5, "adaptive_clock_interval")):
            payload = {**good, key: value}
            self.error(code, lambda: validate_command_clocks(payload, self.profile, 5))

    def test_explicit_conversion_and_profile_construction_are_pure(self):
        clocks = ["z-clock", "a-clock"]
        q = create_clock_profile(session_id="fictional-session-2", origin_utc_s=1800000000,
                                 utc_source_clocks=clocks)
        before = copy.deepcopy(q)
        args = {"value": 1800000005.25, "units": "utc_epoch_seconds", "source_clock_id": "a-clock", "profile": q}
        self.assertEqual(normalize_time(**args), normalize_time(**args))
        self.assertEqual(clocks, ["z-clock", "a-clock"])
        self.assertEqual(q, before)
        self.assertEqual(normalize_time(**args)["provenance"]["original_value"], args["value"])


class AdaptiveRevisionPreparationTests(unittest.TestCase):
    def setUp(self):
        self.identity = {"site_id": "fictional-site", "room_id": "room-1",
                         "session_id": "session-1", "domain_epoch": "domain-1"}
        self.clock = create_clock_profile(session_id=self.identity["session_id"])
        self.subject = {"kind": "session", "id": "session-1"}
        self.auth = {"owner_id": "owner-1", "producer_id": "declared-owner", "scope": self.identity,
                     "subject_rights": [{"subject": self.subject, "chain_ids": ["band-1", "band-2"],
                                         "rights": ["read", "write"]}]}

    def store(self, **options):
        return RevisionStore(identity=self.identity, clock_profile=self.clock, **options)

    def test_configured_worker_read_namespace_survives_write_restore(self):
        auth = copy.deepcopy(self.auth)
        auth["subject_rights"][0]["derived_goal_namespace"] = "goal:adaptive-worker:"
        row = self.row(0)
        store = self.store(configured_worker_id="adaptive-worker"); store.admit(row, auth)
        restored = self.store(configured_worker_id="adaptive-worker"); restored.restore(store.snapshot(), lambda _: auth)
        self.assertEqual(restored.snapshot(), store.snapshot())
        with self.assertRaises(JournalError):
            self.store(configured_worker_id="another-worker").admit(row, auth)

    def row(self, n, *, previous=None, chain="band-1", effective=None, available=None,
            committed=None, received=None, until=90, status="active", cursor=None):
        effective = n if effective is None else effective
        available = n if available is None else available
        committed = n if committed is None else committed
        received = committed if received is None else received
        payload = {"schema": "home-preference-snapshot-1", "version": 1, "id": chain + ":" + str(n),
                   **self.identity, "owner_id": self.auth["owner_id"], "subject": self.subject,
                   "preference_set_id": chain, "revision": n, "expected_predecessor": previous,
                   "status": status, "issued_at": min(available, committed), "effective_at": effective,
                   "available_at": available, "valid_until": until, "clock_profile_digest": self.clock["digest"]}
        return {"publication": {"id": payload["id"], "producer_id": self.auth["producer_id"],
                "journal_epoch": "journal-1", "cursor": n + 1 if cursor is None else cursor,
                "payload_type": payload["schema"], "payload_version": "1.0", "payload": payload,
                "payload_digest": digest(payload), "committed_at": committed}, "received_at": received}

    @staticmethod
    def predecessor(row):
        return {"revision": row["publication"]["payload"]["revision"],
                "digest": row["publication"]["payload_digest"]}

    def error(self, code, function):
        with self.assertRaises(JournalError) as raised:
            function()
        self.assertEqual(raised.exception.code, code)

    def test_python_worker_revision_state_matches_actual_javascript(self):
        a = self.row(0)
        b = self.row(1, previous=self.predecessor(a), effective=20, available=10, committed=5, received=5)
        c = self.row(2, previous=self.predecessor(b), effective=21, available=21, committed=6,
                     received=6, until=22)
        d = self.row(0, chain="band-2", cursor=4, status="ended")
        rows = [a, b, c, d]
        store = self.store(consumer_receipts=True)
        for row in rows:
            store.admit(row, self.auth)
        times = [0, 19, 20, 21, 22, 30]
        expected = {"snapshot": store.snapshot(), "heads": [store.materialize(t) for t in times]}
        script = r"""
const R=require('./app/src/home-adaptive-experience-revisions.js');let input='';process.stdin.on('data',x=>input+=x);process.stdin.on('end',()=>{const c=JSON.parse(input),s=R.createRevisionStore({identity:c.identity,clock_profile:c.clock});for(const row of c.rows)s.admit(row,c.auth);const restored=R.createRevisionStore({identity:c.identity,clock_profile:c.clock});restored.restore(s.snapshot(),()=>c.auth);process.stdout.write(JSON.stringify({snapshot:restored.snapshot(),heads:c.times.map(t=>restored.materialize(t))}));});
"""
        run = subprocess.run(["node", "-e", script], input=json.dumps({"identity": self.identity,
                             "clock": self.clock, "auth": self.auth, "rows": rows, "times": times}),
                             cwd=ROOT, text=True, capture_output=True, timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(expected, json.loads(run.stdout))

    def test_future_admitted_head_fences_fork_before_it_is_causally_visible(self):
        s = self.store(); a = self.row(0)
        b = self.row(1, previous=self.predecessor(a), effective=20, available=10, committed=5)
        s.admit(a, self.auth); s.admit(b, self.auth)
        before = s.snapshot()
        fork = self.row(1, previous=self.predecessor(a), cursor=3)
        fork["publication"]["id"] = fork["publication"]["payload"]["id"] = "distinct-fork"
        fork["publication"]["payload_digest"] = digest(fork["publication"]["payload"])
        self.error("adaptive_revision_cas", lambda: s.admit(fork, self.auth))
        self.assertEqual(s.materialize(19)[0]["row"], a)
        self.assertEqual(s.materialize(20)[0]["row"], b)
        self.assertEqual(s.snapshot(), before)

    def test_server_and_delayed_consumer_heads_keep_distinct_original_eligibility(self):
        server = self.store(); worker = self.store(consumer_receipts=True)
        a = self.row(0); b = self.row(1, previous=self.predecessor(a), received=10)
        for s in (server, worker):
            s.admit(a, self.auth); s.admit(b, self.auth)
        self.assertEqual(server.materialize(2)[0]["row"], b)
        self.assertEqual(worker.materialize(2)[0]["row"], a)
        restored = self.store(consumer_receipts=True); restored.restore(worker.snapshot(), lambda _: self.auth)
        self.assertEqual(restored.materialize(9), worker.materialize(9))
        self.assertEqual(restored.materialize(10)[0]["row"], b)
        server_only = copy.deepcopy(a); del server_only["received_at"]
        self.store().admit(server_only, self.auth)
        self.error("adaptive_clock_missing", lambda: self.store(consumer_receipts=True).admit(server_only, self.auth))

    def test_newest_expired_and_ended_heads_are_tombstones_not_fallback(self):
        s = self.store(); a = self.row(0); b = self.row(1, previous=self.predecessor(a), until=3)
        c = self.row(2, previous=self.predecessor(b), status="ended", until=90)
        s.admit(a, self.auth); s.admit(b, self.auth)
        self.assertEqual(s.materialize(4)[0]["disposition"], "expired")
        self.assertEqual(s.materialize(4)[0]["row"], b)
        s.admit(c, self.auth)
        self.assertEqual(s.materialize(4)[0]["disposition"], "ended")
        self.assertEqual(s.materialize(4)[0]["row"], c)

    def test_retry_preserves_original_receipt_and_cannot_bypass_trusted_grant(self):
        s = self.store(consumer_receipts=True); a = self.row(0); s.admit(a, self.auth)
        retry = copy.deepcopy(a); retry["received_at"] = 20
        self.assertEqual(s.admit(retry, self.auth), {"accepted": False, "retry": True, "row": a})
        wrong = copy.deepcopy(self.auth); wrong["subject_rights"] = []
        self.error("adaptive_revision_subject", lambda: s.admit(retry, wrong))
        tampered = copy.deepcopy(a); tampered["publication"]["payload"]["valid_until"] = 80
        tampered["publication"]["payload_digest"] = digest(tampered["publication"]["payload"])
        self.error("adaptive_revision_event_conflict", lambda: s.admit(tampered, self.auth))
        self.assertEqual(s.snapshot()["rows"], [a])

    def test_read_filter_applies_owner_subject_chain_and_scope_without_geometry_rights(self):
        a = self.row(0)["publication"]
        denied = []
        for patch in ({"subject": {"kind": "household", "id": "unbound-household"}},
                      {"preference_set_id": "ungranted-chain"}, {"owner_id": "other-owner"},
                      {"room_id": "other-room"}, {"schema": "home-object-geometry-estimate-3"}):
            p = copy.deepcopy(a); p["payload"].update(patch); denied.append(p)
        result = filter_subject_publications([a, *denied], self.auth, self.identity)
        self.assertEqual(result, [a])
        result[0]["payload"]["owner_id"] = "mutation"
        self.assertEqual(a["payload"]["owner_id"], "owner-1")
        write_only = copy.deepcopy(self.auth); write_only["subject_rights"][0]["rights"] = ["write"]
        self.assertEqual(filter_subject_publications([a], write_only, self.identity), [])

    def test_derived_goal_namespace_is_explicit_worker_bound_and_never_preference_write(self):
        auth = copy.deepcopy(self.auth); auth["producer_id"] = "adaptive-worker"
        rule = auth["subject_rights"][0]; rule["chain_ids"] = []
        rule.update(rights=["read", "derive", "write"], derived_goal_namespace="goal:adaptive-worker:")
        p = copy.deepcopy(self.row(0)["publication"]["payload"])
        p.update(schema="home-experience-goal-1", goal_id="goal:adaptive-worker:book-1")
        del p["preference_set_id"]
        self.assertTrue(subject_chain_allowed(p, auth, self.identity, right="derive"))
        self.assertTrue(subject_chain_allowed(p, auth, self.identity, right="read"))
        self.assertFalse(subject_chain_allowed(p, auth, self.identity, right="write"))
        p["goal_id"] = "goal:other-worker:book-1"
        self.assertFalse(subject_chain_allowed(p, auth, self.identity, right="derive"))
        pref = copy.deepcopy(self.row(0)["publication"]["payload"])
        pref["preference_set_id"] = "goal:adaptive-worker:book-1"
        self.assertFalse(subject_chain_allowed(pref, auth, self.identity, right="write"))
        rule["derived_goal_namespace"] = "goal:other-worker:"
        self.error("adaptive_revision_subject", lambda: validate_subject_authorization(auth, self.identity))

    def test_payload_principal_assertion_cannot_authorize_wrong_subject_or_producer(self):
        row = self.row(0); row["publication"]["payload"]["principal_id"] = "owner-1"
        row["publication"]["payload"]["subject"] = {"kind": "household", "id": "unbound"}
        row["publication"]["payload_digest"] = digest(row["publication"]["payload"])
        self.error("adaptive_revision_subject", lambda: self.store().admit(row, self.auth))
        row = self.row(0); row["publication"]["producer_id"] = "payload-forger"
        self.error("adaptive_revision_owner", lambda: self.store().admit(row, self.auth))

    def test_restore_duplicate_clock_and_fork_errors_leave_prior_state_unchanged(self):
        s = self.store(consumer_receipts=True); a = self.row(0); s.admit(a, self.auth)
        before = s.snapshot()
        duplicate = copy.deepcopy(before); duplicate["rows"].append(copy.deepcopy(a))
        self.error("adaptive_revision_restore", lambda: s.restore(duplicate, lambda _: self.auth))
        self.assertEqual(s.snapshot(), before)
        b = self.row(1, previous=self.predecessor(a)); bad = copy.deepcopy(before); bad["rows"].append(b)
        bad["rows"][1]["received_at"] = .5
        self.error("adaptive_original_receipt_clock", lambda: s.restore(bad, lambda _: self.auth))
        self.assertEqual(s.snapshot(), before)
        bad = copy.deepcopy(before); bad["rows"].append(self.row(2, previous=self.predecessor(a)))
        self.error("adaptive_revision_cas", lambda: s.restore(bad, lambda _: self.auth))
        self.assertEqual(s.snapshot(), before)

    def test_preflight_is_atomic_across_revisions_and_bound_exhaustion(self):
        s = self.store(maximum_rows=2); a = self.row(0); s.admit(a, self.auth)
        b = self.row(1, previous=self.predecessor(a)); c = self.row(2, previous=self.predecessor(b))
        before = s.snapshot()
        self.error("adaptive_revision_budget", lambda: s.preflight([b, c], lambda _: self.auth))
        self.assertEqual(s.snapshot(), before)
        result = s.preflight([b], lambda _: self.auth)
        self.assertEqual(len(result["snapshot"]["rows"]), 2)
        self.assertEqual(s.snapshot(), before)
        s.restore(result["snapshot"], lambda _: self.auth)
        self.assertEqual(s.materialize(2)[0]["row"], b)

    def test_expiry_must_follow_both_effective_and_available_clocks(self):
        row = self.row(0, effective=0, available=2, committed=0, until=1)
        self.error("adaptive_clock_interval", lambda: self.store().admit(row, self.auth))


class AdaptiveAuthorityPreparationTests(unittest.TestCase):
    def setUp(self):
        self.identity = {"site_id": "fictional-site", "room_id": "room-1",
                         "session_id": "session-1", "domain_epoch": "domain-1"}
        self.person = {**self.identity, "track_epoch": "people-1", "pid": 1}
        self.object = {**self.identity, "object_epoch": "objects-1", "oid": "book-1"}
        self.second_person = {**self.person, "pid": 2}
        self.second_object = {**self.object, "oid": "book-2"}
        self.authority = {"room": {"motion_generation": 0, "motion_allowed": True},
                          "sources": [{"source_id": "source-1", "revocation_generation": 0, "revoked": False}],
                          "registrations": [{"registration_ref": "registry-1", "registration_generation": 0,
                                             "registry_digest": "a" * 64, "revoked": False}],
                          "follows": [self.follow(self.person, self.object), self.follow(self.second_person, self.second_object)]}
        self.authority["follows"].sort(key=lambda e: canonical([e["person_ref"], e["object_ref"],
                                       e["association_generation"], e["source_id"], e["registration_ref"]]))

    @staticmethod
    def follow(person, material):
        return {"person_ref": person, "object_ref": material, "association_generation": 0,
                "source_id": "source-1", "registration_ref": "registry-1", "follow_generation": 0,
                "follow_allowed": True}

    def allowed(self, authority, person=None, material=None):
        return following_allowed(authority, self.identity, person_ref=person or self.person,
                                 object_ref=material or self.object, association_generation=0,
                                 source_id="source-1", registration_ref="registry-1")

    def error(self, code, function):
        with self.assertRaises(JournalError) as raised:
            function()
        self.assertEqual(raised.exception.code, code)

    def test_complete_cas_compares_generations_flags_and_registry_digest(self):
        self.assertEqual(assert_authority_cas(self.authority, self.authority, self.identity), self.authority)
        mutations = [("room", None, "motion_generation", 1), ("room", None, "motion_allowed", False),
                     ("sources", 0, "revocation_generation", 1), ("sources", 0, "revoked", True),
                     ("registrations", 0, "registration_generation", 1), ("registrations", 0, "registry_digest", "b" * 64),
                     ("follows", 0, "follow_generation", 1), ("follows", 0, "follow_allowed", False)]
        for field, index, key, value in mutations:
            current = copy.deepcopy(self.authority)
            (current[field] if index is None else current[field][index])[key] = value
            self.error("adaptive_authority_changed", lambda: assert_authority_cas(self.authority, current, self.identity))

    def test_book_one_follow_prohibition_preserves_book_two_real_motion_authority(self):
        changed = copy.deepcopy(self.authority)
        first = next(e for e in changed["follows"] if e["object_ref"]["oid"] == "book-1")
        first.update(follow_generation=1, follow_allowed=False)
        self.assertFalse(self.allowed(changed))
        self.assertTrue(self.allowed(changed, self.second_person, self.second_object))
        self.assertEqual(changed["room"], self.authority["room"])
        self.assertEqual(changed["sources"], self.authority["sources"])
        self.assertEqual(changed["registrations"], self.authority["registrations"])

    def test_real_room_source_registration_revocation_fences_both_books(self):
        for field, key in (("room", "motion_allowed"), ("sources", "revoked"), ("registrations", "revoked")):
            changed = copy.deepcopy(self.authority)
            if field == "room":
                changed[field][key] = False
            else:
                changed[field][0][key] = True
            self.assertFalse(self.allowed(changed))
            self.assertFalse(self.allowed(changed, self.second_person, self.second_object))

    def test_missing_or_reused_epoch_follow_scope_does_not_gain_permission(self):
        self.assertFalse(self.allowed(self.authority, {**self.person, "track_epoch": "people-2"}))
        self.assertFalse(self.allowed(self.authority, material={**self.object, "object_epoch": "objects-2"}))
        changed = copy.deepcopy(self.authority); changed["follows"] = []
        self.assertFalse(self.allowed(changed))

    def test_authority_requires_closed_sorted_unique_complete_map_and_bound(self):
        mutations = []
        bad = copy.deepcopy(self.authority); bad["extra"] = 1; mutations.append((bad, "adaptive_authority_shape"))
        bad = copy.deepcopy(self.authority); bad["sources"][0]["revoked"] = 0; mutations.append((bad, "adaptive_authority_shape"))
        bad = copy.deepcopy(self.authority); bad["sources"] *= 2; mutations.append((bad, "adaptive_authority_order"))
        bad = copy.deepcopy(self.authority); bad["follows"].reverse(); mutations.append((bad, "adaptive_authority_order"))
        bad = copy.deepcopy(self.authority); bad["sources"] = []; mutations.append((bad, "adaptive_authority_binding"))
        bad = copy.deepcopy(self.authority); bad["sources"] *= 5; mutations.append((bad, "adaptive_authority_shape"))
        bad = copy.deepcopy(self.authority); bad["follows"][0]["person_ref"]["room_id"] = "other-room"; mutations.append((bad, "adaptive_authority_shape"))
        for bad, error in mutations:
            self.error(error, lambda: validate_authority(bad, self.identity))

    def test_authority_and_returned_cas_state_are_not_mutable_aliases(self):
        before = copy.deepcopy(self.authority)
        result = assert_authority_cas(self.authority, self.authority, self.identity)
        result["follows"][0]["follow_allowed"] = False
        self.assertEqual(self.authority, before)


if __name__ == "__main__":
    unittest.main()
