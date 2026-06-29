#!/usr/bin/env python3
"""analyze-routing.py — joins the HAOS routing log (over SSH) with the
local SSE corpus JSONL, detects decline patterns, surfaces classifier
gaps, emits a markdown report.

Usage:

    # default: pull routing log over SSH, read local corpus, write report
    python3 analyze-routing.py

    # use already-downloaded routing log (offline mode)
    python3 analyze-routing.py --routing-log ./routing.log

    # optional: send flagged-suspicious turns to gpt-4o-mini for second-
    # opinion decline judgment (~$0.0001/turn)
    python3 analyze-routing.py --llm-judge

    # dry corpus dump (just print the cleaned input texts; no analysis)
    python3 analyze-routing.py --candidates-only

Sources:
    /config/external_routing.log   (HAOS, authoritative for ALL turns
                                    incl. external — pulled via SSH)
    routing-corpus.jsonl           (workstation, the SSE tap's output;
                                    rich detail for LOCAL turns only)

Output:
    routing-report-<ts>.md         in the same dir as this script
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


HERE = Path(__file__).resolve().parent
DEFAULT_CORPUS = HERE / "routing-corpus.jsonl"
DEFAULT_SSH    = "root@homeassistant.local"
DEFAULT_PORT   = "22222"
DEFAULT_REMOTE_LOG = "/config/external_routing.log"

MIN_TURNS    = 30   # below this, gap analysis is statistically meaningless
MIN_DECLINES = 5    # below this, no signal to optimize against


# ─────────────────────────────────────────────────────────────────────
# Decline regex — broader than the initial naive set. Captures common
# refusal/decline phrasings. Two passes: this regex, then optional
# LLM judgement on flagged turns.
# ─────────────────────────────────────────────────────────────────────
DECLINE_PATTERNS = re.compile(
    r"\b("
    r"don't (know|have)|"
    r"do not (know|have)|"
    r"can't (help|explain|do|answer|provide|tell you)|"
    r"cannot (help|explain|do|answer|provide|tell you)|"
    r"not able|"
    r"unable to|"
    r"outside (my|the) (scope|purview|expertise)|"
    r"not designed (to|for)|"
    r"i'm (sorry|not (able|sure|aware|familiar))|"
    r"i can(?:not|'t)|"
    r"i do not have|"
    r"that's not (something|in)|"
    r"i'm afraid|"
    r"beyond (my|the) scope|"
    r"i lack (the|access|that)|"
    r"i'm only (designed|able)"
    r")\b",
    re.IGNORECASE,
)


def _pull_remote_log(ssh_host: str, ssh_port: str, remote_path: str) -> str:
    """Pull the routing log from HAOS via SSH. Returns the text contents.
    Empty string if the file doesn't exist remotely or SSH fails."""
    try:
        result = subprocess.run(
            ["ssh", "-p", ssh_port, ssh_host, "cat", remote_path],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if result.returncode != 0:
            print(f"WARN: ssh cat returned {result.returncode}: {result.stderr.strip()[:200]}",
                  file=sys.stderr)
            return ""
        return result.stdout
    except subprocess.TimeoutExpired:
        print("WARN: ssh timed out fetching routing log", file=sys.stderr)
        return ""
    except FileNotFoundError:
        print("WARN: ssh binary not found on PATH", file=sys.stderr)
        return ""


def _parse_jsonl(text: str) -> list[dict]:
    """Parse JSONL text. Skips malformed lines silently (the routing log
    is append-only and may have partial last lines from interrupted
    writes)."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _load_corpus(corpus_path: Path) -> list[dict]:
    if not corpus_path.exists():
        return []
    return _parse_jsonl(corpus_path.read_text(encoding="utf-8"))


def _join(routing_entries: list[dict], corpus_entries: list[dict]) -> list[dict]:
    """Join routing log entries with chat-tee corpus by conv_id (primary
    key). For routing entries without a corpus match, the chat-tee data
    is left as None — they're still usable for decline analysis since
    the routing log has its own local_reply_snippet."""
    by_conv = defaultdict(list)
    for entry in corpus_entries:
        cid = entry.get("conversation_id")
        if cid:
            by_conv[cid].append(entry)

    joined = []
    # The routing log writes TWO entries per turn (decision + final outcome).
    # We want only the final outcome (the second write), which has the
    # local_reply_snippet filled in. Dedupe by (conv_id, text) and keep
    # the entry with the longest local_reply_chars or the external dispatch.
    by_key = {}
    for r in routing_entries:
        cid = r.get("conv_id") or "(no-cid)"
        txt = r.get("text") or ""
        key = (cid, txt)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = r
            continue
        # Prefer the entry with more information.
        if (r.get("local_reply_chars") or 0) > (existing.get("local_reply_chars") or 0):
            by_key[key] = r
        elif r.get("dispatched") == "external" and existing.get("dispatched") != "external":
            by_key[key] = r

    for r in by_key.values():
        cid = r.get("conv_id")
        joined.append({
            **r,
            "chat_tee": by_conv.get(cid, []),
        })
    return joined


def _is_decline(text: str) -> bool:
    return bool(text) and bool(DECLINE_PATTERNS.search(text))


def _is_short_response_to_question(local_chars: int | None, user_text_len: int | None) -> bool:
    """Suspicious-decline heuristic: very short response to a substantive
    question."""
    if local_chars is None or user_text_len is None:
        return False
    return local_chars < 30 and user_text_len > 20


def _suggest_rule(text: str) -> str:
    """Suggest which classifier rule would catch this text. Best-effort
    heuristic for the analyzer's "would have caught it" column."""
    s = text.lower()
    if re.search(r"\b(who|when|where|what year|what time)\b", s):
        return "add to GENERAL: who/when/where/what-year/what-time"
    if re.search(r"\b(tell me a|tell me about|describe|list|name|define)\b", s):
        return "add to GENERAL: tell-me-a/describe/list/name/define"
    if re.search(r"\b(is|are|was|were) [a-z]+ (a|an|the)\b", s):
        return "add to GENERAL: 'is X a Y' pattern"
    if re.search(r"\b(could you|would you|can you) (explain|tell)\b", s):
        return "GENERAL.explain already matches — re-check rule order"
    return "consider: add a new GENERAL pattern for this phrasing"


def _llm_judge_decline(text: str, reply: str, api_key: str) -> Optional[bool]:
    """Ask gpt-4o-mini: did this reply actually answer the question?
    Returns True if it's a decline, False if it answered, None on error.
    """
    import urllib.request
    import urllib.error
    body = json.dumps({
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": (
                "You judge whether an assistant response actually answered "
                "the user's question. Reply with exactly one word: ANSWERED "
                "(if the response gave real information addressing the "
                "question) or DECLINED (if it said it doesn't know, can't "
                "help, isn't able to, or gave a non-answer)."
            )},
            {"role": "user", "content": f"User asked: {text!r}\nAssistant replied: {reply!r}\nWhich is it?"},
        ],
        "max_tokens": 5,
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        data=body,
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        label = data["choices"][0]["message"]["content"].strip().upper()
        return label.startswith("DECL")
    except Exception:  # noqa: BLE001
        return None


def _summary(joined: list[dict]) -> dict:
    total = len(joined)
    by_intent = Counter(r.get("intent", "?") for r in joined)
    by_matched = Counter(r.get("matched", "?") for r in joined)
    by_dispatched = Counter(r.get("dispatched", "?") for r in joined)
    declines = []
    for r in joined:
        local = r.get("local_reply_snippet") or ""
        if r.get("dispatched") in ("local", "external→fallback"):
            if _is_decline(local) or _is_short_response_to_question(
                r.get("local_reply_chars"), r.get("text_len")
            ):
                declines.append(r)
    return {
        "total": total,
        "by_intent": dict(by_intent),
        "by_matched": dict(by_matched),
        "by_dispatched": dict(by_dispatched),
        "declines": declines,
    }


def _llm_judge_filter(declines: list[dict]) -> list[dict]:
    """Second-pass: use OpenAI to confirm each flagged decline. Filters
    out false positives (replies the regex called declines but that
    actually answered the question)."""
    key_path = HERE.parent / "ha-config" / "extended_openai_conversation_external_key"
    # Try the configured key on disk; fall back to env var.
    api_key = ""
    if key_path.exists():
        api_key = key_path.read_text(encoding="utf-8").strip()
    api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("WARN: --llm-judge requested but no OPENAI_API_KEY available", file=sys.stderr)
        return declines
    confirmed = []
    for r in declines:
        verdict = _llm_judge_decline(
            r.get("text") or "",
            r.get("local_reply_snippet") or "",
            api_key,
        )
        if verdict is True:
            confirmed.append(r)
        # verdict=False → filter out; verdict=None → keep (don't lose data on error)
        elif verdict is None:
            confirmed.append(r)
    return confirmed


def _build_report(joined: list[dict], summary: dict, llm_judged: bool) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    total = summary["total"]
    declines = summary["declines"]

    lines = []
    lines.append(f"# Routing reliability report — {ts}\n")
    lines.append(f"## Corpus size\n")
    lines.append(f"- Total turns: **{total}**")
    lines.append(f"- Intent split: {summary['by_intent']}")
    lines.append(f"- Matched rule: {summary['by_matched']}")
    lines.append(f"- Dispatched: {summary['by_dispatched']}\n")

    if total < MIN_TURNS or len(declines) < MIN_DECLINES:
        lines.append("## INSUFFICIENT CORPUS\n")
        lines.append(
            f"Need ≥{MIN_TURNS} total turns and ≥{MIN_DECLINES} declines. "
            f"Got {total} turns / {len(declines)} declines. "
            "Wait for more usage and re-run.\n"
        )
        return "\n".join(lines)

    lines.append(f"## Declines / misroutes ({len(declines)}{' — LLM-judged' if llm_judged else ' — regex only'})\n")
    lines.append("| Input | Matched rule | Local reply (truncated) | Suggested fix |")
    lines.append("|---|---|---|---|")
    for r in declines[:25]:
        text = (r.get("text") or "").replace("|", "\\|").replace("\n", " ")[:80]
        matched = r.get("matched") or "-"
        reply = (r.get("local_reply_snippet") or "").replace("|", "\\|").replace("\n", " ")[:80]
        suggested = _suggest_rule(r.get("text") or "")
        lines.append(f"| `{text}` | `{matched}` | {reply!r} | {suggested} |")
    if len(declines) > 25:
        lines.append(f"\n_(showing first 25 of {len(declines)})_\n")

    # False-external candidates: external calls where the input looks
    # home-related (would be a classifier over-reach).
    over_reach = []
    home_kw = re.compile(r"\b(light|lamp|kitchen|living room|office|dining|sonos|camera|frigate|home assistant|hvac|thermostat|garage)\b", re.IGNORECASE)
    for r in joined:
        if r.get("dispatched") == "external" and home_kw.search(r.get("text") or ""):
            over_reach.append(r)
    if over_reach:
        lines.append("\n## Classifier over-reach (external routed but looks home-related)\n")
        for r in over_reach[:10]:
            text = (r.get("text") or "")[:100]
            lines.append(f"- `{text}`  (matched={r.get('matched')})")

    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--routing-log", default=None,
                    help="path to a local routing.log (skip SSH fetch)")
    ap.add_argument("--corpus", default=str(DEFAULT_CORPUS),
                    help=f"workstation chat-tee JSONL (default {DEFAULT_CORPUS})")
    ap.add_argument("--ssh-host", default=DEFAULT_SSH)
    ap.add_argument("--ssh-port", default=DEFAULT_PORT)
    ap.add_argument("--remote-log", default=DEFAULT_REMOTE_LOG)
    ap.add_argument("--llm-judge", action="store_true",
                    help="confirm flagged declines via OpenAI gpt-4o-mini")
    ap.add_argument("--candidates-only", action="store_true",
                    help="just print the cleaned input texts; no analysis")
    ap.add_argument("--out", default=None,
                    help="output report path (default: routing-report-<ts>.md)")
    args = ap.parse_args()

    if args.routing_log:
        routing_text = Path(args.routing_log).read_text(encoding="utf-8")
    else:
        routing_text = _pull_remote_log(args.ssh_host, args.ssh_port, args.remote_log)
    routing_entries = _parse_jsonl(routing_text)
    corpus_entries = _load_corpus(Path(args.corpus))
    print(f"loaded {len(routing_entries)} routing entries, {len(corpus_entries)} corpus entries",
          file=sys.stderr)
    joined = _join(routing_entries, corpus_entries)
    print(f"joined → {len(joined)} unique turns", file=sys.stderr)

    if args.candidates_only:
        for r in joined:
            t = (r.get("text") or "").replace("\n", " ").strip()
            if t:
                print(t)
        return 0

    summary = _summary(joined)
    if args.llm_judge and summary["declines"]:
        summary["declines"] = _llm_judge_filter(summary["declines"])

    report = _build_report(joined, summary, args.llm_judge)
    out_path = Path(args.out) if args.out else (
        HERE / f"routing-report-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.md"
    )
    out_path.write_text(report, encoding="utf-8")
    print(f"wrote report → {out_path}", file=sys.stderr)
    # Also print a tiny stdout summary so the caller knows what happened.
    print(f"turns={summary['total']} declines={len(summary['declines'])} report={out_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
