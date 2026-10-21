"""Host signal readers. Each parser takes text or a root path, so the tests
replay recorded or synthetic fixtures; nothing here writes or changes state.

Signal keys (all floats):
- ``temp.zone.<type>``: /sys/class/thermal zones, plus ``temp.zone_max``
- ``temp.hwmon.<chip>.<label>``: hwmon sensors (k10temp Tctl/Tccd, nvme Composite)
- ``temp.tegra.<name>``: tegrastats temperatures (Jetson)
- ``mem.total_mib``, ``mem.available_mib``, ``swap.total_mib``, ``swap.used_pct``
- ``gpu.<i>.temp_c``, ``gpu.<i>.mem_used_mib``, ``gpu.<i>.mem_free_mib``,
  ``gpu.<i>.util_pct``, plus the worst across GPUs: ``gpu.temp_c``,
  ``gpu.mem_used_mib``, ``gpu.mem_free_mib``
- ``disk.<name>.free_gib``
"""
from __future__ import annotations

import fnmatch
import math
import glob
import json
import os
import re
import shutil

_KEY_RE = re.compile(r"[^a-z0-9_]+")


def _key(text):
    return _KEY_RE.sub("_", str(text).strip().lower()).strip("_") or "unknown"


def _read(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    except (OSError, TypeError, UnicodeError):
        # Some unsupported Jetson sysfs channels return no bytes, causing the
        # text decoder to raise TypeError. Treat an unreadable required input
        # as missing evidence; never let it bypass the monitor's Stop latch.
        return None


def _put_max(out, key, value):
    if value is not None and (key not in out or value > out[key]):
        out[key] = value


def read_thermal_zones(root="/sys/class/thermal", exclude_types=()):
    """Thermal zones in °C. Unreadable zones are skipped (as jetson_lifecycle does)."""
    out = {}
    excluded = {_key(t) for t in exclude_types}
    for zone in sorted(glob.glob(os.path.join(root, "thermal_zone*"))):
        kind = _key(_read(os.path.join(zone, "type")) or os.path.basename(zone))
        if kind in excluded:
            continue
        raw = _read(os.path.join(zone, "temp"))
        try:
            value = int(raw) / 1000.0
        except (TypeError, ValueError):
            continue
        if value <= -40 or value >= 200:
            continue
        _put_max(out, "temp.zone." + kind, value)
    if out:
        out["temp.zone_max"] = max(out.values())
    return out


def read_hwmon(root="/sys/class/hwmon"):
    """hwmon temperatures in °C, keyed by chip name and label (max over chips
    with the same name, e.g. two NVMe drives)."""
    out = {}
    for chip in sorted(glob.glob(os.path.join(root, "hwmon*"))):
        name = _key(_read(os.path.join(chip, "name")) or os.path.basename(chip))
        for sensor in sorted(glob.glob(os.path.join(chip, "temp*_input"))):
            raw = _read(sensor)
            try:
                value = int(raw) / 1000.0
            except (TypeError, ValueError):
                continue
            stem = os.path.basename(sensor)[: -len("_input")]
            label = _key(_read(os.path.join(chip, stem + "_label")) or stem)
            if -40 < value < 200:
                _put_max(out, "temp.hwmon.%s.%s" % (name, label), value)
    return out


def hwmon_input_available(root, row):
    """Check each commissioned input independently of aggregate maxima."""
    chip, sensor = row['path'].split('/')
    stem = sensor[:-len('_input')]
    if (_read(os.path.join(root,chip,'name'))!=row['chip']
            or (_read(os.path.join(root,chip,stem+'_label')) or stem)!=row['label']):
        return False
    try:
        value=int(_read(os.path.join(root,chip,sensor)))/1000.
    except (TypeError,ValueError):
        return False
    return -40<value<200


def parse_meminfo(text):
    fields = {}
    for line in str(text or "").splitlines():
        match = re.match(r"^(\w+):\s+(\d+)\s*kB", line)
        if match:
            fields[match.group(1)] = int(match.group(2)) / 1024.0
    out = {}
    if "MemTotal" in fields:
        out["mem.total_mib"] = fields["MemTotal"]
    if "MemAvailable" in fields:
        out["mem.available_mib"] = fields["MemAvailable"]
    if "SwapTotal" in fields:
        out["swap.total_mib"] = fields["SwapTotal"]
        free = fields.get("SwapFree", fields["SwapTotal"])
        out["swap.used_pct"] = 0.0 if fields["SwapTotal"] <= 0 else 100.0 * (fields["SwapTotal"] - free) / fields["SwapTotal"]
    return out


NVIDIA_SMI_ARGS = ("--query-gpu=index,temperature.gpu,memory.used,memory.total,utilization.gpu",
                   "--format=csv,noheader,nounits")


def parse_nvidia_smi(text):
    out = {}
    for line in str(text or "").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 5:
            continue
        try:
            index = int(parts[0])
            temp, used, total = float(parts[1]), float(parts[2]), float(parts[3])
        except ValueError:
            continue
        prefix = "gpu.%d." % index
        out[prefix + "temp_c"] = temp
        out[prefix + "mem_used_mib"] = used
        out[prefix + "mem_free_mib"] = total - used
        try:
            out[prefix + "util_pct"] = float(parts[4])
        except ValueError:
            pass
    temps = [v for k, v in out.items() if k.endswith(".temp_c")]
    if temps:
        out["gpu.temp_c"] = max(temps)
        out["gpu.mem_used_mib"] = max(v for k, v in out.items() if k.endswith(".mem_used_mib"))
        out["gpu.mem_free_mib"] = min(v for k, v in out.items() if k.endswith(".mem_free_mib"))
    return out


_TEGRA_TEMP = re.compile(r"(?<![\w%])([A-Za-z][A-Za-z0-9_]*)@(-?\d+(?:\.\d+)?)C\b")


def parse_tegrastats(line):
    """One tegrastats line: RAM/SWAP use, GR3D load and the named temperatures."""
    text = str(line or "")
    out = {}
    ram = re.search(r"\bRAM (\d+)/(\d+)MB", text)
    if ram:
        out["tegra.ram_used_mib"], out["tegra.ram_total_mib"] = float(ram.group(1)), float(ram.group(2))
    swap = re.search(r"\bSWAP (\d+)/(\d+)MB", text)
    if swap and float(swap.group(2)) > 0:
        out["tegra.swap_used_pct"] = 100.0 * float(swap.group(1)) / float(swap.group(2))
    gr3d = re.search(r"\bGR3D_FREQ (\d+)%", text)
    if gr3d:
        out["tegra.gr3d_pct"] = float(gr3d.group(1))
    for name, value in _TEGRA_TEMP.findall(text):
        temp = float(value)
        if -40 < temp < 200:
            _put_max(out, "temp.tegra." + _key(name), temp)
    return out


def read_disks(paths):
    """{name: path} -> ``disk.<name>.free_gib``. A missing path is skipped."""
    out = {}
    for name, path in sorted((paths or {}).items()):
        try:
            out["disk.%s.free_gib" % _key(name)] = shutil.disk_usage(path).free / 1024.0 ** 3
        except OSError:
            continue
    return out


SYSTEMCTL_FAILED_ARGS = ("list-units", "--failed", "--plain", "--no-legend", "--no-pager")


def parse_failed_units(text, *, strict=False):
    units = []
    for line in str(text or "").splitlines():
        parts = line.replace("●", " ").split()
        if strict and parts and (len(parts) < 4 or not re.fullmatch(r"[A-Za-z0-9_.:@\\-]+\.[A-Za-z]+", parts[0]) or "failed" not in parts[1:4]):
            raise ValueError("malformed failed-unit output")
        if parts and "." in parts[0]:
            units.append(parts[0])
    return sorted(set(units))


# Kernel fault classes, most severe level first. The level is the back-off
# step a new record triggers (CONFIG may override per class).
FAULT_CLASSES = (
    ("mce", 3, r"\bmce:|machine check"),
    ("hardware_error", 3, r"hardware error|\baer:.*(uncorrect|fatal)"),
    # A kernel GPF carries an oops number ("[#1]"); a user-space one is a trap.
    ("panic", 3, r"kernel panic|\boops\b|bug: unable to handle|general protection fault.*\[#\d+\]|kernel bug at"),
    ("lockup", 3, r"soft lockup|hard lockup|watchdog: bug|rcu.{0,24}stall|blocked for more than \d+ seconds"),
    ("io_error", 3, r"i/o error|blk_update_request|ext4-fs error|critical medium error|nvme\S*:.*(timeout|reset|abort)"),
    ("thermal", 2, r"temperature above threshold|thermal.*(critical|shutdown|trip)|throttl|soctherm.*(oc|alarm)|over-?current"),
    ("oom", 2, r"out of memory|oom-kill|invoked oom-killer|oom_reaper"),
    ("gpu_xid", 2, r"nvrm: xid"),
    ("segfault", 1, r"segfault at|traps:.*general protection"),
)
_FAULT_RES = tuple((name, level, re.compile(pattern, re.IGNORECASE)) for name, level, pattern in FAULT_CLASSES)


def classify_fault(message):
    """(class, default level) for a kernel record, or None."""
    for name, level, regex in _FAULT_RES:
        if regex.search(message):
            return name, level
    return None


def load_fault_baseline(path, expected_host=None):
    """The documented historical baseline: known, reviewed records to ignore.

    ``{"schema": "den-ops-fault-baseline-1", "host": ..., "source": ...,
    "ignore": [{"class": "thermal" | "*", "pattern": "<regex>", "why": ...}]}``
    The monitor only reads this file; it never writes or edits it.
    """
    with open(path, encoding="utf-8") as handle:
        doc = json.load(handle)
    if not isinstance(doc, dict) or doc.get("schema") != "den-ops-fault-baseline-1" or not isinstance(doc.get("ignore"), list):
        raise ValueError("fault baseline must be den-ops-fault-baseline-1 with an ignore list")
    if expected_host is not None and doc.get("host") != expected_host:
        raise ValueError("fault baseline host mismatch")
    rules = []
    for entry in doc["ignore"]:
        if not isinstance(entry, dict) or not entry.get("pattern") or not entry.get("why"):
            raise ValueError("each baseline entry needs a pattern and a why")
        rules.append((entry.get("class", "*"), re.compile(entry["pattern"], re.IGNORECASE)))
    return rules


def journal_message(entry):
    message = entry.get("MESSAGE", "")
    if isinstance(message, list):  # journald encodes non-UTF-8 messages as byte arrays
        try:
            message = bytes(message).decode("utf-8", "replace")
        except (TypeError, ValueError):
            message = ""
    return str(message)


def parse_journal_json(text, *, strict=False):
    """``journalctl -o json`` lines -> list of entries (bad lines skipped)."""
    entries = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            if strict:
                raise ValueError("malformed kernel journal JSON")
            continue
        if strict and (not isinstance(entry, dict) or not isinstance(entry.get("__CURSOR"), str) or not entry["__CURSOR"] or "MESSAGE" not in entry or not str(entry.get("__REALTIME_TIMESTAMP", "")).isdigit()):
            raise ValueError("incomplete kernel journal record")
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def new_faults(entries, baseline_rules, level_overrides=None):
    """Classify journal entries. Returns (faults, baseline_matches, last_cursor);
    each fault is {class, level, message, at}."""
    overrides = level_overrides or {}
    faults, ignored, cursor = [], 0, None
    for entry in entries:
        cursor = entry.get("__CURSOR", cursor)
        message = journal_message(entry)
        found = classify_fault(message)
        if not found:
            continue
        name, level = found
        if any((cls in ("*", name)) and regex.search(message) for cls, regex in baseline_rules):
            ignored += 1
            continue
        try:
            at = int(entry.get("__REALTIME_TIMESTAMP", 0)) / 1e6
        except (TypeError, ValueError):
            at = 0.0
        faults.append({"class": name, "level": int(overrides.get(name, level)), "message": message[:200], "at": at})
    return faults, ignored, cursor


def pick(signals, pattern, op):
    """The worst value among keys matching ``pattern`` (max for >=, min for <=)."""
    values = [v for k, v in signals.items() if fnmatch.fnmatchcase(k, pattern)]
    if not values or any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) for v in values):
        return None
    return max(values) if op == ">=" else min(values)
