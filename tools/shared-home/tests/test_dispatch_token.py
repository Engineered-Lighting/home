import base64
from dataclasses import FrozenInstanceError
import hashlib
import hmac
import json
import unittest

from home_shared.dispatch_token import DispatchTokenAuthority, DispatchTokenError, MAX_BODY_BYTES


class DispatchTokenTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.key = bytes(range(32))
        self.body = b'{"model":"qwen3-vl-30b","messages":[]}'
        self.digest = hashlib.sha256(self.body).hexdigest()
        self.authority = self.make()

    def make(self, **kwargs):
        return DispatchTokenAuthority(kwargs.get("manifest", "a" * 64), kwargs.get("epoch", "epoch-1"),
                                      kwargs.get("key", self.key), max_deadline_seconds=kwargs.get("max", 60),
                                      clock=kwargs.get("clock", lambda: self.now))

    def issue(self, **kwargs):
        return self.authority.issue(kwargs.get("ledger", "victoria:ledger-1"), "b" * 64,
                                    kwargs.get("digest", self.digest), kwargs.get("deadline", 1030),
                                    purpose=kwargs.get("purpose", "execute"),
                                    input_bound_tokens=kwargs.get("bound", 512))

    def sign(self, raw):
        encoded = base64.urlsafe_b64encode(raw).rstrip(b"=")
        signature = hmac.digest(self.key, b"home-shared-dispatch-v2\x00" + encoded, "sha256")
        return (encoded + b"." + base64.urlsafe_b64encode(signature).rstrip(b"=")).decode()

    def payload(self, token):
        encoded = token.split(".")[0]
        return json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))

    def test_roundtrip_exact_binding_immutable_and_replay_requires_ledger(self):
        token = self.issue()
        binding = self.authority.verify(token, self.body)
        self.assertEqual(binding.manifest_digest, "a" * 64)
        self.assertEqual(binding.engine_epoch, "epoch-1")
        self.assertEqual(binding.ledger_id, "victoria:ledger-1")
        self.assertEqual(binding.request_digest, "b" * 64)
        self.assertEqual(binding.upstream_body_sha256, self.digest)
        self.assertEqual(binding.request_deadline, 1030.0)
        self.assertEqual(binding.purpose, "execute")
        self.assertEqual(binding.input_bound_tokens, 512)
        self.assertEqual(self.payload(token)["version"], 2)
        self.assertEqual(self.authority.verify(token, self.body), binding)
        with self.assertRaises(FrozenInstanceError):
            binding.ledger_id = "echo:ledger-1"

    def test_payload_tamper_site_ledger_request_identity_and_epoch(self):
        token = self.issue()
        original = self.payload(token)
        for field, value in (("ledger_id", "echo:ledger-1"), ("request_digest", "e" * 64),
                             ("engine_epoch", "epoch-2"), ("manifest_digest", "f" * 64),
                             ("purpose", "prepare"), ("input_bound_tokens", 513)):
            with self.subTest(field=field):
                changed = {**original, field: value}
                payload = base64.urlsafe_b64encode(json.dumps(changed).encode()).rstrip(b"=").decode()
                with self.assertRaises(DispatchTokenError):
                    self.authority.verify(payload + "." + token.split(".")[1], self.body)

    def test_wrong_key_manifest_and_epoch_reject_even_valid_original_token(self):
        token = self.issue()
        for authority in (self.make(key=b"x" * 32), self.make(manifest="f" * 64), self.make(epoch="epoch-2")):
            with self.assertRaises(DispatchTokenError):
                authority.verify(token, self.body)

    def test_exact_body_digest_not_semantic_json(self):
        token = self.issue()
        for body in (self.body + b" ", b"{}", bytearray(self.body), "not bytes", b""):
            with self.assertRaises(DispatchTokenError):
                self.authority.verify(token, body)

    def test_expiry_and_maximum_lifetime(self):
        token = self.issue()
        self.now = 1030
        with self.assertRaises(DispatchTokenError):
            self.authority.verify(token, self.body)
        self.now = 960
        with self.assertRaises(DispatchTokenError):
            self.authority.verify(token, self.body)
        self.now = 1000
        for deadline in (1000, 999, 1060.001, float("nan"), float("inf"), True, "1030", 10**1000):
            with self.subTest(deadline=str(deadline)[:20]), self.assertRaises(DispatchTokenError):
                self.issue(deadline=deadline)
        self.assertEqual(self.authority.verify(self.issue(deadline=1060), self.body).request_deadline, 1060.0)

    def test_expiry_during_verification_and_clock_reversal(self):
        token = self.issue()
        for times in ((1000, 1030), (1000, 999), (1000, float("nan"))):
            ticks = iter(times)
            authority = self.make(clock=lambda: next(ticks))
            with self.assertRaises(DispatchTokenError):
                authority.verify(token, self.body)

    def test_configuration_and_identifier_bounds(self):
        for kwargs in ({"key": b"short"}, {"key": "x" * 32}, {"max": True}, {"max": 601},
                       {"max": 0}, {"max": float("nan")}, {"epoch": "bad\n"}, {"manifest": "z" * 64}):
            with self.assertRaises(DispatchTokenError):
                self.make(**kwargs)
        for ledger in ("", "x" * 193, "hello world", None, "victoria\n"):
            with self.assertRaises(DispatchTokenError):
                self.issue(ledger=ledger)

    def test_malformed_and_oversized_tokens(self):
        token = self.issue()
        for bad in (None, b"token", "", "x" * 2049, token + "=", token + ".x", "é." + "a" * 43,
                    "." + "a" * 43, token[:-1] + "!", "AAAA." + "A" * 43):
            with self.subTest(token=str(bad)[:20]), self.assertRaises(DispatchTokenError):
                self.authority.verify(bad, self.body)
        with self.assertRaises(DispatchTokenError):
            self.authority.verify(token, b"x" * (MAX_BODY_BYTES + 1))

    def test_maximum_body_and_identifier_boundaries(self):
        body = b"x" * MAX_BODY_BYTES
        digest = hashlib.sha256(body).hexdigest()
        token = self.issue(ledger="x" * 192, digest=digest)
        self.assertEqual(self.authority.verify(token, body).upstream_body_sha256, digest)

    def test_signed_noncanonical_or_wrong_schema_rejected(self):
        original = self.payload(self.issue())
        malformed = [json.dumps(original).encode()]
        for change in ({"version": True}, {"version": 1}, {"site_id": "echo"},
                       {"request_deadline": 1030}, {"ledger_id": 5}, {"request_deadline": float("nan")}):
            malformed.append(json.dumps({**original, **change}, sort_keys=True, separators=(",", ":")).encode())
        malformed.extend((b"[]", b"null", b"{", b'{}', b'{"version":1,"version":1}', b'"text"'))
        canonical = json.dumps(original, sort_keys=True, separators=(",", ":")).encode()
        malformed.append(b'{"version":2,' + canonical[1:])
        for raw in malformed:
            with self.subTest(raw=raw[:80]), self.assertRaises(DispatchTokenError):
                self.authority.verify(self.sign(raw), self.body)

    def test_key_not_present_in_representation_or_errors(self):
        self.assertNotIn(repr(self.key), repr(self.authority))
        with self.assertRaises(DispatchTokenError) as caught:
            self.authority.verify("secret-token", self.body)
        self.assertNotIn("secret-token", str(caught.exception))
        self.assertNotIn(repr(self.key), str(caught.exception))

    def test_prepare_roundtrip_has_no_execution_bound(self):
        token = self.issue(purpose="prepare", bound=None)
        binding = self.authority.verify(token, self.body)
        self.assertEqual(binding.purpose, "prepare")
        self.assertIsNone(binding.input_bound_tokens)
        original = self.payload(token)
        changed = {**original, "purpose": "execute", "input_bound_tokens": 512}
        encoded = base64.urlsafe_b64encode(json.dumps(changed, sort_keys=True, separators=(",", ":")).encode()).rstrip(b"=").decode()
        with self.assertRaises(DispatchTokenError):
            self.authority.verify(encoded + "." + token.split(".")[1], self.body)

    def test_execute_bound_required_and_purpose_type_enforced(self):
        with self.assertRaises(DispatchTokenError):
            self.authority.issue("ledger-1", "b" * 64, self.digest, 1030)
        for bound in (None, 0, -1, True, 1.0, "512", 1_000_001):
            with self.subTest(bound=bound), self.assertRaises(DispatchTokenError):
                self.issue(bound=bound)
        for bound in (1, 1_000_000):
            binding = self.authority.verify(self.issue(bound=bound), self.body)
            self.assertEqual(binding.input_bound_tokens, bound)
        for purpose in ("", "EXECUTE", "other", None, True, ["prepare"]):
            with self.subTest(purpose=purpose), self.assertRaises(DispatchTokenError):
                self.issue(purpose=purpose)

    def test_prepare_rejects_every_nonnull_bound_and_signed_invalid_combinations(self):
        original = self.payload(self.issue())
        for bound in (0, 1, True, False, "", [], {}):
            with self.subTest(bound=bound), self.assertRaises(DispatchTokenError):
                self.issue(purpose="prepare", bound=bound)
        for changes in ({"purpose": "prepare", "input_bound_tokens": 512},
                        {"purpose": "execute", "input_bound_tokens": None},
                        {"purpose": "execute", "input_bound_tokens": True},
                        {"purpose": "other", "input_bound_tokens": None}):
            raw = json.dumps({**original, **changes}, sort_keys=True, separators=(",", ":")).encode()
            with self.assertRaises(DispatchTokenError):
                self.authority.verify(self.sign(raw), self.body)
        for missing in ("purpose", "input_bound_tokens"):
            incomplete = dict(original)
            del incomplete[missing]
            raw = json.dumps(incomplete, sort_keys=True, separators=(",", ":")).encode()
            with self.assertRaises(DispatchTokenError):
                self.authority.verify(self.sign(raw), self.body)

    def test_v1_domain_is_not_accepted(self):
        original = self.payload(self.issue())
        original["version"] = 1
        del original["purpose"]
        del original["input_bound_tokens"]
        encoded = base64.urlsafe_b64encode(json.dumps(original, sort_keys=True, separators=(",", ":")).encode()).rstrip(b"=")
        signature = hmac.digest(self.key, b"home-shared-dispatch-v1\x00" + encoded, "sha256")
        token = (encoded + b"." + base64.urlsafe_b64encode(signature).rstrip(b"=")).decode()
        with self.assertRaises(DispatchTokenError):
            self.authority.verify(token, self.body)


if __name__ == "__main__":
    unittest.main()
