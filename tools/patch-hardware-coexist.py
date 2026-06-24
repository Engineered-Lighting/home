#!/usr/bin/env python3
"""patch-hardware-coexist.py — P-2B helper (Addendum 33 AR33-32 / US-1-8).

Inserts a `input_datetime.set_datetime` action at the START of an
existing automation's actions list so the new lighting layer's
60s suppression window is honored after a hardware-control touch
(Pilar/Symfonisk/Aurora/Aqara wall switches).

Uses ruamel.yaml round-trip to preserve comments + ordering + quote
styles that HA's UI cares about.

Workflow:
  1. SCP automations.yaml from HAOS
  2. Locate target automation by id
  3. Insert the new action (at top of actions OR at top of nested
     sequence, depending on target spec)
  4. Backup /config/automations.yaml → /config/automation-backups/
  5. Push patched file back
  6. Reload automations
  7. Validate the automation re-armed

Default mode is --dry-run (prints diff, exits). Use --apply to commit.

CLI:
  py -3 tools/patch-hardware-coexist.py --target <name>
  py -3 tools/patch-hardware-coexist.py --target <name> --apply
  py -3 tools/patch-hardware-coexist.py --list-targets

Targets defined in TARGETS table below (Phase 0a scope = 3 in-scope
automations; expand as more zones promote in P-3 / P-7).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

HAOS_HOST = "root@homeassistant.local"
HAOS_PORT = "22222"
REMOTE_AUTOMATIONS = "/config/automations.yaml"
REMOTE_BACKUP_DIR = "/config/automation-backups"


# Per-target spec:
#   id          — HA automation id (string match against entry['id'])
#   path        — where to insert the new action ('actions' = top, or
#                 a list of keys to walk into for nested insertion)
#   zones       — zone slugs whose last_manual_at timestamp gets bumped
#   description — human-readable note for the surface output
TARGETS = {
    "pilar_kitchen": {
        "id": "1768731841780",
        "alias": "Pilar Knob - Kitchen",
        "path": ["actions", 0, "choose", 0, "sequence"],
        "zones": ["dining_left", "dining_right", "sink", "island_left", "island_right"],
        "description": "Pilar Kitchen knob rotation → 5 kitchen zone touches",
    },
    "aqara_all_on": {
        "id": "1778459968791",
        "alias": "Aqara Living Room Switch 1 -- All Lights ON",
        "path": ["actions"],
        "zones": ["sink", "island_left", "island_right"],  # kitchen-area zones only (dining is dining_room area)
        "description": "Aqara All-Lights ON → 3 kitchen zones",
    },
    "aqara_all_off": {
        "id": "1768934708918",
        "alias": "Aqara Living Room Switch 1 -- All Lights OFF",
        "path": ["actions"],
        "zones": ["sink", "island_left", "island_right"],
        "description": "Aqara All-Lights OFF → 3 kitchen zones",
    },
}


def _ssh(cmd: str, timeout: int = 30) -> tuple[int, str, str]:
    r = subprocess.run(
        ["ssh", "-p", HAOS_PORT, "-o", "StrictHostKeyChecking=no",
         HAOS_HOST, cmd],
        capture_output=True, text=True, timeout=timeout,
    )
    return (r.returncode, r.stdout, r.stderr)


def _scp_get(remote: str, local: Path, timeout: int = 30) -> bool:
    r = subprocess.run(
        ["scp", "-P", HAOS_PORT, "-o", "StrictHostKeyChecking=no",
         f"{HAOS_HOST}:{remote}", str(local)],
        capture_output=True, text=True, timeout=timeout,
    )
    return r.returncode == 0


def _scp_put(local: Path, remote: str, timeout: int = 30) -> bool:
    r = subprocess.run(
        ["scp", "-P", HAOS_PORT, "-o", "StrictHostKeyChecking=no",
         str(local), f"{HAOS_HOST}:{remote}"],
        capture_output=True, text=True, timeout=timeout,
    )
    return r.returncode == 0


def _build_action(zones: list[str]) -> CommentedMap:
    """Build the input_datetime.set_datetime action node."""
    target_entities = CommentedSeq(
        f"input_datetime.living_lights_zone_{z}_last_manual_at" for z in zones
    )
    target = CommentedMap()
    target["entity_id"] = target_entities

    data = CommentedMap()
    data["datetime"] = "{{ now().isoformat() }}"

    action = CommentedMap()
    action["action"] = "input_datetime.set_datetime"
    action["target"] = target
    action["data"] = data
    return action


def _walk_to(root, path: list):
    """Walk the YAML tree by key/index path; return the container to mutate."""
    node = root
    for step in path:
        if isinstance(step, int):
            node = node[step]
        else:
            node = node[step]
    return node


def _find_automation(data, target_id: str):
    """Find automation entry with matching id; return (index, entry)."""
    for idx, entry in enumerate(data):
        if isinstance(entry, dict) and str(entry.get("id", "")) == target_id:
            return idx, entry
    return None, None


def _already_patched(actions_list, marker_entity: str) -> bool:
    """Heuristic: scan actions for an existing input_datetime.set_datetime
    that touches our marker entity_id."""
    for item in actions_list:
        if not isinstance(item, dict):
            continue
        action_name = item.get("action") or item.get("service")
        if action_name != "input_datetime.set_datetime":
            continue
        target = item.get("target", {}) or {}
        eids = target.get("entity_id", []) or []
        if isinstance(eids, str):
            eids = [eids]
        for eid in eids:
            if marker_entity in str(eid):
                return True
    return False


def cmd_list_targets() -> int:
    print("Hardware-coexistence targets:")
    for name, spec in TARGETS.items():
        print(f"  {name}: id={spec['id']} alias={spec['alias']!r}")
        print(f"    zones: {spec['zones']}")
        print(f"    path: {spec['path']}")
    return 0


def cmd_apply_target(target_name: str, apply_mode: bool) -> int:
    spec = TARGETS.get(target_name)
    if not spec:
        print(f"unknown target: {target_name}")
        print(f"available: {sorted(TARGETS.keys())}")
        return 2

    tmpdir = Path(tempfile.mkdtemp(prefix="ll-patch-hw-"))
    local_yaml = tmpdir / "automations.yaml"
    print(f"[{target_name}] fetching {REMOTE_AUTOMATIONS}...")
    if not _scp_get(REMOTE_AUTOMATIONS, local_yaml):
        print(f"[{target_name}] SCP fetch failed")
        return 3

    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.width = 4096
    with local_yaml.open("r", encoding="utf-8") as f:
        data = yaml.load(f)

    idx, entry = _find_automation(data, spec["id"])
    if entry is None:
        print(f"[{target_name}] automation id {spec['id']} not found")
        return 3

    print(f"[{target_name}] found at index {idx}: alias={entry.get('alias')!r}")

    # Walk to insertion point
    try:
        container = _walk_to(entry, spec["path"])
    except (KeyError, IndexError, TypeError) as e:
        print(f"[{target_name}] could not walk path {spec['path']}: {e}")
        return 3

    if not isinstance(container, (list, CommentedSeq)):
        print(f"[{target_name}] insertion point is not a list: {type(container).__name__}")
        return 3

    marker = f"input_datetime.living_lights_zone_{spec['zones'][0]}_last_manual_at"
    if _already_patched(container, marker):
        print(f"[{target_name}] already patched (found existing set_datetime targeting {marker})")
        return 0

    action = _build_action(spec["zones"])
    container.insert(0, action)

    # Render diff for surface
    diff_out = tmpdir / "automations.patched.yaml"
    with diff_out.open("w", encoding="utf-8") as f:
        yaml.dump(data, f)

    # Show what changed (lines around the inserted action)
    print(f"[{target_name}] dry-run diff (proposed insertion):")
    print(f"   new action targets: {[f'input_datetime.living_lights_zone_{z}_last_manual_at' for z in spec['zones']]}")

    if not apply_mode:
        print(f"[{target_name}] DRY-RUN — pass --apply to write")
        return 0

    # Backup
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_remote = f"{REMOTE_BACKUP_DIR}/automations.hwcoexist.{target_name}.{ts}.yaml"
    print(f"[{target_name}] backing up → {backup_remote}")
    rc, _, err = _ssh(f"mkdir -p {REMOTE_BACKUP_DIR} && cp {REMOTE_AUTOMATIONS} {backup_remote}")
    if rc != 0:
        print(f"[{target_name}] backup failed: {err}")
        return 3

    # Push patched file
    print(f"[{target_name}] pushing patched file...")
    if not _scp_put(diff_out, REMOTE_AUTOMATIONS):
        print(f"[{target_name}] SCP push failed (backup at {backup_remote})")
        return 3

    # Reload automations
    print(f"[{target_name}] reloading automations...")
    _ssh("ha core reload 2>&1 | head -5")  # safer than reload_config_entry

    print(f"[{target_name}] PATCHED. Backup: {backup_remote}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list-targets", action="store_true")
    g.add_argument("--target", help="Target name from TARGETS table")
    g.add_argument("--all", action="store_true", help="Apply ALL targets sequentially")
    ap.add_argument("--apply", action="store_true",
                    help="Required to actually write; default is dry-run")
    args = ap.parse_args()

    if args.list_targets:
        return cmd_list_targets()

    if args.all:
        rc_overall = 0
        for name in TARGETS:
            print(f"\n=== TARGET: {name} ===")
            rc = cmd_apply_target(name, args.apply)
            if rc != 0:
                rc_overall = rc
                print(f"=== {name} returned {rc}; continuing to next target ===")
        return rc_overall

    return cmd_apply_target(args.target, args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
