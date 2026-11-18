"""Child deadline gates with mocked work only: no torch, GPU or model imports."""
from contextlib import nullcontext
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from den_models.backend import VjepaBackend
from den_models.contracts import ContractError


class BackendDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.backend = VjepaBackend({}, {}, clock=lambda: self.now)
        self.backend.load = Mock()
        self.backend.preprocess = Mock(return_value=(object(), object()))
        self.backend.native_gate = Mock()
        self.backend.torch = Mock(inference_mode=nullcontext)
        self.backend.model = Mock()
        self.backend.model.choose.return_value = (0, [1., 0., 0., 0., 0.])
        self.backend.model.chat.return_value = "local test output"
        self.request = {"expires_at": 102.0, "mode": "choose"}

    def expire(self, *args, **kwargs):
        self.now = self.request["expires_at"]

    def assert_no_model_call(self):
        self.backend.model.choose.assert_not_called()
        self.backend.model.chat.assert_not_called()

    def test_expired_or_unknown_deadline_refuses_before_load(self):
        for expiry in (100, 99, None, True, float("nan"), float("inf")):
            with self.subTest(expiry=expiry), self.assertRaisesRegex(ContractError, "clip_expired_before_native_work"):
                self.backend.infer(dict(self.request, expires_at=expiry))
        self.backend.load.assert_not_called()
        self.backend.preprocess.assert_not_called()
        self.assert_no_model_call()

    def test_expiry_during_load_refuses_before_preprocess(self):
        self.backend.load.side_effect = self.expire
        with self.assertRaises(ContractError):
            self.backend.infer(self.request)
        self.backend.preprocess.assert_not_called()
        self.backend.native_gate.assert_not_called()
        self.assert_no_model_call()

    def test_expiry_during_preprocess_refuses_before_resource_gate(self):
        self.backend.preprocess.side_effect = self.expire
        with self.assertRaises(ContractError):
            self.backend.infer(self.request)
        self.backend.native_gate.assert_not_called()
        self.assert_no_model_call()

    def test_expiry_during_resource_gate_refuses_choose_and_chat(self):
        for mode in ("choose", "chat"):
            self.now = 100
            self.backend.native_gate.side_effect = self.expire
            with self.subTest(mode=mode), self.assertRaises(ContractError):
                self.backend.infer(dict(self.request, mode=mode))
            self.assert_no_model_call()

    def test_expiry_while_entering_inference_context_refuses_native_call(self):
        owner = self
        class Context:
            def __enter__(self):
                owner.expire()
            def __exit__(self, *args):
                return False
        self.backend.torch.inference_mode = Context
        with self.assertRaises(ContractError):
            self.backend.infer(self.request)
        self.assert_no_model_call()

    def test_fresh_choose_and_chat_keep_existing_bounded_contract(self):
        self.assertEqual(self.backend.infer(self.request)["intent"], "continue")
        output = self.backend.infer(dict(self.request, mode="chat"))
        self.assertEqual(output["intent"], "unclear")
        self.backend.model.chat.assert_called_once_with(self.backend.preprocess.return_value,
                                                        unittest.mock.ANY, max_new_tokens=64)


if __name__ == "__main__":
    unittest.main()
