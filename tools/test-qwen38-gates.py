#!/usr/bin/env python3
"""Tests for tools/qwen38_gates.py — the migration's decision core.

These assertions are the reason the cutover can be trusted, so they test
the ways each gate could WRONGLY PASS, not just that the happy path works:
ties inflating n, a normal approximation shifting a critical value, a leak
hiding in the renamed reasoning field, and box markup that the sidecar
parses but the app renders raw.

Run: python3 tools/test-qwen38-gates.py
"""
from __future__ import annotations

import math
import pathlib
import sys

# `qwen38_gates` is a plain module next to this file, not a package member.
# Import it by path rather than via importlib.spec_from_file_location: the
# latter leaves the module out of sys.modules, and @dataclass resolves its
# annotations through sys.modules[cls.__module__].
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import qwen38_gates as G  # noqa: E402

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILURES.append(name)


# ── exact binomial ───────────────────────────────────────────────────────
print("exact_binomial")
check("P(X>=0) == 1", G.binom_sf_ge(0, 10, 0.5) == 1.0)
check("P(X>n) == 0", G.binom_sf_ge(11, 10, 0.5) == 0.0)
check("fair coin, all heads", math.isclose(G.binom_sf_ge(10, 10, 0.5), 0.5 ** 10))
# At p=0.5 the tails mirror: P(X>=k) + P(X>=n-k+1) == 1.
check("tails are symmetric at p=0.5",
      all(math.isclose(G.binom_sf_ge(k, 10, 0.5) + G.binom_sf_ge(10 - k + 1, 10, 0.5), 1.0)
          for k in range(1, 11)))
# Known value: P(X >= 8 | n=10, p=0.5) = (45 + 10 + 1)/1024
check("known tail value is exact",
      math.isclose(G.binom_sf_ge(8, 10, 0.5), 56 / 1024))
# A normal approximation would put this near 0.0571 and flip the verdict.
check("small-n tail is not normal-approximated",
      abs(G.binom_sf_ge(9, 12, 0.5) - 0.0729980) < 1e-6,
      f"got {G.binom_sf_ge(9, 12, 0.5)}")

print("\ncritical_value")
check("critical value is the smallest passing k",
      G.critical_value(10, 0.5, 0.05) == 9,
      f"got {G.critical_value(10, 0.5, 0.05)}")
check("k-1 would not have passed",
      G.binom_sf_ge(8, 10, 0.5) > 0.05 >= G.binom_sf_ge(9, 10, 0.5))
check("tiny n is unreachable -> None",
      G.critical_value(3, 0.5, 0.05) is None,
      f"got {G.critical_value(3, 0.5, 0.05)}")

# ── the shared non-inferiority engine ────────────────────────────────────
print("\nnoninferiority_test")
r = G.noninferiority_test("t", candidate_wins=20, incumbent_wins=2, ties=28)
check("clear win passes", r.passed)
check("ties are excluded from the test", r.n_discordant == 22)
check("ties are still reported", r.ties == 28 and r.n_pairs == 50)

r = G.noninferiority_test("t", candidate_wins=2, incumbent_wins=20, ties=28)
check("clear loss fails", not r.passed)

# The failure mode this guards: counting ties as evidence. With 48 ties a
# naive n=50 test would look well-powered; conditioned correctly it is not.
r = G.noninferiority_test("t", candidate_wins=2, incumbent_wins=0, ties=48)
check("mostly-tied corpus is reported underpowered", r.underpowered)
check("underpowered cannot pass", not r.passed)

r = G.noninferiority_test("t", candidate_wins=0, incumbent_wins=0, ties=50)
check("all ties do not pass", not r.passed)
check("all ties flagged in the note", "tied" in r.note)

r = G.noninferiority_test("t", candidate_wins=30, incumbent_wins=20, margin=0.0)
r2 = G.noninferiority_test("t", candidate_wins=30, incumbent_wins=20, margin=0.10)
check("a wider margin is easier to pass", r2.p_value < r.p_value)
check("margin is recorded in the result", r2.margin == 0.10)

try:
    G.noninferiority_test("t", 1, 1, margin=0.7)
    check("invalid margin rejected", False, "no exception")
except ValueError:
    check("invalid margin rejected", True)
try:
    G.noninferiority_test("t", -1, 1)
    check("negative counts rejected", False, "no exception")
except ValueError:
    check("negative counts rejected", True)

# ── McNemar wiring (G2 / G4) ─────────────────────────────────────────────
print("\nmcnemar_from_pairs")
pairs = ([(False, True)] * 15 + [(True, False)] * 2
         + [(True, True)] * 40 + [(False, False)] * 3)
r = G.mcnemar_from_pairs("G2", pairs)
check("concordant pairs become ties", r.ties == 43)
check("discordant counted correctly", r.candidate_wins == 15 and r.incumbent_wins == 2)
check("candidate improvement passes", r.passed)

r = G.mcnemar_from_pairs("G2", [(True, True)] * 500)
check("identical performance does not pass on concordance alone", not r.passed)
check("identical performance is flagged underpowered", r.underpowered)

# G4 direction check: caller passes `not hallucinated`, so "candidate_wins"
# means the candidate avoided a false presence the incumbent emitted.
g4 = G.mcnemar_from_pairs("G4", [(not inc, not cand) for inc, cand in
                                 [(True, False)] * 12 + [(False, True)] * 1])
check("G4 direction: fewer hallucinations is a candidate win",
      g4.candidate_wins == 12 and g4.incumbent_wins == 1)

# ── G5 leakage ───────────────────────────────────────────────────────────
print("\ng5_reasoning_leaks")
check("clean message has no leaks",
      G.find_reasoning_leaks({"content": "The kitchen light is on."}) == [])
check("<think> in content is a leak",
      any(l.where == "content" for l in
          G.find_reasoning_leaks({"content": "<think>hmm</think> yes"})))
check("closing tag alone is a leak",
      any(l.where == "content" for l in
          G.find_reasoning_leaks({"content": "ok</think> yes"})))
# R5: the rename is the trap — a harness checking one field passes a leak.
check("non-empty `reasoning` (new name) is a leak",
      any(l.where == "reasoning" for l in
          G.find_reasoning_leaks({"content": "yes", "reasoning": "step 1..."})))
check("non-empty `reasoning_content` (old name) is a leak",
      any(l.where == "reasoning_content" for l in
          G.find_reasoning_leaks({"content": "yes", "reasoning_content": "step 1..."})))
check("empty reasoning field is not a leak",
      G.find_reasoning_leaks({"content": "yes", "reasoning": "  "}) == [])
check("null reasoning field is not a leak",
      G.find_reasoning_leaks({"content": "yes", "reasoning": None}) == [])
check("multimodal content parts are scanned",
      any(l.where == "content" for l in G.find_reasoning_leaks(
          {"content": [{"type": "text", "text": "<think>x</think>"}]})))
check("non-dict message is handled", G.find_reasoning_leaks(None) == [])

# A paired <think>...</think> matches both the opening and closing pattern,
# so one leaking block can yield more than one Leak record. G5 is
# zero-tolerance, so the count never gates anything — only "any" does.
check("scan_response covers buffered choices",
      len(G.scan_response({"choices": [{"message": {"content": "<think>a</think>"}}]})) >= 1)
check("both tags of one block are reported",
      len(G.find_reasoning_leaks({"content": "<think>a</think>"})) == 2)
check("scan_response covers streaming deltas",
      len(G.scan_response({"choices": [{"delta": {"reasoning": "a"}}]})) == 1)
check("scan_response on empty response", G.scan_response({}) == [])

# ── G7 production parsers ────────────────────────────────────────────────
print("\ng7_production_parsers")
canonical = "The mug <ref>mug</ref><box>100,200,300,400</box> is on the counter."
prims = G.parse_primitives(canonical)
check("sidecar parses canonical ref/box", len(prims) == 1)
check("label and coords extracted",
      prims[0]["label"] == "mug" and prims[0]["bbox_1000"] == [100, 200, 300, 400])
check("app strips canonical markup cleanly", G.app_residual_markup(canonical) == [])

# The divergence this gate exists to catch: the sidecar accepts Qwen3-VL's
# bracketed form and a bare '>' close. Until 2026-08-16 the app strippers
# did not, so this markup rendered raw while the server parse looked
# healthy. Both sides are now widened — these cases assert PARITY, and they
# mirror tools/run-look-tests.js so a one-sided edit fails a suite.
bracketed = "A cat <ref>cat</ref><box>[[10,20,30,40]]</box> sits."
check("sidecar accepts the bracketed variant", len(G.parse_primitives(bracketed)) == 1)
check("app now strips the bracketed variant too",
      G.app_residual_markup(bracketed) == [],
      f"residual: {G.app_residual_markup(bracketed)}")
check("bracketed variant therefore passes G7", G.score_g7(bracketed).passed)

bare_close = "A dog <ref>dog</ref><box>10,20,30,40> runs."
check("sidecar accepts the bare '>' close", len(G.parse_primitives(bare_close)) == 1)
check("app now strips the bare '>' close too",
      G.app_residual_markup(bare_close) == [],
      f"residual: {G.app_residual_markup(bare_close)}")
check("single-bracket variant strips on both sides",
      len(G.parse_primitives("<ref>x</ref><box>[10,20,30,40]</box>")) == 1
      and G.app_residual_markup("<ref>x</ref><box>[10,20,30,40]</box>") == [])

# Observed live on the incumbent at max_tokens=500: generation stops
# mid-markup and the close tag never arrives. Before the 2026-08-16
# widening the app rendered the dangling opener; it now strips it, at every
# cut point — including one character earlier, which is the same defect
# wearing a different hat.
truncated = "I see a mug on the left of the counter. <box>"
check("unpaired <box> is stripped, not rendered",
      G.app_residual_markup(truncated) == [],
      f"residual: {G.app_residual_markup(truncated)}")
# It still fails G7 — on EXTRACTION, because a truncated box parses to no
# primitive. The gate must fail here for the right reason.
s = G.score_g7(truncated)
check("truncated trace still fails G7", not s.passed)
check("...and fails on extraction, not on residual markup",
      not s.extracted and s.app_clean)
for tail in ("<box", "<bo", "<b", "<ref", "<re", "</box", "</ref"):
    check(f"tag truncated as {tail!r} is stripped",
          G.app_residual_markup("prose " + tail) == [],
          f"residual: {G.app_residual_markup('prose ' + tail)}")
check("a lone '<' in prose is left alone",
      G.app_render("5 < 6 mugs") == "5 < 6 mugs")
check("an unrelated tag is left alone",
      "<b>bold</b>" in G.app_render("prose <b>bold</b>"))

check("degenerate zero-area boxes are dropped",
      G.parse_primitives("<ref>x</ref><box>10,20,10,20</box>") == [])
check("coords are clamped to 0-1000",
      G.parse_primitives("<ref>x</ref><box>0,0,5000,5000</box>")[0]["bbox_1000"]
      == [0, 0, 1000, 1000])
check("bare-box fallback labels sequentially",
      [p["label"] for p in G.parse_primitives("<box>1,1,9,9</box><box>2,2,8,8</box>")]
      == ["object 1", "object 2"])
check("ref/box wins over the bare fallback",
      [p["label"] for p in G.parse_primitives(
          "<ref>a</ref><box>1,1,9,9</box><box>2,2,8,8</box>")] == ["a"])

check("ANSWER line extracted", G.extract_answer_line("blah\nANSWER: it is a mug")
      == "it is a mug")
check("missing ANSWER line is None", G.extract_answer_line("no answer here") is None)
check("app_render drops the ANSWER line",
      "ANSWER" not in G.app_render("stuff\nANSWER: hidden"))
check("app_render keeps the ref label as text",
      G.app_render("see <ref>mug</ref><box>1,1,9,9</box> there").strip()
      == "see mug there")

print("\ng7_scoring")
s = G.score_g7(canonical)
check("clean trace passes G7", s.passed and s.extracted and s.app_clean)
check("no-box trace does not pass", not G.score_g7("Just prose, no boxes.").passed)
runaway = " ".join(["The counter is also near a thing."] * 30)
check("runaway trace fails G7", not G.score_g7(runaway).passed)
check("runaway is detected by prefix repetition",
      G.score_g7(runaway).max_prefix_repeat >= 4)

print()
if FAILURES:
    print(f"qwen38 gate-core tests: {len(FAILURES)} FAILED -> {FAILURES}")
    sys.exit(1)
print("qwen38 gate-core tests: all passed")
