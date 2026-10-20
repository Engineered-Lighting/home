import json
import unittest
from unittest.mock import patch

from jev_provider import JevProvider, ProviderFailure, post_systemone
from reasoning import ACTIVITIES


class JevProviderTests(unittest.TestCase):
    def setUp(self):
        self.now = 10
        self.calls, self.grants = [], []
        self.response = {"model": "jev-1.13.0", "answers": {"activity": {
            "type": "choice", "choice": "reading", "confidence": .8,
            "probabilities": {k: 1.0 if k == "reading" else 0.0 for k in ACTIVITIES}}}}
        self.records = [{"id": "a1", "site_id": "victoria", "session_id": "s1", "arm": "a",
            "generation": 1, "observed_at": 9, "available_at": 10, "valid_until": 12,
            "objects": [{"label": "book"}], "annotations": ["human truth"]}]
        self.args = dict(request_id="r1", site_id="victoria", session_id="s1", arm="a",
                         generation=1, cutoff=10, deadline=15)

    def authorize(self, ctx, packet, phase):
        self.grants.append((ctx, packet, phase))
        return {"allowed": True, "scope": "household_structured", "revision": "review1",
                "generation": 2, "expires_at": 30}

    def transport(self, body, token, timeout):
        self.calls.append((json.loads(body), token, timeout))
        return json.dumps(self.response).encode()

    def provider(self, **kw):
        args = dict(model_selector="jev-1.13", response_version="jev-1.13.0",
            authorize=self.authorize, credential=lambda: "fixture-key", transport=self.transport,
            clock=lambda: self.now)
        return JevProvider(**(args | kw))

    def test_independent_arms_and_provenance_without_labels(self):
        p = self.provider()
        self.assertEqual(self.calls, [])
        records = self.records + [{**self.records[0], "id": "b1", "arm": "b", "objects": [{"label": "phone"}]}]
        a = p.evaluate(records, **self.args)
        b = p.evaluate(records, **(self.args | {"arm": "b", "request_id": "r2"}))
        self.assertEqual(a["provenance"]["evidence_ids"], ["a1"])
        self.assertEqual(b["provenance"]["evidence_ids"], ["b1"])
        self.assertNotIn("human truth", json.dumps(self.calls))
        self.assertEqual(a["judgment"]["confidence"], .8)
        self.assertEqual(a["judgment"]["probabilities"]["reading"], 1)
        self.assertFalse(a["actuation"])
        self.assertEqual([x[2] for x in self.grants], ["send", "publish"]*2)

    def test_public_eval_does_not_authorize_household(self):
        def deny(*args):
            return self.authorize(*args) | {"scope": "public_eval"}
        with self.assertRaisesRegex(ProviderFailure, "household_egress_not_authorized"):
            self.provider(authorize=deny).evaluate(self.records, **self.args)
        self.assertEqual(self.calls, [])

    def test_history_is_retained_but_cannot_refresh_current_evidence(self):
        old = {**self.records[0], 'id':'past', 'observed_at':2, 'available_at':3, 'valid_until':5}
        result = self.provider().evaluate([old] + self.records, **self.args)
        self.assertEqual(result['provenance']['evidence_ids'], ['past','a1'])
        self.assertEqual(self.calls[0][0]['state']['evidence'][0]['valid_until'], 5)
        self.calls.clear()
        result = self.provider().evaluate([old], **self.args)
        self.assertEqual(result['status'], 'missing_evidence')
        self.assertEqual(self.calls, [])

    def test_explicit_lab_history_mode_keeps_expiry_and_declares_historical_task(self):
        old = {**self.records[0], 'observed_at':2, 'available_at':3, 'valid_until':5}
        result=self.provider(allow_recent_history=True).evaluate([old],**self.args)
        self.assertFalse(result['provenance']['fresh_evidence'])
        self.assertTrue(result['provenance']['allow_recent_history'])
        self.assertEqual(self.calls[0][0]['state']['evidence'][0]['valid_until'],5)
        self.assertIn('historical',self.calls[0][0]['state']['temporal_task'])
        self.calls.clear();self.grants.clear()
        with self.assertRaisesRegex(ProviderFailure,'request_invalid') as cm:
            self.provider(allow_recent_history=True).evaluate([old],**self.args,window_seconds=20)
        self.assertFalse(cm.exception.indeterminate)
        self.assertEqual((self.calls,self.grants),([],[]))

    def ha(self, observed_at, valid_until, **changes):
        return {**self.records[0], 'id':'ha'+str(observed_at), 'source_kind':'ha.observation', 'objects':[],
            'ha':{'binary_sensor.pc':{'state':'on','status':'known'}}, 'observed_at':observed_at,
            'available_at':observed_at, 'valid_until':valid_until, **changes}

    def test_fresh_ha_cannot_make_stale_visual_current_or_dispatch_alone(self):
        bindings = {'binary_sensor.pc':'computer_active'}
        stale = {**self.records[0], 'source_kind':'perception.output', 'observed_at':9, 'valid_until':9.5}
        fresh_ha = self.ha(9.8, 12.8)
        result = self.provider(ha_bindings=bindings).evaluate([stale, fresh_ha], **self.args)
        self.assertEqual(result['status'], 'missing_evidence')
        self.assertFalse(result['provenance']['fresh_evidence'])
        self.assertEqual((self.calls, self.grants), ([], []))
        result = self.provider(allow_recent_history=True, ha_bindings=bindings).evaluate([stale, fresh_ha], **self.args)
        self.assertFalse(result['provenance']['fresh_evidence'])
        self.assertEqual(result['provenance']['evidence_ids'], [stale['id'], fresh_ha['id']])
        self.assertEqual(len(self.calls), 1)
        self.calls.clear();self.grants.clear()
        for history in (False, True):
            result = self.provider(allow_recent_history=history, ha_bindings=bindings).evaluate([fresh_ha], **self.args)
            self.assertEqual(result['status'], 'missing_evidence')
            self.assertFalse(result['provenance']['fresh_evidence'])
        # Future non-visual context follows the same rule as HA.
        caption = {**self.records[0], 'id':'caption', 'source_kind':'historical_qwen'}
        self.assertEqual(self.provider(allow_recent_history=True).evaluate([caption], **self.args)['status'], 'missing_evidence')
        self.assertEqual((self.calls, self.grants), ([], []))

    def test_pre_send_validation_is_a_known_failure_not_unknown_delivery(self):
        invalid = [dict(request_id="bad id"), dict(deadline=9), dict(deadline=40)]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaisesRegex(ProviderFailure, 'request_invalid') as cm:
                self.provider().evaluate(self.records, **(self.args | changes))
            self.assertFalse(cm.exception.indeterminate)
        with self.assertRaisesRegex(ProviderFailure, 'request_invalid') as cm:
            self.provider(ha_bindings={'binary_sensor.pc':'unregistered_fact'}).evaluate(self.records, **self.args)
        self.assertFalse(cm.exception.indeterminate)
        # One frame's records are never trimmed: over the hard token cap it is
        # refused locally as over budget (was the 64 KiB byte guard's request_invalid).
        oversized = [{**self.records[0], 'id':'a'+str(i), 'objects':[{'label':'book','confidence':.5}]*128} for i in range(40)]
        with self.assertRaisesRegex(ProviderFailure, 'request_over_budget') as cm:
            self.provider().evaluate(oversized, **self.args)
        self.assertFalse(cm.exception.indeterminate)
        self.assertEqual((self.calls, self.grants), ([], []))

    def test_versions_and_malformed_usage_after_delivery(self):
        from jev_semantics import QUESTIONS
        self.response['answers'].update({k:{'type':'noul','noul':.2} for k in QUESTIONS})
        self.response['usage'] = ['not', 'a', 'mapping']
        result = self.provider(semantic=True).evaluate(self.records, **self.args)
        self.assertEqual(result['diagnostics']['projection_version'], 'structured-semantic-8')
        self.assertEqual(result['diagnostics']['question_version'], 'den-activity-7-semantic')
        self.assertEqual(result['diagnostics']['request']['state']['semantic']['version'], 'den-semantics-7')
        self.assertEqual((result['diagnostics']['usage'], result['diagnostics']['usage_status']), ({}, 'not_reported'))

    def test_revocation_and_changed_generation_discard_answer(self):
        for change in ({"allowed": False}, {"generation": 3}):
            def revoke(ctx, packet, phase):
                return self.authorize(ctx, packet, phase) | (change if phase == "publish" else {})
            with self.assertRaises(ProviderFailure):
                self.provider(authorize=revoke).evaluate(self.records, **self.args)

    def test_no_evidence_and_expired_response_are_not_successes(self):
        self.assertEqual(self.provider().evaluate([], **self.args)["status"], "missing_evidence")
        self.assertEqual(self.calls, [])
        def slow(*args):
            self.now = 16
            return self.transport(*args)
        self.assertEqual(self.provider(transport=slow).evaluate(self.records, **self.args)["status"], "expired")

    def test_provider_drift_and_invalid_distribution_rejected(self):
        self.response["model"] = "jev-1.14.0"
        with self.assertRaisesRegex(ProviderFailure, "provider_response_invalid"):
            self.provider().evaluate(self.records, **self.args)
        self.response["model"] = "jev-1.13.0"
        self.response["answers"]["activity"]["confidence"] = float("nan")
        with self.assertRaises(ProviderFailure):
            self.provider().evaluate(self.records, **self.args)

    def test_errors_hide_sensitive_details_and_do_not_retry(self):
        calls = []
        def failed(*args):
            calls.append(1)
            raise TimeoutError("credential or private evidence in remote diagnostic")
        with self.assertRaises(ProviderFailure) as cm:
            self.provider(transport=failed).evaluate(self.records, **self.args)
        self.assertTrue(cm.exception.indeterminate)
        self.assertNotIn("private", str(cm.exception))
        self.assertEqual(calls, [1])

    def test_fixed_http_destination_and_response_bounds(self):
        with patch("jev_provider.http.client.HTTPSConnection") as factory:
            conn = factory.return_value
            response = conn.getresponse.return_value
            response.status = 200
            response.getheader.return_value = "application/json; charset=utf-8"
            response.read1.side_effect = [b"{}", b""]
            self.assertEqual(post_systemone(b"{}", "fixture", 3), b"{}")
            factory.assert_called_once_with("api.typesafe.ai", timeout=3)
            self.assertEqual(conn.request.call_args.args[:2], ("POST", "/v1/systemone"))
            conn.close.assert_called_once()
            response.read1.side_effect = [b"x"*65537]
            with self.assertRaisesRegex(ProviderFailure, "too_large"):
                post_systemone(b"{}", "fixture", 3)

    def http_failure(self, status, chunks):
        """post_systemone against a fake connection answering `status` with a
        body delivered as `chunks` (an exception in it is raised by read1)."""
        with patch("jev_provider.http.client.HTTPSConnection") as factory:
            conn = factory.return_value
            response = conn.getresponse.return_value
            response.status = status
            response.getheader.return_value = "application/json"
            response.read1.side_effect = list(chunks)
            with self.assertRaises(ProviderFailure) as cm:
                post_systemone(b"{}", "fixture", 3)
            self.assertEqual(factory.call_count, 1)   # never retried
            conn.close.assert_called_once()
        return cm.exception

    def test_documented_refusal_with_a_body_is_failed_not_indeterminate(self):
        for status in (400, 401, 403, 404, 413, 422):
            with self.subTest(status=status):
                error = self.http_failure(status, [b'{"error":"private detail"}', b""])
                self.assertEqual((error.reason, error.indeterminate, error.http_status),
                                 ("provider_rejected_%d" % status, False, status))
                self.assertNotIn("private", str(error))

    def test_statuses_without_proof_of_non_execution_stay_indeterminate(self):
        # 429/529 are documented as retryable, not as unexecuted; 5xx and
        # redirects are undocumented. A refusal whose body never arrives, is
        # empty or overflows proves nothing either.
        cases = [(429, [b"{}", b""]), (500, [b"{}", b""]), (502, [b""]), (503, [b"{}", b""]), (529, [b"{}", b""]),
                 (302, [b""]), (422, [b""]), (413, [TimeoutError("read")]), (400, [b"x"*65537])]
        for status, chunks in cases:
            with self.subTest(status=status, chunks=str(chunks)[:30]):
                error = self.http_failure(status, chunks)
                self.assertEqual((error.reason, error.indeterminate, error.http_status),
                                 ("provider_http_error", True, status))

    def test_rejection_status_reaches_the_request_result_as_a_code_only(self):
        def rejected(*_):
            raise ProviderFailure("provider_rejected_422", http_status=422)
        with self.assertRaises(ProviderFailure) as cm:
            self.provider(transport=rejected).evaluate(self.records, **self.args)
        self.assertEqual((cm.exception.reason, cm.exception.indeterminate, cm.exception.http_status),
                         ("provider_rejected_422", False, 422))
        self.assertIsNone(ProviderFailure("x", http_status="422 body").http_status)

    def test_redirect_does_not_forward_credentials(self):
        with patch("jev_provider.http.client.HTTPSConnection") as factory:
            factory.return_value.getresponse.return_value.status = 302
            with self.assertRaises(ProviderFailure):
                post_systemone(b"{}", "fixture", 3)
            self.assertEqual(factory.call_count, 1)


    def caption(self, **changes):
        return {"id": "c1", "kind": "observation", "site_id": "victoria", "camera_id": "den", "session_id": "s1",
                "arm": "a", "generation": 1, "observed_at": 2, "available_at": 9, "valid_until": 11,
                "source_kind": "caption.receipt", "evidence_roots": ["caption:k"],
                "historical_caption": {"activity": "reading", "interpretation": "historical_model_interpretation",
                                       "version": "caption-projection-2:0123456789ab"}, **changes}

    def test_caption_only_or_ha_only_context_never_dispatches(self):
        ha = {**self.records[0], "id": "h1", "source_kind": "ha.observation", "objects": [], "ha": {"x": {"state": "on"}}}
        for records in ([], [ha]):
            result = self.provider(allow_recent_history=True, semantic=True).evaluate(
                records, **self.args, shared_context=[self.caption()])
            self.assertEqual(result["status"], "missing_evidence")
            self.assertFalse(result["provenance"]["fresh_evidence"])
        self.assertEqual((self.calls, self.grants), ([], []))

    def test_shared_caption_bypasses_the_window_but_never_its_validity(self):
        from jev_semantics import QUESTIONS
        self.response['answers'].update({k: {'type': 'noul', 'noul': .2} for k in QUESTIONS})
        for caption, included in ((self.caption(), True), (self.caption(valid_until=10), False),
                                  (self.caption(available_at=10.5), False), (self.caption(arm="b"), False),
                                  (self.caption(session_id="other"), False)):
            self.calls.clear()
            self.provider(allow_recent_history=True, semantic=True).evaluate(
                self.records, **(self.args | {"window_seconds": 0}), shared_context=[caption])
            packet = self.calls[0][0]
            ids = [r["id"] for r in packet["state"]["evidence"]]
            self.assertEqual("c1" in ids, included, caption)
            self.assertEqual(packet["state"]["semantic"]["source_coverage"]["qwen_caption"],
                             "supplied" if included else "not_supplied")
        # Supplied as ordinary records (not shared context) a caption never enters.
        self.calls.clear()
        self.provider(semantic=True).evaluate(self.records+[self.caption()], **self.args)
        self.assertNotIn("c1", [r["id"] for r in self.calls[0][0]["state"]["evidence"]])
        with self.assertRaises(ProviderFailure):
            self.provider().evaluate(self.records, **self.args, shared_context=[self.caption(), self.caption(id="c2")])


if __name__ == "__main__":
    unittest.main()
