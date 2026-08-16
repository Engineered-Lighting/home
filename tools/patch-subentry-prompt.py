#!/usr/bin/env python3
"""patch-subentry-prompt.py — surgically amend the LIVE EOC system prompt.

The subentry prompt lives in HA storage, not in a file: there is no repo
copy to edit, and the repo's component is the containment refactor that
must never be deployed. This patches the live value in place, with a
timestamped backup and a config-entry reload (no HA restart).

Default is **--dry-run**: it prints the diff and writes nothing.

Why this exists
---------------
Measured 2026-08-16, the voice tail was 6.12 s p95 on multi-tool reads.
Instrumented against the engine's own counters, turn duration correlates
with generated tokens at **r = 0.981** — the tail is decode, and decode is
reply length. The model was answering "are any lights on" by naming all
twelve entities, sometimes as a markdown bullet list, read aloud by TTS.

The prompt ALREADY forbids that, at "## How to speak": *"Never use
markdown, bullets... One short sentence is the default."* It is being
ignored only for live-state queries, and the reason is positional:
**RULE 0.7b is the last thing in the prompt**, it commands the model to
check state before answering, and it says nothing about how to report what
it finds. Non-state queries obey brevity perfectly in the same session
(33 chars, 0.38 s) while state queries enumerate (225 chars, 4.12 s).

So the fix is not another brevity rule — one exists and loses on recency.
It is a reporting clause inside the rule that causes the enumeration.

Usage:
  tools/patch-subentry-prompt.py                 # dry-run diff
  tools/patch-subentry-prompt.py --apply         # patch + reload
  tools/patch-subentry-prompt.py --revert        # restore newest backup
  tools/patch-subentry-prompt.py --show          # print the live prompt tail
"""
from __future__ import annotations

import argparse
import difflib
import json
import pathlib
import subprocess
import sys
import time

HA_SSH = ["ssh", "-o", "BatchMode=yes", "-p", "22222", "root@homeassistant.local"]
HA_STORAGE = "/config/.storage/core.config_entries"
HA_URL = "http://192.168.0.125:8123"
DOMAIN = "extended_openai_conversation"

# The anchor: the final rule in the live prompt, matched on its heading so a
# reworded body still patches. If this is not found the tool refuses rather
# than appending somewhere arbitrary.
ANCHOR = "# RULE 0.7b - LIVE STATE AND COMPARISON QUERIES"

# Appended to RULE 0.7b. Deliberately short, and it names the conflict it is
# resolving — the model is not disobeying "How to speak" out of nowhere, it
# is following the more recent instruction to report state.
PATCH = """

REPORTING WHAT YOU FIND: answer in ONE spoken sentence. When several entities match, summarise by count and area — "Yes, twelve lights are on, mostly the living room and kitchen" — instead of naming each one. Name individual entities only when the user asked which ones. Never volunteer brightness percentages, colour temperatures, or entity_ids unless the user asked for that specific value. The "How to speak" rules above apply to state answers exactly as they do to everything else: this rule tells you to CHECK the state, not to recite it."""


def ssh_read(path: str) -> str:
    r = subprocess.run(HA_SSH + [f"cat {path}"], capture_output=True, timeout=90)
    if r.returncode != 0:
        sys.exit(f"cannot read {path}: {r.stderr.decode()[:200]}")
    return r.stdout.decode("utf-8", "replace")


def ha_token() -> str:
    envf = pathlib.Path("/opt/home-ai-voice/.env")
    for line in envf.read_text(errors="replace").splitlines():
        if line.startswith("HA_TOKEN="):
            return line.split("=", 1)[1].strip().strip("\"'")
    sys.exit("no HA_TOKEN in /opt/home-ai-voice/.env")


def locate(doc: dict):
    """Return (entry, conversation subentry) or exit."""
    for e in doc["data"]["entries"]:
        if e.get("domain") == DOMAIN:
            for s in e.get("subentries", []) or []:
                if s.get("subentry_type") == "conversation":
                    return e, s
    sys.exit(f"no {DOMAIN} conversation subentry found")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--revert", action="store_true")
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()

    if args.revert:
        listing = subprocess.run(
            HA_SSH + ["ls -1t /config/.storage/core.config_entries.bak.prompt.* 2>/dev/null | head -1"],
            capture_output=True, timeout=60).stdout.decode().strip()
        if not listing:
            sys.exit("no prompt backup found on the HA host")
        print(f"restoring {listing}")
        subprocess.run(HA_SSH + [f"cp -a {listing} {HA_STORAGE}"], timeout=60, check=True)
        _reload()
        print("reverted and reloaded.")
        return 0

    raw = ssh_read(HA_STORAGE)
    doc = json.loads(raw)
    entry, sub = locate(doc)
    prompt = sub["data"].get("prompt", "")

    if args.show:
        print(prompt[-1500:])
        return 0

    if ANCHOR not in prompt:
        sys.exit(f"anchor not found in the live prompt: {ANCHOR!r}\n"
                 "The prompt has changed shape; re-read it before patching.")
    if "REPORTING WHAT YOU FIND" in prompt:
        print("already patched — the reporting clause is present. Nothing to do.")
        return 0

    idx = prompt.index(ANCHOR)
    end = prompt.find("\n#", idx + len(ANCHOR))
    end = len(prompt) if end == -1 else end
    patched = prompt[:end] + PATCH + prompt[end:]

    print("=== diff (live prompt) ===")
    for line in difflib.unified_diff(
            prompt[-1200:].splitlines(), patched[-1200 - len(PATCH):].splitlines(),
            fromfile="live", tofile="patched", lineterm="", n=2):
        print(line)
    print(f"\nprompt: {len(prompt)} -> {len(patched)} chars (+{len(patched) - len(prompt)})")

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to patch and reload.")
        return 0

    sub["data"]["prompt"] = patched
    stamp = int(time.time())
    backup = f"{HA_STORAGE}.bak.prompt.{stamp}"
    print(f"\nbacking up -> {backup}")
    subprocess.run(HA_SSH + [f"cp -a {HA_STORAGE} {backup}"], timeout=60, check=True)

    payload = json.dumps(doc, ensure_ascii=False)
    proc = subprocess.run(HA_SSH + [f"cat > {HA_STORAGE}"], input=payload.encode(),
                          capture_output=True, timeout=120)
    if proc.returncode != 0:
        sys.exit(f"write failed: {proc.stderr.decode()[:200]}")
    print("written.")
    _reload(entry["entry_id"])
    print("\nPatched and reloaded. Revert any time with --revert.")
    return 0


def _reload(entry_id: str | None = None) -> None:
    import urllib.request
    tok = ha_token()
    if entry_id is None:
        doc = json.loads(ssh_read(HA_STORAGE))
        entry_id = locate(doc)[0]["entry_id"]
    body = json.dumps({"entry_id": entry_id}).encode()
    req = urllib.request.Request(
        f"{HA_URL}/api/services/homeassistant/reload_config_entry", data=body,
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            print(f"reload_config_entry -> HTTP {r.status}")
    except Exception as e:  # noqa: BLE001
        print(f"reload FAILED ({e}). The file is patched; reload the "
              f"'{DOMAIN}' entry from the HA UI, or --revert.")


if __name__ == "__main__":
    raise SystemExit(main())
