"""Child deadline gates with mocked work only: no torch, GPU or model imports."""
from contextlib import nullcontext
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from den_models.backend import NONFATAL_FAILURES, VjepaBackend
from den_models.contracts import ContractError
from den_models.resources import DependencyFlap


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

    def test_control_flap_at_native_gate_is_one_fixed_nonfatal_refusal(self):
        backend = VjepaBackend({key: None for key in ("control_state_file", "execution_lease_file", "queue_receipt_file",
                                                     "queue_receipt_gid", "qwen_observer_config_sha256",
                                                     "qwen_binding_policy_sha256")}, {"execution": {}})
        reader = Mock()
        reader.return_value.read.side_effect = ContractError("control_admission_blocked")
        with patch("den_models.resources.ControlState", reader), \
                self.assertRaisesRegex(ContractError, r"\Acontrol_state_unavailable_before_native_work\Z"):
            backend.native_gate(cold=False)
        self.assertEqual(NONFATAL_FAILURES["control_state_unavailable_before_native_work"], "invalidated")

    def test_warmup_rides_out_a_pure_dependency_lapse_but_live_work_does_not(self):
        gib = 1024**3
        config = {key: None for key in ("control_state_file", "execution_lease_file", "queue_receipt_file",
                                        "queue_receipt_gid", "qwen_observer_config_sha256",
                                        "qwen_binding_policy_sha256")}
        config["gpu_selector"] = "0"
        backend = VjepaBackend(config, {"execution": {
            "gpu_reservation_bytes": 22 * gib, "minimum_free_bytes": 16 * gib,
            "cold_host_reservation_bytes": 24 * gib, "host_minimum_available_bytes": 8 * gib}})
        verified = {"qwen_busy": False, "unallocated_reserve_bytes": 0, "dependencies": []}
        reader = Mock()
        refused = r"\Acontrol_state_unavailable_before_native_work\Z"
        with patch("den_models.resources.ControlState", reader), \
                patch("den_models.resources.NvidiaProbe") as gpu, patch("den_models.resources.HostProbe") as host:
            gpu.return_value.free_bytes.return_value = 50 * gib
            host.return_value.available_bytes.return_value = 40 * gib
            reader.return_value.read.side_effect = DependencyFlap("control_state_unavailable", verified)
            backend.native_gate(cold=True, warmup=True)   # The cold load before warmup.
            backend.native_gate(cold=False, warmup=True)  # The synthetic warmup inference.
            for cold in (True, False):
                with self.subTest(live_cold=cold), self.assertRaisesRegex(ContractError, refused):
                    backend.native_gate(cold=cold)
            # Qwen and headroom still gate warmup through the verified lapse state.
            reader.return_value.read.side_effect = DependencyFlap("control_state_unavailable", dict(verified, qwen_busy=True))
            with self.assertRaisesRegex(ContractError, "qwen_busy_before_native_work"):
                backend.native_gate(cold=True, warmup=True)
            reader.return_value.read.side_effect = DependencyFlap("control_state_unavailable", verified)
            gpu.return_value.free_bytes.return_value = 38 * gib - 1
            with self.assertRaisesRegex(ContractError, "gpu_headroom_changed_before_native_work"):
                backend.native_gate(cold=True, warmup=True)
            gpu.return_value.free_bytes.return_value = 50 * gib
            # Ops backoff, Qwen evidence, attestation, or a lapse without verified state stop warmup as before.
            for failure in (ContractError("control_state_unavailable"), DependencyFlap("control_state_unavailable")):
                reader.return_value.read.side_effect = failure
                with self.subTest(failure=type(failure).__name__), self.assertRaisesRegex(ContractError, refused):
                    backend.native_gate(cold=True, warmup=True)

    def test_only_the_warmup_path_asks_for_the_binding_free_gate(self):
        backend = VjepaBackend({"io_device": "8:0", "snapshot_root": "s", "receipt_root": "r"}, {"execution": {}})
        backend.native_gate = Mock(side_effect=ContractError("stopped_before_native_load"))
        with patch.multiple("den_models.backend", cgroup_limits=Mock(), read_receipt=Mock(return_value=({}, "d")),
                            read_runtime_manifest=Mock(), check_runtime=Mock(),
                            verify_snapshot=Mock(return_value=(Path("s"), "d"))):
            for warmup, call in ((True, lambda: backend.warmup()), (False, lambda: backend.load())):
                backend.native_gate.reset_mock()
                with self.subTest(warmup=warmup), self.assertRaisesRegex(ContractError, "stopped_before_native_load"):
                    call()
                backend.native_gate.assert_called_once_with(cold=True, warmup=warmup)
        self.backend.infer(self.request)
        self.backend.native_gate.assert_called_once_with(cold=False)

    def test_fresh_choose_and_chat_keep_existing_bounded_contract(self):
        self.assertEqual(self.backend.infer(self.request)["intent"], "continue")
        output = self.backend.infer(dict(self.request, mode="chat"))
        self.assertEqual(output["intent"], "unclear")
        self.backend.model.chat.assert_called_once_with(self.backend.preprocess.return_value,
                                                        unittest.mock.ANY, max_new_tokens=64)


if __name__ == "__main__":
    unittest.main()
