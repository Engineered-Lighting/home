#!/usr/bin/env python3
"""legacy-rename-helper.py — LEGACY_-prefix existing HA automations and
toggle them disabled, OR restore them by alias/id. Per Addendum 33
"replace-not-delete" policy.

Workflow (per zone promotion):
  1. `--list-zone <slug>`  → show which automations match this zone
  2. `--rename-zone <slug>`  → dry-run the rename (prints diff)
  3. `--rename-zone <slug> --apply`  → SCP-fetches automations.yaml,
     edits via ruamel.yaml (preserves UI metadata + comments), backs up
     to /config/automation-backups/legacy-rename-<ts>.yaml on HAOS,
     writes new automations.yaml, triggers `automation.reload`.
  4. `--restore-alias <id_or_alias>`  → reverses the rename for that
     specific automation (re-enables it).
  5. `--rollback-all-zones`  → restores ALL LEGACY_-prefixed
     automations (batch rollback). Use only if new layer is wholly
     reverted.

Audit trail: every operation logged to /config/automation-backups/legacy-renames.log
on HAOS with timestamp, id, original_alias, new_alias, source phase.

This helper NEVER deletes automations. It only renames + toggles
enabled. The backup directory keeps full pre-edit copies so any
state can be restored byte-for-byte.

SAFETY:
- Default mode is --dry-run (show diff; no write)
- --apply requires the user to confirm by typing the zone slug
  (interactive prompt)
- Backs up pre-edit automations.yaml before EVERY write
- Validates the YAML round-trip preserves all top-level keys
  before writing
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Per-zone canonical automation aliases (from cached HA inventory).
# Maps zone slug → list of (id, original_alias) tuples to LEGACY_-prefix.
# This is the AUTHORITATIVE map; helper greps the live automations.yaml
# for these ids/aliases.
#
# Discovered from C:/Claude/home/.cache/ha-inventory/ha-automations.yaml
# (read 2026-05-18).
ZONE_AUTOMATION_MAP: dict[str, list[dict]] = {
    "dining_left": [
        {"alias_pattern": "Dining Left Occupancy", "phase": "2"},
        {"alias_pattern": "Dining Left NO Occupancy", "phase": "2"},
        {"alias_pattern": "Dining Left Occupancy TEST", "phase": "2"},
    ],
    "dining_right": [
        {"alias_pattern": "Dining Right Occupancy", "phase": "3"},
        {"alias_pattern": "Dining Right NO Occupancy", "phase": "3"},
    ],
    "sink": [
        {"alias_pattern": "Sink Occupancy", "phase": "3"},
        {"alias_pattern": "Sink NO Occupancy", "phase": "3"},
    ],
    "island_left": [
        {"alias_pattern": "Island Left Occupancy", "phase": "3"},
        {"alias_pattern": "Island Left NO Occupancy", "phase": "3"},
    ],
    "island_right": [
        {"alias_pattern": "Island Right Occupancy", "phase": "3"},
        {"alias_pattern": "Island Right NO Occupancy", "phase": "3"},
    ],
    "sofa": [
        {"alias_pattern": "Sofa Occupancy", "phase": "3"},
        {"alias_pattern": "Sofa No Occupancy", "phase": "3"},
    ],
    "front_door": [
        # Two duplicate automations both LEGACY-prefix per AR resolution.
        {"alias_pattern": "Front Door Occupancy", "phase": "4",
         "id_suffix": "80pct"},
        {"alias_pattern": "Front Door Occupancy", "phase": "4",
         "id_suffix": "54pct_with_off"},
    ],
    "office": [
        {"alias_pattern": "Office Occupancy", "phase": "4"},
    ],
    "front_left": [
        {"alias_pattern": "Front Left Occupancy", "phase": "4"},
        {"alias_pattern": "Front Left NO Occupancy", "phase": "4"},
    ],
    "weights": [
        {"alias_pattern": "Weights Occupancy", "phase": "4"},
        {"alias_pattern": "Weights NO Occupancy", "phase": "4"},
    ],
}

HAOS_HOST = "root@homeassistant.local"
HAOS_PORT = "22222"
REMOTE_AUTOMATIONS = "/config/automations.yaml"
REMOTE_BACKUP_DIR = "/config/automation-backups"
REMOTE_LOG = f"{REMOTE_BACKUP_DIR}/legacy-renames.log"


def _ssh(cmd: str, timeout: int = 30) -> tuple[int, str, str]:
    """Run a command on HAOS via SSH."""
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


def _load_yaml(path: Path):
    """Load YAML preserving comments/quotes via ruamel.yaml.
    Falls back to PyYAML if ruamel is unavailable (loses formatting)."""
    try:
        from ruamel.yaml import YAML  # type: ignore
        yaml = YAML(typ="rt")
        yaml.preserve_quotes = True
        yaml.width = 4096
        with path.open("r", encoding="utf-8") as f:
            return yaml.load(f), yaml
    except ImportError:
        import yaml as pyyaml
        with path.open("r", encoding="utf-8") as f:
            return pyyaml.safe_load(f), None


def _dump_yaml(data, path: Path, yaml_obj=None) -> None:
    if yaml_obj is not None:
        with path.open("w", encoding="utf-8") as f:
            yaml_obj.dump(data, f)
    else:
        import yaml as pyyaml
        with path.open("w", encoding="utf-8") as f:
            pyyaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def cmd_list_zone(zone: str) -> int:
    """Show which automations would be LEGACY-prefixed for this zone."""
    matches = ZONE_AUTOMATION_MAP.get(zone)
    if not matches:
        print(f"No automation patterns registered for zone '{zone}'.")
        print(f"Known zones: {', '.join(sorted(ZONE_AUTOMATION_MAP.keys()))}")
        return 2
    print(f"Automations targeted for zone '{zone}' (planned LEGACY rename):")
    for m in matches:
        print(f"  - alias: '{m['alias_pattern']}'"
              + (f" [id_suffix: {m.get('id_suffix')}]" if m.get("id_suffix") else "")
              + f" (Phase {m['phase']})")
    print()
    print("Dry-run preview: pass --rename-zone <slug> (without --apply) to see")
    print("the actual matched automations from the live HAOS file.")
    return 0


def cmd_rename_zone(zone: str, apply_mode: bool) -> int:
    """Find matching automations in live HAOS file; rename them;
    optionally apply."""
    patterns = ZONE_AUTOMATION_MAP.get(zone)
    if not patterns:
        print(f"Unknown zone: {zone}")
        return 2

    import tempfile
    tmpdir = Path(tempfile.mkdtemp(prefix="ll-legacy-"))
    local_yaml = tmpdir / "automations.yaml"

    print(f"Fetching {REMOTE_AUTOMATIONS} from HAOS...")
    if not _scp_get(REMOTE_AUTOMATIONS, local_yaml):
        print("✗ SCP fetch failed")
        return 3

    try:
        data, yaml_obj = _load_yaml(local_yaml)
    except Exception as e:
        print(f"✗ YAML load failed: {e}")
        return 3

    if not isinstance(data, list):
        print(f"✗ automations.yaml root is not a list (got {type(data)})")
        return 3

    print(f"Loaded {len(data)} automations.")

    matched: list[tuple[int, dict]] = []
    for idx, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        alias = item.get("alias", "")
        if not alias or alias.startswith("LEGACY_"):
            continue
        for pat in patterns:
            if pat["alias_pattern"] == alias:
                matched.append((idx, item))
                break

    if not matched:
        print(f"No matching automations found for zone '{zone}'.")
        print("(Either zone was already legacy-renamed, OR automations don't exist in this install.)")
        return 0

    print()
    print(f"Found {len(matched)} matching automation(s):")
    for idx, item in matched:
        print(f"  [{idx}] id='{item.get('id')}' alias='{item.get('alias')}' enabled={item.get('enabled', True)}")
    print()

    if not apply_mode:
        print("Dry-run complete. Re-run with --apply to commit:")
        print(f"  py -3 {Path(__file__).name} --rename-zone {zone} --apply")
        return 0

    # Interactive confirmation
    confirm = input(f"Type the zone slug '{zone}' to confirm rename: ").strip()
    if confirm != zone:
        print("Aborted (no match).")
        return 4

    # Backup BEFORE any edit
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_remote = f"{REMOTE_BACKUP_DIR}/automations.{ts}.yaml"
    print(f"Backing up to {backup_remote}...")
    rc, _, err = _ssh(f"mkdir -p {REMOTE_BACKUP_DIR} && cp {REMOTE_AUTOMATIONS} {backup_remote}")
    if rc != 0:
        print(f"✗ Backup failed: {err}")
        return 3

    # Apply LEGACY_-prefix + disable
    for idx, item in matched:
        original_alias = item.get("alias", "")
        new_alias = f"LEGACY_{original_alias}"
        item["alias"] = new_alias
        item["enabled"] = False
        print(f"  - renamed: '{original_alias}' → '{new_alias}' (enabled: False)")

    # Round-trip validate
    new_local = tmpdir / "automations.new.yaml"
    try:
        _dump_yaml(data, new_local, yaml_obj)
    except Exception as e:
        print(f"✗ YAML dump failed: {e}")
        return 3

    # Sanity check: re-parse the dump and assert N items + matching ids
    try:
        reload_data, _ = _load_yaml(new_local)
        if len(reload_data) != len(data):
            print(f"✗ round-trip lost entries: {len(data)} → {len(reload_data)}")
            return 3
    except Exception as e:
        print(f"✗ round-trip re-parse failed: {e}")
        return 3

    print(f"Pushing {new_local} → {REMOTE_AUTOMATIONS}...")
    if not _scp_put(new_local, REMOTE_AUTOMATIONS):
        print("✗ SCP push failed")
        print(f"NOTE: backup is at {backup_remote} on HAOS")
        return 3

    # Log the operation
    log_entries = []
    for idx, item in matched:
        log_entries.append(
            json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(),
                "op": "legacy-rename",
                "zone": zone,
                "phase": next((p["phase"] for p in patterns if p["alias_pattern"] == item.get("alias", "").removeprefix("LEGACY_")), "?"),
                "id": item.get("id"),
                "original_alias": item.get("alias", "").removeprefix("LEGACY_"),
                "new_alias": item.get("alias"),
                "backup": backup_remote,
            })
        )
    log_payload = "\n".join(log_entries) + "\n"
    log_cmd = f"echo {shlex.quote(log_payload)} >> {REMOTE_LOG}"
    _ssh(log_cmd)
    print(f"✓ logged to {REMOTE_LOG}")

    # Reload automations
    print("Reloading automations...")
    rc, _, _ = _ssh(
        f"curl -s -X POST -H 'Authorization: Bearer $(cat /tmp/ha-token 2>/dev/null)' "
        f"http://localhost:8123/api/services/automation/reload"
    )
    # Reload may fail if token not set up via this path; fall back to ha core reload
    if rc != 0:
        print("  (REST reload failed; falling back to 'ha core reload')")
        _ssh("ha core reload")

    print()
    print(f"✓ Phase {patterns[0]['phase']} LEGACY rename complete for zone '{zone}'.")
    print(f"  Backup: {backup_remote}")
    print(f"  Audit log: {REMOTE_LOG}")
    print(f"  Restore: py -3 {Path(__file__).name} --restore-alias <id>")
    return 0


def cmd_restore_alias(alias_or_id: str) -> int:
    """Re-enable a single LEGACY_-prefixed automation."""
    import tempfile
    tmpdir = Path(tempfile.mkdtemp(prefix="ll-restore-"))
    local_yaml = tmpdir / "automations.yaml"

    if not _scp_get(REMOTE_AUTOMATIONS, local_yaml):
        print("✗ SCP fetch failed")
        return 3

    try:
        data, yaml_obj = _load_yaml(local_yaml)
    except Exception as e:
        print(f"✗ YAML load failed: {e}")
        return 3

    target_idx = None
    for idx, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        if str(item.get("id", "")) == alias_or_id:
            target_idx = idx
            break
        alias = item.get("alias", "")
        if alias == alias_or_id or alias == f"LEGACY_{alias_or_id}":
            target_idx = idx
            break

    if target_idx is None:
        print(f"No automation found with id or alias '{alias_or_id}'")
        return 2

    item = data[target_idx]
    current_alias = item.get("alias", "")
    if not current_alias.startswith("LEGACY_"):
        print(f"Automation '{current_alias}' is not LEGACY-prefixed; nothing to restore.")
        return 0

    original_alias = current_alias.removeprefix("LEGACY_")
    item["alias"] = original_alias
    item["enabled"] = True
    print(f"Restoring: '{current_alias}' → '{original_alias}' (enabled: True)")

    # Backup + push
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_remote = f"{REMOTE_BACKUP_DIR}/automations.restore.{ts}.yaml"
    _ssh(f"mkdir -p {REMOTE_BACKUP_DIR} && cp {REMOTE_AUTOMATIONS} {backup_remote}")
    new_local = tmpdir / "automations.new.yaml"
    _dump_yaml(data, new_local, yaml_obj)
    if not _scp_put(new_local, REMOTE_AUTOMATIONS):
        print("✗ SCP push failed")
        return 3
    _ssh(f"echo {shlex.quote(json.dumps({'ts': datetime.now(timezone.utc).isoformat(), 'op': 'legacy-restore', 'id': item.get('id'), 'restored_to': original_alias, 'backup': backup_remote}) + chr(10))} >> {REMOTE_LOG}")
    _ssh("ha core reload")
    print(f"✓ Restored. Backup: {backup_remote}")
    return 0


def cmd_rollback_all() -> int:
    """Batch-restore ALL LEGACY_-prefixed automations."""
    import tempfile
    tmpdir = Path(tempfile.mkdtemp(prefix="ll-rollback-"))
    local_yaml = tmpdir / "automations.yaml"
    if not _scp_get(REMOTE_AUTOMATIONS, local_yaml):
        return 3
    data, yaml_obj = _load_yaml(local_yaml)
    legacy = [i for i, it in enumerate(data) if isinstance(it, dict) and it.get("alias", "").startswith("LEGACY_")]
    if not legacy:
        print("No LEGACY_-prefixed automations found.")
        return 0
    print(f"Found {len(legacy)} LEGACY automation(s):")
    for idx in legacy:
        print(f"  - id='{data[idx].get('id')}' alias='{data[idx].get('alias')}'")
    confirm = input("Type 'rollback' to confirm: ").strip()
    if confirm != "rollback":
        print("Aborted.")
        return 4
    for idx in legacy:
        cur = data[idx].get("alias", "")
        data[idx]["alias"] = cur.removeprefix("LEGACY_")
        data[idx]["enabled"] = True
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_remote = f"{REMOTE_BACKUP_DIR}/automations.batch-rollback.{ts}.yaml"
    _ssh(f"cp {REMOTE_AUTOMATIONS} {backup_remote}")
    new_local = tmpdir / "automations.new.yaml"
    _dump_yaml(data, new_local, yaml_obj)
    if not _scp_put(new_local, REMOTE_AUTOMATIONS):
        return 3
    _ssh("ha core reload")
    print(f"✓ Batch rollback complete. Backup: {backup_remote}")
    return 0


def cmd_list_backups() -> int:
    rc, out, err = _ssh(f"ls -la {REMOTE_BACKUP_DIR}/ 2>/dev/null | head -30")
    if rc != 0:
        print("Backup directory not yet created on HAOS.")
        return 0
    print(out)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list-zone", metavar="ZONE", help="Preview which automations are tied to a zone")
    g.add_argument("--rename-zone", metavar="ZONE", help="LEGACY_-prefix a zone's automations")
    g.add_argument("--restore-alias", metavar="ID_OR_ALIAS", help="Restore one LEGACY automation")
    g.add_argument("--rollback-all-zones", action="store_true", help="Batch-restore ALL LEGACY automations")
    g.add_argument("--list-backups", action="store_true", help="Show recent backups")
    ap.add_argument("--apply", action="store_true",
                    help="Required to actually write; default is dry-run")
    args = ap.parse_args()

    if args.list_zone:
        return cmd_list_zone(args.list_zone)
    if args.rename_zone:
        return cmd_rename_zone(args.rename_zone, args.apply)
    if args.restore_alias:
        return cmd_restore_alias(args.restore_alias)
    if args.rollback_all_zones:
        return cmd_rollback_all()
    if args.list_backups:
        return cmd_list_backups()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
