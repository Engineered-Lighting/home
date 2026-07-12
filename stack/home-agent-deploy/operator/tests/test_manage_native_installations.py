from __future__ import annotations

import base64
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "manage_native_installations.py"
SPEC = importlib.util.spec_from_file_location("manage_native_installations", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
registry = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(registry)

INSTALLATION_ID = "018f6f42-3a8b-4c11-8123-123456789abc"
GX = bytes.fromhex("6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296")
GY = bytes.fromhex("4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5")


def encoded(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def installation(**changes):
    value = {
        "installation_id": INSTALLATION_ID,
        "ha_user_id": "marcelo-ha-user",
        "status": "active",
        "public_key_jwk": {
            "kty": "EC",
            "crv": "P-256",
            "x": encoded(GX),
            "y": encoded(GY),
            "kid": INSTALLATION_ID,
        },
    }
    value.update(changes)
    return value


class NativeInstallationRegistryTests(unittest.TestCase):
    def test_valid_registry_matches_bff_contract(self) -> None:
        result = registry._validate_registry(
            {"schema_version": 1, "installations": [installation()]}
        )
        self.assertEqual(result["installations"][0]["installation_id"], INSTALLATION_ID)
        self.assertEqual(result["installations"][0]["public_key_jwk"]["crv"], "P-256")

    def test_unknown_duplicate_and_malformed_identity_fields_fail_closed(self) -> None:
        cases = [
            {"schema_version": 1, "installations": [installation(extra=True)]},
            {"schema_version": 1, "installations": [installation(), installation()]},
            {
                "schema_version": 1,
                "installations": [installation(ha_user_id="bad\nuser")],
            },
        ]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(registry.RegistryError):
                registry._validate_registry(value)

    def test_invalid_or_mismatched_public_keys_fail_closed(self) -> None:
        off_curve = installation()
        off_curve["public_key_jwk"] = {
            **off_curve["public_key_jwk"],
            "y": encoded((1).to_bytes(32, "big")),
        }
        wrong_kid = installation()
        wrong_kid["public_key_jwk"] = {
            **wrong_kid["public_key_jwk"],
            "kid": "bdad331c-997a-438a-aa00-fce3d85928ce",
        }
        for value in (off_curve, wrong_kid):
            with self.assertRaises(registry.RegistryError):
                registry._validate_installation(value)

    def test_one_public_key_cannot_bind_multiple_installations_or_users(self) -> None:
        second_id = "bdad331c-997a-438a-aa00-fce3d85928ce"
        duplicate_key = installation(
            installation_id=second_id,
            ha_user_id="different-ha-user",
        )
        duplicate_key["public_key_jwk"] = {
            **duplicate_key["public_key_jwk"],
            "kid": second_id,
        }
        with self.assertRaisesRegex(
            registry.RegistryError,
            "public key is bound to multiple installations",
        ):
            registry._validate_registry(
                {
                    "schema_version": 1,
                    "installations": [installation(), duplicate_key],
                }
            )

    def test_enrollment_is_idempotent_but_never_rebinds_or_reactivates(self) -> None:
        empty = {"schema_version": 1, "installations": []}
        operation = {"action": "enroll", "installation": installation()}
        enrolled = registry._apply(empty, operation)
        self.assertEqual(registry._apply(enrolled, operation), enrolled)

        rebound = installation(ha_user_id="different-ha-user")
        with self.assertRaisesRegex(registry.RegistryError, "already bound"):
            registry._apply(
                enrolled, {"action": "enroll", "installation": rebound}
            )

        revoked = registry._apply(
            enrolled, {"action": "revoke", "installation_id": INSTALLATION_ID}
        )
        self.assertEqual(revoked["installations"][0]["status"], "revoked")
        self.assertEqual(
            registry._apply(
                revoked, {"action": "revoke", "installation_id": INSTALLATION_ID}
            ),
            revoked,
        )
        with self.assertRaisesRegex(registry.RegistryError, "already bound"):
            registry._apply(revoked, operation)

    def test_duplicate_json_keys_are_rejected(self) -> None:
        with self.assertRaisesRegex(registry.RegistryError, "duplicate JSON key"):
            registry._load_json(b'{"action":"initialize","action":"enroll"}', "test")

    def test_validate_action_is_content_free_and_exact(self) -> None:
        operation = registry._load_json(b'{"action":"validate"}', "test")
        registry._exact_keys(operation, {"action"}, "validate operation")
        with self.assertRaises(registry.RegistryError):
            registry._exact_keys(
                {"action": "validate", "installation_id": INSTALLATION_ID},
                {"action"},
                "validate operation",
            )

    @unittest.skipUnless(os.name == "posix", "POSIX mode and fsync contract")
    def test_atomic_write_is_root_only_and_canonical(self) -> None:
        if os.geteuid() != 0:
            self.skipTest("root ownership contract")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "native_installations.json"
            value = {"schema_version": 1, "installations": [installation()]}
            registry._write_atomic(path, value)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(path.stat().st_uid, 0)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), value)
            self.assertEqual(list(root.glob(".*.tmp.*")), [])

    def test_cli_source_avoids_identity_values_in_arguments_and_logs(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn("sys.stdin.buffer.read", source)
        self.assertIn("os.O_EXCL", source)
        self.assertIn("os.replace", source)
        self.assertIn("fcntl.LOCK_EX | fcntl.LOCK_NB", source)
        self.assertNotIn("print(registry", source)
        self.assertNotIn("print(operation", source)


if __name__ == "__main__":
    unittest.main()
