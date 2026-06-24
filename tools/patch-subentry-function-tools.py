#!/usr/bin/env python3
"""Augment the extended_openai_conversation subentry's `functions` YAML
with tools that are present in DEFAULT_CONF_FUNCTION_TOOLS (const.py)
but missing from the live HAOS subentry.

Addendum 30A: the subentry's `functions` key overrides defaults entirely
(per conversation.py:_get_function_tools). When new tools (find_clips,
describe_clip, recap, …) ship in const.py defaults, they don't reach
the live agent until the subentry is augmented.

Default mode is --dry-run (prints the diff, exits without writing).
Pass --apply to actually patch + reload the integration.

Tools that are intentionally omitted (load_skill, bash) by default stay
omitted — see SAFETY_SKIPLIST below. Override with --include-unsafe if
you really want them.

Usage:
    py -3 tools/patch-subentry-function-tools.py           # dry-run
    py -3 tools/patch-subentry-function-tools.py --apply   # patch + reload
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
CONST_PY = REPO_ROOT / "ha-config" / "extended_openai_conversation" / "const.py"
HA_SSH = ["ssh", "-p", "22222", "root@homeassistant.local"]
HA_STORAGE = "/config/.storage/core.config_entries"
HA_BACKUP_DIR = "/config/.storage"
INTEGRATION_DOMAIN = "extended_openai_conversation"
SUBENTRY_TITLE = "Extended OpenAI Conversation"

# Don't patch these into the subentry by default. Both are exposed in
# const.py defaults but the user appears to have intentionally omitted
# them (likely for safety). Override with --include-unsafe.
SAFETY_SKIPLIST = {"load_skill", "bash"}


def _ssh(*cmd: str, capture: bool = True) -> str:
    full = HA_SSH + list(cmd)
    res = subprocess.run(full, capture_output=capture, text=True, encoding="utf-8")
    if res.returncode != 0:
        raise SystemExit(f"ssh failed: {' '.join(full)}\nstderr: {res.stderr}")
    return res.stdout


def _ha_env() -> tuple[str, str]:
    """Pull HA_URL + HA_TOKEN from the Ubuntu bridge."""
    res = subprocess.run(
        ["ssh", "hav-ubuntu", "grep", "-E", "^(HA_URL|HA_TOKEN)=", "/opt/home-ai-voice/.env"],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        raise SystemExit("could not fetch HA_URL/HA_TOKEN from hav-ubuntu .env")
    ha_url, ha_token = "", ""
    for line in res.stdout.splitlines():
        if line.startswith("HA_URL="):
            ha_url = line.split("=", 1)[1].strip()
        elif line.startswith("HA_TOKEN="):
            ha_token = line.split("=", 1)[1].strip()
    if not ha_url or not ha_token:
        raise SystemExit("HA_URL or HA_TOKEN missing")
    return ha_url, ha_token


def _load_default_tools() -> list[dict]:
    """Parse DEFAULT_CONF_FUNCTION_TOOLS from const.py."""
    spec = __import__("importlib.util").util
    src = CONST_PY.read_text(encoding="utf-8")
    # const.py imports a lot of HA stuff we don't have on the workstation.
    # Slice out just the DEFAULT_CONF_FUNCTION_TOOLS = [ ... ] block and
    # eval it as a Python literal (it's pure-data dicts).
    start_marker = "DEFAULT_CONF_FUNCTION_TOOLS = ["
    start = src.find(start_marker)
    if start < 0:
        raise SystemExit("DEFAULT_CONF_FUNCTION_TOOLS not found in const.py")
    # Walk forward, counting brackets to find the matching ]
    # FIXED 2026-05-19: skip Python `#` comments (the original parser
    # treated apostrophes in comments like "HA's" as open quotes →
    # accidentally consumed the closing ] as string content).
    depth = 0
    i = start + len("DEFAULT_CONF_FUNCTION_TOOLS = ")
    end = -1
    in_str = None
    while i < len(src):
        c = src[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == in_str:
                in_str = None
            i += 1
            continue
        # Skip Python `#` line comments (only when NOT inside a string)
        if c == "#":
            nl = src.find("\n", i)
            if nl < 0:
                break
            i = nl + 1
            continue
        if c in ('"', "'"):
            in_str = c
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
        i += 1
    if end < 0:
        raise SystemExit("could not find end of DEFAULT_CONF_FUNCTION_TOOLS list")
    list_src = src[start + len("DEFAULT_CONF_FUNCTION_TOOLS = "):end]
    # Strip any trailing comments inside the literal that ast.literal_eval
    # would reject. Use eval with an empty namespace — list_src is pure
    # data (dicts of str→primitives), no function calls or imports.
    return eval(list_src, {"__builtins__": {}}, {})


def _tool_name(entry: dict) -> str:
    return entry.get("spec", {}).get("name", "<unnamed>")


def _slurp_subentry_state() -> tuple[dict, dict, dict, str]:
    """Download core.config_entries, locate the integration's Conversation
    subentry, parse its functions YAML.

    Returns (full_data, entry, subentry, functions_yaml_string).
    """
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = tmp.name
    subprocess.run(
        ["scp", "-P", "22222", f"root@homeassistant.local:{HA_STORAGE}", tmp_path],
        check=True, capture_output=True,
    )
    data = json.loads(Path(tmp_path).read_text(encoding="utf-8"))
    Path(tmp_path).unlink(missing_ok=True)
    for entry in data.get("data", {}).get("entries", []):
        if entry.get("domain") != INTEGRATION_DOMAIN:
            continue
        for s in entry.get("subentries", []) or []:
            if s.get("title") == SUBENTRY_TITLE:
                functions_yaml = s.get("data", {}).get("functions") or ""
                return data, entry, s, functions_yaml
    raise SystemExit(f"could not find {INTEGRATION_DOMAIN}/{SUBENTRY_TITLE} subentry")


def _compute_missing(existing: list[dict], defaults: list[dict], include_unsafe: bool) -> list[dict]:
    existing_names = {_tool_name(e) for e in existing}
    out = []
    for d in defaults:
        name = _tool_name(d)
        if name in existing_names:
            continue
        if not include_unsafe and name in SAFETY_SKIPLIST:
            continue
        out.append(d)
    return out


def _dump_yaml(entries: list[dict]) -> str:
    """Dump entries as YAML in the same shape HA expects (list of
    function-spec dicts). Use block style + 2-space indent + sort_keys=False
    to preserve readability."""
    return yaml.dump(entries, sort_keys=False, default_flow_style=False, allow_unicode=True)


def _write_subentry(full_data: dict, dry_run: bool) -> Path:
    """Serialize full_data back to a tempfile. Returns the path."""
    tmp = tempfile.NamedTemporaryFile(
        suffix=".json", delete=False, mode="w", encoding="utf-8"
    )
    json.dump(full_data, tmp, ensure_ascii=False, separators=(", ", ": "), indent=4)
    tmp.close()
    return Path(tmp.name)


def _upload_and_reload(patched_path: Path, entry_id: str, dry_run: bool) -> None:
    backup_name = f"core.config_entries.bak.preaddend30.{int(time.time())}"
    print(f"  → SSH backup: cp {HA_STORAGE} {HA_BACKUP_DIR}/{backup_name}")
    if not dry_run:
        _ssh("cp", HA_STORAGE, f"{HA_BACKUP_DIR}/{backup_name}")
    print(f"  → SCP patched file → {HA_STORAGE}")
    if not dry_run:
        subprocess.run(
            ["scp", "-P", "22222", str(patched_path),
             f"root@homeassistant.local:{HA_STORAGE}"],
            check=True, capture_output=True,
        )
    print(f"  → Reload integration entry {entry_id}")
    if not dry_run:
        ha_url, ha_token = _ha_env()
        req = urllib.request.Request(
            f"{ha_url}/api/services/homeassistant/reload_config_entry",
            data=json.dumps({"entry_id": entry_id}).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {ha_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                code = resp.getcode()
                body = resp.read().decode("utf-8", errors="replace")[:200]
                print(f"    reload response: HTTP {code}, body[:200]={body!r}")
                if code >= 400:
                    raise SystemExit(f"reload failed: {code}")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:400]
            raise SystemExit(f"reload HTTPError {e.code}: {body}")
        except Exception as e:
            raise SystemExit(f"reload failed: {e}")
        print("  → Sleeping 3s for integration to spin back up…")
        time.sleep(3)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="actually patch the file + reload (default: dry-run)")
    ap.add_argument("--include-unsafe", action="store_true",
                    help="include load_skill + bash (skipped by default)")
    ap.add_argument("--include", action="append", default=[],
                    help="only add these named tools (repeatable). Default: all missing")
    args = ap.parse_args()
    dry_run = not args.apply

    print(f"=== Subentry function_tools patcher (dry_run={dry_run}) ===\n")
    print("Step 1: parsing DEFAULT_CONF_FUNCTION_TOOLS from const.py…")
    defaults = _load_default_tools()
    print(f"  ✓ found {len(defaults)} default tools")
    default_names = [_tool_name(d) for d in defaults]
    print(f"    names: {sorted(default_names)}")

    print("\nStep 2: pulling live subentry from HAOS…")
    full_data, entry, subentry, functions_yaml = _slurp_subentry_state()
    entry_id = entry.get("entry_id")
    print(f"  ✓ entry: {entry_id} ({entry.get('title')})")
    print(f"  ✓ subentry: {subentry.get('subentry_id')} ({subentry.get('title')})")

    existing = yaml.safe_load(functions_yaml) or [] if functions_yaml else []
    existing_names = [_tool_name(e) for e in existing]
    print(f"  ✓ subentry currently has {len(existing)} tools")
    print(f"    names: {sorted(existing_names)}")

    print("\nStep 3: computing missing tools…")
    missing = _compute_missing(existing, defaults, args.include_unsafe)
    if args.include:
        missing = [m for m in missing if _tool_name(m) in set(args.include)]
    missing_names = [_tool_name(m) for m in missing]
    print(f"  → missing count: {len(missing)}")
    print(f"    missing names: {missing_names}")
    if args.include_unsafe:
        print("    (--include-unsafe: load_skill + bash will be included)")
    else:
        print(f"    (SAFETY_SKIPLIST excludes: {sorted(SAFETY_SKIPLIST)})")

    if not missing:
        print("\n✓ subentry already includes all desired tools. Nothing to do.")
        return 0

    print("\nStep 4: building merged tool list…")
    merged = existing + missing
    print(f"  ✓ merged length: {len(merged)} ({len(existing)} existing + {len(missing)} new)")

    # Re-render YAML. PRESERVE the existing YAML for already-present
    # entries (don't reformat them) by appending only the new entries'
    # YAML to the end. This minimizes the diff + keeps any user-edited
    # comments intact.
    new_entries_yaml = _dump_yaml(missing)
    # Trim trailing newlines on existing, ensure newline separator
    base = functions_yaml.rstrip()
    merged_yaml = (base + "\n" + new_entries_yaml) if base else new_entries_yaml

    # Sanity-check: parse merged YAML, assert it includes all expected
    # names. Catches YAML formatting bugs in the dumper.
    re_parsed = yaml.safe_load(merged_yaml) or []
    re_parsed_names = {_tool_name(e) for e in re_parsed}
    expected_after = set(existing_names) | set(missing_names)
    if re_parsed_names != expected_after:
        print(f"\n  ✗ sanity check failed!")
        print(f"    re-parsed names: {sorted(re_parsed_names)}")
        print(f"    expected:        {sorted(expected_after)}")
        return 1
    print(f"  ✓ merged YAML re-parses to {len(re_parsed)} tools (all names accounted for)")

    print("\nStep 5: writing patched config back to subentry…")
    subentry["data"]["functions"] = merged_yaml
    # Bump version metadata (per HA convention for storage migrations)
    cur_minor = full_data.get("minor_version", 0)
    print(f"  ✓ keeping version metadata (current minor_version={cur_minor})")
    patched_path = _write_subentry(full_data, dry_run)
    print(f"  ✓ wrote patched file: {patched_path}")
    if dry_run:
        print("\n=== DRY-RUN MODE — no changes made to HAOS ===")
        print(f"\nDIFF SUMMARY:")
        print(f"  + {len(missing)} tools added: {missing_names}")
        print(f"\nRun with --apply to actually patch + reload.")
        return 0

    print("\nStep 6: uploading + reloading integration…")
    _upload_and_reload(patched_path, entry_id, dry_run=False)

    print("\nStep 7: verifying post-patch subentry state…")
    _data, _entry, post, post_yaml = _slurp_subentry_state()
    post_entries = yaml.safe_load(post_yaml) or []
    post_names = sorted({_tool_name(e) for e in post_entries})
    print(f"  ✓ post-patch subentry has {len(post_entries)} tools")
    print(f"    names: {post_names}")
    for n in missing_names:
        if n not in post_names:
            print(f"  ✗ expected {n} missing from post-patch state")
            return 1
    print("\n✓ all expected tools confirmed in live subentry.")
    print(f"  backup saved at: {HA_BACKUP_DIR}/core.config_entries.bak.preaddend30.<ts>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
