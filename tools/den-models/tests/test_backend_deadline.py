"""Child deadline gates with mocked work only: no torch, GPU or model imports."""
from contextlib import ExitStack, nullcontext
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from den_models.backend import NONFATAL_FAILURES, WARMUP_QWEN_IDLE_WAIT_SECONDS, WARMUP_QWEN_POLL_SECONDS, VjepaBackend
from den_models.contracts import ContractError
from den_models.resources import DependencyFlap

GIB = 1024**3
GATE_CONFIG = dict({key: None for key in ("control_state_file", "execution_lease_file", "queue_receipt_file",
                                          "queue_receipt_gid", "qwen_observer_config_sha256",
                                          "qwen_binding_policy_sha256")}, gpu_selector="0")
GATE_PROFILE = {"execution": {"gpu_reservation_bytes": 22 * GIB, "minimum_free_bytes": 16 * GIB,
                              "cold_host_reservation_bytes": 24 * GIB, "host_minimum_available_bytes": 8 * GIB}}


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

    def gate_backend(self):
        """A backend whose Qwen-idle wait runs on the test clock: sleeping only advances it."""
        backend = VjepaBackend(GATE_CONFIG, GATE_PROFILE, clock=lambda: self.now)
        self.sleeps = []
        def sleep(seconds):
            self.sleeps.append(seconds)
            self.now += seconds
        backend.sleep = sleep
        return backend

    def test_warmup_rides_out_a_pure_dependency_lapse_but_live_work_does_not(self):
        gib = GIB
        backend = self.gate_backend()
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
            # Qwen (after its bounded idle wait) and headroom still gate warmup through the verified lapse state.
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

    def gate_reads(self, *states):
        """Patch the child's control reader to return or raise `states` in order; the probes report headroom."""
        reader = Mock()
        reader.return_value.read.side_effect = list(states)
        stack = ExitStack()
        stack.enter_context(patch("den_models.resources.ControlState", reader))
        gpu = stack.enter_context(patch("den_models.resources.NvidiaProbe"))
        host = stack.enter_context(patch("den_models.resources.HostProbe"))
        gpu.return_value.free_bytes.return_value = 50 * GIB
        host.return_value.available_bytes.return_value = 40 * GIB
        return stack, reader.return_value.read, gpu.return_value.free_bytes

    def test_warmup_waits_through_busy_qwen_and_proceeds_on_an_idle_instant(self):
        busy = {"qwen_busy": True, "unallocated_reserve_bytes": 0, "dependencies": []}
        idle = dict(busy, qwen_busy=False)
        backend = self.gate_backend()
        # Den's own Qwen asks keep it busy for 2 s, through a binding lapse, then an idle verified lapse state.
        reads = [busy] * 8 + [DependencyFlap("control_state_unavailable", busy),
                              DependencyFlap("control_state_unavailable", idle)]
        stack, read, free = self.gate_reads(*reads)
        with stack:
            backend.native_gate(cold=True, warmup=True)
        self.assertEqual(read.call_count, 10)
        self.assertEqual(self.sleeps, [WARMUP_QWEN_POLL_SECONDS] * 9)
        free.assert_called_once()
        # Every other check uses the state finally read: its reservation, a non-lapse failure, a lapse without state.
        for final, refused in ((dict(idle, unallocated_reserve_bytes=50 * GIB), "gpu_headroom_changed_before_native_work"),
                               (ContractError("control_state_unavailable"), "control_state_unavailable_before_native_work"),
                               (DependencyFlap("control_state_unavailable"), "control_state_unavailable_before_native_work")):
            stack, read, free = self.gate_reads(busy, busy, final)
            with self.subTest(refused=refused, final=type(final).__name__), stack, \
                    self.assertRaisesRegex(ContractError, r"\A%s\Z" % refused):
                backend.native_gate(cold=True, warmup=True)
            self.assertEqual(read.call_count, 3)

    def test_warmup_still_refuses_when_qwen_never_idles_within_the_wait(self):
        busy = {"qwen_busy": True, "unallocated_reserve_bytes": 0, "dependencies": []}
        backend = self.gate_backend()
        stack, read, free = self.gate_reads(*[busy] * 1000)
        with stack, self.assertRaisesRegex(ContractError, r"\Aqwen_busy_before_native_work\Z"):
            backend.native_gate(cold=True, warmup=True)
        self.assertEqual(self.now - 100, WARMUP_QWEN_IDLE_WAIT_SECONDS)
        self.assertEqual(self.sleeps, [WARMUP_QWEN_POLL_SECONDS] * 120)
        self.assertEqual(read.call_count, 121)
        free.assert_not_called()
        self.assertEqual(NONFATAL_FAILURES["qwen_busy_before_native_work"], "failed")

    def test_live_work_refuses_busy_qwen_at_once(self):
        busy = {"qwen_busy": True, "unallocated_reserve_bytes": 0, "dependencies": [{"track_id": "track-1"}]}
        backend = self.gate_backend()
        for cold in (True, False):
            stack, read, free = self.gate_reads(busy, dict(busy, qwen_busy=False))
            with self.subTest(cold=cold), stack, self.assertRaisesRegex(ContractError, r"\Aqwen_busy_before_native_work\Z"):
                backend.native_gate(cold=cold)
            self.assertEqual(read.call_count, 1)
        self.assertEqual((self.sleeps, self.now), ([], 100.0))

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
