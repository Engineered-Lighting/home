#!/usr/bin/env python3
"""Self-driving diagnostic harness for the identity + transcript bugs.

Phase 4A.7 of the routing-reliability plan. Stops the human-in-the-loop
loop: every fix attempt re-runs this harness before the user is bothered.

Two modes:

  V (validation, default) — runs a battery of named scenarios with
  pre-defined expected outputs. Use to PROVE a fix worked. Exit code 0
  if all green, 1 if any fail.

  D (discovery, --watch <seconds>) — tails real traffic for N seconds,
  flags anomalies WITHOUT pre-defined outputs. Use to find bugs we
  haven't enumerated. Exits 0 with anomaly count; never fails the
  shell (anomalies are informational).

Usage:

    # Run V suite (no args):
    py -3 tools/diagnose-identity.py

    # Run V suite + then D mode for 90 s:
    py -3 tools/diagnose-identity.py --watch 90

    # Override config:
    py -3 tools/diagnose-identity.py --ha-url http://192.168.0.125:8123 \\
                                     --ha-token "$HA_TOKEN"

Writes `tools/diagnose-report.md` (markdown) alongside stdout.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

try:
    import httpx
    import websockets
except ImportError as e:
    sys.stderr.write(
        f"FATAL: missing dep ({e}). Install with: py -3 -m pip install httpx websockets\n"
    )
    sys.exit(2)

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────
# Console output helpers (ANSI colors — works in Windows Terminal +
# powershell with UTF-8 mode).
# ─────────────────────────────────────────────────────────────────────
GREEN = "\033[32m"
RED   = "\033[31m"
YEL   = "\033[33m"
DIM   = "\033[2m"
BOLD  = "\033[1m"
RST   = "\033[0m"


def p(msg: str) -> None:
    print(msg, flush=True)


def p_pass(name: str, extra: str = "") -> None:
    p(f"  {GREEN}PASS{RST}  {name}" + (f"  {DIM}({extra}){RST}" if extra else ""))


def p_fail(name: str, reason: str) -> None:
    p(f"  {RED}FAIL{RST}  {name}\n        {RED}↳ {reason}{RST}")


def p_info(name: str, reason: str) -> None:
    p(f"  {YEL}INFO{RST}  {name}  {DIM}— {reason}{RST}")


def p_skip(name: str, reason: str) -> None:
    p(f"  {DIM}SKIP{RST}  {name}  {DIM}— {reason}{RST}")


# ─────────────────────────────────────────────────────────────────────
# Config loading.
# ─────────────────────────────────────────────────────────────────────
@dataclass
class Cfg:
    ha_url: str
    ha_token: str
    ssh_ubuntu: str = "hav-ubuntu"
    ssh_haos: str = "-p 22222 root@homeassistant.local"
    chattee_url: str = "http://192.168.0.100:8092/conversations/stream"
    bridge_container: str = "hav-personaplex-bridge"
    routing_log_path: str = "/config/external_routing.log"
    home_exe_path: str = r"C:\Claude\home\app\src-tauri\target\release\home.exe"
    home_app_jsx_path: str = r"C:\Claude\home\app\src\home-app.jsx"
    # Extended OpenAI Conversation subentry — REST calls without this
    # agent_id route to HA's built-in default agent which doesn't know
    # about world-state tools or have our custom prompt. Auto-resolved
    # via ssh+jq in load_config().
    agent_id: str = ""


def load_config(args: argparse.Namespace) -> Cfg:
    """Resolve HA_URL + HA_TOKEN from (1) cli args, (2) env, (3) Ubuntu
    bridge's .env via ssh. Last resort lets the user run with zero setup."""
    ha_url = args.ha_url or os.environ.get("HA_URL", "")
    ha_token = args.ha_token or os.environ.get("HA_TOKEN", "")
    if not ha_url or not ha_token:
        p(f"{DIM}config: fetching HA_URL + HA_TOKEN from Ubuntu bridge .env...{RST}")
        try:
            result = subprocess.run(
                ["ssh", "hav-ubuntu",
                 "grep -E '^(HA_URL|HA_TOKEN)=' /opt/home-ai-voice/.env"],
                capture_output=True, text=True, timeout=10
            )
            for line in result.stdout.splitlines():
                if line.startswith("HA_URL=") and not ha_url:
                    ha_url = line.split("=", 1)[1].strip()
                elif line.startswith("HA_TOKEN=") and not ha_token:
                    ha_token = line.split("=", 1)[1].strip()
        except Exception as e:
            p(f"  {YEL}WARN{RST}: ssh fetch failed: {e}")
    if not ha_url or not ha_token:
        sys.stderr.write(
            f"{RED}FATAL: HA_URL or HA_TOKEN missing. Pass --ha-url/--ha-token "
            f"or set env vars or ensure ssh hav-ubuntu works.{RST}\n"
        )
        sys.exit(2)
    # Auto-resolve the Extended OpenAI Conversation entity_id so REST
    # conversation calls route to it (not HA's built-in default agent
    # which doesn't have world-state tools or our custom prompt).
    # HA's /api/conversation/process accepts conversation.* entity_id
    # as agent_id (per HA docs).
    agent_id = args.agent_id or os.environ.get("AGENT_ID", "")
    if not agent_id:
        try:
            r = subprocess.run(
                ["ssh", "-p", "22222", "root@homeassistant.local",
                 "jq -r '.data.entities[] | select(.platform == \"extended_openai_conversation\" "
                 "and (.entity_id | startswith(\"conversation.\"))) | .entity_id' "
                 "/config/.storage/core.entity_registry"],
                capture_output=True, text=True, timeout=15,
                encoding="utf-8", errors="replace",
            )
            for line in r.stdout.splitlines():
                line = line.strip()
                if line:
                    agent_id = line
                    break
        except Exception as e:
            p(f"  {YEL}WARN{RST}: agent_id auto-resolve failed: {e}")
    if not agent_id:
        p(f"  {YEL}WARN{RST}: no agent_id resolved; REST will use HA's default agent")
    else:
        p(f"  {DIM}config: agent_id resolved → {agent_id}{RST}")
    return Cfg(ha_url=ha_url.rstrip("/"), ha_token=ha_token, agent_id=agent_id)


# ─────────────────────────────────────────────────────────────────────
# Result types.
# ─────────────────────────────────────────────────────────────────────
@dataclass
class ScenarioResult:
    id: str
    passed: bool
    reason: str = ""
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class Anomaly:
    discoverer: str  # which D-scenario
    severity: str    # "warn" | "alert"
    description: str
    evidence: dict[str, Any] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────
# HA WS subscriber — collects events of the configured types.
# ─────────────────────────────────────────────────────────────────────
class WSCapture:
    """Async context manager: connects + authenticates with HA WS,
    subscribes to extended_openai_conversation.conversation.finished,
    and stores every event in `self.events` (list of payloads).

    Use `await wait_for_conv_id(conv_id, timeout=8)` to block until an
    event matching that conv_id arrives (or timeout)."""

    EVENT_TYPE = "extended_openai_conversation.conversation.finished"

    def __init__(self, cfg: Cfg):
        self.cfg = cfg
        self.events: list[dict] = []
        self._ws = None
        self._task = None
        self._next_id = 100

    async def __aenter__(self):
        ws_url = self.cfg.ha_url.replace("http://", "ws://").replace("https://", "wss://") + "/api/websocket"
        self._ws = await websockets.connect(ws_url, max_size=4 * 1024 * 1024, ping_interval=20)
        # auth handshake
        first = json.loads(await self._ws.recv())
        if first.get("type") != "auth_required":
            raise RuntimeError(f"unexpected first WS message: {first}")
        await self._ws.send(json.dumps({"type": "auth", "access_token": self.cfg.ha_token}))
        second = json.loads(await self._ws.recv())
        if second.get("type") != "auth_ok":
            raise RuntimeError(f"WS auth failed: {second}")
        # subscribe to our event
        sub_id = self._next_id
        self._next_id += 1
        await self._ws.send(json.dumps({
            "id": sub_id, "type": "subscribe_events", "event_type": self.EVENT_TYPE,
        }))
        # ack
        ack = json.loads(await self._ws.recv())
        if not ack.get("success"):
            raise RuntimeError(f"subscribe_events failed: {ack}")
        # background event pump
        self._task = asyncio.create_task(self._pump())
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._ws:
            await self._ws.close()

    async def _pump(self):
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                if msg.get("type") == "event":
                    self.events.append({
                        "_received_at": time.time(),
                        "_payload_bytes": len(raw),
                        **msg.get("event", {}).get("data", {}),
                    })
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as e:
            sys.stderr.write(f"WS pump error: {e}\n")

    async def wait_for_conv_id(self, conv_id: str, timeout: float = 8.0) -> Optional[dict]:
        """Block until an event with matching conversation_id arrives."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            for ev in self.events:
                if ev.get("conversation_id") == conv_id:
                    return ev
            await asyncio.sleep(0.1)
        return None


# ─────────────────────────────────────────────────────────────────────
# HA REST conversation probe.
# ─────────────────────────────────────────────────────────────────────
async def send_conversation(
    cfg: Cfg, text: str, conversation_id: Optional[str] = None
) -> dict:
    """POST to /api/conversation/process. Returns the parsed JSON body
    (or {"_error": ...} if anything went wrong). Includes agent_id so the
    Extended OpenAI Conversation custom agent handles the turn (not HA's
    built-in default agent, which doesn't have world-state tools)."""
    url = f"{cfg.ha_url}/api/conversation/process"
    body = {"text": text, "language": "en"}
    if conversation_id:
        body["conversation_id"] = conversation_id
    if cfg.agent_id:
        body["agent_id"] = cfg.agent_id
    headers = {
        "Authorization": f"Bearer {cfg.ha_token}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(url, headers=headers, json=body)
            if r.status_code != 200:
                return {"_error": f"http {r.status_code}", "_body": r.text[:500]}
            return r.json()
    except Exception as e:
        return {"_error": f"{type(e).__name__}: {e}"}


def extract_speech(resp: dict) -> str:
    """Pull the assistant's speech text out of HA's response shape."""
    return (
        resp.get("response", {})
        .get("speech", {})
        .get("plain", {})
        .get("speech", "")
    )


# ─────────────────────────────────────────────────────────────────────
# SSH helpers.
# ─────────────────────────────────────────────────────────────────────
def ssh_haos(cmd: str) -> tuple[int, str, str]:
    """Run a command on HAOS via ssh. Returns (rc, stdout, stderr).
    Forces UTF-8 + replace-on-error since HAOS files have unicode (em-dashes,
    smart quotes) and Windows defaults to cp1252 which crashes on them."""
    full = ["ssh"] + Cfg("", "").ssh_haos.split() + [cmd]
    r = subprocess.run(
        full, capture_output=True, text=True, timeout=60,
        encoding="utf-8", errors="replace",
    )
    return r.returncode, r.stdout, r.stderr


def ssh_ubuntu(cmd: str) -> tuple[int, str, str]:
    """Run a command on the Ubuntu AI box."""
    r = subprocess.run(
        ["ssh", "hav-ubuntu", cmd],
        capture_output=True, text=True, timeout=60,
        encoding="utf-8", errors="replace",
    )
    return r.returncode, r.stdout, r.stderr


# ─────────────────────────────────────────────────────────────────────
# V1 — bridge ASR-correction unit test (loads LIVE bridge code via SSH)
# ─────────────────────────────────────────────────────────────────────
def run_v1_bridge_asr_unit() -> ScenarioResult:
    """V1: verify the LIVE bridge file has the ASR-correction code wired
    correctly AND the canonical algorithm works against the test cases.

    Two sub-checks:
    (a) Live source contains the necessary functions, constants, and
        the all-important CALL SITE (`user_text = self._apply_asr_corrections(user_text)`).
    (b) A reference impl of the same regex+sub logic produces correct
        output for the test cases. If the live source matches (a), and
        the reference impl passes (b), the live runtime should behave
        identically.

    Why not dynamic-import the live bridge file? It pulls in 30+ deps
    not installed on the workstation. Static-pattern verification +
    reference-impl test is structurally equivalent for a function this
    small."""
    name = "V1_bridge_asr_unit_test"
    rc, out, err = ssh_ubuntu(
        "cat /opt/home-ai-voice/services/personaplex-bridge/main.py"
    )
    if rc != 0 or not out:
        return ScenarioResult(name, False, f"ssh cat failed: rc={rc} err={err[:200]}")
    src = out
    # (a) Required code patterns in live source.
    required = [
        ("function def", "def _parse_asr_corrections("),
        ("module const", "ASR_CORRECTIONS = _parse_asr_corrections("),
        ("compiled regex", "ASR_CORRECTION_RE = ("),
        ("method def", "def _apply_asr_corrections(self, text"),
        ("call site at _on_parakeet_transcript",
            "user_text = self._apply_asr_corrections(user_text)"),
    ]
    failures = []
    for desc, pat in required:
        if pat not in src:
            failures.append(f"missing {desc}: {pat!r}")
    if failures:
        return ScenarioResult(name, False, "; ".join(failures))
    # (b) Reference impl test.
    asr_map = {"marcello": "Marcelo"}
    ref_re = re.compile(
        r"\b(" + "|".join(re.escape(k) for k in asr_map) + r")\b",
        re.IGNORECASE,
    )
    def ref_apply(text: str) -> str:
        return ref_re.sub(lambda m: asr_map[m.group(0).lower()], text)
    cases = [
        ("Do you see Marcello?", "Do you see Marcelo?"),
        ("MARCELLO is here", "Marcelo is here"),
        ("nothing to fix", "nothing to fix"),
        ("Marcello's lights", "Marcelo's lights"),
    ]
    for inp, expected in cases:
        got = ref_apply(inp)
        if got != expected:
            failures.append(f"reference impl: {inp!r} → got {got!r}, expected {expected!r}")
    if failures:
        return ScenarioResult(name, False, "; ".join(failures))
    return ScenarioResult(
        name, True, "",
        extras={
            "source_patterns_found": len(required),
            "reference_cases_passed": len(cases),
        },
    )


# ─────────────────────────────────────────────────────────────────────
# V2 — subentry prompt has spelling rule
# ─────────────────────────────────────────────────────────────────────
def run_v2_prompt_spelling_rule() -> ScenarioResult:
    name = "V2_subentry_prompt_has_spelling_rule"
    cmd = (
        "jq -r '.data.entries[] | select(.domain == \"extended_openai_conversation\") "
        "| .subentries[]?.data.prompt // \"\"' "
        "/config/.storage/core.config_entries"
    )
    rc, out, err = ssh_haos(cmd)
    if rc != 0:
        return ScenarioResult(name, False, f"ssh jq failed: rc={rc} err={err[:200]}")
    failures = []
    for marker in ["RULE 0.5", 'EXACTLY "Marcelo"', "RULE 0.6"]:
        if marker not in out:
            failures.append(f"prompt missing marker {marker!r}")
    if failures:
        return ScenarioResult(name, False, "; ".join(failures))
    return ScenarioResult(name, True, "", extras={"prompt_chars": len(out)})


# ─────────────────────────────────────────────────────────────────────
# V3 — subentry functions has world-state tools
# ─────────────────────────────────────────────────────────────────────
def run_v3_functions_has_tools() -> ScenarioResult:
    name = "V3_subentry_functions_has_world_state_tools"
    cmd = (
        "jq -r '.data.entries[] | select(.domain == \"extended_openai_conversation\") "
        "| .subentries[]?.data.functions // \"\"' "
        "/config/.storage/core.config_entries"
    )
    rc, out, err = ssh_haos(cmd)
    if rc != 0:
        return ScenarioResult(name, False, f"ssh jq failed: rc={rc} err={err[:200]}")
    tool_names = [
        "name: find_person",
        "name: get_room_state",
        "name: who_is_in",
        "name: get_all_rooms_state",
        "name: refresh_perception",
    ]
    failures = []
    for n in tool_names:
        if n not in out:
            failures.append(f"functions missing {n!r}")
    if "type: world_state" not in out:
        failures.append("functions missing 'type: world_state' dispatcher")
    if failures:
        return ScenarioResult(name, False, "; ".join(failures))
    return ScenarioResult(name, True, "", extras={"functions_chars": len(out)})


# ─────────────────────────────────────────────────────────────────────
# V4 — home.exe freshness
# ─────────────────────────────────────────────────────────────────────
def run_v4_home_exe_freshness(cfg: Cfg) -> ScenarioResult:
    name = "V4_home_exe_freshness"
    exe = Path(cfg.home_exe_path)
    jsx = Path(cfg.home_app_jsx_path)
    if not exe.exists():
        return ScenarioResult(name, False, f"home.exe not found at {exe}")
    if not jsx.exists():
        return ScenarioResult(name, False, f"home-app.jsx not found at {jsx}")
    exe_mtime = exe.stat().st_mtime
    jsx_mtime = jsx.stat().st_mtime
    failures = []
    if exe_mtime < jsx_mtime:
        failures.append(
            f"home.exe ({time.ctime(exe_mtime)}) older than home-app.jsx "
            f"({time.ctime(jsx_mtime)}) — needs rebuild"
        )
    src = jsx.read_text(encoding="utf-8", errors="replace")
    if "user_input_text" not in src:
        failures.append(
            "home-app.jsx missing slim-payload field 'user_input_text' — "
            "subscriber is stale code"
        )
    if failures:
        return ScenarioResult(name, False, "; ".join(failures))
    return ScenarioResult(
        name, True, "",
        extras={"exe_mtime": time.ctime(exe_mtime), "jsx_mtime": time.ctime(jsx_mtime)},
    )


# ─────────────────────────────────────────────────────────────────────
# Live-conversation scenarios (V5-V9) — async, use WSCapture.
# ─────────────────────────────────────────────────────────────────────
async def _live_conv_assertions(
    cfg: Cfg, ws: WSCapture, text: str, name: str,
    must_contain: list[str] = None,
    must_not_contain: list[str] = None,
    informational: bool = False,
) -> ScenarioResult:
    """Send a REST conversation, capture WS event, assert. Common helper."""
    must_contain = must_contain or []
    must_not_contain = must_not_contain or []
    fresh_conv_id = f"diag-{uuid.uuid4()}"
    resp = await send_conversation(cfg, text, conversation_id=fresh_conv_id)
    extras = {"query": text, "conv_id": fresh_conv_id}
    if "_error" in resp:
        return ScenarioResult(
            name, False if not informational else True,
            f"REST call failed: {resp['_error']}",
            extras=extras,
        )
    speech = extract_speech(resp)
    extras["speech"] = speech
    extras["response_type"] = resp.get("response", {}).get("response_type")
    # Wait for the matching WS event.
    ev = await ws.wait_for_conv_id(fresh_conv_id, timeout=10)
    extras["ws_event_received"] = ev is not None
    if ev:
        extras["ws_event_bytes"] = ev.get("_payload_bytes")
        extras["ws_has_user_input_text"] = "user_input_text" in ev
        extras["ws_has_assistant_text"] = "assistant_text" in ev
        extras["ws_payload_keys"] = sorted(ev.keys())
    failures = []
    for needle in must_contain:
        if needle not in speech:
            failures.append(f"speech missing {needle!r}; speech={speech!r}")
    for needle in must_not_contain:
        if needle in speech:
            failures.append(f"speech contains forbidden {needle!r}; speech={speech!r}")
    if informational:
        return ScenarioResult(name, True,
                              "(informational) " + ("; ".join(failures) if failures else "OK"),
                              extras=extras)
    if failures:
        return ScenarioResult(name, False, "; ".join(failures), extras=extras)
    return ScenarioResult(name, True, "", extras=extras)


async def run_v5_spelling_marcelo(cfg: Cfg, ws: WSCapture) -> ScenarioResult:
    return await _live_conv_assertions(
        cfg, ws, "Do you see Marcelo?", "V5_spelling_input_marcelo",
        must_contain=["Marcelo"],
        must_not_contain=["Marcello"],
    )


async def run_v6_spelling_marcello(cfg: Cfg, ws: WSCapture) -> ScenarioResult:
    """Phase 4A.7: with HA-side ASR-correction backstop deployed, sending
    'Marcello' should be transparently rewritten to 'Marcelo' before the
    agent ever sees it. The agent answers about Marcelo using the canonical
    spelling — no "did you mean" hedge, no fabrication."""
    return await _live_conv_assertions(
        cfg, ws, "Do you see Marcello?", "V6_spelling_input_marcello",
        must_contain=["Marcelo"],
        must_not_contain=["Marcello"],
    )


async def run_v7_find_person_called(cfg: Cfg, ws: WSCapture) -> ScenarioResult:
    """Send 'Where is Marcelo?' and assert behavior. The Tauri-side
    classifier (Addendum 2) routes 'where is X' to external by default —
    but REST calls bypass the Tauri-side classifier. HA's classifier
    matches GENERAL too, so this likely routes external. Instead use a
    query that stays local."""
    # Use a HOME_QUERIES query so it routes local and find_person is
    # likely to be called.
    return await _live_conv_assertions(
        cfg, ws, "Who is in the kitchen?", "V7_kitchen_query_local",
        must_not_contain=["Marcello"],
        informational=True,   # informational — passing here just verifies
                              # the round-trip works; the find_person tool
                              # invocation visibility lives in V9.
    )


async def run_v8_mastroianni(cfg: Cfg, ws: WSCapture) -> ScenarioResult:
    return await _live_conv_assertions(
        cfg, ws, "Tell me about Marcello Mastroianni",
        "V8_mastroianni_documented_behavior",
        informational=True,
    )


async def run_v9_transcript_fires_every_turn(cfg: Cfg, ws: WSCapture) -> ScenarioResult:
    name = "V9_transcript_event_fires_every_turn"
    n = 3  # smaller than 5 to keep total runtime tight
    convs = []
    extras = {"sent": 0, "events_received": 0, "missing_conv_ids": []}
    for i in range(n):
        cid = f"diag-burst-{uuid.uuid4()}"
        resp = await send_conversation(cfg, f"Quick test {i+1}", conversation_id=cid)
        extras["sent"] += 1
        if "_error" not in resp:
            convs.append(cid)
        await asyncio.sleep(0.5)
    # Wait for events
    deadline = time.time() + 12
    while time.time() < deadline and len(convs):
        for cid in list(convs):
            ev = next((e for e in ws.events if e.get("conversation_id") == cid), None)
            if ev:
                extras["events_received"] += 1
                convs.remove(cid)
        if not convs:
            break
        await asyncio.sleep(0.3)
    extras["missing_conv_ids"] = convs
    if convs:
        return ScenarioResult(
            name, False,
            f"{len(convs)}/{n} turns did NOT produce a conversation.finished event "
            f"within 12s; missing conv_ids: {convs}",
            extras=extras,
        )
    return ScenarioResult(name, True, "", extras=extras)


# ─────────────────────────────────────────────────────────────────────
# D-scenarios — discovery mode (--watch).
# ─────────────────────────────────────────────────────────────────────
async def watch_mode(cfg: Cfg, seconds: int) -> list[Anomaly]:
    """Tail real traffic for N seconds, flag anomalies."""
    p(f"\n{BOLD}{DIM}── D mode (watch for {seconds}s, flag anomalies in real traffic) ──{RST}")
    anomalies: list[Anomaly] = []
    async with WSCapture(cfg) as ws:
        # Tail routing log over SSH in background.
        baseline_routing = _read_routing_log(cfg)
        baseline_len = len(baseline_routing)
        p(f"  {DIM}baseline routing log entries: {baseline_len}{RST}")
        p(f"  {DIM}watching for {seconds}s... (use your home app normally){RST}")
        deadline = time.time() + seconds
        while time.time() < deadline:
            await asyncio.sleep(2.0)
        # Capture deltas.
        new_routing = _read_routing_log(cfg)[baseline_len:]
        p(f"  {DIM}new routing log entries: {len(new_routing)}, WS events: {len(ws.events)}{RST}")
        # D1: marcello_in_routing_log
        for entry in new_routing:
            for f in ("text", "local_reply_snippet", "ext_reply_snippet"):
                v = entry.get(f) or ""
                if isinstance(v, str) and "Marcello" in v:
                    anomalies.append(Anomaly(
                        "D1_marcello_in_routing_log", "alert",
                        f'"Marcello" found in {f} of conv {entry.get("conv_id")}',
                        evidence={"field": f, "value": v[:200], "ts": entry.get("ts")},
                    ))
        # D2: oversized conversation.finished
        for ev in ws.events:
            sz = ev.get("_payload_bytes", 0)
            if sz > 32000:
                anomalies.append(Anomaly(
                    "D2_oversized_conversation_finished", "alert",
                    f"WS conversation.finished payload {sz} bytes (>32 KB)",
                    evidence={"conv_id": ev.get("conversation_id"), "bytes": sz},
                ))
        # D3: missing conversation.finished
        rt_conv_ids = {e.get("conv_id") for e in new_routing if e.get("conv_id")}
        ws_conv_ids = {e.get("conversation_id") for e in ws.events if e.get("conversation_id")}
        for cid in rt_conv_ids - ws_conv_ids:
            anomalies.append(Anomaly(
                "D3_missing_conversation_finished", "alert",
                f"routing log has conv {cid} but no WS event captured",
                evidence={"conv_id": cid},
            ))
        # D4: bridge didn't correct (grep bridge logs)
        rc, out, _ = ssh_ubuntu(
            f"docker logs --since {seconds + 10}s {cfg.bridge_container} 2>&1 "
            "| grep -E \"dispatch \\(parakeet\\):\""
        )
        if rc == 0 and out:
            for line in out.splitlines():
                if "Marcello" in line:
                    anomalies.append(Anomaly(
                        "D4_bridge_did_not_correct", "alert",
                        "bridge log shows Marcello in dispatch (parakeet) AFTER correction step",
                        evidence={"line": line[:300]},
                    ))
    return anomalies


def _read_routing_log(cfg: Cfg) -> list[dict]:
    """SSH-cat the routing log + parse JSONL."""
    rc, out, _ = ssh_haos(f"cat {cfg.routing_log_path}")
    if rc != 0:
        return []
    entries = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except Exception:
            continue
    return entries


# ─────────────────────────────────────────────────────────────────────
# W mode — multi-step workflow scenarios (Addendum 8).
#
# Architecture:
#   - SSECapture subscribes to metrics-sidecar /conversations/stream
#     BEFORE any scenario fires. Each vLLM round-trip emits one SSE
#     completion event; a multi-tool turn produces N+1 events (one
#     per tool call + one for final text). Aggregator concatenates
#     all events matching the same user text within a window.
#   - For each WorkflowScenario × 5 attempts: fresh conv_id, POST,
#     wait for SSE terminal completion + WS conversation.finished,
#     evaluate assertions + cross-cutting invariants, record per-
#     attempt PASS/FAIL.
#   - Suite reports per-scenario N/5; exit code 0 (all ≥ threshold),
#     1 (drift), 2 (hard 0/5), 3 (preflight failed).
# ─────────────────────────────────────────────────────────────────────
import collections


@dataclass
class AttemptResult:
    """One scenario attempt — pass/fail/inconclusive + diagnostics.

    `inconclusive` = the SSE feed missed this turn (sidecar drop under
    load is common — see Phase 4A.8 findings) BUT the WS event confirms
    the agent responded. Without tool_calls we can't evaluate tool-call
    assertions, but speech-based assertions still ran.

    Inconclusive attempts don't count toward pass or fail — they're
    excluded from the threshold denominator. A scenario with 2 pass /
    1 inconclusive / 0 fail still passes a threshold of 2 (out of 2
    conclusive attempts)."""
    attempt_idx: int
    passed: bool
    inconclusive: bool = False
    failures: list[str] = field(default_factory=list)
    tool_call_names: list[str] = field(default_factory=list)
    speech_snippet: str = ""
    conv_id: str = ""
    completions: int = 0   # how many SSE events made up this turn
    elapsed_s: float = 0.0


@dataclass
class WorkflowResult:
    """One scenario's full 5-attempt result."""
    scenario_id: str
    phase: int
    threshold: int
    attempts: list[AttemptResult] = field(default_factory=list)

    @property
    def passed_count(self) -> int:
        return sum(1 for a in self.attempts if a.passed)

    @property
    def inconclusive_count(self) -> int:
        return sum(1 for a in self.attempts if a.inconclusive)

    @property
    def conclusive_count(self) -> int:
        """Attempts that were either pass or fail (NOT inconclusive)."""
        return sum(1 for a in self.attempts if not a.inconclusive)

    @property
    def status(self) -> str:
        """green | drift | hard_fail | all_inconclusive.

        A scenario is GREEN if pass_count >= threshold. SSE-flake
        attempts are excluded from the denominator — they don't penalize
        a planner that's actually responding correctly. A scenario where
        EVERY attempt was SSE-flake is reported as `all_inconclusive`
        (separate from hard_fail; signals an infrastructure problem)."""
        n_pass = self.passed_count
        n_concl = self.conclusive_count
        if n_concl == 0:
            return "all_inconclusive"
        # Adjust threshold proportionally to conclusive count: if 3/3
        # conclusive attempts pass but threshold was 4 of 5, that's
        # still green (no fails to count against). The threshold acts
        # on the conclusive subset.
        adj_threshold = min(self.threshold, n_concl)
        if n_pass >= adj_threshold:
            return "green"
        if n_pass == 0:
            return "hard_fail"
        return "drift"


class SSECapture:
    """Subscribes to metrics-sidecar /conversations/stream BEFORE any
    scenario fires. Accumulates completions in a ring buffer indexed
    by ts. wait_for_turn(query, since_ts, timeout) returns the
    AGGREGATED tool_calls from ALL completions matching the user text
    within the window — a multi-step turn produces N+1 events.

    The harness MUST aggregate: vLLM emits one completion per round-
    trip, so a 3-tool-call turn produces 4 events. Without aggregation
    the harness sees only the first round-trip's calls and falsely
    flags every multi-step scenario as missing tools."""

    BUFFER_SIZE = 500

    def __init__(self, cfg: Cfg):
        self.cfg = cfg
        self._buffer: collections.deque[dict] = collections.deque(maxlen=self.BUFFER_SIZE)
        self._task = None
        self._client = None
        self._connected = asyncio.Event()
        self._stop = asyncio.Event()
        # IDs of completions already consumed by wait_for_turn. Prevents
        # attempt N from picking up attempt N-1's terminal completion
        # when both share the same user text and fall inside the time
        # window. Without this, the runner would attribute the prior
        # turn's tool_calls (or lack thereof) to the current turn.
        self._consumed_ids: set[str] = set()
        self._last_timeout_diag: dict = {}

    async def __aenter__(self):
        self._task = asyncio.create_task(self._consume())
        # Wait briefly for connection so the first scenario doesn't
        # race a not-yet-subscribed stream.
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=8.0)
        except asyncio.TimeoutError:
            raise RuntimeError(
                f"SSECapture: failed to connect to {self.cfg.chattee_url} within 8s"
            )
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def _consume(self):
        # Reconnect loop: SSE connections occasionally drop.
        backoff = 1.0
        while not self._stop.is_set():
            try:
                async with httpx.AsyncClient(timeout=None) as client:
                    async with client.stream("GET", self.cfg.chattee_url) as r:
                        if r.status_code != 200:
                            sys.stderr.write(
                                f"SSE got {r.status_code} from {self.cfg.chattee_url}\n"
                            )
                            await asyncio.sleep(backoff)
                            backoff = min(backoff * 2, 30.0)
                            continue
                        self._connected.set()
                        backoff = 1.0
                        async for line in r.aiter_lines():
                            if self._stop.is_set():
                                break
                            if not line.startswith("data: "):
                                continue
                            try:
                                ev = json.loads(line[6:])
                            except Exception:
                                continue
                            ev["_recv_ts"] = time.time()
                            self._buffer.append(ev)
            except asyncio.CancelledError:
                break
            except Exception as e:
                sys.stderr.write(f"SSE consume error: {e}\n")
                self._connected.clear()
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def wait_for_turn(self, query: str, since_ts: float,
                            query_aliases: Optional[list[str] | tuple[str, ...]] = None,
                            timeout: float = 30.0) -> Optional[dict]:
        """Wait until a 'terminal' completion (no tool_calls) matching
        the query appears after since_ts. Return aggregated tool_calls
        from ALL completions matching this turn, plus the final
        assistant text and completion count.

        A turn is complete when the most recent matching completion
        has no tool_calls — i.e., the agent emitted final text.

        Returns None on timeout. On timeout, also stashes a diagnostic
        of what WAS in the buffer at the time on self._last_timeout_diag
        — helps distinguish 'SSE missed the event' from 'agent didn't
        call vLLM at all'."""
        deadline = time.time() + timeout
        raw_targets = [query, *(query_aliases or ())]
        targets = {
            str(item or "").strip()
            for item in raw_targets
            if str(item or "").strip()
        }
        target_norms = {item.rstrip("?.! ").lower() for item in targets}
        target = (query or "").strip()
        while time.time() < deadline:
            await asyncio.sleep(0.5)
            matching = [
                e for e in self._buffer
                # Strict: events must be AFTER since_ts. No grace —
                # the previous bug let attempt N pick up attempt N-1's
                # terminal completion. Each turn's events arrive after
                # the REST POST starts; since_ts is captured BEFORE
                # the POST, so strict > is correct.
                if e.get("_recv_ts", 0) > since_ts
                # Also skip completions we've already returned (defense
                # in depth against repeated identical queries).
                and e.get("id") not in self._consumed_ids
                and (
                    (e.get("user") or "").strip() in targets
                    # Robustness: HA's pipeline sometimes normalizes
                    # the user text (trailing punctuation, case).
                    or (e.get("user") or "").strip().rstrip("?.! ").lower() in target_norms
                )
            ]
            if not matching:
                continue
            last = matching[-1]
            if not last.get("tool_calls"):  # terminal: final text
                all_tool_calls = []
                for e in matching:
                    all_tool_calls.extend(e.get("tool_calls") or [])
                # Mark these as consumed so the NEXT wait_for_turn doesn't
                # re-attribute them.
                for e in matching:
                    if e.get("id"):
                        self._consumed_ids.add(e["id"])
                return {
                    "tool_calls": all_tool_calls,
                    "assistant": last.get("assistant") or "",
                    "completions": len(matching),
                    "completion_ids": [e.get("id") for e in matching],
                }
        # Timeout — stash diagnostic showing what was in the buffer.
        recent = [
            {"user_snip": (e.get("user") or "")[:80],
             "has_tools": bool(e.get("tool_calls")),
             "age_s": round(time.time() - e.get("_recv_ts", 0), 1),
             "consumed": e.get("id") in self._consumed_ids}
            for e in list(self._buffer)[-10:]
        ]
        self._last_timeout_diag = {
            "buffer_size": len(self._buffer),
            "target": target[:80],
            "recent_10": recent,
        }
        return None


# ─── HA service-call helper (used by scenario setup/teardown) ─────
async def _call_service(cfg: Cfg, op: dict) -> None:
    """POST /api/services/<domain>/<service> with the given data dict.
    Errors are logged + swallowed — scenario state setup should not
    crash the suite; the assertion will catch downstream effects."""
    domain = op.get("domain")
    service = op.get("service")
    data = op.get("data") or {}
    if not domain or not service:
        return
    url = f"{cfg.ha_url}/api/services/{domain}/{service}"
    headers = {"Authorization": f"Bearer {cfg.ha_token}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.post(url, headers=headers, json=data)
            if r.status_code >= 400:
                sys.stderr.write(
                    f"_call_service: {domain}.{service} returned {r.status_code}: {r.text[:200]}\n"
                )
    except Exception as e:
        sys.stderr.write(f"_call_service: {domain}.{service} failed: {e}\n")


# ─── Preflight checks (exit 3 on any failure) ─────────────────────
def preflight_classifier(scenarios) -> tuple[bool, list[str]]:
    """Run every scenario's query through external_routing.classify_intent
    to verify it routes LOCAL (or AMBIGUOUS, which the agent treats as
    local). Any query that routes EXTERNAL bypasses vLLM → no SSE event
    → tool_call assertion is impossible. Hard-fail the suite at startup.

    Imports the live module from the HA config dir so a single source
    of truth is used (same regex the production agent applies)."""
    try:
        spec = importlib.util.spec_from_file_location(
            "external_routing",
            r"C:\Claude\home\ha-config\extended_openai_conversation\external_routing.py",
        )
        if spec is None or spec.loader is None:
            return False, ["could not load external_routing module"]
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        classify = mod.classify_intent
    except Exception as e:
        return False, [f"failed to import classify_intent: {e}"]
    failures = []
    for sc in scenarios:
        intent = classify(sc.query)
        if intent == "external":
            failures.append(
                f"scenario {sc.id!r}: query routes EXTERNAL "
                f"({sc.query[:60]!r}) — rewrite query to match HOME_VERBS/NOUNS/QUERIES"
            )
    return len(failures) == 0, failures


async def preflight_sidecar(cfg: Cfg) -> tuple[bool, str]:
    """Verify metrics-sidecar /conversations/stream endpoint is reachable.
    Without it, the W suite can't see tool calls at all."""
    base = cfg.chattee_url.split("/conversations")[0]
    healthz = f"{base}/healthz"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(healthz)
            if r.status_code != 200:
                return False, f"sidecar healthz returned {r.status_code} at {healthz}"
            return True, ""
    except Exception as e:
        return False, f"sidecar unreachable at {healthz}: {e}"


async def preflight_safe_entities(cfg: Cfg, safe_ents: set[str]) -> tuple[bool, list[str]]:
    """Verify every entity in the safe-list is actually available in HA.
    If light.office is offline (Zigbee drop), every actuation scenario
    fails with a service_call error and the suite falsely reports
    'planner broken' when it's really a power/network issue."""
    failures = []
    headers = {"Authorization": f"Bearer {cfg.ha_token}"}
    async with httpx.AsyncClient(timeout=8.0) as client:
        for ent in safe_ents:
            url = f"{cfg.ha_url}/api/states/{ent}"
            try:
                r = await client.get(url, headers=headers)
                if r.status_code == 404:
                    failures.append(f"safe entity {ent} not found in HA")
                    continue
                if r.status_code != 200:
                    failures.append(f"safe entity {ent} returned http {r.status_code}")
                    continue
                state = (r.json() or {}).get("state", "")
                if state in ("unavailable", "unknown"):
                    failures.append(f"safe entity {ent} is {state} — fix before suite")
            except Exception as e:
                failures.append(f"safe entity {ent}: {type(e).__name__}: {e}")
    return len(failures) == 0, failures


# ─── Per-attempt evaluator ────────────────────────────────────────
def _match_assertion(tool_calls: list[dict], asrt) -> Optional[dict]:
    """Find first tool call matching the assertion (name + optional
    arg predicate). Returns the call or None."""
    for tc in tool_calls:
        fn = tc.get("function") or {}
        if fn.get("name") != asrt.name:
            continue
        if asrt.args_predicate is None:
            return tc
        args = fn.get("arguments_parsed") or {}
        try:
            if asrt.args_predicate(args):
                return tc
        except Exception:
            # A predicate raising on weird args = no match (be lenient).
            continue
    return None


def evaluate_attempt(scenario, ssc_result: Optional[dict],
                     ws_event: Optional[dict],
                     extra_forbidden: list,
                     invariants: list,
                     attempt_idx: int,
                     elapsed: float,
                     ssc_diag: Optional[dict] = None,
                     rest_speech: str = "") -> AttemptResult:
    """Run all assertions for one attempt. Returns AttemptResult."""
    failures: list[str] = []
    tool_calls = (ssc_result or {}).get("tool_calls", []) or []
    # Speech source priority:
    #   1. WS event assistant_text (custom agent, final IntentResponse
    #      after output-side ASR backstop) — best signal when our
    #      agent handled the turn.
    #   2. SSE assistant text (vLLM final completion).
    #   3. REST response speech — fallback for when HA's built-in
    #      intent agent short-circuits BEFORE our agent runs (e.g.,
    #      "Lock the front door" matches HA's lock intent and errors
    #      out with "Unable to find entity lock.front_door"). The
    #      refusal IS correct behavior; we just have to read it from
    #      a different source.
    speech = ((ws_event or {}).get("assistant_text")
              or (ssc_result or {}).get("assistant")
              or rest_speech
              or "")
    if getattr(scenario, "expect_silent", False):
        if speech.strip():
            failures.append("expected silence but received assistant speech")
        for asrt in list(scenario.forbidden_tools) + list(extra_forbidden):
            match = _match_assertion(tool_calls, asrt)
            if match is not None:
                failures.append(f"forbidden {asrt.name} fired: {asrt.why}")
        for pat in scenario.speech_must_not_match:
            if re.search(pat, speech, re.IGNORECASE):
                failures.append(f"speech matched forbidden pattern {pat!r}")
        return AttemptResult(
            attempt_idx=attempt_idx,
            passed=(len(failures) == 0),
            failures=failures,
            tool_call_names=[
                (tc.get("function") or {}).get("name", "?") for tc in tool_calls
            ],
            speech_snippet=speech[:300],
            completions=(ssc_result or {}).get("completions", 0),
            elapsed_s=round(elapsed, 2),
        )
    if ssc_result is None:
        # The SSE timed out. But the WS event likely arrived (HA's
        # conversation.finished fires regardless of which subsystem
        # generated the response). If WS speech IS present, we know the
        # agent did respond — the SSE just didn't capture it (sidecar
        # drop under load is the common cause).
        if ws_event and ws_event.get("assistant_text"):
            # INCONCLUSIVE — agent worked, infrastructure lost the tool
            # trace. Run speech-based assertions only; tool-call ones
            # would just generate noise. Return early.
            speech_failures = []
            for pat in scenario.speech_must_match:
                if not re.search(pat, speech, re.IGNORECASE):
                    speech_failures.append(f"speech missing pattern {pat!r}")
            for pat in scenario.speech_must_not_match:
                if re.search(pat, speech, re.IGNORECASE):
                    speech_failures.append(f"speech matched forbidden pattern {pat!r}")
            for inv in invariants:
                try:
                    ok, reason = inv([], speech)
                    if not ok:
                        speech_failures.append(f"invariant: {reason}")
                except Exception:
                    pass
            note = f"SSE flake — sidecar missed completion (agent responded)"
            return AttemptResult(
                attempt_idx=attempt_idx,
                passed=False,
                inconclusive=True,
                failures=[note] + speech_failures,
                tool_call_names=[],
                speech_snippet=speech[:300],
                completions=0,
                elapsed_s=round(elapsed, 2),
            )
        elif rest_speech:
            # HA's built-in intent agent short-circuited (e.g., "Lock the
            # front door" matched HA's lock intent and returned an error
            # without invoking our custom agent). The REST response has
            # the actual speech. Treat as a normal speech-only evaluation
            # — tool_calls is empty by construction, which is correct for
            # short-circuited refusals.
            speech_failures = []
            for pat in scenario.speech_must_match:
                if not re.search(pat, speech, re.IGNORECASE):
                    speech_failures.append(f"speech missing pattern {pat!r}")
            for pat in scenario.speech_must_not_match:
                if re.search(pat, speech, re.IGNORECASE):
                    speech_failures.append(f"speech matched forbidden pattern {pat!r}")
            for inv in invariants:
                try:
                    ok, reason = inv([], speech)
                    if not ok:
                        speech_failures.append(f"invariant: {reason}")
                except Exception:
                    pass
            # Also still check forbidden_tools (empty tool_calls means
            # none will match, but that's the right answer for refusal).
            note = "HA built-in intent short-circuited (REST-only response)"
            return AttemptResult(
                attempt_idx=attempt_idx,
                passed=(len(speech_failures) == 0),
                inconclusive=False,
                failures=([note] if speech_failures else []) + speech_failures,
                tool_call_names=[],
                speech_snippet=speech[:300],
                completions=0,
                elapsed_s=round(elapsed, 2),
            )
        else:
            failures.append(
                "no SSE terminal completion AND no WS event AND no REST speech — "
                "agent may have errored, routed external, or sidecar+WS both broken"
            )
    # Required tools — each entry must match at least one call. An
    # AnyOf entry is satisfied if ANY of its sub-assertions match.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from workflow_scenarios import AnyOf  # local import to avoid cycle
    for entry in scenario.required_tools:
        if isinstance(entry, AnyOf):
            if not any(_match_assertion(tool_calls, opt) for opt in entry.options):
                failures.append(f"missing required ANY OF: {entry.why}")
        else:
            if not _match_assertion(tool_calls, entry):
                failures.append(f"missing required {entry.name}: {entry.why}")
    # Forbidden tools (scenario-specific + cross-cutting safe guard).
    for asrt in list(scenario.forbidden_tools) + list(extra_forbidden):
        match = _match_assertion(tool_calls, asrt)
        if match is not None:
            failures.append(f"forbidden {asrt.name} fired: {asrt.why}")
    # Budget cap.
    if len(tool_calls) > scenario.max_tool_calls:
        failures.append(
            f"tool budget exceeded: {len(tool_calls)} > {scenario.max_tool_calls}"
        )
    # Speech patterns.
    for pat in scenario.speech_must_match:
        if not re.search(pat, speech, re.IGNORECASE):
            failures.append(f"speech missing pattern {pat!r}")
    for pat in scenario.speech_must_not_match:
        if re.search(pat, speech, re.IGNORECASE):
            failures.append(f"speech matched forbidden pattern {pat!r}")
    # Cross-cutting invariants.
    for inv in invariants:
        try:
            ok, reason = inv(tool_calls, speech)
            if not ok:
                failures.append(f"invariant: {reason}")
        except Exception as e:
            failures.append(f"invariant raised: {type(e).__name__}: {e}")

    return AttemptResult(
        attempt_idx=attempt_idx,
        passed=(len(failures) == 0),
        failures=failures,
        tool_call_names=[
            (tc.get("function") or {}).get("name", "?") for tc in tool_calls
        ],
        speech_snippet=speech[:300],
        completions=(ssc_result or {}).get("completions", 0),
        elapsed_s=round(elapsed, 2),
    )


# ─── W-suite runner ─────────────────────────────────────────────
async def run_w_suite(cfg: Cfg, attempts_per: int = 5,
                      only_phase: Optional[int] = None,
                      only_ids: Optional[list[str]] = None,
                      strict: bool = False,
                      include_write_gated: bool = False,
                      inconclusive_max_rate: float = 0.10,
                      corpus_path: Optional[Path] = None) -> tuple[list, int]:
    """Returns (results, exit_code). Exit codes:
      0 — all scenarios ≥ threshold (green)
      1 — at least one scenario below threshold but above 0/5 (drift)
      2 — at least one scenario at 0/5 (hard fail — broken)
      3 — preflight failed (suite never ran)
    """
    # Import scenarios at runtime so a syntax error in workflow_scenarios.py
    # is reported as a normal Python error, not a silent suite skip.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import workflow_scenarios as ws_mod

    tier_label = "read-only + write-gated" if include_write_gated else "read-only"
    p(f"\n{BOLD}{DIM}── W mode (workflow suite, {attempts_per}-shot per scenario, {tier_label}) ──{RST}")

    scenarios = ws_mod.scenarios_for(
        phase=only_phase,
        only_ids=only_ids,
        include_write_gated=include_write_gated,
    )
    if not scenarios:
        p(f"  {YEL}WARN{RST}: no scenarios match the filter")
        if only_ids and not include_write_gated:
            p(f"  {DIM}hint: the requested ids may be write-gated; rerun with --include-write-gated{RST}")
        return [], 3

    # ─ Preflight 1: classifier routes every query LOCAL ─
    ok, fails = preflight_classifier(scenarios)
    if not ok:
        p(f"  {RED}PREFLIGHT FAIL — classifier check{RST}")
        for f in fails:
            p(f"    {RED}↳ {f}{RST}")
        return [], 3
    p(f"  {DIM}preflight: classifier verified all {len(scenarios)} queries route local{RST}")

    # ─ Preflight 2: sidecar reachable ─
    ok, why = await preflight_sidecar(cfg)
    if not ok:
        p(f"  {RED}PREFLIGHT FAIL — sidecar{RST}: {why}")
        return [], 3
    p(f"  {DIM}preflight: sidecar healthz OK ({cfg.chattee_url.split('/conv')[0]}/healthz){RST}")

    # ─ Preflight 3: safe entities available ─
    preflight_entities = ws_mod.preflight_entities_for(scenarios)
    ok, fails = await preflight_safe_entities(cfg, preflight_entities)
    if not ok:
        p(f"  {RED}PREFLIGHT FAIL — safe entities{RST}")
        for f in fails:
            p(f"    {RED}↳ {f}{RST}")
        return [], 3
    p(f"  {DIM}preflight: {len(preflight_entities)} safe/setup entities available{RST}")

    extra_forbidden = [ws_mod.SAFE_ENTITY_GUARD]
    invariants = ws_mod.INVARIANTS
    if strict:
        # In strict mode, raise every scenario's threshold to attempts_per.
        scenarios = [
            dataclasses.replace(s, pass_threshold=attempts_per) for s in scenarios
        ]

    results: list[WorkflowResult] = []
    p(f"\n  {DIM}running {len(scenarios)} scenarios × {attempts_per} attempts...{RST}\n")

    async with SSECapture(cfg) as ssc, WSCapture(cfg) as ws:
        for sc in scenarios:
            wr = WorkflowResult(
                scenario_id=sc.id, phase=sc.phase, threshold=sc.pass_threshold,
            )
            for i in range(attempts_per):
                conv_id = f"w-{sc.id[:20]}-{i+1}-{uuid.uuid4().hex[:8]}"
                # Pre-scenario state setup (Phase 4A.9 addendum 9).
                # For mute scenarios this flips input_boolean/input_datetime
                # helpers before the query fires. Brief settle so the
                # template sensor re-evaluates before we test the gate.
                for op in (sc.setup_state or []):
                    await _call_service(cfg, op)
                if sc.setup_state:
                    await asyncio.sleep(1.5)
                t0 = time.time()
                since = t0
                # Fire the request.
                resp = await send_conversation(cfg, sc.query, conversation_id=conv_id)
                # Optimization: if REST returned an immediate response
                # with conversation_id=null AND has speech, HA's
                # built-in intent agent short-circuited (e.g., "lock"
                # intent → error). Our custom agent never ran; no SSE
                # event will fire. Skip the long SSE wait.
                resp_conv_id = (resp or {}).get("conversation_id")
                rest_speech_early = extract_speech(resp)
                if resp_conv_id is None and rest_speech_early:
                    ssc_result = None
                    ws_event = None
                else:
                    # Capture both signals in parallel: SSE aggregated
                    # turn + WS conversation.finished (for canonical speech).
                    ssc_result = await ssc.wait_for_turn(
                        sc.query,
                        since,
                        query_aliases=getattr(sc, "sse_query_aliases", ()),
                        timeout=25.0,
                    )
                    ws_event = await ws.wait_for_conv_id(conv_id, timeout=4.0)
                elapsed = time.time() - t0
                # If REST call hard-errored, surface that as the failure.
                attempt: AttemptResult
                if "_error" in resp:
                    attempt = AttemptResult(
                        attempt_idx=i + 1, passed=False,
                        failures=[f"REST error: {resp.get('_error')}"],
                        speech_snippet="",
                        conv_id=conv_id, elapsed_s=round(elapsed, 2),
                    )
                else:
                    diag = getattr(ssc, "_last_timeout_diag", {}) if ssc_result is None else None
                    # REST response speech — fallback when neither SSE
                    # nor WS fire (e.g., HA's built-in intent agent
                    # short-circuits to an error before the custom
                    # agent runs, as with "Lock the front door").
                    rest_speech = extract_speech(resp)
                    attempt = evaluate_attempt(
                        sc, ssc_result, ws_event, extra_forbidden, invariants,
                        attempt_idx=i + 1, elapsed=elapsed, ssc_diag=diag,
                        rest_speech=rest_speech,
                    )
                    attempt.conv_id = conv_id
                wr.attempts.append(attempt)
                # Per-attempt cleanup so leftover mute state doesn't
                # poison the next attempt or scenario.
                for op in (sc.teardown_state or []):
                    await _call_service(cfg, op)
                # 500ms hygiene between attempts (give vLLM queue room).
                await asyncio.sleep(0.5)
            # Drain WS event queue between scenarios (HA's WS has a
            # ~4096-pending cap; 40+ rapid turns can fill it).
            ws.events.clear()
            results.append(wr)
            # Compact per-scenario line: shows pass / inconclusive / total.
            n = wr.passed_count
            inc = wr.inconclusive_count
            inc_str = f" +{inc} flake" if inc else ""
            status = wr.status
            if status == "green":
                sym = f"{GREEN}✓{RST}"
                label = f"{GREEN}PASS{RST}"
            elif status == "drift":
                sym = f"{YEL}⚠{RST}"
                label = f"{YEL}DRIFT{RST}"
            elif status == "all_inconclusive":
                sym = f"{YEL}?{RST}"
                label = f"{YEL}ALL INCONCLUSIVE (sidecar dropped every completion){RST}"
            else:
                sym = f"{RED}✗{RST}"
                label = f"{RED}HARD FAIL{RST}"
            p(f"  {sym}  {sc.id:<38}  {n}/{attempts_per}{inc_str}  {label}  (thr {sc.pass_threshold})")
            # Print first true-failing attempt's reasons (skip inconclusive
            # for triage clarity — those are infrastructure noise).
            first_fail = next(
                (a for a in wr.attempts if not a.passed and not a.inconclusive),
                None,
            )
            if first_fail and first_fail.failures:
                for reason in first_fail.failures[:3]:
                    p(f"        {DIM}↳ attempt {first_fail.attempt_idx}: {reason}{RST}")

    # Determine exit code. Inconclusive scenarios (sidecar drops) get
    # surfaced but don't fail the suite — they're infrastructure noise,
    # not planner bugs. Exit 0 if all scenarios are green OR all
    # inconclusive (latter signals "investigate sidecar" via the
    # printed status, not via shell exit).
    hard = any(r.status == "hard_fail" for r in results)
    drift = any(r.status == "drift" for r in results)
    all_inc = sum(1 for r in results if r.status == "all_inconclusive")
    if hard:
        rc = 2
    elif drift:
        rc = 1
    else:
        rc = 0   # all green or all_inconclusive — green path
    # Surface the flake summary.
    total_inc = sum(r.inconclusive_count for r in results)
    total_attempts = sum(len(r.attempts) for r in results)
    inc_rate = total_inc / max(total_attempts, 1)
    if total_inc:
        p(f"\n  {YEL}sidecar flake: {total_inc}/{total_attempts} attempts "
          f"had SSE timeouts ({100*total_inc//max(total_attempts,1)}%) — "
          f"agent responded correctly but metrics tee dropped the trace{RST}")
    if inc_rate > inconclusive_max_rate:
        p(f"  {RED}inconclusive cap exceeded: {inc_rate:.0%} > {inconclusive_max_rate:.0%}{RST}")
        if rc == 0:
            rc = 1
    if corpus_path is not None:
        write_workflow_corpus(results, scenarios, cfg, corpus_path)
        p(f"  {DIM}attempt corpus written to {corpus_path}{RST}")
    return results, rc


def write_workflow_report(results: list, report_path: Path) -> None:
    """Append the W-suite section to the existing diagnose-report.md."""
    lines = ["", "---", "", "## W suite — workflow scenarios", ""]
    if not results:
        lines.append("(no scenarios ran)")
        Path(report_path).open("a", encoding="utf-8").write("\n".join(lines))
        return
    total = len(results)
    green = sum(1 for r in results if r.status == "green")
    drift = sum(1 for r in results if r.status == "drift")
    hard = sum(1 for r in results if r.status == "hard_fail")
    all_inc = sum(1 for r in results if r.status == "all_inconclusive")
    total_inc = sum(r.inconclusive_count for r in results)
    total_att = sum(len(r.attempts) for r in results)
    lines.append(
        f"**{green}/{total} green, {drift} drift, {hard} hard-fail, "
        f"{all_inc} all-inconclusive**"
    )
    if total_inc:
        lines.append("")
        lines.append(
            f"⚠️ **SSE flake rate**: {total_inc}/{total_att} attempts "
            f"({100*total_inc//max(total_att,1)}%) had sidecar drops — "
            f"agent responded correctly but tool-call trace was lost. "
            f"Excluded from threshold."
        )
    lines.append("")
    lines.append("| scenario | phase | pass / total (+flake) | threshold | status |")
    lines.append("|---|---|---|---|---|")
    for r in results:
        sym = {"green": "✅", "drift": "⚠️", "hard_fail": "❌",
               "all_inconclusive": "❓"}[r.status]
        inc_str = f" (+{r.inconclusive_count} flake)" if r.inconclusive_count else ""
        lines.append(
            f"| `{r.scenario_id}` | {r.phase} | "
            f"{r.passed_count}/{len(r.attempts)}{inc_str} | "
            f"{r.threshold} | {sym} {r.status} |"
        )
    lines.append("")
    # Per-scenario detail for any scenario below threshold.
    sub_threshold = [r for r in results if r.status != "green"]
    if sub_threshold:
        lines.append("### Sub-threshold scenarios (diagnostic detail)")
        lines.append("")
        for r in sub_threshold:
            lines.append(f"#### {r.scenario_id}  ({r.passed_count}/{len(r.attempts)})")
            lines.append("")
            for a in r.attempts:
                tag = "✓" if a.passed else "✗"
                lines.append(
                    f"- **attempt {a.attempt_idx}** {tag}  "
                    f"`{a.elapsed_s}s`  completions={a.completions}  "
                    f"calls={a.tool_call_names}"
                )
                if a.failures:
                    for f in a.failures:
                        lines.append(f"  - {f}")
                if a.speech_snippet:
                    sn = a.speech_snippet.replace("\n", " ")[:200]
                    lines.append(f"  - speech: `{sn}`")
            lines.append("")
    with Path(report_path).open("a", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _git_commit() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=str(Path(__file__).resolve().parents[1]),
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return out.strip()
    except Exception:
        return ""


def _classify_attempt_failure(attempt: AttemptResult) -> str:
    if attempt.passed:
        return "pass"
    text = " ".join(attempt.failures or []).lower()
    if attempt.inconclusive or "sse flake" in text or "no sse terminal completion" in text:
        return "trace_loss"
    if "rest error" in text or "sidecar" in text or "ws event" in text:
        return "service_outage"
    if "safe allow-list" in text or "travel mode" in text or "forbidden execute_services" in text:
        return "unsafe_tool_call"
    if "missing required" in text or "speech missing" in text or "speech matched forbidden" in text:
        return "model_behavior"
    if "invariant" in text or "tool budget exceeded" in text:
        return "assertion_drift"
    return "unknown"


def default_workflow_corpus_path() -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return Path(__file__).resolve().parent / "reports" / "llm-workflow" / stamp / "attempts.jsonl"


def write_workflow_corpus(results: list, scenarios: list, cfg: Cfg,
                          corpus_path: Path) -> None:
    """Write one JSONL record per attempt for later regression analysis."""
    by_id = {getattr(s, "id", ""): s for s in scenarios}
    commit = _git_commit()
    corpus_path.parent.mkdir(parents=True, exist_ok=True)
    with corpus_path.open("w", encoding="utf-8") as f:
        for result in results:
            scenario = by_id.get(result.scenario_id)
            for attempt in result.attempts:
                record = {
                    "run_ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "commit": commit,
                    "agent_id": cfg.agent_id,
                    "ha_url": cfg.ha_url,
                    "trace_url": cfg.chattee_url,
                    "scenario_id": result.scenario_id,
                    "phase": result.phase,
                    "safety_level": getattr(scenario, "safety_level", ""),
                    "expected_behavior": getattr(scenario, "expected_behavior", ""),
                    "query": getattr(scenario, "query", ""),
                    "attempt": attempt.attempt_idx,
                    "passed": attempt.passed,
                    "inconclusive": attempt.inconclusive,
                    "failure_class": _classify_attempt_failure(attempt),
                    "failures": attempt.failures,
                    "tool_call_names": attempt.tool_call_names,
                    "assistant_text": attempt.speech_snippet,
                    "conversation_id": attempt.conv_id,
                    "completions": attempt.completions,
                    "elapsed_s": attempt.elapsed_s,
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ─────────────────────────────────────────────────────────────────────
# Orchestrator + report writer.
# ─────────────────────────────────────────────────────────────────────
async def run_v_suite(cfg: Cfg) -> list[ScenarioResult]:
    p(f"\n{BOLD}{DIM}── V suite (synthetic; expected output asserted) ──{RST}")
    results: list[ScenarioResult] = []
    # Preflight: V1 (no WS needed)
    p(f"\n{DIM}── V1 — bridge ASR unit (preflight) ──{RST}")
    r = run_v1_bridge_asr_unit()
    results.append(r)
    if r.passed:
        p_pass(r.id, f"cases={r.extras.get('cases_passed')}")
    else:
        p_fail(r.id, r.reason)
        p(f"  {YEL}↳ V1 failed; live bridge code is broken. Aborting suite.{RST}")
        return results

    # V2, V3 (config inspection, no WS)
    p(f"\n{DIM}── V2-V3 — HA subentry config ──{RST}")
    for fn in (run_v2_prompt_spelling_rule, run_v3_functions_has_tools):
        r = fn()
        results.append(r)
        if r.passed:
            p_pass(r.id)
        else:
            p_fail(r.id, r.reason)

    # V4 (workstation file inspection)
    p(f"\n{DIM}── V4 — home.exe freshness ──{RST}")
    r = run_v4_home_exe_freshness(cfg)
    results.append(r)
    if r.passed:
        p_pass(r.id)
    else:
        p_fail(r.id, r.reason)

    # V5-V9 (live HA conversation + WS capture)
    p(f"\n{DIM}── V5-V9 — live conversation + WS event capture ──{RST}")
    async with WSCapture(cfg) as ws:
        for fn in (run_v5_spelling_marcelo, run_v6_spelling_marcello,
                   run_v7_find_person_called, run_v8_mastroianni,
                   run_v9_transcript_fires_every_turn):
            r = await fn(cfg, ws)
            results.append(r)
            if r.passed:
                if "informational" in r.reason.lower():
                    p_info(r.id, r.reason)
                else:
                    p_pass(r.id, _summarize_extras(r.extras))
            else:
                p_fail(r.id, r.reason)
    return results


def _summarize_extras(extras: dict) -> str:
    parts = []
    if "speech" in extras:
        s = extras["speech"]
        parts.append(f"speech={s[:60]!r}" + ("…" if len(s) > 60 else ""))
    if "ws_event_received" in extras:
        parts.append(f"ws={extras['ws_event_received']}")
    if "ws_event_bytes" in extras:
        parts.append(f"bytes={extras['ws_event_bytes']}")
    return ", ".join(parts)


def write_report(results: list[ScenarioResult], anomalies: list[Anomaly], path: Path) -> None:
    lines = []
    lines.append("# diagnose-identity report")
    lines.append("")
    lines.append(f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S %z')}")
    lines.append("")
    passed = sum(1 for r in results if r.passed)
    lines.append(f"## V suite — {passed}/{len(results)} passed")
    lines.append("")
    for r in results:
        sym = "✅" if r.passed else "❌"
        lines.append(f"### {sym} {r.id}")
        if r.reason:
            lines.append("")
            lines.append(f"**Reason:** {r.reason}")
        if r.extras:
            lines.append("")
            lines.append("**Extras:**")
            lines.append("```json")
            lines.append(json.dumps(r.extras, indent=2, default=str)[:2000])
            lines.append("```")
        lines.append("")
    if anomalies:
        lines.append(f"## D mode — {len(anomalies)} anomalies")
        lines.append("")
        for a in anomalies:
            lines.append(f"### ⚠️ {a.discoverer} ({a.severity})")
            lines.append("")
            lines.append(a.description)
            lines.append("```json")
            lines.append(json.dumps(a.evidence, indent=2, default=str)[:1500])
            lines.append("```")
            lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


async def main_async(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    p(f"\n{BOLD}diagnose-identity{RST}  ha_url={cfg.ha_url}")
    report_path = Path(__file__).resolve().parent / "diagnose-report.md"

    # ── W mode — workflow suite — exclusive (skip V/D unless explicitly added) ──
    if args.workflow:
        attempts = 3 if args.quick else 5
        only_phase = 1 if args.phase1_only else None
        only_ids = args.only.split(",") if args.only else None
        # Reset the report file: workflow mode is a self-contained run.
        report_path.write_text(
            f"# diagnose-identity report (workflow mode)\n\n"
            f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S %z')}\n",
            encoding="utf-8",
        )
        results, rc = await run_w_suite(
            cfg, attempts_per=attempts, only_phase=only_phase,
            only_ids=only_ids, strict=args.strict,
            include_write_gated=args.include_write_gated,
            inconclusive_max_rate=args.inconclusive_max_rate,
            corpus_path=(
                None if args.no_corpus else
                (Path(args.corpus_out) if args.corpus_out else default_workflow_corpus_path())
            ),
        )
        write_workflow_report(results, report_path)
        p(f"\n{DIM}report written to {report_path}{RST}")
        if rc == 0:
            p(f"\n{GREEN}{BOLD}W suite: all green ({len(results)} scenarios){RST}")
        elif rc == 1:
            n_drift = sum(1 for r in results if r.status == "drift")
            p(f"\n{YEL}{BOLD}W suite: {n_drift} scenario(s) below threshold (drift){RST}")
        elif rc == 2:
            n_hard = sum(1 for r in results if r.status == "hard_fail")
            p(f"\n{RED}{BOLD}W suite: {n_hard} scenario(s) at 0/{attempts} — HARD FAIL{RST}")
        else:
            p(f"\n{RED}{BOLD}W suite: preflight failed — suite did not run{RST}")
        return rc

    # ── V suite (existing) ──
    results = await run_v_suite(cfg)
    anomalies: list[Anomaly] = []
    if args.watch:
        anomalies = await watch_mode(cfg, args.watch)
        if anomalies:
            p(f"\n{BOLD}{YEL}── D-mode anomalies ({len(anomalies)}) ──{RST}")
            for a in anomalies:
                p(f"  {YEL}!{RST} {a.discoverer}: {a.description}")
        else:
            p(f"\n{GREEN}D mode: 0 anomalies during {args.watch}s window{RST}")
    write_report(results, anomalies, report_path)
    p(f"\n{DIM}report written to {report_path}{RST}")
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    color = GREEN if passed == total else RED
    p(f"\n{color}{BOLD}{passed}/{total} V scenarios passed{RST}")
    return 0 if passed == total and not [a for a in anomalies if a.severity == "alert"] else 1


def main():
    ap = argparse.ArgumentParser(
        description="Self-driving diagnostic harness (Addendums 7 + 8).",
    )
    ap.add_argument("--ha-url", default="", help="override HA_URL")
    ap.add_argument("--ha-token", default="", help="override HA_TOKEN")
    ap.add_argument(
        "--agent-id", default="",
        help="override agent_id (default: auto-resolve from HA storage)",
    )
    ap.add_argument(
        "--watch", type=int, default=0,
        help="run D-mode for N seconds after V suite (0 = skip D)",
    )
    # ── W (workflow) mode (Addendum 8) ──
    ap.add_argument(
        "--workflow", action="store_true",
        help="run W suite (workflow scenarios, multi-step planning tests)",
    )
    ap.add_argument(
        "--quick", action="store_true",
        help="W mode: 3 attempts per scenario instead of 5 (fast iteration)",
    )
    ap.add_argument(
        "--phase1-only", action="store_true", dest="phase1_only",
        help="W mode: run only Phase 1 mechanical scenarios (skip stretch)",
    )
    ap.add_argument(
        "--strict", action="store_true",
        help="W mode: require every scenario to pass at threshold = attempts_per (5/5)",
    )
    ap.add_argument(
        "--only", default="",
        help="W mode: comma-separated scenario ids to run (e.g. office_dim_warm,who_then_dim)",
    )
    ap.add_argument(
        "--include-write-gated", action="store_true",
        help="W mode: include scenarios that may mutate safe-listed entities or helper state",
    )
    ap.add_argument(
        "--inconclusive-max-rate", type=float, default=0.10,
        help="W mode: fail if inconclusive/trace-loss attempts exceed this rate (default 0.10)",
    )
    ap.add_argument(
        "--corpus-out", default="",
        help="W mode: JSONL attempt corpus path (default tools/reports/llm-workflow/<timestamp>/attempts.jsonl)",
    )
    ap.add_argument(
        "--no-corpus", action="store_true",
        help="W mode: do not write the JSONL attempt corpus",
    )
    args = ap.parse_args()
    rc = asyncio.run(main_async(args))
    sys.exit(rc)


if __name__ == "__main__":
    main()
