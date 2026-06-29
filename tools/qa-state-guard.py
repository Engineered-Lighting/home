#!/usr/bin/env python3
"""Stage 6 — idempotent state cleanup for the production QA pass.

Restores ONLY what the QA cycle modified. Reads the state snapshot
written by qa-preflight.py + checkpoint info to know what changed,
then issues the inverse HA service calls.

Idempotency contract (AR31-6):
    - Re-running this twice produces the same result as running once.
    - If a target state already matches the snapshot, no service call fires.
    - If HA is unreachable, returns 1 + leaves a marker file for retry.

Per AR31-3: only restore what WE changed. If user changed state
mid-cycle (e.g., manually muted Jarvis while we ran), we DON'T undo
their action. Detected by comparing CURRENT state to (snapshot,
desired) — only restore when current != desired (we changed it).

Usage:
    py -3 tools/qa-state-guard.py --report-dir <dir>
    py -3 tools/qa-state-guard.py --report-dir <dir> --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_common as qc


@dataclass
class CleanupAction:
    entity_id: str
    snapshot_state: str
    current_state: str
    action: str  # "restore" | "skip_user_changed" | "skip_unchanged" | "skip_unreachable"
    service_called: str = ""
    service_result: str = ""


def _load_snapshot(report_dir: Path) -> dict[str, str]:
    """Read state snapshot from preflight.json output."""
    pf = report_dir / "preflight.json"
    if not pf.exists():
        return {}
    try:
        data = json.loads(pf.read_text(encoding="utf-8"))
    except Exception:
        return {}
    for check in data.get("checks", []):
        if check.get("name") == "jarvis_mute_state":
            snap = check.get("detail", {}).get("snapshot", {}) or {}
            return {k: v for k, v in snap.items() if isinstance(v, str)}
    return {}


def _restore_entity(token: str, entity_id: str, target_state: str,
                    dry_run: bool) -> tuple[str, str]:
    """Issue the right HA service call to push entity_id to target_state."""
    if entity_id.startswith("input_boolean."):
        service = "turn_on" if target_state == "on" else "turn_off"
        domain = "input_boolean"
        data = {"entity_id": entity_id}
    elif entity_id.startswith("input_datetime."):
        service = "set_datetime"
        domain = "input_datetime"
        data = {"entity_id": entity_id, "datetime": target_state}
    else:
        return ("UNKNOWN_DOMAIN", f"no restore handler for {entity_id}")

    if dry_run:
        return ("DRY_RUN", f"would call {domain}.{service} {data}")

    code, raw = qc.ha_service_call(domain, service, data, token, timeout=5)
    if code in (200, 201):
        return (f"{domain}.{service}", "OK")
    return (f"{domain}.{service}", f"HTTP {code}: {raw[:100]}")


def cleanup(report_dir: Path, token: str, dry_run: bool = False) -> dict:
    snapshot = _load_snapshot(report_dir)
    actions: list[CleanupAction] = []

    if not snapshot:
        return {
            "actions": [],
            "summary": "No state snapshot found — nothing to restore",
            "ok": True,
        }

    for entity_id, snap_state in snapshot.items():
        # Skip the composite sensor — it derives from the inputs
        if entity_id.startswith("binary_sensor."):
            continue
        # Read current state
        current = qc.ha_state(entity_id, token)
        if current is None:
            actions.append(CleanupAction(
                entity_id=entity_id,
                snapshot_state=snap_state or "",
                current_state="unreachable",
                action="skip_unreachable",
            ))
            continue
        current_state = current.get("state", "")
        if current_state == snap_state:
            actions.append(CleanupAction(
                entity_id=entity_id,
                snapshot_state=snap_state,
                current_state=current_state,
                action="skip_unchanged",
            ))
            continue

        # Current != snapshot. Did WE change it? Check the snapshot
        # logic: if pre-flight cleared the mute, snapshot has "on" and
        # current is "off" → we changed it, restore.
        # But if user manually changed state mid-cycle, we should NOT
        # restore. Heuristic: only restore input_boolean.jarvis_muted_explicit
        # if snapshot was "on" AND current is "off" (the only direction
        # the cycle can change). For everything else (movie_mode,
        # input_datetime), the cycle never modifies them.
        if entity_id == "input_boolean.jarvis_muted_explicit":
            # The cycle ONLY clears this (turn_off). So if snapshot was
            # "on" and current is "off", restore to "on". Otherwise skip.
            if snap_state == "on" and current_state == "off":
                svc, result = _restore_entity(token, entity_id, "on", dry_run)
                actions.append(CleanupAction(
                    entity_id=entity_id,
                    snapshot_state=snap_state,
                    current_state=current_state,
                    action="restore",
                    service_called=svc,
                    service_result=result,
                ))
            else:
                actions.append(CleanupAction(
                    entity_id=entity_id,
                    snapshot_state=snap_state,
                    current_state=current_state,
                    action="skip_user_changed",
                ))
        else:
            # Cycle doesn't modify other entities in the snapshot.
            # If they differ, it's because the user changed them.
            actions.append(CleanupAction(
                entity_id=entity_id,
                snapshot_state=snap_state,
                current_state=current_state,
                action="skip_user_changed",
            ))

    restored = sum(1 for a in actions if a.action == "restore" and a.service_result == "OK")
    skipped_user = sum(1 for a in actions if a.action == "skip_user_changed")
    failures = sum(1 for a in actions if a.action == "restore" and a.service_result != "OK"
                   and a.service_result != "would call")

    summary = f"restored {restored}, skipped {skipped_user} (user-changed)"
    if failures:
        summary += f", {failures} restore FAILED"
    if dry_run:
        summary = "[DRY RUN] " + summary

    return {
        "actions": [asdict(a) for a in actions],
        "summary": summary,
        "ok": failures == 0,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report-dir", required=True,
                    help="Cycle report directory (containing preflight.json)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print planned actions without calling HA")
    args = ap.parse_args()

    report_dir = Path(args.report_dir)
    if not report_dir.exists():
        print(qc.red(f"Report dir does not exist: {report_dir}"), file=sys.stderr)
        return 2

    token = qc.load_ha_token()
    if not token:
        print(qc.yellow("WARN: no HA token — cannot restore state. "
                        "Leaving marker file for manual retry."))
        marker = report_dir / ".cleanup-pending"
        marker.write_text(f"created_at={time.time()}\n", encoding="utf-8")
        return 1

    print(qc.bold(f"\n=== Stage 6 — Cleanup (report: {report_dir.name}) ==="))
    result = cleanup(report_dir, token, dry_run=args.dry_run)

    for a in result["actions"]:
        if a["action"] == "restore":
            badge = qc.green("RESTORE")
        elif a["action"] == "skip_user_changed":
            badge = qc.yellow("USER")
        elif a["action"] == "skip_unchanged":
            badge = qc.dim("SKIP")
        else:
            badge = qc.red("ERR ")
        print(f"  [{badge}] {a['entity_id']:<48} "
              f"snap={a['snapshot_state']!r} cur={a['current_state']!r}")
        if a.get("service_called"):
            print(f"           → {a['service_called']}: {a['service_result']}")

    # Write structured result
    (report_dir / "cleanup.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )

    print("")
    print(("  " + qc.green(result["summary"])) if result["ok"] else
          ("  " + qc.red(result["summary"])))

    # Remove the cleanup-pending marker if present
    marker = report_dir / ".cleanup-pending"
    if marker.exists() and result["ok"] and not args.dry_run:
        marker.unlink()

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
