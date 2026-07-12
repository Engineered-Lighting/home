from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
import urllib.error
import uuid
from unittest import mock


MODULE_PATH = Path(__file__).parents[1] / "provision_identity.py"
SPEC = importlib.util.spec_from_file_location(
    "home_agent_provision_identity", MODULE_PATH
)
assert SPEC is not None and SPEC.loader is not None
provision = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = provision
SPEC.loader.exec_module(provision)


REQUEST_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
REVIEW_CODE = "ABCDEFGHJKLMNPQ2"
PERSON_ID = "11111111-1111-4111-8111-111111111111"
PROPOSAL_ID = "22222222-2222-4222-8222-222222222222"
SOURCE_SHA = "b" * 64
RECEIPT = "c" * 64
REQUEST = {
    "request_id": REQUEST_ID,
    "review_code": REVIEW_CODE,
    "state": "pending",
    "requested_at": "2026-07-12T12:00:00Z",
    "expires_at": "2026-07-13T12:00:00Z",
}
PERSON = {
    "person_id": PERSON_ID,
    "display_name": "Marcelo",
    "privacy_scope": "private",
    "status": "active",
}
PROPOSAL = {
    "proposal_id": PROPOSAL_ID,
    "request_id": REQUEST_ID,
    "person_id": PERSON_ID,
    "reviewed_display_label": "Marcelo",
    "state": "ready",
    "stage_receipt_digest": RECEIPT,
    "staged_at": "2026-07-12T12:05:00Z",
    "expires_at": "2026-07-12T12:35:00Z",
}


class Response:
    def __init__(self, payload: dict[str, object], status: int) -> None:
        self.payload = json.dumps(payload).encode("utf-8")
        self.status = status
        self.headers = {"Content-Length": str(len(self.payload))}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, limit: int) -> bytes:
        return self.payload[:limit]


class ProvisionIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        provision.OPERATOR_TOKEN = "operator-token"
        provision.BOOTSTRAP_TOKEN = "bootstrap-token"

    def test_operator_client_sends_no_subject_identity_or_service_token(self) -> None:
        with mock.patch.object(
            provision.OPENER,
            "open",
            return_value=Response({"requests": []}, 200),
        ) as opened:
            result = provision.request_json(
                "GET", "/v1/operator/principal-binding-requests"
            )

        self.assertEqual(result, {"requests": []})
        request = opened.call_args.args[0]
        self.assertEqual(request.method, "GET")
        self.assertEqual(
            request.get_header("Authorization"), "Bearer operator-token"
        )
        self.assertEqual(
            request.get_header("X-home-agent-bootstrap"), "bootstrap-token"
        )
        self.assertIsNone(request.get_header("X-authenticated-ha-user"))
        self.assertIsNone(request.data)

    def test_only_exact_operator_endpoints_and_methods_are_allowed(self) -> None:
        for method, path, body in (
            ("POST", "/v1/principal-bindings", {}),
            ("POST", "/v1/source-entity-bindings", {}),
            ("POST", "/v1/relationships/parent-confirmations", {}),
            ("POST", "/v1/places", {}),
            ("POST", "/v1/people", {}),
            ("POST", "/v1/operator/principal-binding-requests", {}),
            ("GET", "/v1/operator/principal-binding-proposals", None),
        ):
            with self.subTest(method=method, path=path), self.assertRaisesRegex(
                SystemExit, "endpoint"
            ):
                provision.request_json(method, path, body)

        with self.assertRaisesRegex(SystemExit, "body"):
            provision.request_json(
                "GET", "/v1/operator/principal-binding-requests", {}
            )

    def test_pending_requests_are_strict_and_selectable_by_code_or_uuid(self) -> None:
        with mock.patch.object(
            provision,
            "request_json",
            return_value={"requests": [REQUEST]},
        ):
            requests = provision.list_pending_requests()

        selected_by_code = provision.select_request(requests, REVIEW_CODE.lower())
        selected_by_id = provision.select_request(requests, REQUEST_ID)
        self.assertEqual(selected_by_code["request_id"], REQUEST_ID)
        self.assertEqual(selected_by_id["review_code"], REVIEW_CODE)
        self.assertNotIn("ha_user_id", selected_by_code)

        malformed = dict(REQUEST, ha_user_id="must-not-be-exposed")
        with (
            mock.patch.object(
                provision,
                "request_json",
                return_value={"requests": [malformed]},
            ),
            self.assertRaisesRegex(SystemExit, "schema"),
        ):
            provision.list_pending_requests()

    def test_unknown_or_ambiguous_request_fails_without_staging(self) -> None:
        requests = [
            {
                key: str(value)
                for key, value in REQUEST.items()
                if key != "state"
            }
        ]
        with self.assertRaisesRegex(SystemExit, "no unique pending request"):
            provision.select_request(
                requests,
                "99999999-9999-4999-8999-999999999999",
            )
        with self.assertRaisesRegex(SystemExit, "review code or request UUID"):
            provision.select_request(requests, "not-an-identifier")

    def test_existing_person_is_verified_exactly_and_never_created(self) -> None:
        with mock.patch.object(
            provision,
            "request_json",
            return_value=PERSON,
        ) as request:
            result = provision.verify_existing_person(
                PERSON_ID, "Marcelo", SOURCE_SHA
            )

        self.assertEqual(result, PERSON)
        request.assert_called_once_with(
            "POST",
            "/v1/people/verify-reviewed",
            {
                "person_id": PERSON_ID,
                "display_name": "Marcelo",
                "legacy_source_sha256": SOURCE_SHA,
            },
        )

        with (
            mock.patch.object(
                provision,
                "request_json",
                return_value=dict(PERSON, display_name="Different Person"),
            ),
            self.assertRaisesRegex(SystemExit, "did not match"),
        ):
            provision.verify_existing_person(PERSON_ID, "Marcelo", SOURCE_SHA)

    def test_stage_requires_matching_pair_and_random_operator_request(self) -> None:
        selected = {
            key: str(value)
            for key, value in REQUEST.items()
            if key != "state"
        }
        with mock.patch.object(
            provision,
            "request_json",
            return_value=PROPOSAL,
        ) as request:
            result = provision.stage_proposal(selected, PERSON)

        self.assertEqual(result["stage_receipt_digest"], RECEIPT)
        args = request.call_args.args
        self.assertEqual(
            args[:2],
            ("POST", "/v1/operator/principal-binding-proposals"),
        )
        body = args[2]
        self.assertEqual(body["request_id"], REQUEST_ID)
        self.assertEqual(body["review_code"], REVIEW_CODE)
        self.assertEqual(body["person_id"], PERSON_ID)
        self.assertEqual(uuid.UUID(body["operator_request_id"]).version, 4)
        self.assertNotIn("ha_user_id", body)
        self.assertNotIn("confirmation_artifact_id", body)

    def test_stage_rejects_response_label_or_identity_substitution(self) -> None:
        selected = {
            key: str(value)
            for key, value in REQUEST.items()
            if key != "state"
        }
        for changed, message in (
            ({"request_id": str(uuid.uuid4())}, "did not match"),
            ({"person_id": str(uuid.uuid4())}, "did not match"),
            ({"reviewed_display_label": "Someone Else"}, "display label"),
            ({"stage_receipt_digest": "not-a-digest"}, "receipt"),
        ):
            with self.subTest(changed=changed), mock.patch.object(
                provision,
                "request_json",
                return_value=dict(PROPOSAL, **changed),
            ), self.assertRaisesRegex(SystemExit, message):
                provision.stage_proposal(selected, PERSON)

    def test_record_only_conflict_is_fail_closed(self) -> None:
        reflected = urllib.error.HTTPError(
            provision.BASE + "/v1/operator/principal-binding-requests",
            409,
            "conflict",
            {},
            io.BytesIO(b"private-reflected-canary"),
        )
        with (
            mock.patch.object(provision.OPENER, "open", side_effect=reflected),
            self.assertRaises(SystemExit) as raised,
        ):
            provision.request_json(
                "GET", "/v1/operator/principal-binding-requests"
            )
        message = str(raised.exception)
        self.assertIn("current rollout", message)
        self.assertIn("no changes", message)
        self.assertNotIn("private-reflected-canary", message)

    def test_http_client_disables_proxy_and_redirect_following(self) -> None:
        self.assertTrue(
            any(
                isinstance(handler, provision._NoRedirect)
                for handler in provision.OPENER.handlers
            )
        )
        self.assertFalse(
            any(
                isinstance(handler, __import__("urllib.request").request.ProxyHandler)
                and handler.proxies
                for handler in provision.OPENER.handlers
            )
        )

    def test_main_accepts_no_command_line_authority(self) -> None:
        with (
            mock.patch.object(sys, "argv", ["provision_identity.py", REQUEST_ID]),
            mock.patch.object(provision, "configure_credentials") as credentials,
            self.assertRaisesRegex(SystemExit, "no command-line arguments"),
        ):
            provision.main()
        credentials.assert_not_called()

    def test_source_digest_parser_is_exact(self) -> None:
        with mock.patch("builtins.input", return_value=SOURCE_SHA.upper()):
            self.assertEqual(
                provision.reviewed_source_sha256("digest: "), SOURCE_SHA
            )
        with (
            mock.patch("builtins.input", return_value="not-a-digest"),
            self.assertRaisesRegex(SystemExit, "SHA-256"),
        ):
            provision.reviewed_source_sha256("digest: ")

    def test_source_contains_no_ha_uuid_or_legacy_side_effect_workflow(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("X-Authenticated-HA-User", source)
        self.assertNotIn("/v1/principal-bindings", source)
        self.assertNotIn("/v1/source-entity-bindings", source)
        self.assertNotIn("/v1/relationships/parent-confirmations", source)
        self.assertNotIn("/v1/places", source)
        self.assertNotIn("device_tracker", source)


if __name__ == "__main__":
    unittest.main()
