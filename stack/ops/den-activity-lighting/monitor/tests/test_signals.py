import os

import pytest

from conftest import fixture_json, fixture_text, write_tree
from den_ops import signals as sig
from den_ops.hostio import RefusedCommand, check_readonly


def test_jetson_thermal_zones_skip_unreadable_and_bogus(tmp_path):
    write_tree(str(tmp_path), fixture_json("sysfs.json")["jetson_thermal"])
    out = sig.read_thermal_zones(str(tmp_path))
    assert out["temp.zone.cpu_thermal"] == 47.5
    assert out["temp.zone.tj_thermal"] == pytest.approx(48.1)
    assert "temp.zone.cv0_thermal" not in out      # -256 °C: an off sensor
    assert "temp.zone.soc1_thermal" not in out     # no temp file
    assert out["temp.zone_max"] == pytest.approx(48.1)


def test_thermal_zone_exclusion(tmp_path):
    write_tree(str(tmp_path), fixture_json("sysfs.json")["jetson_thermal"])
    out = sig.read_thermal_zones(str(tmp_path), exclude_types=["tj-thermal"])
    assert "temp.zone.tj_thermal" not in out and out["temp.zone_max"] == 47.5


def test_excluded_channel_is_never_read(tmp_path, monkeypatch):
    write_tree(str(tmp_path), {'thermal_zone0': {'type': 'cv0-thermal', 'temp': '0'},
                               'thermal_zone1': {'type': 'cpu-thermal', 'temp': '48000'}})
    original = sig._read
    def read(path):
        assert not str(path).endswith('thermal_zone0/temp'), 'unsupported driver called'
        return original(path)
    monkeypatch.setattr(sig, '_read', read)
    assert sig.read_thermal_zones(str(tmp_path), ['cv0-thermal'])['temp.zone_max'] == 48


@pytest.mark.parametrize('failure', [TypeError("can't concat NoneType to bytes"), UnicodeError('bad sysfs')])
def test_unreadable_sysfs_channel_becomes_missing_evidence(monkeypatch, failure):
    class Channel:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): raise failure
    monkeypatch.setattr('builtins.open', lambda *a, **k: Channel())
    assert sig._read('/sys/class/thermal/thermal_zone0/temp') is None


def test_hwmon_k10temp_and_two_nvme_drives(tmp_path):
    write_tree(str(tmp_path), fixture_json("sysfs.json")["la_hwmon"])
    out = sig.read_hwmon(str(tmp_path))
    assert out["temp.hwmon.k10temp.tctl"] == 61.25
    assert out["temp.hwmon.k10temp.tccd2"] == 57.5
    assert out["temp.hwmon.nvme.composite"] == pytest.approx(46.85)  # the hotter drive
    assert out["temp.hwmon.acpitz.temp1"] == pytest.approx(27.8)     # no label file
    assert sig.pick(out, "temp.hwmon.k10temp.tccd*", ">=") == 57.5


def test_meminfo_and_swap():
    la = sig.parse_meminfo(fixture_text("meminfo.la.txt"))
    assert la["mem.available_mib"] == 98304 and la["swap.used_pct"] == 0
    jetson = sig.parse_meminfo(fixture_text("meminfo.jetson.txt"))
    assert jetson["mem.available_mib"] == 5120
    assert jetson["swap.used_pct"] == pytest.approx(10.26, abs=0.01)
    assert sig.parse_meminfo("") == {}


def test_nvidia_smi_worst_gpu():
    out = sig.parse_nvidia_smi(fixture_text("nvidia-smi.normal.csv"))
    assert out["gpu.temp_c"] == 52 and out["gpu.0.util_pct"] == 4
    assert out["gpu.mem_free_mib"] == 97887 - 70218
    two = sig.parse_nvidia_smi("0, 50, 1000, 10000, 1\n1, 71, 9000, 10000, 80\n[N/A]\n")
    assert two["gpu.temp_c"] == 71 and two["gpu.mem_free_mib"] == 1000 and two["gpu.mem_used_mib"] == 9000
    assert sig.parse_nvidia_smi("NVIDIA-SMI has failed") == {}


def test_tegrastats_line():
    normal, hot = fixture_text("tegrastats.orin.txt").splitlines()
    out = sig.parse_tegrastats(normal)
    assert out["tegra.ram_used_mib"] == 2893 and out["tegra.ram_total_mib"] == 7620
    assert out["tegra.gr3d_pct"] == 41
    assert out["temp.tegra.tj"] == pytest.approx(48.1) and out["temp.tegra.cpu"] == 47.5
    assert not any(k.startswith("temp.tegra.") and "729" in k for k in out)  # CPU freq tokens are not temps
    assert sig.parse_tegrastats(hot)["temp.tegra.gpu"] == pytest.approx(80.6)
    assert sig.parse_tegrastats(hot)["tegra.swap_used_pct"] == pytest.approx(76.1, abs=0.1)


def test_failed_units_with_and_without_bullets():
    assert sig.parse_failed_units(fixture_text("systemctl-failed.txt")) == [
        "fwupd-refresh.service", "lighting-brain.service"]
    assert sig.parse_failed_units("") == []


def test_kernel_fault_classes():
    entries = sig.parse_journal_json(fixture_text("journal-kernel.jsonl"))
    assert len(entries) == 12  # the non-JSON line is skipped
    faults, ignored, cursor = sig.new_faults(entries, [])
    classes = [f["class"] for f in faults]
    assert classes == ["mce", "oom", "segfault", "thermal", "segfault", "lockup", "io_error", "gpu_xid", "panic"]
    assert cursor == "s=1;i=111"
    levels = {f["class"]: f["level"] for f in faults}
    assert levels["mce"] == 3 and levels["oom"] == 2 and levels["segfault"] == 1 and levels["panic"] == 3
    assert ignored == 0
    assert faults[0]["at"] == pytest.approx(1792432802.0)


def test_user_space_gpf_is_a_trap_not_a_kernel_panic():
    assert sig.classify_fault("traps: node[777] general protection fault ip:1 sp:2 error:0")[0] == "segfault"
    assert sig.classify_fault("general protection fault, probably for non-canonical address 0x1: 0000 [#1] SMP")[0] == "panic"


def test_benign_records_are_not_faults():
    for line in ("thermal LNXTHERM:00: registered as thermal_zone0", "Linux version 6.8.0",
                 "nvme nvme0: 16/0/0 default/read/poll queues", "EXT4-fs (nvme0n1p2): mounted filesystem"):
        assert sig.classify_fault(line) is None, line


def test_baseline_ignores_reviewed_records_and_level_overrides(tmp_path):
    path = tmp_path / "baseline.json"
    path.write_text('{"schema": "den-ops-fault-baseline-1", "host": "x", "source": "test", "ignore": ['
                    '{"class": "thermal", "pattern": "cpu clock throttled", "why": "reviewed note"},'
                    '{"pattern": "Xid .*: 79", "why": "reviewed note"}]}', encoding="utf-8")
    rules = sig.load_fault_baseline(str(path))
    entries = sig.parse_journal_json(fixture_text("journal-kernel.jsonl"))
    faults, ignored, _ = sig.new_faults(entries, rules, {"segfault": 2})
    assert ignored == 2
    assert "thermal" not in [f["class"] for f in faults] and "gpu_xid" not in [f["class"] for f in faults]
    assert {f["level"] for f in faults if f["class"] == "segfault"} == {2}


def test_baseline_entries_need_a_why(tmp_path):
    path = tmp_path / "baseline.json"
    path.write_text('{"schema": "den-ops-fault-baseline-1", "ignore": [{"pattern": "x"}]}', encoding="utf-8")
    with pytest.raises(ValueError):
        sig.load_fault_baseline(str(path))


def test_non_utf8_journal_message():
    assert sig.journal_message({"MESSAGE": [100, 101, 110, 255]}).startswith("den")


def test_disks(tmp_path):
    out = sig.read_disks({"recordings": str(tmp_path), "gone": os.path.join(str(tmp_path), "missing")})
    assert out["disk.recordings.free_gib"] > 0 and "disk.gone.free_gib" not in out


@pytest.mark.parametrize("argv", [
    ("systemctl", "restart", "lighting-brain.service"),
    ("systemctl", "reset-failed"),
    ("systemctl", "--user", "stop", "perception-lab-managed.service"),
    ("systemctl", "kill", "x.service"),
    ("journalctl", "--vacuum-time=1s"),
    ("journalctl", "--rotate"),
    ("journalctl", "-k", "--flush"),
    ("nvidia-smi", "-r"),
    ("nvidia-smi", "--gpu-reset"),
    ("rm", "-rf", "/"),
    ("sh", "-c", "systemctl restart x"),
    ("tegrastats", "--stop"),
])
def test_only_read_only_commands_can_run(argv):
    with pytest.raises(RefusedCommand):
        check_readonly(argv)


@pytest.mark.parametrize("argv", [
    ("systemctl",) + sig.SYSTEMCTL_FAILED_ARGS,
    ("systemctl", "--user") + sig.SYSTEMCTL_FAILED_ARGS,
    ("systemctl", "show", "lighting-brain.service", "--property=ActiveState,NRestarts,MemoryCurrent,MemoryMax"),
    ("journalctl", "-k", "-o", "json", "-q", "--no-pager", "--after-cursor=s=1;i=5"),
    ("journalctl", "-k", "-o", "json", "-q", "--no-pager", "-n", "1"),
    ("nvidia-smi",) + sig.NVIDIA_SMI_ARGS,
    ("tegrastats", "--interval", "1000"),
])
def test_read_only_commands_are_allowed(argv):
    check_readonly(argv)
