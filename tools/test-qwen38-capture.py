#!/usr/bin/env python3
"""Tests for tools/qwen38_capture.py — the migration's measurement instrument.

The rules these assertions protect are the ones the migration doc says have
already been got wrong: a latency number without its measurement point and
cache state, and a missing cached_tokens field silently reading as zero.

Run: python3 tools/test-qwen38-capture.py
"""
from __future__ import annotations

import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import qwen38_capture as C  # noqa: E402

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILURES.append(name)


def turn(**kw):
    base = dict(cell="t", measurement_point="sidecar", cache_state="cold",
                latency_s=1.0)
    base.update(kw)
    return C.CapturedTurn(**base)


print("measurement_labels_are_mandatory")
try:
    turn(measurement_point="somewhere")
    check("unknown measurement point rejected", False, "no exception")
except ValueError as e:
    check("unknown measurement point rejected", "not admissible" in str(e))
try:
    turn(cache_state="probably-warm")
    check("unknown cache state rejected", False, "no exception")
except ValueError:
    check("unknown cache state rejected", True)
check("valid labels accepted", turn().measurement_point == "sidecar")
check("every documented point is constructible",
      all(turn(measurement_point=p) for p in C.MEASUREMENT_POINTS))

print("\ncached_tokens_semantics")
# R4 hinges on telling "no cache hit" apart from "nobody measured".
t = turn(prompt_tokens=1000, cached_tokens=0)
check("zero cached tokens is a real 0.0 ratio", t.cache_hit_ratio == 0.0)
t = turn(prompt_tokens=1000, cached_tokens=None)
check("absent cached tokens is None, not 0", t.cache_hit_ratio is None)
t = turn(prompt_tokens=1000, cached_tokens=250)
check("ratio computed correctly", t.cache_hit_ratio == 0.25)
t = turn(prompt_tokens=0, cached_tokens=0)
check("zero prompt tokens does not divide by zero", t.cache_hit_ratio is None)

print("\nusage_parsing")
pt, ct, cached = C._usage({"usage": {
    "prompt_tokens": 900, "completion_tokens": 40,
    "prompt_tokens_details": {"cached_tokens": 128}}})
check("nested prompt_tokens_details read", (pt, ct, cached) == (900, 40, 128))
pt, ct, cached = C._usage({"usage": {"prompt_tokens": 900, "cached_tokens": 64}})
check("hoisted cached_tokens read", cached == 64)
pt, ct, cached = C._usage({"usage": {"prompt_tokens": 900}})
check("missing cached_tokens is None", cached is None)
check("missing usage block is all None", C._usage({}) == (None, None, None))

print("\ncontent_extraction")
t = turn(response={"choices": [{"message": {"content": "hello"}}]})
check("plain content", t.content() == "hello")
t = turn(response={"choices": [{"message": {"content": [
    {"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}}]})
check("multimodal content parts joined", t.content() == "a b")
check("malformed response yields empty string", turn(response={}).content() == "")

print("\nrequest_redaction")
big = "data:image/jpeg;base64," + "A" * 5000
body = {"messages": [{"role": "user", "content": [
    {"type": "image_url", "image_url": {"url": big}},
    {"type": "text", "text": "what is this"}]}]}
red = C._redact_request(body)
url = red["messages"][0]["content"][0]["image_url"]["url"]
check("image bytes are not archived", "AAAA" not in url)
check("image presence and size are kept", "b64 chars" in url)
check("text parts survive redaction",
      red["messages"][0]["content"][1]["text"] == "what is this")
check("original body is not mutated",
      body["messages"][0]["content"][0]["image_url"]["url"] == big)

print("\nr4_cache_counters")
SCRAPE = """# HELP vllm:prefix_cache_queries_total queries
# TYPE vllm:prefix_cache_queries_total counter
vllm:prefix_cache_queries_total{engine="0",model_name="qwen3-vl-30b"} 419783.0
vllm:prefix_cache_hits_total{engine="0",model_name="qwen3-vl-30b"} 152864.0
vllm:mm_cache_queries_total{engine="0",model_name="qwen3-vl-30b"} 368.0
vllm:mm_cache_hits_total{engine="0",model_name="qwen3-vl-30b"} 136.0
vllm:num_requests_running{engine="0"} 0.0
"""
counters = C.read_cache_counters(SCRAPE)
check("prefix counters parsed",
      counters["vllm:prefix_cache_queries_total"] == 419783.0)
check("mm counters parsed", counters["vllm:mm_cache_hits_total"] == 136.0)
check("unrelated metrics ignored",
      "vllm:num_requests_running" not in counters)
check("comment lines ignored", len(counters) == 4)

before = {"vllm:prefix_cache_queries_total": 100.0, "vllm:prefix_cache_hits_total": 10.0}
after = {"vllm:prefix_cache_queries_total": 200.0, "vllm:prefix_cache_hits_total": 90.0}
d = C.cache_hit_delta(before, after)
check("delta rate uses the interval, not lifetime",
      d["prefix_cache"]["rate"] == 0.8, str(d["prefix_cache"]))
d = C.cache_hit_delta(before, before)
check("idle interval reports None, not a 0% hit rate",
      d["prefix_cache"]["rate"] is None)

print("\npercentile")
check("nearest-rank, no interpolation", C.percentile([1, 2, 3, 4], 50) in (2, 3))
check("p95 of a single value", C.percentile([7], 95) == 7)
check("p100 is the max", C.percentile([1, 5, 9], 100) == 9)
check("p0 is the min", C.percentile([1, 5, 9], 0) == 1)
check("empty input is None", C.percentile([], 50) is None)
check("None values are ignored", C.percentile([None, 4, None], 50) == 4)

print("\nsummary_carries_its_labels")
turns = [turn(latency_s=1.0, prompt_tokens=100, cached_tokens=0),
         turn(latency_s=2.0, prompt_tokens=100, cached_tokens=0)]
s = C.summarize(turns, "cellA")
check("summary names the measurement point", "measured at sidecar" in s)
check("summary names the cache state", "cache cold" in s)
check("summary reports the R4 hit ratio", "prefix-cache hit ratio" in s)
s2 = C.summarize([turn(latency_s=1.0, prompt_tokens=100)], "cellB")
check("no cached_tokens is said out loud, not shown as 0",
      "no cached_tokens field" in s2)
check("all-error batch is reported honestly",
      "no successful turns" in C.summarize([turn(status="error")], "cellC"))

print()
if FAILURES:
    print(f"qwen38 capture tests: {len(FAILURES)} FAILED -> {FAILURES}")
    sys.exit(1)
print("qwen38 capture tests: all passed")
