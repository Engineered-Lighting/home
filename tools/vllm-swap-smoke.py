#!/usr/bin/env python3
"""Smoke and latency checks for a vLLM model swap (docs/VLLM-QWEN36-SWAP.md).

Runs inside the hav-vllm container, which already has python3, numpy and cv2,
and talks to vLLM on loopback. Every image and video is synthetic, generated
here, so no camera frame is read or sent. Only metrics are printed.

    Get-Content tools\\vllm-swap-smoke.py -Raw |
      ssh hav-ubuntu "tr -d '\\r' | docker exec -i hav-vllm python3 - --runs 5"

Checks: served models; plain chat (no thinking leak); tool call and tool
restraint; image recognition; a live-facts-shaped json_schema reply over
three stills (the lab-live-scene-1 enums from perception-lab qwen_scene.py);
and one 15 s, 1 fps video item. Exits 1 if any required check fails; the
video check is reported but not required when --video-optional is given
(the incumbent runs with "video": 0).
"""
import argparse
import base64
import json
import os
import statistics
import sys
import tempfile
import time
import urllib.error
import urllib.request

BASE = "http://localhost:8000"

SCENE_FIELDS = {
    "people_visible": ["none", "one", "multiple", "unclear"],
    "subject_posture": ["sitting", "standing", "lying", "walking", "unclear"],
    "held_object": ["book", "phone", "cup", "none", "other", "unclear"],
    "book_state": ["open", "closed", "not_visible", "unclear"],
    "gaze_target": ["book", "phone", "screen", "tv", "person", "elsewhere", "unclear"],
    "typing_visible": ["yes", "no", "unclear"],
    "screen_facing_subject": ["yes", "no", "not_visible"],
    "activity_hypothesis": ["reading", "phone_use", "computer_work", "resting", "transition", "other", "unclear"],
    "change_vs_prior": ["no_change", "changed", "unclear", "no_prior"],
}
SCENE_BASIS = ["person_visible", "no_person_visible", "still_between_frames", "moving_between_frames",
               "low_light_or_blur"]
SCENE_KEYS = ["people_visible", "subject_posture", "held_object", "book_state", "gaze_target", "typing_visible",
              "screen_facing_subject", "activity_hypothesis", "visible_basis", "change_vs_prior"]

TOOLS = [{"type": "function", "function": {
    "name": "light_turn_off",
    "description": "Turn off the lights in one area of the home, or everywhere.",
    "parameters": {"type": "object", "properties": {
        "area": {"type": "string", "description": "Area name, or 'all' for every area."}},
        "required": ["area"]}}}]


def post(path, body, timeout):
    request = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read()), time.monotonic() - started
    except urllib.error.HTTPError as error:
        return error.code, {"error": error.read().decode(errors="replace")[:300]}, time.monotonic() - started


def stream_ttft(body, timeout):
    """(ttft_s, total_s, text) for a streamed chat completion."""
    body = dict(body, stream=True)
    request = urllib.request.Request(BASE + "/v1/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    started, first, parts = time.monotonic(), None, []
    with urllib.request.urlopen(request, timeout=timeout) as response:
        for raw in response:
            line = raw.decode().strip()
            if not line.startswith("data:") or line == "data: [DONE]":
                continue
            choices = json.loads(line[5:]).get("choices") or [{}]
            delta = choices[0].get("delta") or {}
            text = (delta.get("content") or "") + (delta.get("reasoning_content") or delta.get("reasoning") or "")
            if text and first is None:
                first = time.monotonic() - started
            parts.append(delta.get("content") or "")
    return first, time.monotonic() - started, "".join(parts)


def jpeg(draw):
    import cv2
    import numpy as np
    frame = np.zeros((360, 640, 3), np.uint8)
    draw(frame, cv2)
    ok, data = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    assert ok
    return "data:image/jpeg;base64," + base64.b64encode(data.tobytes()).decode()


def video_15s_1fps():
    """A white square crossing left to right, 15 frames at 1 fps, as mp4."""
    import cv2
    import numpy as np
    path = os.path.join(tempfile.mkdtemp(prefix="vllm-smoke-"), "clip.mp4")
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), 1.0, (640, 360))
    if not writer.isOpened():
        raise RuntimeError("cv2 cannot write mp4v here")
    for index in range(15):
        frame = np.zeros((360, 640, 3), np.uint8)
        x = 20 + index * 38
        frame[150:210, x:x + 60] = 255
        writer.write(frame)
    writer.release()
    with open(path, "rb") as handle:
        data = handle.read()
    os.remove(path)
    os.rmdir(os.path.dirname(path))
    return "data:video/mp4;base64," + base64.b64encode(data).decode()


def chat(model, content, **extra):
    return dict({"model": model, "messages": [{"role": "user", "content": content}], "temperature": 0,
                 "max_tokens": 64}, **extra)


def message_of(reply):
    choice = reply["choices"][0]
    return choice["message"], choice["finish_reason"]


def leaked_thinking(message):
    return "<think>" in (message.get("content") or "") or bool(message.get("reasoning_content") or message.get("reasoning"))


def pct(values, q):
    values = sorted(values)
    return round(values[min(len(values) - 1, int(round(q * (len(values) - 1))))], 3) if values else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen3-vl-30b")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--video-optional", action="store_true")
    args = parser.parse_args()
    results, failures = {}, []

    def record(name, ok, required=True, **fields):
        results[name] = dict(fields, ok=ok)
        if not ok and required:
            failures.append(name)

    with urllib.request.urlopen(BASE + "/v1/models", timeout=10) as response:
        models = json.loads(response.read())["data"]
    record("models", models[0]["id"] == args.model, ids=[m["id"] for m in models], root=models[0].get("root"),
           max_model_len=models[0].get("max_model_len"))

    ttfts, totals, leaks = [], [], 0
    for _ in range(args.runs):
        ttft, total, text = stream_ttft(chat(args.model, "Reply with the single word: ready"), args.timeout)
        ttfts.append(ttft or total)
        totals.append(total)
        leaks += "<think>" in text or "ready" not in text.lower()
    record("text", leaks == 0, ttft_p50=pct(ttfts, .5), ttft_p95=pct(ttfts, .95), total_p95=pct(totals, .95),
           bad_replies=leaks)

    tool_ok, tool_lat, restraint_bad = 0, [], 0
    for _ in range(args.runs):
        status, reply, seconds = post("/v1/chat/completions", chat(
            args.model, "turn off my lights everywhere", tools=TOOLS, tool_choice="auto", max_tokens=128), args.timeout)
        tool_lat.append(seconds)
        if status == 200:
            message, finish = message_of(reply)
            calls = message.get("tool_calls") or []
            try:
                good = (finish == "tool_calls" and len(calls) == 1 and calls[0]["function"]["name"] == "light_turn_off"
                        and isinstance(json.loads(calls[0]["function"]["arguments"]).get("area"), str)
                        and "<tool_call>" not in (message.get("content") or ""))
            except (ValueError, KeyError, TypeError):
                good = False
            tool_ok += good
        status, reply, _ = post("/v1/chat/completions", chat(
            args.model, "What is two plus two? Answer with a number.", tools=TOOLS, tool_choice="auto"), args.timeout)
        restraint_bad += status != 200 or bool(message_of(reply)[0].get("tool_calls"))
    record("tool_call", tool_ok == args.runs and restraint_bad == 0, correct=tool_ok, runs=args.runs,
           spurious_calls=restraint_bad, p50=pct(tool_lat, .5), p95=pct(tool_lat, .95))

    circle = jpeg(lambda f, cv2: cv2.circle(f, (320, 180), 90, (0, 0, 255), -1))
    image_ok, image_lat = 0, []
    for _ in range(args.runs):
        status, reply, seconds = post("/v1/chat/completions", chat(args.model, [
            {"type": "image_url", "image_url": {"url": circle}},
            {"type": "text", "text": "What colour is the circle? Answer with one word."}]), args.timeout)
        image_lat.append(seconds)
        if status == 200:
            message, _ = message_of(reply)
            image_ok += "red" in (message.get("content") or "").lower() and not leaked_thinking(message)
    record("image", image_ok == args.runs, correct=image_ok, p50=pct(image_lat, .5), p95=pct(image_lat, .95))

    properties = {k: {"type": "string", "enum": v} for k, v in SCENE_FIELDS.items()}
    properties["visible_basis"] = {"type": "array", "items": {"type": "string", "enum": SCENE_BASIS}, "maxItems": 6}
    schema = {"type": "json_schema", "json_schema": {"name": "lab_live_scene_1", "strict": True, "schema": {
        "type": "object", "additionalProperties": False, "required": SCENE_KEYS, "properties": properties}}}
    stills = [jpeg(lambda f, cv2, i=i: cv2.rectangle(f, (40 + 20 * i, 60), (200 + 20 * i, 300), (90, 90, 90), -1))
              for i in range(3)]
    content = [{"type": "text", "text": "Three consecutive frames from a fixed den camera, oldest first."}]
    content += [{"type": "image_url", "image_url": {"url": s}} for s in stills]
    scene_ok, scene_lat, scene_reasons = 0, [], []
    for _ in range(args.runs):
        body = {"model": args.model, "temperature": 0, "max_tokens": 192, "stream": False, "response_format": schema,
                "messages": [{"role": "system", "content": "Describe only what is visible. Reply with one JSON "
                              "object using the allowed values only; use unclear when unsure."},
                             {"role": "user", "content": content}]}
        status, reply, seconds = post("/v1/chat/completions", body, args.timeout)
        scene_lat.append(seconds)
        reason = "http_%d" % status
        if status == 200:
            message, finish = message_of(reply)
            reason = "ok"
            try:
                value = json.loads(message.get("content") or "")
                if finish != "stop" or reply.get("model") != args.model or message.get("tool_calls"):
                    reason = "finish_or_model"
                elif not isinstance(value, dict) or set(value) != set(SCENE_KEYS):
                    reason = "keys"
                elif any(value[k] not in v for k, v in SCENE_FIELDS.items()):
                    reason = "enum"
                elif (not isinstance(value["visible_basis"], list) or len(value["visible_basis"]) > 6
                      or any(t not in SCENE_BASIS for t in value["visible_basis"])):
                    reason = "basis"
            except ValueError:
                reason = "not_json"
        scene_ok += reason == "ok"
        scene_reasons.append(reason)
    record("live_facts_shape", scene_ok == args.runs, correct=scene_ok, reasons=sorted(set(scene_reasons)),
           p50=pct(scene_lat, .5), p95=pct(scene_lat, .95))

    try:
        clip = video_15s_1fps()
        video_ok, video_lat, tokens, error = 0, [], None, None
        for _ in range(args.runs):
            status, reply, seconds = post("/v1/chat/completions", chat(args.model, [
                {"type": "video_url", "video_url": {"url": clip}},
                {"type": "text", "text": "Which way does the white square move: left, right, up or down? "
                                         "Answer with one word."}], mm_processor_kwargs={"fps": 1}), args.timeout)
            video_lat.append(seconds)
            if status != 200:
                error = reply.get("error")
                break
            message, _ = message_of(reply)
            tokens = reply.get("usage", {}).get("prompt_tokens")
            video_ok += "right" in (message.get("content") or "").lower()
        record("video_15s_1fps", video_ok == args.runs, required=not args.video_optional, correct=video_ok,
               prompt_tokens=tokens, p50=pct(video_lat, .5), p95=pct(video_lat, .95), error=error)
    except Exception as error:  # cv2 or encoder missing: report, never crash the run
        record("video_15s_1fps", False, required=not args.video_optional, error=str(error)[:200])

    print(json.dumps({"model": args.model, "runs": args.runs, "failed": failures, "results": results}, indent=1))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
