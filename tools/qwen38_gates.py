"""qwen38_gates.py — the shared correctness core for the Qwen3.8 migration
acceptance gates (docs/QWEN38-MIGRATION.md, gates G1–G7).

Every gate in this migration is a PAIRED comparison against the incumbent —
the same frame, the same stored request, the same negative — scored twice.
That shape means one statistical engine serves G1, G2 and G4: an exact
one-sided binomial test over the DISCORDANT pairs only. Ties carry no
information about which model is better and must not inflate n; the
migration doc says so explicitly for G1 ("binomial critical value on the
actual non-tied count") and it is the same reason McNemar conditions on
b and c.

This module holds no I/O and no host dependencies, so the parts that decide
whether production changes model are testable offline:

  * `noninferiority_test` — the shared exact test (G1 sign test, G2/G4
    McNemar) with an explicit, recorded non-inferiority margin.
  * `find_reasoning_leaks` — G5. Checks `<think>` in content AND BOTH
    response fields, because vLLM 0.20.2 renamed `reasoning_content` to
    `reasoning` (R5) and a harness that checks one field passes a leaking
    model.
  * `parse_primitives` / `app_residual_markup` — G7. The FIRST is the
    sidecar's production parser, ported byte-for-byte. The SECOND applies
    the app's two stripper regexes, which are STRICTER than the sidecar's.
    G7 passes only if both accept the output; scoring with a tolerant
    parser (as `probe-grounded-reasoning.py` does by default) would pass a
    model whose markup renders raw in the UI.

Nothing here samples, rounds, or approximates: `math.comb` gives exact
binomial tails at the sizes these gates use (n <= a few thousand).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

# ─────────────────────────────────────────────────────────────────────────
# Statistics — the shared paired-comparison engine
# ─────────────────────────────────────────────────────────────────────────


def binom_sf_ge(k: int, n: int, p: float) -> float:
    """P(X >= k) for X ~ Binomial(n, p). Exact, no normal approximation.

    The gates run at n as small as ~20 discordant pairs, where a normal
    approximation misstates the critical value by a whole count — enough to
    flip a cutover decision.
    """
    if n < 0:
        raise ValueError("n must be non-negative")
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    if p <= 0.0:
        return 0.0 if k > 0 else 1.0
    if p >= 1.0:
        return 1.0
    return math.fsum(
        math.comb(n, i) * (p ** i) * ((1.0 - p) ** (n - i))
        for i in range(k, n + 1)
    )


def critical_value(n: int, p0: float, alpha: float) -> int | None:
    """Smallest k with P(X >= k | n, p0) <= alpha, or None if unreachable.

    None means the test is UNPOWERED at this n: no outcome, not even a
    clean sweep, can reject H0. Reporting that honestly is the point —
    a gate that cannot fail is not a gate.
    """
    for k in range(0, n + 1):
        if binom_sf_ge(k, n, p0) <= alpha:
            return k
    return None


@dataclass
class GateResult:
    """The verdict plus everything needed to audit it later."""
    name: str
    n_pairs: int                 # every pair examined, ties included
    n_discordant: int            # the only pairs the test uses
    candidate_wins: int
    incumbent_wins: int
    ties: int
    margin: float
    alpha: float
    p_value: float
    critical: int | None
    passed: bool
    underpowered: bool
    note: str = ""

    def summary(self) -> str:
        verdict = "PASS" if self.passed else "FAIL"
        if self.underpowered:
            verdict += " (UNDERPOWERED — cannot reject at any outcome)"
        return (
            f"{self.name}: {verdict}\n"
            f"  pairs={self.n_pairs} (ties={self.ties}, discordant={self.n_discordant})\n"
            f"  candidate_wins={self.candidate_wins} incumbent_wins={self.incumbent_wins}\n"
            f"  H0: p_win <= {0.5 - self.margin:.3f} (margin {self.margin:.2f}), "
            f"alpha={self.alpha}\n"
            f"  p_value={self.p_value:.5f} critical_value="
            f"{self.critical if self.critical is not None else 'unreachable'}\n"
            + (f"  note: {self.note}\n" if self.note else "")
        )


def noninferiority_test(name: str, candidate_wins: int, incumbent_wins: int,
                        ties: int = 0, margin: float = 0.10,
                        alpha: float = 0.05) -> GateResult:
    """Exact one-sided non-inferiority test on discordant pairs.

    H0: p_win <= 0.5 - margin   (the candidate is materially worse)
    H1: p_win >  0.5 - margin   (the candidate is non-inferior)

    Rejecting H0 is the PASS condition. `margin` is a deliberate policy
    choice, not a statistical constant — it is how much worse the candidate
    may be on discordant pairs and still ship. It is recorded in every
    result so a later reader can see what was tolerated. margin=0 makes
    this a strict superiority sign test.

    G1 feeds it judge win/loss/tie; G2 and G4 feed it McNemar's b and c,
    which is the same computation conditioned on the same quantity.
    """
    if candidate_wins < 0 or incumbent_wins < 0 or ties < 0:
        raise ValueError("counts must be non-negative")
    if not 0.0 <= margin < 0.5:
        raise ValueError("margin must be in [0, 0.5)")
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")

    n_disc = candidate_wins + incumbent_wins
    p0 = 0.5 - margin
    p_value = binom_sf_ge(candidate_wins, n_disc, p0) if n_disc else 1.0
    crit = critical_value(n_disc, p0, alpha) if n_disc else None
    underpowered = crit is None
    passed = (not underpowered) and candidate_wins >= crit

    note = ""
    if n_disc == 0:
        note = ("every pair tied — no evidence either way; the gate cannot "
                "pass on ties alone")
    elif underpowered:
        note = (f"n={n_disc} discordant pairs is too few to reject H0 at "
                f"alpha={alpha}; collect more pairs before judging")

    return GateResult(
        name=name, n_pairs=n_disc + ties, n_discordant=n_disc,
        candidate_wins=candidate_wins, incumbent_wins=incumbent_wins,
        ties=ties, margin=margin, alpha=alpha, p_value=p_value,
        critical=crit, passed=passed, underpowered=underpowered, note=note,
    )


def mcnemar_from_pairs(name: str, pairs, margin: float = 0.10,
                       alpha: float = 0.05) -> GateResult:
    """Non-inferiority over paired BOOLEAN outcomes (G2 validity, G4
    false-presence).

    `pairs` is an iterable of (incumbent_good, candidate_good). Concordant
    pairs — both good, both bad — carry no information and become ties.

    For G4 the "good" outcome is the ABSENCE of a false presence: pass
    `not hallucinated` for each side so the direction stays consistent
    ("candidate_wins" always means the candidate did the better thing).
    """
    b = c = t = 0
    for inc_good, cand_good in pairs:
        if bool(inc_good) == bool(cand_good):
            t += 1
        elif cand_good:
            b += 1          # candidate good, incumbent bad
        else:
            c += 1          # incumbent good, candidate bad
    return noninferiority_test(name, candidate_wins=b, incumbent_wins=c,
                               ties=t, margin=margin, alpha=alpha)


# ─────────────────────────────────────────────────────────────────────────
# G5 — reasoning leakage
# ─────────────────────────────────────────────────────────────────────────

# vLLM 0.20.2 renamed `reasoning_content` -> `reasoning` (R5). Both names
# are checked: the old one because older builds and the sidecar tee may
# still emit it, the new one because that is what the pinned engine emits.
REASONING_FIELDS = ("reasoning", "reasoning_content")

_THINK_PATTERNS = (
    re.compile(r"<think\b", re.IGNORECASE),
    re.compile(r"</think>", re.IGNORECASE),
    re.compile(r"<\|thinking\|>", re.IGNORECASE),
    re.compile(r"<reasoning\b", re.IGNORECASE),
)


@dataclass
class Leak:
    where: str      # "content" | "reasoning" | "reasoning_content"
    pattern: str
    excerpt: str


def find_reasoning_leaks(message: dict) -> list[Leak]:
    """G5: ZERO tolerance. Returns every leak found in one message.

    Three independent surfaces leak differently and all three are checked:
      * `content` carrying literal <think> markup — the app renders it
        verbatim, so this reaches the user and, via TTS, is spoken;
      * `reasoning` — the pinned engine's field name;
      * `reasoning_content` — the pre-rename field name.

    A NON-EMPTY reasoning field is itself a leak even without <think>
    markup: it means `enable_thinking:false` was not honoured, which is the
    condition G5 exists to detect. Checking only for markup would pass a
    model that reasons into a structured field.
    """
    leaks: list[Leak] = []
    if not isinstance(message, dict):
        return leaks

    content = message.get("content")
    if isinstance(content, list):      # multimodal content parts
        content = " ".join(
            part.get("text", "") for part in content
            if isinstance(part, dict))
    if isinstance(content, str):
        for pat in _THINK_PATTERNS:
            m = pat.search(content)
            if m:
                start = max(0, m.start() - 40)
                leaks.append(Leak("content", pat.pattern,
                                  content[start:m.end() + 80]))

    for field_name in REASONING_FIELDS:
        value = message.get(field_name)
        if isinstance(value, str) and value.strip():
            leaks.append(Leak(field_name, "<non-empty>", value[:160]))

    return leaks


def scan_response(response: dict) -> list[Leak]:
    """Apply `find_reasoning_leaks` across every choice of a chat response,
    for both buffered (`message`) and streaming (`delta`) shapes."""
    leaks: list[Leak] = []
    for choice in (response or {}).get("choices", []) or []:
        for key in ("message", "delta"):
            if isinstance(choice.get(key), dict):
                leaks.extend(find_reasoning_leaks(choice[key]))
    return leaks


# ─────────────────────────────────────────────────────────────────────────
# G7 — grounded-box compatibility, scored with PRODUCTION parsers
# ─────────────────────────────────────────────────────────────────────────

# Ported byte-for-byte from the live vision-sidecar (stack/services/vision/
# app.py). Tolerant of Qwen3-VL's bracketed variant and a bare '>' close.
_BOX_INNER = (r"<box>\s*\[*\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)"
              r"\s*\]*\s*(?:</box>|>)")
_REF_BOX_RE = re.compile(r"<ref>\s*(.*?)\s*</ref>\s*" + _BOX_INNER, re.IGNORECASE)
_BARE_BOX_RE = re.compile(_BOX_INNER, re.IGNORECASE)

# The app's stripper regexes, kept in lockstep with app/src/home-look.jsx
# (`LK_BOX_TAG_SRC` / `LK_MARKUP_RE` / `lkStripMarkup`). Until 2026-08-16
# the app accepted ONLY `<box>digits, commas, whitespace</box>`, which made
# it strictly stricter than the sidecar: bracketed coordinates, a bare '>'
# close, and generation-cap truncation all rendered as raw markup to the
# user while the server-side parse looked healthy. Both sides are now
# widened to the same tolerance.
#
# ⚠ PARITY: if `LK_BOX_TAG_SRC` in home-look.jsx changes, change this too.
# `tools/run-look-tests.js` holds the JS side to the same cases as
# `tools/test-qwen38-gates.py` holds this one, so a one-sided edit fails a
# suite rather than silently un-syncing the gate from production.
_APP_BOX_TAG = r"<box>\s*\[*\s*[\d,\s]*\s*\]*\s*(?:</box>|>)"
_APP_MARKUP_RE = re.compile(                                   # lkStripMarkup
    _APP_BOX_TAG
    + r"|</?(?:ref|box)\b[^>]*>"
    + r"|</?(?:r(?:e(?:f)?)?|b(?:o(?:x)?)?)$",
    re.IGNORECASE)
_APP_REF_RE = re.compile(                                      # lkRenderReasoning
    r"<ref>([\s\S]*?)</ref>\s*(?:" + _APP_BOX_TAG + r")?", re.IGNORECASE)
_APP_ANSWER_RE = re.compile(r"\n*ANSWER:[\s\S]*$", re.IGNORECASE)

# Residual markup the app would render raw. Two alternatives, because
# truncation at the generation cap can stop ANYWHERE:
#   1. a complete but unpaired tag, e.g. a `<box>` whose `</box>` never
#      arrived — this is the case observed live on the incumbent; and
#   2. a tag cut off before its own '>', e.g. a trailing `<box` or `<re`.
# Matching only the first would report "clean" for a trace truncated one
# character earlier, which is the same defect wearing a different hat.
_ANY_MARKUP_RE = re.compile(
    r"</?(?:ref|box)\b[^>]*>"          # complete tag
    r"|<\/?(?:r(?:e(?:f)?)?|b(?:o(?:x)?)?)$",   # tag truncated before '>'
    re.IGNORECASE)


def _clamp1000(v: int) -> int:
    return max(0, min(1000, v))


def parse_primitives(text: str) -> list[dict]:
    """The sidecar's production parser, ported verbatim. Degenerate
    (zero-area) boxes are dropped, exactly as live."""
    prims: list[dict] = []
    for m in _REF_BOX_RE.finditer(text):
        label = (m.group(1) or "").strip() or "object"
        x1, y1, x2, y2 = (_clamp1000(int(g)) for g in m.groups()[1:])
        if x2 > x1 and y2 > y1:
            prims.append({"label": label, "bbox_1000": [x1, y1, x2, y2]})
    if not prims:
        for i, m in enumerate(_BARE_BOX_RE.finditer(text)):
            x1, y1, x2, y2 = (_clamp1000(int(g)) for g in m.groups())
            if x2 > x1 and y2 > y1:
                prims.append({"label": f"object {i + 1}",
                              "bbox_1000": [x1, y1, x2, y2]})
    return prims


def app_render(text: str) -> str:
    """What the app would actually show, applying its strippers in the same
    order as `lkRenderReasoning` then `lkStripMarkup`."""
    t = _APP_ANSWER_RE.sub("", str(text or "")).strip()
    t = _APP_REF_RE.sub(lambda m: (m.group(1) or "").strip(), t)
    return _APP_MARKUP_RE.sub("", t)


def app_residual_markup(text: str) -> list[str]:
    """Markup the app's strippers would leave on screen. Non-empty means a
    G7 FAIL regardless of how cleanly the sidecar parsed the same text —
    the user sees raw tags."""
    return _ANY_MARKUP_RE.findall(app_render(text))


def extract_answer_line(text: str) -> str | None:
    m = re.search(r"ANSWER:\s*(.+)", str(text or ""), re.IGNORECASE)
    return m.group(1).strip() if m else None


@dataclass
class G7Score:
    n_primitives: int
    has_answer_line: bool
    residual_markup: list[str] = field(default_factory=list)
    runaway: bool = False
    n_sentences: int = 0
    max_prefix_repeat: int = 0

    @property
    def app_clean(self) -> bool:
        return not self.residual_markup

    @property
    def extracted(self) -> bool:
        return self.n_primitives > 0

    @property
    def passed(self) -> bool:
        """G7 needs BOTH consumers to accept the output, plus no runaway."""
        return self.extracted and self.app_clean and not self.runaway


def runaway_score(text: str) -> tuple[int, int]:
    """(sentence count, most-repeated 5-word opening) — the enumerate-forever
    detector, kept identical to probe-grounded-reasoning.py so the two
    tools' runaway numbers are comparable."""
    from collections import Counter
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    pref = [" ".join(s.split()[:5]).lower() for s in sents]
    c = Counter(pref)
    return len(sents), (max(c.values()) if c else 0)


def score_g7(text: str, runaway_repeat_threshold: int = 4,
             runaway_char_threshold: int = 1400) -> G7Score:
    """Score one grounded-reasoning trace with the PRODUCTION parsers."""
    n_sent, max_rep = runaway_score(text)
    return G7Score(
        n_primitives=len(parse_primitives(text)),
        has_answer_line=extract_answer_line(text) is not None,
        residual_markup=app_residual_markup(text),
        runaway=(max_rep >= runaway_repeat_threshold
                 or len(text) > runaway_char_threshold),
        n_sentences=n_sent,
        max_prefix_repeat=max_rep,
    )
