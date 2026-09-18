#!/usr/bin/env python3
"""Ladder level 1: public-dataset text rows through the five lighting questions.

Plan rev 5, M3. Each row from ``public_windows`` (a Charades description with
three-way targets per question) becomes one ``lighting-beliefs-state/v1``
packet: the description is the single current claim of one camera and a
synthetic ``devices.media`` row (role ``tv``, playing) is added when a
television class is present. Every packet goes through ``build_packet`` and
``leak_guard.assert_clean`` exactly as a household packet would, the five
questions are asked in ONE request per row, the answers are reduced by
probability only, and ``P(level >= k)`` (Score) or ``P(yes)`` (Noul) is
compared with the row's target at every threshold of a sensitivity table.
Targets that are ``unobserved`` are ignored for that question.

Camera naming (level 1): the questions are room-specific (``tv_attention``
reads ``cameras.living_room``, ``food_prep`` reads ``cameras.kitchen``) while
Charades scenes are spread over the house, so the packet camera is named
after the QUESTION's room, not the scene: ``living_room`` for a tv row (a
television class present or a positive ``tv_attention`` target), else
``kitchen`` for a positive ``food_prep`` row, else the Charades scene when
it is one of ``living_room`` / ``kitchen`` / ``dining_room``, else
``other_room``. The policy and the resulting camera counts are recorded in
``run.json`` (``camera_policy``) because the choice changes packet digests;
the original scene is kept per row so ``scores.md`` also reports recall per
scene.

Level-1 metric: the exporter's targets are positive-only for four questions
(an absent annotation is never a negative), so the acceptance number for
``tv_attention``, ``eating``, ``food_prep`` and ``settling`` is RECALL at
the a-priori threshold; ``rest_state`` has negatives (locomotion rows) and
is judged on precision and recall at its a-priori threshold. The table still
reports agreement and all four counts at every threshold.

Dry run is the default: it builds and validates every packet, runs the gate
check, counts would-be calls and bytes, writes ``run.json`` with
``executed: false`` and never imports the SDK's network client.

``--execute`` checks its preconditions in this order (the first failure
refuses, nothing is sent): ``--max-calls N`` given; ``--out`` below the eval
root and holding no ``run.json``; ``--cache`` below the eval root; the
roster readable at mode 0600 when given; the rows parse; a pinned model id
equal to the record's ``model_id``; the gate preflight for scope
``public_eval`` allows (record enabled for the scope, ``TYPESAFE_EGRESS=1``,
a fresh kill-switch mirror file from ``--toggle``); and finally, inside the
transport factory, the SDK installed and the API key non-empty in the
environment variable named by ``API_KEY_ENV`` (read by the SDK, never
printed or logged).

One gate ticket is one SDK invocation, and one SDK invocation is up to
``1 + MAX_RETRIES`` HTTP requests: the SDK ``RetryPolicy`` retries 408, 429,
5xx, connection and timeout errors on its own, so ``--max-calls`` and the
gate's per-minute bound count invocations, not requests. The run stops on
the first authentication (401) or permission (403) error, waits out the
gate's rate denials, aborts on any other denial or on three consecutive
call errors, and any other exception raised during a call releases the
ticket, marks the run aborted and still writes ``run.json`` (the exception
type only, never its text). A second ``--execute`` on an ``--out`` that
already holds an executed receipt refuses. Answers are cached by (row
digest, question digest, model id) so a rerun into a fresh ``--out`` costs
nothing.

Receipts under ``--out`` (0700 directory, 0600 files): ``run.json``,
``packets.jsonl`` (the packets as built, for review), ``answers.jsonl`` (raw
answers keyed by row id), ``scores.md`` (the sensitivity table) and
``egress-journal.jsonl`` (the gate's decisions). Timestamps in ``run.json``
are durations only.

Row contract (one JSON object per line; unknown keys are ignored):

    row_id       string (aliases: id, window_id); derived from the packet
                 digest when absent
    description  string (aliases: text, script); the claim text
    scene        string, optional (alias: camera); the Charades scene, kept
                 for the per-scene tables (see camera naming above)
    classes      list of Charades class ids or names, optional
                 (aliases: native_classes, a list of {class_id, class_name}
                 objects as public_windows writes; actions, the CSV
                 "c132 0.00 12.60;..." string)
    tv_present   bool, optional; overrides class detection
    targets      mapping question id -> true | false | null; string forms
                 yes/no/unobserved and positive/negative are accepted, and a
                 mapping {"value": ..., "k": <level>} sets the Score cutoff
                 for that row

Run the tests with ``python3 -m unittest tests/living_lights/test_public_story_eval.py``.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

REPO = Path(__file__).resolve().parents[1]
PUBLISHER_DIR = REPO / "stack" / "services" / "lighting-publisher"
if str(PUBLISHER_DIR) not in sys.path:
    sys.path.insert(0, str(PUBLISHER_DIR))

from lighting_beliefs import egress, leak_guard  # noqa: E402
from lighting_beliefs import packet as packet_mod  # noqa: E402
from lighting_beliefs import questions as questions_mod  # noqa: E402
from lighting_beliefs import reduce as reduce_mod  # noqa: E402

EVAL_ROOT = Path("/home/marcelo-lima/vjepa-home/experiments/public-story-eval")
SCOPE = "public_eval"
LEVEL = 1
API_KEY_ENV = "TYPESAFE_API_KEY"
SDK_DISTRIBUTION = "typesafe-sdk"
SDK_PIN = "0.6.0"
DEFAULT_THRESHOLDS = (0.5, 0.6, 0.7, 0.8, 0.9)
# Score cutoffs: the belief compared with the target is P(level >= k). The
# defaults match the exporter's label policy (class membership: "watching
# television" is at least partial attention, "holding some food" is at least
# light preparation, "lying on a bed" is at least resting), so level 1 tests
# the wording, not the story machines' stricter cutoffs (WATCHING is
# attention >= 2, couch sleep is rest == 2); pass ``--cutoffs`` for those. A
# row may override its own cutoff in ``targets``.
DEFAULT_CUTOFFS = {"tv_attention": 1, "food_prep": 1, "settling": 1, "rest_state": 1}
# A-priori thresholds the plan's acceptance line refers to (agreement at
# least 0.70 per question); the table still reports every threshold.
APRIORI_THRESHOLDS = {"tv_attention": 0.6, "eating": 0.5, "food_prep": 0.5, "settling": 0.5, "rest_state": 0.6}
# Level-1 acceptance metric per question (see the module docstring).
LEVEL1_METRIC = {"tv_attention": "recall", "eating": "recall", "food_prep": "recall",
                 "settling": "recall", "rest_state": "precision_recall"}
# Charades classes that put a television in the scene.
TV_CLASS_IDS = frozenset({"c131", "c132"})
# Camera naming for level 1: question rooms first, then the scene when it is
# one of the rooms the questions know, else a generic name.
TV_ROW_CAMERA = "living_room"
FOOD_PREP_ROW_CAMERA = "kitchen"
SCENE_CAMERAS = {"living_room": "living_room", "kitchen": "kitchen", "dining_room": "dining_room"}
FALLBACK_CAMERA = "other_room"
CAMERA_POLICY = {
    "rule": "question_room",
    "tv_row": TV_ROW_CAMERA,
    "food_prep_row": FOOD_PREP_ROW_CAMERA,
    "scene_map": dict(SCENE_CAMERAS),
    "fallback": FALLBACK_CAMERA,
    "order": ["tv_row", "food_prep_row", "scene", "fallback"],
}
# docs.typesafe.ai/models, read 2026-09-17: jev-1.13.0 is charged per input
# token at 42 USD per billion tokens; output tokens are free.
PRICE_USD_PER_INPUT_TOKEN = 0.042 / 1_000_000
DRY_RUN_CHARS_PER_TOKEN = 4
PINNED_MODEL_RE = re.compile(r"^jev-\d+\.\d+\.\d+$")
MAX_RETRIES = 3
RETRY_BUDGET_S = 60.0
CALL_TIMEOUT_S = 30.0
MAX_CONSECUTIVE_ERRORS = 3
MAX_RECORDED_FAILURES = 50
DIR_MODE = 0o700
FILE_MODE = 0o600

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_REFUSED = 3
EXIT_ABORTED = 4


# -- errors and transport -----------------------------------------------------
class EvalError(RuntimeError):
    """A refusal or precondition failure; the message is safe to print."""


class TransportError(RuntimeError):
    """One call failed after the SDK's own retries."""


class AuthenticationStop(TransportError):
    """The API rejected the key: the run stops on the first one."""


@dataclass(frozen=True)
class Answered:
    """One answered request in the HTTP/JSON answer shape ``reduce`` accepts."""

    model: str
    answers: dict
    input_tokens: int | None = None
    output_tokens: int | None = None
    request_id: str | None = None


class Transport(Protocol):
    """The only seam that touches the network: ask one packet, get answers."""

    def ask(self, state: Mapping[str, Any], questions: Mapping[str, Any], model: str) -> Answered: ...


def sdk_version() -> str | None:
    """Installed SDK version from package metadata without importing it."""
    try:
        from importlib.metadata import PackageNotFoundError, version
        return version(SDK_DISTRIBUTION)
    except PackageNotFoundError:
        return None
    except Exception:  # pragma: no cover - metadata oddities
        return None


class SdkTransport:
    """Real transport over ``typesafe_sdk.TypeSafeClient``; built only for --execute.

    The API key is handed to the client from the environment mapping and is
    never stored on this object, logged or included in any receipt. Score
    answers come back with integer level keys; they are re-keyed as strings
    to the HTTP shape ``reduce`` documents.
    """

    def __init__(self, api_key: str, model: str):
        import typesafe_sdk  # the only import of the network client

        self._sdk = typesafe_sdk
        self._client = typesafe_sdk.TypeSafeClient(
            api_key=api_key, model=model,
            retry=typesafe_sdk.RetryPolicy(max_retries=MAX_RETRIES, timeout=RETRY_BUDGET_S),
            timeout=CALL_TIMEOUT_S)

    def ask(self, state: Mapping[str, Any], questions: Mapping[str, Any], model: str) -> Answered:
        sdk = self._sdk
        try:
            response = self._client.system_one(dict(state), questions, model=model)
        except (sdk.TypeSafeAuthenticationError, sdk.TypeSafePermissionDeniedError) as exc:
            # 401 and 403 both mean the key will not work: stop before the
            # consecutive-error rule spends more tickets.
            raise AuthenticationStop(f"authentication failed (status {exc.status})") from exc
        except sdk.TypeSafeAPIError as exc:
            raise TransportError(f"api error status {exc.status} request {exc.request_id}") from exc
        except sdk.TypeSafeError as exc:
            raise TransportError(type(exc).__name__) from exc
        answers: dict = {}
        for qid, ans in response.answers.items():
            if isinstance(ans, sdk.ScoreAnswer):
                answers[qid] = {
                    "type": "score", "score": ans.score, "confidence": ans.confidence,
                    "legend": {str(k): v for k, v in ans.legend.items()},
                    "probabilities": {str(k): float(v) for k, v in ans.probabilities.items()},
                }
            elif isinstance(ans, sdk.NoulAnswer):
                answers[qid] = {"type": "noul", "noul": float(ans.noul)}
        usage = response.usage
        return Answered(response.model, answers, usage.input_tokens, usage.output_tokens,
                        getattr(response, "request_id", None))

    def close(self) -> None:
        self._client.close()


def default_transport_factory(model: str, env: Mapping[str, str]) -> Transport:
    """Build the SDK transport; refuses when the SDK is missing or the key is absent."""
    if sdk_version() is None:
        raise EvalError(f"{SDK_DISTRIBUTION}=={SDK_PIN} is not installed; --execute needs it")
    key = env.get(API_KEY_ENV, "")
    if not key.strip():
        raise EvalError(f"{API_KEY_ENV} is not set; --execute needs the API key in that variable")
    return SdkTransport(key, model)


# -- rows ---------------------------------------------------------------------
@dataclass(frozen=True)
class Row:
    """One public-dataset window, parsed and normalised."""

    row_id: str
    description: str
    camera: str
    tv_present: bool
    scene: str = ""
    camera_reason: str = "fallback"
    targets: dict = field(default_factory=dict)
    cutoffs: dict = field(default_factory=dict)


_TRUE_WORDS = {"yes", "true", "pos", "positive", "1"}
_FALSE_WORDS = {"no", "false", "neg", "negative", "0"}
_UNOBSERVED_WORDS = {"", "unobserved", "null", "none", "na", "n/a", "unknown"}


def parse_target(value: Any) -> tuple[bool | None, int | None]:
    """A three-way target (True, False or None) plus an optional Score cutoff."""
    cutoff = None
    if isinstance(value, Mapping):
        cutoff = value.get("k", value.get("cutoff"))
        if cutoff is not None and (isinstance(cutoff, bool) or not isinstance(cutoff, int) or cutoff < 1):
            raise ValueError(f"bad cutoff {cutoff!r}")
        value = value.get("value", value.get("target"))
    if value is None or isinstance(value, bool):
        return value, cutoff
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value), cutoff
    if isinstance(value, str):
        word = value.strip().lower()
        if word in _TRUE_WORDS:
            return True, cutoff
        if word in _FALSE_WORDS:
            return False, cutoff
        if word in _UNOBSERVED_WORDS:
            return None, cutoff
    raise ValueError(f"bad target {value!r}")


def _scene_name(raw: Any) -> str:
    """The Charades scene as a lowercase identifier (never a household name)."""
    text = re.sub(r"[^a-z0-9]+", "_", str(raw or "").lower()).strip("_")
    return text[:packet_mod.NAME_CAP] or "unknown"


def camera_for_row(scene: str, tv_present: bool, targets: Mapping[str, Any]) -> tuple[str, str]:
    """(camera name, reason) under ``CAMERA_POLICY``: the question's room first."""
    if tv_present or targets.get("tv_attention") is True:
        return TV_ROW_CAMERA, "tv_row"
    if targets.get("food_prep") is True:
        return FOOD_PREP_ROW_CAMERA, "food_prep_row"
    for prefix, camera in SCENE_CAMERAS.items():
        if scene == prefix or scene.startswith(prefix + "_"):
            return camera, "scene"
    return FALLBACK_CAMERA, "fallback"


def _class_tokens(raw: Any) -> list[str]:
    """Class mentions as strings: a CSV actions string, a list of names or
    ids, or a list of ``{class_id, class_name}`` objects."""
    if isinstance(raw, str):
        return [part.strip() for part in raw.split(";") if part.strip()]
    if isinstance(raw, (list, tuple)):
        out = []
        for part in raw:
            if isinstance(part, Mapping):
                out.append(" ".join(str(part.get(k, "")) for k in ("class_id", "class_name") if part.get(k)))
            else:
                out.append(str(part))
        return out
    return []


def television_present(row: Mapping[str, Any]) -> bool:
    """True when the row says so or a television class appears in its classes."""
    if isinstance(row.get("tv_present"), bool):
        return row["tv_present"]
    raw = row.get("classes", row.get("native_classes", row.get("actions")))
    for token in _class_tokens(raw):
        first = token.split()[0].lower() if token.split() else ""
        if first in TV_CLASS_IDS or "television" in token.lower():
            return True
    return False


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def split_claims(text: str, limit: int = packet_mod.TEXT_CAP, max_claims: int = packet_mod.MAX_CLAIMS) -> list[str]:
    """Pack sentences into at most ``max_claims`` claims of at most ``limit`` characters.

    Used only with ``--split-long``; the default keeps one claim and lets the
    packet builder cap it. A single sentence longer than ``limit`` is cut.
    """
    claims: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(" ".join(text.split())):
        if not sentence:
            continue
        candidate = f"{current} {sentence}".strip()
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            claims.append(current)
        current = sentence[:limit]
    if current:
        claims.append(current)
    return claims[:max_claims]


def parse_row(raw: Mapping[str, Any], line_no: int) -> Row:
    """Normalise one JSON row; ValueError names the line, never the text."""
    description = raw.get("description", raw.get("text", raw.get("script")))
    if not isinstance(description, str) or not description.strip():
        raise ValueError(f"line {line_no}: no description")
    row_id = raw.get("row_id", raw.get("id", raw.get("window_id")))
    targets: dict = {}
    cutoffs: dict = {}
    raw_targets = raw.get("targets") or {}
    if not isinstance(raw_targets, Mapping):
        raise ValueError(f"line {line_no}: targets must be a mapping")
    for qid, value in raw_targets.items():
        if qid not in questions_mod.QUESTION_IDS:
            raise ValueError(f"line {line_no}: unknown question id in targets")
        try:
            target, cutoff = parse_target(value)
        except ValueError as exc:
            raise ValueError(f"line {line_no}: {qid}: {exc}") from exc
        targets[qid] = target
        if cutoff is not None:
            cutoffs[qid] = cutoff
    for qid in questions_mod.QUESTION_IDS:
        targets.setdefault(qid, None)
    scene = _scene_name(raw.get("scene", raw.get("camera")))
    tv_present = television_present(raw)
    camera, reason = camera_for_row(scene, tv_present, targets)
    return Row(str(row_id) if row_id is not None else "", description, camera, tv_present,
               scene, reason, targets, cutoffs)


def load_rows(path: Path) -> list[Row]:
    """Read a rows.jsonl file; blank lines are skipped."""
    rows = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except ValueError as exc:
                raise ValueError(f"line {line_no}: not JSON") from exc
            if not isinstance(raw, Mapping):
                raise ValueError(f"line {line_no}: not an object")
            rows.append(parse_row(raw, line_no))
    return rows


# -- packets ------------------------------------------------------------------
def description_truncated(text: str) -> bool:
    """True when the packet builder's text cap will cut this description."""
    return len(packet_mod.cap_text(text, 10 * packet_mod.TEXT_CAP)) > packet_mod.TEXT_CAP


def build_row_packet(row: Row, split_long: bool = False) -> dict:
    """The packet for one row: one camera, one claim, a tv row when present.

    The packet's media vocabulary has no ``on``; ``playing`` is the state a
    switched-on television reports. Zones and people are left empty so the
    model judges the claim alone; an empty field is not evidence. With
    ``split_long`` a description over the text cap becomes several claims.
    """
    claims = split_claims(row.description) if split_long and description_truncated(row.description) \
        else [row.description]
    camera = {"coverage_status": "fresh", "coverage_age_s": 0, "zones": {}, "people": [],
              "claims": claims, "person_adjacent": []}
    media = [{"role": "tv", "state": "playing", "source_kind": "unknown", "age_s": 0}] if row.tv_present else []
    return packet_mod.build_packet({row.camera: camera}, media, None)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def question_digests(questions: Mapping[str, Mapping[str, Any]]) -> dict:
    """Per-question digests plus one over the whole request wording."""
    per = {qid: sha256_text(canonical(body)) for qid, body in questions.items()}
    return {"per_question": per, "request": sha256_text(canonical(questions))}


def payload_bytes(packet: Mapping[str, Any], questions: Mapping[str, Any], model: str) -> int:
    return len(canonical({"state": packet, "model": model, "questions": questions}).encode("ascii"))


# -- cache --------------------------------------------------------------------
def cache_key(row_digest: str, question_digest: str, model: str) -> str:
    return sha256_text("|".join((row_digest, question_digest, model)))


class AnswerCache:
    """Answers on disk keyed by (row digest, question digest, model id)."""

    def __init__(self, directory: Path):
        self.directory = directory

    def path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str) -> dict | None:
        p = self.path(key)
        if not p.is_file():
            return None
        try:
            entry = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            return None
        return entry if isinstance(entry, Mapping) and isinstance(entry.get("answers"), Mapping) else None

    def put(self, key: str, entry: Mapping[str, Any]) -> None:
        ensure_private_dir(self.directory)
        write_private(self.path(key), canonical(entry) + "\n")


# -- private files ------------------------------------------------------------
def ensure_private_dir(path: Path) -> None:
    """Create ``path`` (and parents) with mode 0700; tighten an existing one."""
    path.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
    os.chmod(path, DIR_MODE)


def write_private(path: Path, text: str) -> None:
    """Write ``text`` to a fresh 0600 file, replacing any previous content."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(path, FILE_MODE)


def append_private(path: Path, line: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, FILE_MODE)
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        fh.write(line)


# -- scoring ------------------------------------------------------------------
def belief_probability(belief: Any, cutoff: int | None) -> float:
    """``P(level >= cutoff)`` for a Score belief, ``P(yes)`` for a Noul belief."""
    if isinstance(belief, reduce_mod.NoulBelief):
        return belief.p_yes
    if cutoff is None or cutoff < 1 or cutoff >= len(belief.p_at_least):
        raise ValueError(f"cutoff {cutoff!r} outside 1..{len(belief.p_at_least) - 1}")
    return belief.p_ge(cutoff)


def row_probabilities(answers: Mapping[str, Any], cutoffs: Mapping[str, int]) -> dict:
    """Reduce one answer set and take the compared probability per question."""
    beliefs = reduce_mod.reduce_answers(answers)
    out = {}
    for qid, belief in beliefs.items():
        out[qid] = belief_probability(belief, cutoffs.get(qid))
    return out


def effective_cutoffs(row: Row, base: Mapping[str, int]) -> dict:
    return {**base, **row.cutoffs}


def _cell(observed: list[tuple[bool, float]], threshold: float) -> dict:
    """Counts and rates of one (question, threshold): agreement, recall, precision."""
    tp = sum(1 for t, p in observed if t and p >= threshold)
    tn = sum(1 for t, p in observed if not t and p < threshold)
    fp = sum(1 for t, p in observed if not t and p >= threshold)
    fn = sum(1 for t, p in observed if t and p < threshold)
    return {
        "agreement": (tp + tn) / len(observed) if observed else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "precision": tp / (tp + fp) if tp + fp else None,
        "tp": tp, "tn": tn, "fp": fp, "fn": fn,
    }


def score_table(scored: list[Mapping[str, Any]], thresholds: tuple[float, ...],
                apriori: Mapping[str, float] = APRIORI_THRESHOLDS) -> dict:
    """The sensitivity table: per question, per threshold, agreement, recall, precision and counts.

    ``scored`` items carry ``targets`` (question id -> True/False/None),
    ``p`` (question id -> probability) and optionally ``scene`` (the row's
    Charades scene). A None target is unobserved and skipped for that
    question. Agreement is (tp + tn) / observed, recall tp / positives,
    precision tp / (tp + fp); each is None when its denominator is zero.
    ``metric`` names the level-1 acceptance number (``LEVEL1_METRIC``) and
    ``by_scene`` breaks the a-priori threshold down per scene.
    """
    table: dict = {}
    for qid in questions_mod.QUESTION_IDS:
        observed = [(item["targets"].get(qid), item["p"][qid]) for item in scored
                    if item["targets"].get(qid) is not None and qid in item["p"]]
        unobserved = sum(1 for item in scored if item["targets"].get(qid) is None)
        by_threshold = {f"{threshold:.2f}": _cell(observed, threshold) for threshold in thresholds}
        prior = apriori.get(qid)
        by_scene: dict = {}
        if prior is not None:
            per_scene: dict[str, list] = {}
            for item in scored:
                if item["targets"].get(qid) is None or qid not in item["p"]:
                    continue
                per_scene.setdefault(item.get("scene") or "unknown", []).append((item["targets"][qid], item["p"][qid]))
            for scene in sorted(per_scene):
                by_scene[scene] = {"observed": len(per_scene[scene]),
                                   "positives": sum(1 for t, _ in per_scene[scene] if t),
                                   **_cell(per_scene[scene], prior)}
        table[qid] = {
            "metric": LEVEL1_METRIC.get(qid),
            "observed": len(observed),
            "positives": sum(1 for t, _ in observed if t),
            "unobserved": unobserved,
            "apriori_threshold": prior,
            "by_threshold": by_threshold,
            "by_scene": by_scene,
        }
    return table


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def render_scores(table: Mapping[str, Any], thresholds: tuple[float, ...], cutoffs: Mapping[str, int],
                  apriori: Mapping[str, float], header_lines: list[str]) -> str:
    """Markdown: a summary per question and one sensitivity block per question."""
    lines = ["# Public story eval, level 1", ""]
    lines.extend(header_lines)
    lines.append("")
    lines.append("Level-1 metric: recall at the a-priori threshold for the positive-only questions "
                 "(tv_attention, eating, food_prep, settling); precision and recall at the a-priori "
                 "threshold for rest_state, the one question with negatives. Agreement equals recall "
                 "where there are no negatives.")
    lines.append("")
    lines.append("| question | metric | compared | observed | positives | unobserved | a-priori "
                 "| recall@a-priori | precision@a-priori | agreement@a-priori | best | agreement@best |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for qid, entry in table.items():
        compared = "P(yes)" if questions_mod.question_by_id(qid).primitive == questions_mod.NOUL \
            else f"P(level >= {cutoffs.get(qid)})"
        prior = apriori.get(qid)
        prior_key = f"{prior:.2f}" if prior is not None else None
        prior_cell = entry["by_threshold"].get(prior_key, {}) if prior_key else {}
        best_key, best_agreement = None, None
        for key, cell in entry["by_threshold"].items():
            if cell["agreement"] is not None and (best_agreement is None or cell["agreement"] > best_agreement):
                best_key, best_agreement = key, cell["agreement"]
        lines.append(f"| {qid} | {entry.get('metric') or 'n/a'} | {compared} | {entry['observed']} | {entry['positives']} "
                     f"| {entry['unobserved']} | {prior_key or 'n/a'} | {_fmt(prior_cell.get('recall'))} "
                     f"| {_fmt(prior_cell.get('precision'))} | {_fmt(prior_cell.get('agreement'))} "
                     f"| {best_key or 'n/a'} | {_fmt(best_agreement)} |")
    for qid, entry in table.items():
        lines.append("")
        lines.append(f"## {qid}")
        lines.append("")
        lines.append("| threshold | agreement | recall | precision | tp | tn | fp | fn |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for key, cell in entry["by_threshold"].items():
            lines.append(f"| {key} | {_fmt(cell['agreement'])} | {_fmt(cell.get('recall'))} | {_fmt(cell.get('precision'))} "
                         f"| {cell['tp']} | {cell['tn']} | {cell['fp']} | {cell['fn']} |")
        by_scene = entry.get("by_scene") or {}
        if by_scene:
            lines.append("")
            lines.append(f"Per scene at the a-priori threshold ({entry.get('apriori_threshold')}); "
                         "the packet camera is the question's room, the scene is where Charades filmed it:")
            lines.append("")
            lines.append("| scene | observed | positives | recall | precision | tp | tn | fp | fn |")
            lines.append("|---|---|---|---|---|---|---|---|---|")
            for scene, cell in by_scene.items():
                lines.append(f"| {scene} | {cell['observed']} | {cell['positives']} | {_fmt(cell['recall'])} "
                             f"| {_fmt(cell['precision'])} | {cell['tp']} | {cell['tn']} | {cell['fp']} | {cell['fn']} |")
    lines.append("")
    return "\n".join(lines)


# -- arguments ----------------------------------------------------------------
def parse_thresholds(text: str) -> tuple[float, ...]:
    values = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        value = float(part)
        if not 0.0 < value < 1.0:
            raise argparse.ArgumentTypeError(f"threshold {part} outside (0, 1)")
        values.append(value)
    if not values:
        raise argparse.ArgumentTypeError("no thresholds")
    return tuple(sorted(set(values)))


def parse_cutoffs(text: str) -> dict:
    out = dict(DEFAULT_CUTOFFS)
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        qid, _, level = part.partition("=")
        if qid not in DEFAULT_CUTOFFS or not level.isdigit() or int(level) < 1:
            raise argparse.ArgumentTypeError(f"bad cutoff {part}")
        out[qid] = int(level)
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--level", type=int, default=LEVEL, help="ladder level; only 1 (text) is implemented")
    p.add_argument("--rows", required=True, type=Path, help="rows.jsonl from public_windows")
    p.add_argument("--record", required=True, type=Path, help="egress record json (see EGRESS-RECORD.md)")
    p.add_argument("--roster", type=Path, default=None, help="household roster, mode 0600 (optional)")
    p.add_argument("--toggle", type=Path, default=None, help="kill-switch mirror file (required to execute)")
    p.add_argument("--out", required=True, type=Path, help="receipt directory under the eval root")
    p.add_argument("--execute", action="store_true", help="make the calls; default is a dry run")
    p.add_argument("--max-calls", type=int, default=None, help="hard cap on calls (required with --execute)")
    p.add_argument("--model", default=None, help="pinned model id; must equal the record's model_id")
    p.add_argument("--thresholds", type=parse_thresholds, default=DEFAULT_THRESHOLDS,
                   help="comma-separated thresholds for the sensitivity table")
    p.add_argument("--cutoffs", type=parse_cutoffs, default=dict(DEFAULT_CUTOFFS),
                   help="Score cutoffs, e.g. tv_attention=2,food_prep=2,settling=1,rest_state=2")
    p.add_argument("--cache", type=Path, default=None, help="answer cache directory (default <eval root>/cache)")
    p.add_argument("--split-long", action="store_true",
                   help="split a description over the 240-character cap into several claims instead of truncating")
    p.add_argument("--eval-root", type=Path, default=EVAL_ROOT, help=argparse.SUPPRESS)
    p.add_argument("--username", default=None, help=argparse.SUPPRESS)
    return p


# -- the run ------------------------------------------------------------------
def _resolve_model(requested: str | None, record: Mapping[str, Any] | None, execute: bool) -> str:
    recorded = record.get("model_id") if isinstance(record, Mapping) else None
    recorded = recorded if isinstance(recorded, str) and PINNED_MODEL_RE.match(recorded) else None
    model = requested or recorded or "unpinned"
    if execute:
        if not PINNED_MODEL_RE.match(model):
            raise EvalError("--execute needs a pinned model id (for example jev-1.13.0), never an alias")
        if recorded is None:
            raise EvalError("the egress record does not pin a model_id; sign it with one before executing")
        if model != recorded:
            raise EvalError("--model differs from the record's model_id; the signed record wins")
    return model


def _check_below(path: Path, root: Path, flag: str) -> None:
    """Refuse a path that is not strictly below the eval root (symlinks resolved)."""
    if root.resolve() not in path.resolve().parents:
        raise EvalError(f"{flag} must be a directory below {root}")


def _check_out(out: Path, root: Path, execute: bool) -> None:
    _check_below(out, root, "--out")
    receipt = out / "run.json"
    if receipt.exists():
        try:
            previous = json.loads(receipt.read_text(encoding="utf-8"))
        except ValueError:
            previous = {}
        if previous.get("executed") is True:
            raise EvalError(f"{receipt} holds an executed receipt; pick a fresh --out (the cache makes a rerun free)")
        if execute:
            raise EvalError(f"{receipt} exists; --execute never overwrites a receipt, pick a fresh --out")


def _wait_for_rate(gate: egress.EgressGate, sleeper: Callable[[float], None]) -> egress.Decision:
    """Request a ticket; on a rate denial wait for the window and try again."""
    pause = egress.RATE_WINDOW_S / gate.max_calls_per_minute + 0.5
    for _ in range(gate.max_calls_per_minute + 1):
        decision = gate.request(SCOPE)
        if decision.allowed or not decision.reason.startswith("rate_"):
            return decision
        sleeper(pause)
    return decision


def run(args: argparse.Namespace, env: Mapping[str, str], transport_factory: Callable[..., Transport],
        clock: Callable[[], float], sleeper: Callable[[float], None]) -> int:
    """Execute or dry-run one evaluation; returns the process exit code."""
    started = time.monotonic()
    if args.level != LEVEL:
        raise EvalError(f"level {args.level} is not implemented; this runner is level {LEVEL} (text only)")
    execute = bool(args.execute)
    if execute and (args.max_calls is None or args.max_calls < 1):
        raise EvalError("--execute needs --max-calls N (N >= 1)")
    _check_out(args.out, args.eval_root, execute)
    cache_dir = args.cache or (args.eval_root / "cache")
    _check_below(cache_dir, args.eval_root, "--cache")
    username = args.username or getpass.getuser()
    if args.roster is not None:
        leak_guard.load_roster(args.roster)  # mode 0600 enforced; fail early
    rows = load_rows(args.rows)
    record, record_status = egress.load_record(args.record)
    model = _resolve_model(args.model, record, execute)
    questions = questions_mod.questions_as_dicts()
    digests = question_digests(questions)
    thresholds = tuple(args.thresholds)
    cache = AnswerCache(cache_dir)

    ensure_private_dir(args.out)
    packets_path = args.out / "packets.jsonl"
    write_private(packets_path, "")
    built: list[dict] = []
    failures: list[dict] = []
    truncated = 0
    for index, row in enumerate(rows):
        cut = description_truncated(row.description)
        truncated += int(cut and not args.split_long)
        try:
            pkt = build_row_packet(row, args.split_long)
            leak_guard.assert_clean(pkt, username, roster_path=args.roster)
        except (packet_mod.PacketError, leak_guard.LeakError) as exc:
            if len(failures) < MAX_RECORDED_FAILURES:
                failures.append({"row_index": index, "row_id": row.row_id, "error": type(exc).__name__, "detail": str(exc)})
            continue
        digest = packet_mod.content_signature(pkt)
        row_id = row.row_id or digest[:16]
        key = cache_key(digest, digests["request"], model)
        built.append({"index": index, "row": row, "row_id": row_id, "packet": pkt, "digest": digest,
                      "key": key, "bytes": payload_bytes(pkt, questions, model),
                      "cutoffs": effective_cutoffs(row, args.cutoffs)})
        append_private(packets_path, canonical({"row_id": row_id, "row_digest": digest, "truncated": cut and not args.split_long,
                                                "scene": row.scene, "camera": row.camera, "camera_reason": row.camera_reason,
                                                "claims": len(pkt["cameras"][row.camera]["claims"]), "packet": pkt}) + "\n")

    toggle_reader = egress.file_toggle_reader(args.toggle, clock) if args.toggle else (lambda: None)
    journal_path = args.out / "egress-journal.jsonl"
    if not journal_path.exists():
        write_private(journal_path, "")  # the gate appends with the process umask; pre-create at 0600
    gate = egress.EgressGate(args.record, toggle_reader, journal_path, env=env, clock=clock)

    counts = {
        "rows": len(rows), "packets_built": len(built), "packets_failed": len(rows) - len(built),
        "descriptions_truncated": truncated, "tv_rows": sum(1 for item in built if item["row"].tv_present),
        "cache_hits": 0, "would_be_calls": 0, "calls_made": 0, "calls_failed": 0, "not_asked": 0,
        "gate_denied": 0, "bytes_would_send": 0, "input_tokens": 0, "output_tokens": 0,
    }
    camera_counts = {"by_camera": {}, "by_reason": {}}
    for item in built:
        row = item["row"]
        camera_counts["by_camera"][row.camera] = camera_counts["by_camera"].get(row.camera, 0) + 1
        camera_counts["by_reason"][row.camera_reason] = camera_counts["by_reason"].get(row.camera_reason, 0) + 1
    cached_entries = {}
    for item in built:
        entry = cache.get(item["key"])
        if entry is not None:
            cached_entries[item["key"]] = entry
            counts["cache_hits"] += 1
        else:
            counts["would_be_calls"] += 1
            counts["bytes_would_send"] += item["bytes"]

    answered: dict[str, Answered] = {}
    status = "complete"
    stop_reason = None
    call_durations: list[float] = []
    gate_summary: dict

    if not execute:
        decision = gate.request(SCOPE)
        gate.release(decision, "dry_run")
        gate_summary = {"preflight": decision.as_dict()}
        estimated_tokens = counts["bytes_would_send"] // DRY_RUN_CHARS_PER_TOKEN
        spend = {"input_tokens_estimated": estimated_tokens,
                 "usd_estimated": round(estimated_tokens * PRICE_USD_PER_INPUT_TOKEN, 6),
                 "basis": f"bytes / {DRY_RUN_CHARS_PER_TOKEN} chars per token at {PRICE_USD_PER_INPUT_TOKEN * 1e6:.3f} USD per Mtok input"}
    else:
        preflight = gate.request(SCOPE)
        if not preflight.allowed:
            gate.release(preflight, "preflight")
            raise EvalError(f"egress gate denied scope {SCOPE}: {preflight.reason}")
        gate.release(preflight, "preflight")
        transport = transport_factory(model, env)
        ask_questions = questions_mod.questions_as_sdk() if isinstance(transport, SdkTransport) else questions
        gate_summary = {"preflight": preflight.as_dict(), "allowed": 0, "denied": []}
        consecutive_errors = 0
        answers_path = args.out / "answers.jsonl"
        write_private(answers_path, "")
        try:
            for item in built:
                entry = cached_entries.get(item["key"])
                if entry is not None:
                    answered[item["key"]] = Answered(entry.get("model", model), dict(entry["answers"]),
                                                     entry.get("input_tokens"), entry.get("output_tokens"), None)
                    append_private(answers_path, canonical({
                        "row_id": item["row_id"], "row_digest": item["digest"], "question_digest": digests["request"],
                        "model": entry.get("model", model), "cached": True, "answers": entry["answers"]}) + "\n")
                    continue
                if counts["calls_made"] >= args.max_calls:
                    counts["not_asked"] += 1
                    continue
                decision = _wait_for_rate(gate, sleeper)
                if not decision.allowed:
                    counts["gate_denied"] += 1
                    gate_summary["denied"].append(decision.reason)
                    status, stop_reason = "aborted", f"gate denied: {decision.reason}"
                    break
                gate_summary["allowed"] += 1
                counts["calls_made"] += 1
                call_started = time.monotonic()
                try:
                    result = transport.ask(item["packet"], ask_questions, model)
                except AuthenticationStop as exc:
                    gate.release(decision, "auth_error")
                    status, stop_reason = "aborted", str(exc)
                    break
                except TransportError as exc:
                    gate.release(decision, "error", error=type(exc).__name__)
                    counts["calls_failed"] += 1
                    consecutive_errors += 1
                    if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                        status, stop_reason = "aborted", f"{consecutive_errors} consecutive call errors"
                        break
                    continue
                except Exception as exc:  # a runner-side bug: free the ticket, keep the receipt
                    gate.release(decision, "error", error=type(exc).__name__)
                    counts["calls_failed"] += 1
                    status, stop_reason = "aborted", f"unexpected {type(exc).__name__} during a call"
                    break
                call_durations.append(time.monotonic() - call_started)
                gate.release(decision, "done", input_tokens=result.input_tokens)
                consecutive_errors = 0
                counts["input_tokens"] += result.input_tokens or 0
                counts["output_tokens"] += result.output_tokens or 0
                answered[item["key"]] = result
                cache.put(item["key"], {"model": result.model, "answers": result.answers,
                                        "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
                                        "row_digest": item["digest"], "question_digest": digests["request"]})
                append_private(answers_path, canonical({
                    "row_id": item["row_id"], "row_digest": item["digest"], "question_digest": digests["request"],
                    "model": result.model, "cached": False, "request_id": result.request_id,
                    "usage": {"input_tokens": result.input_tokens, "output_tokens": result.output_tokens},
                    "answers": result.answers}) + "\n")
        finally:
            close = getattr(transport, "close", None)
            if callable(close):
                close()
        spend = {"input_tokens": counts["input_tokens"],
                 "usd_estimated": round(counts["input_tokens"] * PRICE_USD_PER_INPUT_TOKEN, 6),
                 "basis": f"reported input tokens at {PRICE_USD_PER_INPUT_TOKEN * 1e6:.3f} USD per Mtok; output is free"}

    scored: list[dict] = []
    reduce_failures: list[dict] = []
    for item in built:
        result = answered.get(item["key"])
        if result is None:
            continue
        try:
            probabilities = row_probabilities(result.answers, item["cutoffs"])
        except (reduce_mod.AnswerError, ValueError) as exc:
            if len(reduce_failures) < MAX_RECORDED_FAILURES:
                reduce_failures.append({"row_id": item["row_id"], "detail": str(exc)})
            continue
        scored.append({"row_id": item["row_id"], "scene": item["row"].scene, "targets": item["row"].targets, "p": probabilities})
    table = score_table(scored, thresholds, APRIORI_THRESHOLDS) if scored else None
    models_seen = sorted({r.model for r in answered.values()})

    if table is not None:
        header = [f"executed: {execute}", f"model: {model} (answered by: {', '.join(models_seen) or 'n/a'})",
                  f"rows scored: {len(scored)} of {len(rows)}", f"cutoffs: {canonical(args.cutoffs)}"]
        write_private(args.out / "scores.md", render_scores(table, thresholds, args.cutoffs, APRIORI_THRESHOLDS, header))

    receipt = {
        "schema": "public-story-eval-run/v1",
        "level": LEVEL,
        "executed": execute,
        "status": status,
        "stop_reason": stop_reason,
        "scope": SCOPE,
        "model_id": model,
        "models_answering": models_seen,
        "sdk_version": sdk_version(),
        "sdk_pin": SDK_PIN,
        "record_status": record_status,
        "record_enabled": bool(record.get("enabled")) if isinstance(record, Mapping) else None,
        "roster_used": args.roster is not None,
        "question_ids": list(questions_mod.QUESTION_IDS),
        "question_digests": digests,
        "thresholds": list(thresholds),
        "cutoffs": dict(args.cutoffs),
        "split_long": bool(args.split_long),
        "apriori_thresholds": dict(APRIORI_THRESHOLDS),
        "level1_metric": dict(LEVEL1_METRIC),
        "camera_policy": {**CAMERA_POLICY, **camera_counts},
        "call_policy": {"sdk_max_retries": MAX_RETRIES, "retry_budget_s": RETRY_BUDGET_S,
                        "call_timeout_s": CALL_TIMEOUT_S, "max_consecutive_errors": MAX_CONSECUTIVE_ERRORS,
                        "note": "one gate ticket is one SDK invocation of up to 1 + sdk_max_retries HTTP requests; "
                                "401 and 403 stop the run at once"},
        "max_calls": args.max_calls,
        "counts": counts,
        "rows_scored": len(scored),
        "spend_estimate": spend,
        "gate": gate_summary,
        "packet_failures": failures,
        "reduce_failures": reduce_failures,
        "table": table,
        "durations_s": {
            "total": round(time.monotonic() - started, 3),
            "call_mean": round(sum(call_durations) / len(call_durations), 3) if call_durations else None,
            "call_max": round(max(call_durations), 3) if call_durations else None,
        },
        "files": {"packets": packets_path.name, "answers": "answers.jsonl" if execute else None,
                  "scores": "scores.md" if table is not None else None, "journal": "egress-journal.jsonl",
                  "cache_dir": str(cache.directory)},
    }
    write_private(args.out / "run.json", json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    return EXIT_OK if status == "complete" else EXIT_ABORTED


def main(argv: list[str] | None = None, *, env: Mapping[str, str] | None = None,
         transport_factory: Callable[..., Transport] = default_transport_factory,
         clock: Callable[[], float] = time.time, sleeper: Callable[[float], None] = time.sleep) -> int:
    """CLI entry point; ``env`` and the factories are injectable for tests."""
    args = build_parser().parse_args(argv)
    try:
        code = run(args, os.environ if env is None else env, transport_factory, clock, sleeper)
    except EvalError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except (ValueError, OSError, leak_guard.RosterError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    receipt = args.out / "run.json"
    print(f"{'executed' if args.execute else 'dry run'}: receipt {receipt}")
    return code


if __name__ == "__main__":
    sys.exit(main())
