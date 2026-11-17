"""Real optional subprocess fixtures, without model weights, cameras or household frames."""
import io
import json
import os
import queue
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

import held_async as A

CHILD = r'''
import json, struct, sys, time
def read(n):
    value = b''
    while len(value) < n:
        part = sys.stdin.buffer.read(n-len(value))
        if not part: raise EOFError
        value += part
    return value
time.sleep(float(sys.argv[2]))
print(json.dumps({'ready': True, 'device': 'cpu'}), flush=True)
while True:
    try:
        header = json.loads(read(struct.unpack('>I', read(4))[0]))
        read(struct.unpack('>I', read(4))[0])
    except EOFError: break
    time.sleep(float(sys.argv[1]))
    if sys.argv[3] == 'error':
        print(json.dumps({'id': header['id'], 'frame_key': header['frame_key'], 'error': 'PermissionError'}), flush=True)
        continue
    key = header['frame_key'] if sys.argv[3] != 'wrong' else ['wrong-frame']
    print(json.dumps({'id': header['id'], 'frame_key': key,
                      'inference_ms': float(sys.argv[1]) * 1000,
                      'person_masks': [{'score': .9, 'polygon': [[10, 20], [20, 20], [20, 30]]}],
                      'held_masks': [{'label': 'book', 'score': .8, 'polygon': [[1, 2], [3, 2], [3, 4]]}]}), flush=True)
'''


def command(delay=.02, load=0, wrong=False, error=False):
    mode = "error" if error else "wrong" if wrong else "ok"
    return [sys.executable, "-u", "-c", CHILD, str(delay), str(load), mode]


def wait_until(check, timeout=3):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if check():
            return
        time.sleep(.005)
    raise AssertionError("condition deadline")


class Process(unittest.TestCase):
    def make(self, **kwargs):
        masks = A.Masks(".", ".", **kwargs)
        self.addCleanup(masks.close)
        return masks

    def test_exact_frame_result_and_old_key_rejected(self):
        masks = self.make(command=command(), timeout=1)
        masks.submit(1, ["epoch", 2], np.zeros((360, 640, 3), np.uint8), {})
        got = masks.wait(1, ["epoch", 2])
        self.assertEqual(got["segmentation_status"], "ok")
        self.assertEqual(got["held_masks"][0]["label"], "book")
        self.assertEqual(got["timings_ms"]["segment_inference"], 20)
        self.assertGreaterEqual(got["timings_ms"]["segment_queue"], 0)
        self.assertEqual(masks.health()["held_device"], "cpu")
        self.assertNotEqual(masks.wait(2, ["epoch", 2])["segmentation_status"], "ok")
        self.assertNotEqual(masks.wait(1, ["new-epoch", 2])["segmentation_status"], "ok")
        masks.close()
        self.assertFalse(masks.thread.is_alive())

    def test_model_error_is_counted_and_next_frame_can_retry(self):
        masks = self.make(command=command(error=True), timeout=1)
        image = np.zeros((360, 640, 3), np.uint8)
        for number in (1, 2):
            masks.submit(number, [number], image, {})
            result = masks.wait(number, [number])
            self.assertEqual(result["segmentation_status"], "error")
            self.assertEqual(result["segmentation_error"], "PermissionError")
            self.assertNotIn("held_masks", result)
            self.assertEqual(masks.health()["held_failures"], number)
            self.assertEqual(masks.health()["held_status"], "ready")
            self.assertIsNone(masks.process.poll())

    def test_live_first_run_refusal_reaches_health(self):
        why = "the held-object pass failed its first run (PermissionError)"
        script = "import json; print(json.dumps({'refused': %r}), flush=True)" % why
        masks = self.make(command=[sys.executable, "-u", "-c", script])
        wait_until(lambda: masks.status == "error")
        self.assertFalse(masks.health()["held"])
        self.assertEqual(masks.health()["held_why"], why)

    def test_one_inflight_and_latest_only_pending(self):
        masks = self.make(command=command(.12), timeout=1)
        image = np.zeros((360, 640, 3), np.uint8)
        masks.submit(1, [1], image, {})
        wait_until(lambda: masks.inflight is not None)
        masks.submit(2, [2], image, {})
        masks.submit(3, [3], image, {})
        self.assertEqual(masks.pending[1], 3)
        self.assertEqual(masks.wait(3, [3])["segmentation_status"], "ok")
        self.assertNotEqual(masks.wait(2, [2])["segmentation_status"], "ok")
        self.assertLessEqual(len(masks.results), 2)

    def test_timeout_kills_hung_child_and_rejects_late_result(self):
        masks = self.make(command=command(.5), timeout=.12)
        wait_until(lambda: masks.status == "ready")
        child = masks.process
        masks.submit(1, [1], np.zeros((360, 640, 3), np.uint8), {})
        self.assertEqual(masks.wait(1, [1])["segmentation_status"], "timeout")
        wait_until(lambda: child.poll() is not None)
        self.assertNotEqual(masks.wait(1, [1])["segmentation_status"], "ok")
        self.assertEqual(masks.results, {})

    def test_startup_timeout_and_wrong_frame_result_fail_optional_only(self):
        for kwargs in ({"command": command(load=.5), "load_timeout": .08},
                       {"command": command(wrong=True), "timeout": 1}):
            masks = self.make(**kwargs)
            masks.submit(1, [1], np.zeros((360, 640, 3), np.uint8), {})
            wait_until(lambda: masks.status in ("timeout", "error"))
            self.assertNotEqual(masks.wait(1, [1])["segmentation_status"], "ok")

    def test_short_acquisition_budget_and_expired_result_are_rejected(self):
        masks = self.make(command=command(.18), timeout=1)
        wait_until(lambda: masks.status == "ready")
        masks.submit(1, [1], np.zeros((360, 640, 3), np.uint8), {})
        began = time.monotonic()
        self.assertEqual(masks.wait(1, [1], max_wait_s=.03)["segmentation_status"], "timeout")
        self.assertLess(time.monotonic() - began, .12)
        self.assertEqual(masks.wait(1, [1])["segmentation_status"], "ok")
        with masks.condition:
            deadline, result = next(iter(masks.results.values()))
            masks.results[next(iter(masks.results))] = (time.monotonic() - 1, result)
        self.assertNotEqual(masks.wait(1, [1])["segmentation_status"], "ok")


class Device(unittest.TestCase):
    def test_default_cpu_skips_cuda_guard_and_auto_uses_existing_guard(self):
        model = object()
        detect = SimpleNamespace(gpu_usable=Mock(return_value=(False, "not free")))
        held = SimpleNamespace(load=Mock(return_value=(model, None)))
        cleanup = Mock()
        precision = Mock()
        self.assertEqual(A.load_segmenter("models", "state", "cpu", detect, held, release_cuda=cleanup,
                                          configure_cuda=precision),
                         (model, "cpu", None, None))
        detect.gpu_usable.assert_not_called()
        held.load.assert_called_once_with("models", "cpu", state="state")
        held.load.reset_mock()
        self.assertEqual(A.load_segmenter("models", "state", "auto", detect, held, release_cuda=cleanup,
                                          configure_cuda=precision),
                         (model, "cpu", None, "gpu_unavailable"))
        detect.gpu_usable.assert_called_once()
        held.load.assert_called_once_with("models", "cpu", state="state")
        cleanup.assert_not_called()
        precision.assert_not_called()

    def test_cuda_precision_is_full_float32_with_tf32_disabled(self):
        torch = SimpleNamespace(set_float32_matmul_precision=Mock(), backends=SimpleNamespace(
            cuda=SimpleNamespace(matmul=SimpleNamespace(allow_tf32=True)), cudnn=SimpleNamespace(allow_tf32=True)))
        A.configure_cuda_precision(torch)
        torch.set_float32_matmul_precision.assert_called_once_with("highest")
        self.assertIs(torch.backends.cuda.matmul.allow_tf32, False)
        self.assertIs(torch.backends.cudnn.allow_tf32, False)

    def test_cuda_success_and_failed_warmup_cleanup_before_fresh_cpu_load(self):
        detect = SimpleNamespace(gpu_usable=Mock(return_value=(True, None)))
        model = object()
        held = SimpleNamespace(load=Mock(return_value=(model, None)))
        cleanup = Mock()
        precision = Mock()
        self.assertEqual(A.load_segmenter("models", "state", "auto", detect, held, release_cuda=cleanup,
                                          configure_cuda=precision),
                         (model, "cuda:0", None, None))
        held.load.assert_called_once_with("models", "cuda:0", state="state")
        cleanup.assert_not_called()
        precision.assert_called_once_with()
        for failed in [(None, "first warmup failed"), RuntimeError("private error text")]:
            with self.subTest(failure=type(failed).__name__):
                order = []
                def load(_models, device, **kwargs):
                    order.append(device)
                    if device == "cuda:0":
                        if isinstance(failed, Exception):
                            raise failed
                        return failed
                    return model, None
                cleanup = Mock(side_effect=lambda: order.append("cleanup"))
                result = A.load_segmenter("models", "state", "auto", detect,
                                          SimpleNamespace(load=load), release_cuda=cleanup,
                                          configure_cuda=lambda: order.append("precision"))
                self.assertEqual(result, (model, "cpu", None, "gpu_load_failed"))
                self.assertEqual(order, ["precision", "cuda:0", "cleanup", "cpu"])
                self.assertNotIn("private error text", str(result))

    def test_failed_cpu_load_still_refuses_and_cleanup_failure_keeps_fallback(self):
        detect = SimpleNamespace(gpu_usable=lambda: (True, None))
        held = SimpleNamespace(load=Mock(side_effect=[(None, "warm failure"), (None, "cpu refused")]))
        result = A.load_segmenter("models", "state", "auto", detect, held,
                                  release_cuda=Mock(side_effect=RuntimeError("cleanup failed")), configure_cuda=Mock())
        self.assertEqual(result, (None, None, "cpu refused", "gpu_load_failed"))

    def test_device_environment_and_truthful_child_health(self):
        popen = subprocess.Popen
        for requested, actual in [("cpu", "cpu"), ("auto", "cuda:0"), ("invalid", "cpu")]:
            script = CHILD.replace("'device': 'cpu'", "'device': %r, 'fallback': 'gpu_unavailable'" % actual)
            child = [sys.executable, "-u", "-c", script, ".01", "0", "ok"]
            with self.subTest(requested=requested), patch.dict(os.environ, {"DEN_MASK_DEVICE": requested, "CUDA_VISIBLE_DEVICES": "2"}), \
                    patch.object(A.subprocess, "Popen", wraps=popen) as spawned:
                masks = A.Masks(".", ".", command=child)
                try:
                    wait_until(lambda: masks.status == "ready")
                    self.assertEqual(masks.health()["held_device"], actual)
                    self.assertEqual(masks.health()["held_why"], "gpu_unavailable")
                    visibility = spawned.call_args.kwargs["env"]["CUDA_VISIBLE_DEVICES"]
                    self.assertEqual(visibility, "2" if requested == "auto" else "")
                finally:
                    masks.close()
        child = [sys.executable, "-u", "-c", "import json,time;print(json.dumps({'ready':True}),flush=True);time.sleep(2)"]
        masks = A.Masks(".", ".", command=child)
        try:
            wait_until(lambda: masks.status == "ready")
            self.assertIsNone(masks.health()["held_device"])
        finally:
            masks.close()


class Lifetime(unittest.TestCase):
    def test_linux_parent_race_is_checked_after_arming_kernel_signal(self):
        import signal
        prctl = Mock(return_value=0)
        with patch.object(A.sys, "platform", "linux"), patch.dict(os.environ, {A.PARENT_PID_ENV: "123"}), \
                patch("ctypes.CDLL", return_value=SimpleNamespace(prctl=prctl)), \
                patch.object(signal, "SIGKILL", 9, create=True), \
                patch.object(A.os, "getppid", side_effect=[123, 124]), patch.object(A.os, "kill") as kill:
            A.arm_parent_lifetime()
        prctl.assert_called_once_with(1, 9, 0, 0, 0)
        kill.assert_called_once_with(os.getpid(), 9)

    @unittest.skipUnless(sys.platform == "linux", "Linux production kernel lifetime fence")
    def test_parent_kill_terminates_native_wait_and_startup_race_child(self):
        child_code = r'''
import json, sys, time
sys.path.insert(0, sys.argv[1])
import held_async
time.sleep(float(sys.argv[2]))
held_async.arm_parent_lifetime()
print(json.dumps({'ready': True}), flush=True)
time.sleep(60)
'''
        parent_code = r'''
import sys, time
sys.path.insert(0, sys.argv[1])
import held_async
command = [sys.executable, '-u', '-c', sys.argv[2], sys.argv[1], sys.argv[3]]
masks = held_async.Masks('.', '.', command=command, load_timeout=5)
until = time.monotonic() + 5
while time.monotonic() < until:
    if sys.argv[3] == '0' and masks.status == 'ready': break
    if sys.argv[3] != '0' and masks.process is not None: break
    if masks.status in ('error', 'timeout'): raise RuntimeError('child startup failed')
    time.sleep(.005)
else: raise RuntimeError('parent startup deadline')
print(masks.process.pid, flush=True)
time.sleep(60)
'''
        for startup_delay in ("0", ".3"):
            with self.subTest(startup_delay=startup_delay):
                parent = subprocess.Popen([sys.executable, "-u", "-c", parent_code, str(Path(A.__file__).parent),
                                           child_code, startup_delay], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                child_pid = None
                lines = queue.Queue()
                threading.Thread(target=lambda: lines.put(parent.stdout.readline()), daemon=True).start()
                try:
                    line = lines.get(timeout=8)
                    if not line.strip().isdigit():
                        parent.kill()
                        _, errors = parent.communicate(timeout=2)
                        self.fail("lifetime fixture failed: " + errors.decode("utf-8", "replace")[-500:])
                    child_pid = int(line)
                    def dead():
                        try:
                            state = Path("/proc/%d/stat" % child_pid).read_text().rsplit(") ", 1)[1][0]
                            return state == "Z"
                        except FileNotFoundError:
                            return True

                    parent.kill()
                    parent.wait(timeout=2)
                    wait_until(dead, timeout=3)
                finally:
                    if parent.poll() is None:
                        parent.kill()
                    parent.wait(timeout=2)
                    if child_pid is not None:
                        try:
                            os.kill(child_pid, 9)
                        except ProcessLookupError:
                            pass
                    parent.stdout.close()
                    parent.stderr.close()


class Dispatch(unittest.TestCase):
    def test_exact_mask_timings_and_body_wait_are_internal_reply_metadata(self):
        replies = queue.Queue()
        entered, release = threading.Event(), threading.Event()
        class Mask:
            def wait(self, worker_id, frame_key, **kwargs):
                entered.set()
                release.wait(1)
                return {"frame_key": frame_key, "segmentation_status": "ok",
                        "timings_ms": {"segment_queue": 15, "segment_inference": 80, "unknown": 99}}
        class Channel:
            def handle(self, header, kept):
                return {"body_id": header["id"], "frame": {"unchanged": True}, "compute_s": .005}
        bodies = A.Bodies(Mask(), replies.put, factory=Channel)
        self.addCleanup(bodies.close)
        bodies.submit({"kind": "body", "id": 1, "remaining_s": 1}, (1, b"bundle", ["frame", 1]))
        self.assertTrue(entered.wait(1))
        self.assertTrue(replies.empty())
        release.set()
        reply = replies.get(timeout=1)
        self.assertEqual(reply["frame"], {"unchanged": True})
        self.assertEqual(reply["timings_ms"]["segment_inference"], 80)
        self.assertEqual(reply["timings_ms"]["segment_queue"], 15)
        self.assertEqual(reply["timings_ms"]["body_compute"], 5)
        self.assertGreaterEqual(reply["timings_ms"]["mask_wait"], 0)
        self.assertLessEqual(reply["timings_ms"]["mask_wait"], 2000)
        self.assertNotIn("unknown", reply["timings_ms"])

    def test_default_channel_receives_live_background_state(self):
        replies, states = queue.Queue(), []
        class Channel:
            def __init__(self, state):
                states.append(state)

            def handle(self, header, kept):
                return {"body_ready": True}
        with patch("body.BodyChannel", Channel):
            bodies = A.Bodies(None, replies.put, state="private-live-state")
            self.addCleanup(bodies.close)
            bodies.submit({"kind": "setup_body", "setup": {}}, None)
            self.assertTrue(replies.get(timeout=1)["body_ready"])
        self.assertTrue(states)
        self.assertEqual(set(states), {"private-live-state"})

    def test_body_wait_reserves_compute_and_decrements_queued_lease(self):
        replies, budgets = queue.Queue(), []
        class Mask:
            def wait(self, worker_id, key, max_wait_s):
                budgets.append(max_wait_s)
                return {"frame_key": key, "segmentation_status": "pending"}
        class Channel:
            def handle(self, header, kept):
                return {"body_id": header["id"]}
        bodies = A.Bodies(Mask(), replies.put, factory=Channel)
        self.addCleanup(bodies.close)
        bodies.submit({"kind": "body", "id": 1, "remaining_s": .14}, (1, b"depth", [1]))
        self.assertEqual(replies.get(timeout=1)["body_id"], 1)
        self.assertLessEqual(budgets[0], .14 - A.BODY_RESERVE_S)
        self.assertGreaterEqual(budgets[0], 0)

    def test_new_setup_fences_old_body_and_resets_channel(self):
        replies, entered, release = queue.Queue(), threading.Event(), threading.Event()
        class Mask:
            def wait(self, worker_id, key, **kwargs):
                entered.set()
                release.wait(1)
                return {"frame_key": key, "segmentation_status": "ok", "person_masks": [], "held_masks": []}
        class Channel:
            def handle(self, header, kept):
                if header["kind"] == "setup_body":
                    return {"body_ready": True, "binding": header["setup"]["binding"]}
                return {"body_id": header["id"], "frame": "wrong if published after setup"}
        bodies = A.Bodies(Mask(), replies.put, factory=Channel)
        self.addCleanup(bodies.close)
        bodies.submit({"kind": "setup_body", "setup": {"binding": "one"}}, None)
        self.assertTrue(replies.get(timeout=1)["body_ready"])
        bodies.submit({"kind": "body", "id": 1, "frame_key": [1], "binding": "one"}, (1, b"depth", [1]))
        self.assertTrue(entered.wait(1))
        bodies.submit({"kind": "setup_body", "setup": {"binding": "two"}}, None)
        release.set()
        self.assertEqual(replies.get(timeout=1)["why"], "scope_changed")
        self.assertEqual(replies.get(timeout=1)["binding"], "two")

    def test_normal_worker_serves_next_detection_and_stills_while_body_waits_then_uses_exact_masks(self):
        import detect_worker as W
        image = np.zeros((360, 640, 3), np.uint8)
        masks = A.Masks(".", ".", command=command(.18), timeout=1)
        self.addCleanup(masks.close)
        wait_until(lambda: masks.status == "ready")
        replies, seen = queue.Queue(), []
        class Channel:
            def handle(self, header, kept):
                if header["kind"] == "setup_body":
                    return {"body_ready": True}
                seen.append(kept)
                return {"body_id": header["id"], "frame_key": kept[2], "frame": kept[3]}
        bodies = A.Bodies(masks, replies.put, factory=Channel)
        self.addCleanup(bodies.close)
        detector = SimpleNamespace(np=np, check=SimpleNamespace(read_bundle=lambda *a, **k: ({}, image.tobytes()[:345600])),
                                   decode=lambda *args: image, run=lambda *a: ({}, 0, .001),
                                   row_of=lambda *a, **k: {"detections": []},
                                   pose=SimpleNamespace(manifest={"model_version": "fixture"}))
        incoming = queue.Queue()
        def read_frame(_stream):
            return incoming.get(timeout=3)
        def frame(n):
            return ({"id": n, "frame_key": ["session", n], "rgb_seq": n, "t_host": n, "delay_s": 0}, b"bundle")
        def serve():
            W.serve_frames(detector, masks, None, SimpleNamespace(keep=lambda *args: None), bodies)
        with patch.object(W, "read_frame", read_frame), patch.object(W, "say", replies.put), \
                patch.object(W.stills, "handle", lambda *a: {"stills_id": 99, "images": []}):
            thread = threading.Thread(target=serve)
            thread.start()
            try:
                incoming.put(({"kind": "setup_body", "setup": {}}, None))
                self.assertTrue(replies.get(timeout=1)["body_ready"])
                incoming.put(frame(1))
                self.assertEqual(replies.get(timeout=1)["id"], 1)
                incoming.put(({"kind": "body", "id": 1, "frame_key": ["session", 1]}, None))
                incoming.put(frame(2))
                incoming.put(({"kind": "stills", "id": 99}, None))
                self.assertEqual(replies.get(timeout=1)["id"], 2)
                self.assertEqual(replies.get(timeout=1)["stills_id"], 99)
                body = replies.get(timeout=2)
                self.assertEqual(body["body_id"], 1)
                self.assertEqual(body["frame_key"], ["session", 1])
                self.assertEqual(body["frame"]["segmentation_status"], "ok")
                self.assertEqual(body["frame"]["held_masks"][0]["label"], "book")
                self.assertEqual(seen[0][:3], (1, b"bundle", ["session", 1]))
            finally:
                incoming.put(None)
                thread.join(timeout=2)
            self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
