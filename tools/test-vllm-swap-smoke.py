#!/usr/bin/env python3
"""Offline test for tools/vllm-swap-smoke.py against a stub OpenAI server.

    python tools/test-vllm-swap-smoke.py
"""
import importlib.util
import io
import json
import os
import sys
import threading
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("smoke", os.path.join(HERE, "vllm-swap-smoke.py"))
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)

GOOD_SCENE = {"people_visible": "none", "subject_posture": "unclear", "held_object": "none",
              "book_state": "not_visible", "gaze_target": "unclear", "typing_visible": "unclear",
              "screen_facing_subject": "not_visible", "activity_hypothesis": "unclear",
              "visible_basis": ["no_person_visible"], "change_vs_prior": "no_prior"}


class Stub(BaseHTTPRequestHandler):
    mode = "good"

    def log_message(self, *args):
        pass

    def reply(self, body, status=200):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.reply({"data": [{"id": "qwen3-vl-30b", "root": "Qwen/Qwen3.6-35B-A3B-FP8", "max_model_len": 32768},
                             {"id": "qwen3.6-35b-a3b"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if body.get("stream"):
            text = "<think>hm</think>ready" if self.mode == "think" else "ready"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for chunk in ({"choices": [{"delta": {"role": "assistant"}}]}, {"choices": [{"delta": {"content": text}}]},
                          {"choices": []}):
                self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")
            return
        content = body["messages"][-1]["content"]
        kinds = [part.get("type") for part in content] if isinstance(content, list) else []
        message, finish = {"role": "assistant", "content": ""}, "stop"
        if "video_url" in kinds:
            if self.mode == "novideo":
                return self.reply({"error": "At most 0 video(s) may be provided in one prompt."}, 400)
            message["content"] = "Right"
        elif body.get("response_format"):
            message["content"] = json.dumps(dict(GOOD_SCENE, held_object="spoon") if self.mode == "enum" else GOOD_SCENE)
        elif "image_url" in kinds:
            message["content"] = "Red"
        elif body.get("tools") and "lights" in content:
            message["tool_calls"] = [{"id": "c1", "type": "function",
                                      "function": {"name": "light_turn_off", "arguments": "{\"area\": \"all\"}"}}]
            finish = "tool_calls"
        else:
            message["content"] = "4"
        self.reply({"model": body["model"], "choices": [{"message": message, "finish_reason": finish}],
                    "usage": {"prompt_tokens": 1800}})


def run(mode, *argv):
    Stub.mode = mode
    server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    smoke.BASE = "http://127.0.0.1:%d" % server.server_port
    smoke.jpeg = lambda draw: "data:image/jpeg;base64,AA=="
    smoke.video_15s_1fps = lambda: "data:video/mp4;base64,AA=="
    sys.argv = ["smoke", "--runs", "2", *argv]
    out = io.StringIO()
    try:
        with redirect_stdout(out):
            code = smoke.main()
    finally:
        server.shutdown()
    return code, json.loads(out.getvalue())


def check(label, condition, detail=None):
    if not condition:
        raise SystemExit("FAIL %s %s" % (label, json.dumps(detail)[:500] if detail is not None else ""))
    print("ok", label)


code, report = run("good")
check("all checks pass on a healthy stub", code == 0 and report["failed"] == [], report)
check("video tokens reported", report["results"]["video_15s_1fps"]["prompt_tokens"] == 1800, report)
check("served root recorded", report["results"]["models"]["root"] == "Qwen/Qwen3.6-35B-A3B-FP8", report)

code, report = run("think")
check("leaked thinking fails text", code == 1 and "text" in report["failed"], report)

code, report = run("enum")
check("out-of-vocabulary scene value fails", "live_facts_shape" in report["failed"]
      and report["results"]["live_facts_shape"]["reasons"] == ["enum"], report)

code, report = run("novideo", "--video-optional")
check("video rejection is optional for the incumbent", code == 0
      and report["results"]["video_15s_1fps"]["ok"] is False
      and "At most 0" in report["results"]["video_15s_1fps"]["error"], report)

code, report = run("novideo")
check("video rejection fails when required", code == 1 and report["failed"] == ["video_15s_1fps"], report)

code, report = run("good", "--model", "qwen3.6-35b-a3b")
check("wrong first served name fails models", "models" in report["failed"], report)
print("all vllm-swap-smoke tests passed")
