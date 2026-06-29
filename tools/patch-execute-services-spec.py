#!/usr/bin/env python3
"""One-shot patcher: extend the LIVE subentry's execute_services spec
with the brightness_pct / color_temp_kelvin / volume_level / etc.
properties (Addendum 31 follow-up).

Mirrors the patch-subentry-function-tools.py SCP+edit+SCP+reload flow,
but updates a single tool's properties instead of inserting new tools.

The subentry stores `functions` as a YAML string inside the JSON
config_entries file. We parse the YAML, locate the execute_services
spec, replace its `service_data` block with the rich version, dump
YAML back, write the JSON, SCP to HAOS, trigger HA restart.

Usage:
    py -3 tools/patch-execute-services-spec.py           # dry-run diff
    py -3 tools/patch-execute-services-spec.py --apply   # write + restart
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

REPO = Path(r"C:/Claude/home").resolve()
HA_SSH = ["ssh", "-p", "22222", "root@homeassistant.local"]
HA_STORAGE = "/config/.storage/core.config_entries"


# The new service_data schema. Mirrors const.py's updated DEFAULT_CONF_FUNCTION_TOOLS
# but trimmed/copied as a dict that pyyaml can dump cleanly.
NEW_SERVICE_DATA = {
    "type": "object",
    "description": (
        "Service-call data. Targets via entity_id/area_id/device_id. "
        "For light.turn_on/light.toggle you SHOULD include brightness_pct "
        "(0-100) AND/OR color_temp_kelvin when the user specifies them. "
        "Calling light.turn_on with no brightness_pct keeps the light at "
        "its PRIOR brightness — DO NOT claim 'set to 100%' unless "
        "brightness_pct: 100 was emitted. For media_player.volume_set "
        "include volume_level (0.0-1.0). For climate.set_temperature "
        "include temperature."
    ),
    "properties": {
        "entity_id": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Target entity_ids (e.g., ['light.kitchen', 'light.office']).",
        },
        "area_id": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Target area_ids (e.g., ['kitchen']) — acts on all entities in the area.",
        },
        "device_id": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Target device_ids (less common than entity_id).",
        },
        "brightness_pct": {
            "type": "number",
            "minimum": 0, "maximum": 100,
            "description": (
                "Brightness 0-100. MUST be set for light.turn_on when "
                "user requests a level (e.g., 'dim to 30%', 'turn lights "
                "up to 100%'). Omitting it keeps the prior level."
            ),
        },
        "brightness": {
            "type": "integer",
            "minimum": 0, "maximum": 255,
            "description": "Brightness 0-255 (HA native). Prefer brightness_pct unless asked in 0-255.",
        },
        "color_temp_kelvin": {
            "type": "integer",
            "minimum": 1500, "maximum": 10000,
            "description": (
                "Color temperature in Kelvin. ~2200K=warm, ~3500K=neutral, "
                "~5500K=cool/daylight. Set when user says 'warm/cool/"
                "neutral/daylight'."
            ),
        },
        "rgb_color": {
            "type": "array",
            "items": {"type": "integer", "minimum": 0, "maximum": 255},
            "minItems": 3, "maxItems": 3,
            "description": "[R, G, B] 0-255 each. Use for named colors.",
        },
        "transition": {
            "type": "number",
            "minimum": 0,
            "description": "Transition time (seconds) for light/cover changes.",
        },
        "volume_level": {
            "type": "number",
            "minimum": 0, "maximum": 1,
            "description": "Volume 0.0-1.0 for media_player.volume_set. NOT 0-100.",
        },
        "media_content_id": {
            "type": "string",
            "description": "URI/URL for media_player.play_media (e.g., Spotify URI).",
        },
        "media_content_type": {
            "type": "string",
            "description": "Type for play_media: music, playlist, station, video.",
        },
        "temperature": {
            "type": "number",
            "description": "Target temperature for climate.set_temperature.",
        },
        "hvac_mode": {
            "type": "string",
            "description": "HVAC mode: heat, cool, auto, heat_cool, off, fan_only, dry.",
        },
        "position": {
            "type": "integer",
            "minimum": 0, "maximum": 100,
            "description": "Cover/fan position 0-100.",
        },
    },
}


def fetch_storage() -> tuple[Path, dict]:
    """SCP core.config_entries down, load as dict."""
    tmp = Path(tempfile.mkdtemp()) / "core.config_entries"
    res = subprocess.run(
        ["scp", "-P", "22222",
         "root@homeassistant.local:" + HA_STORAGE, str(tmp)],
        capture_output=True, text=True, timeout=15,
    )
    if res.returncode != 0:
        raise SystemExit(f"SCP failed: {res.stderr}")
    with open(tmp, encoding="utf-8") as f:
        data = json.load(f)
    return tmp, data


def patch_subentry_functions(funcs_yaml: str) -> tuple[str, bool]:
    """Parse the functions YAML, replace execute_services.service_data,
    return (new_yaml, changed_bool)."""
    if not funcs_yaml.strip():
        return funcs_yaml, False
    tools = yaml.safe_load(funcs_yaml)
    if not isinstance(tools, list):
        return funcs_yaml, False

    changed = False
    for tool in tools:
        spec = tool.get("spec", {}) or {}
        if spec.get("name") != "execute_services":
            continue
        params = spec.get("parameters", {}) or {}
        lst = params.get("properties", {}).get("list", {})
        items = lst.get("items", {})
        service_data = items.get("properties", {}).get("service_data", {})
        # Replace it
        items.setdefault("properties", {})["service_data"] = NEW_SERVICE_DATA
        changed = True
        break

    if not changed:
        return funcs_yaml, False

    # Re-dump as YAML preserving block style + reasonable line widths.
    new_yaml = yaml.dump(tools, sort_keys=False, default_flow_style=False,
                          allow_unicode=True, width=120)
    return new_yaml, True


def apply_to_storage(data: dict) -> tuple[dict, bool, str]:
    """Walk subentries, patch execute_services where it appears.
    Returns (modified_data, changed_bool, summary)."""
    summary_lines = []
    changed_any = False
    for entry in data.get("data", {}).get("entries", []):
        if entry.get("domain") != "extended_openai_conversation":
            continue
        for sub in entry.get("subentries", []):
            sub_data = sub.get("data", {}) or {}
            funcs = sub_data.get("functions", "") or ""
            new_funcs, changed = patch_subentry_functions(funcs)
            if changed:
                old_len = len(funcs)
                new_len = len(new_funcs)
                sub_data["functions"] = new_funcs
                sub["data"] = sub_data
                summary_lines.append(
                    f"  subentry {sub.get('subentry_id', '?')[:8]}: "
                    f"functions YAML {old_len} → {new_len} chars"
                )
                changed_any = True
    return data, changed_any, "\n".join(summary_lines)


def backup_remote() -> str:
    name = f"core.config_entries.bak.qa.{int(time.time())}"
    res = subprocess.run(
        HA_SSH + [f"cp /config/.storage/core.config_entries /config/.storage/{name}"],
        capture_output=True, text=True, timeout=10,
    )
    if res.returncode != 0:
        raise SystemExit(f"backup failed: {res.stderr}")
    return name


def push_storage(local_path: Path) -> None:
    res = subprocess.run(
        ["scp", "-P", "22222", str(local_path),
         "root@homeassistant.local:" + HA_STORAGE],
        capture_output=True, text=True, timeout=15,
    )
    if res.returncode != 0:
        raise SystemExit(f"SCP push failed: {res.stderr}")


def restart_ha() -> None:
    print("  → ha core restart (this takes ~30s)...")
    subprocess.run(HA_SSH + ["ha core restart"], capture_output=True, timeout=10)
    # Poll until back up
    import urllib.request, urllib.error
    deadline = time.time() + 90
    while time.time() < deadline:
        try:
            req = urllib.request.Request("http://192.168.0.125:8123/api/")
            urllib.request.urlopen(req, timeout=2)
            return  # 200 or any non-network response
        except urllib.error.HTTPError:
            return  # 401 = up
        except Exception:
            time.sleep(3)
    raise SystemExit("HA didn't come back within 90s")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="Actually write + restart (default: dry-run)")
    ap.add_argument("--no-restart", action="store_true",
                    help="Apply but skip ha core restart")
    args = ap.parse_args()

    print("Fetching live core.config_entries...")
    local_path, data = fetch_storage()

    print("Patching execute_services.service_data in subentry(s)...")
    new_data, changed, summary = apply_to_storage(data)
    if not changed:
        print("  No subentries needed patching (already current or no execute_services)")
        return 0
    print(summary)

    # Diff: count brightness_pct mentions before/after
    raw_before = local_path.read_text(encoding="utf-8")
    raw_after = json.dumps(new_data, indent=4, ensure_ascii=False)
    print(f"\nDiff stats:")
    print(f"  brightness_pct mentions: {raw_before.count('brightness_pct')} → {raw_after.count('brightness_pct')}")
    print(f"  color_temp_kelvin mentions: {raw_before.count('color_temp_kelvin')} → {raw_after.count('color_temp_kelvin')}")
    print(f"  volume_level mentions: {raw_before.count('volume_level')} → {raw_after.count('volume_level')}")

    if not args.apply:
        print("\n[DRY RUN] No changes pushed. Add --apply to deploy.")
        return 0

    # Write to local tmp + SCP up
    out = local_path.with_suffix(".json.patched")
    out.write_text(raw_after, encoding="utf-8")

    print(f"\nBacking up remote...")
    bk = backup_remote()
    print(f"  backup: {bk}")

    print(f"Pushing patched file to HAOS...")
    push_storage(out)

    if not args.no_restart:
        restart_ha()
        print("  HA is back up")

    print("\nDone. Verify by re-running the conversation API test.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
