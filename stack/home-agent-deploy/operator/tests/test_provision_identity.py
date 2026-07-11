from __future__ import annotations

import importlib.util
import io
from pathlib import Path
import sys
import unittest
import urllib.error
from unittest import mock


MODULE_PATH = Path(__file__).parents[1] / "provision_identity.py"
SPEC = importlib.util.spec_from_file_location("home_agent_provision_identity", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
provision = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = provision
SPEC.loader.exec_module(provision)


HA_USER_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
PERSON_ID = "11111111-1111-4111-8111-111111111111"
SOURCE_SHA = "b" * 64


class ProvisionIdentityTests(unittest.TestCase):
    def test_reviewed_stable_uuid_uses_exact_verify_not_create(self) -> None:
        with mock.patch.object(
            provision,
            "post",
            return_value={"person_id": PERSON_ID},
        ) as request:
            result = provision.create_person(
                HA_USER_ID,
                "Marcelo",
                PERSON_ID,
                SOURCE_SHA,
            )

        self.assertEqual(result, PERSON_ID)
        request.assert_called_once_with(
            "/v1/people/verify-reviewed",
            {
                "person_id": PERSON_ID,
                "display_name": "Marcelo",
                "legacy_source_sha256": SOURCE_SHA,
            },
            HA_USER_ID,
            audience="operator",
        )

    def test_stable_uuid_without_reviewed_digest_fails_before_request(self) -> None:
        with (
            mock.patch.object(provision, "post") as request,
            self.assertRaisesRegex(SystemExit, "source digest"),
        ):
            provision.create_person(HA_USER_ID, "Marcelo", PERSON_ID)
        request.assert_not_called()

    def test_new_person_uses_typed_create_without_legacy_claim(self) -> None:
        with mock.patch.object(
            provision,
            "post",
            return_value={"person_id": PERSON_ID},
        ) as request:
            result = provision.create_person(HA_USER_ID, "New Person")

        self.assertEqual(result, PERSON_ID)
        request.assert_called_once_with(
            "/v1/people",
            {"display_name": "New Person", "privacy_scope": "private"},
            HA_USER_ID,
            audience="operator",
        )

    def test_endpoint_audiences_are_fixed_and_tokens_do_not_cross(self) -> None:
        provision.SERVICE_TOKEN = "service-token"
        provision.OPERATOR_TOKEN = "operator-token"
        provision.BOOTSTRAP_TOKEN = "bootstrap-token"

        class Response:
            headers = {"Content-Length": "2"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return b"{}"

        with mock.patch.object(
            provision.OPENER, "open", return_value=Response()
        ) as opened:
            provision.post(
                "/v1/people", {}, HA_USER_ID, audience="operator"
            )
            operator_request = opened.call_args.args[0]
            self.assertEqual(
                operator_request.get_header("Authorization"),
                "Bearer operator-token",
            )
            provision.post(
                "/v1/principal-bindings", {}, HA_USER_ID, audience="service"
            )
            service_request = opened.call_args.args[0]
            self.assertEqual(
                service_request.get_header("Authorization"),
                "Bearer service-token",
            )

        with self.assertRaisesRegex(SystemExit, "audience"):
            provision.post(
                "/v1/people", {}, HA_USER_ID, audience="service"
            )

    def test_http_client_disables_proxy_redirect_and_reflected_error(self) -> None:
        self.assertTrue(
            any(
                isinstance(handler, provision._NoRedirect)
                for handler in provision.OPENER.handlers
            )
        )
        provision.SERVICE_TOKEN = "service-token"
        provision.OPERATOR_TOKEN = "operator-token"
        provision.BOOTSTRAP_TOKEN = "bootstrap-token"
        reflected = urllib.error.HTTPError(
            provision.BASE + "/v1/principal-bindings",
            400,
            "bad request",
            {},
            io.BytesIO(b"private-reflected-canary"),
        )
        with (
            mock.patch.object(provision.OPENER, "open", side_effect=reflected),
            self.assertRaises(SystemExit) as raised,
        ):
            provision.post(
                "/v1/principal-bindings", {}, HA_USER_ID, audience="service"
            )
        self.assertNotIn("private-reflected-canary", str(raised.exception))
        self.assertFalse(
            any(
                isinstance(handler, __import__("urllib.request").request.ProxyHandler)
                and handler.proxies
                for handler in provision.OPENER.handlers
            )
        )

    def test_reviewed_source_digest_parser_is_exact(self) -> None:
        with mock.patch("builtins.input", return_value=SOURCE_SHA.upper()):
            self.assertEqual(provision.reviewed_source_sha256("digest: "), SOURCE_SHA)
        with (
            mock.patch("builtins.input", return_value="not-a-digest"),
            self.assertRaisesRegex(SystemExit, "SHA-256"),
        ):
            provision.reviewed_source_sha256("digest: ")


if __name__ == "__main__":
    unittest.main()
