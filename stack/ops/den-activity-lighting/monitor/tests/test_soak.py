import copy
import json
import os

import pytest

from conftest import CONFIG, FakeHost, fixture_json, fixture_text
from den_ops import monitor as mon
from den_ops import soak

START = 1_800_000_000.0


def configs():
    soak_cfg = soak.load_soak_config(os.path.join(CONFIG, "soak.la.example.json"))
    monitor_cfg = mon.load_config(os.path.join(CONFIG, "monitor.la.example.json"))
    return soak_cfg, monitor_cfg


def occupied(minute):
    return 30 <= minute < 90 or 120 <= minute < 150


def make_samples(hours=3.05, interval=60.0):
    samples = []
    for i in range(int(hours * 3600 / interval) + 1):
        minute = i * interval / 60
        present = occupied(minute)
        samples.append({
            "at": START + i * interval,
            "signals": {"gpu.temp_c": 55.0, "temp.hwmon.k10temp.tctl": 70.0, "temp.hwmon.k10temp.tccd1": 66.0,
                        "temp.hwmon.nvme.composite": 45.0, "mem.available_mib": 98304.0, "swap.used_pct": 0.0,
                        "gpu.mem_used_mib": 70218.0 + (i % 3), "gpu.mem_free_mib": 27669.0, "disk.root.free_gib": 500.0},
            "faults": [], "notices": [], "rebooted": False,
            "units": {
                "lighting-brain.service": {"active": "active", "n_restarts": 0, "memory_mib": 41.0 + (i % 2) * 0.5,
                                           "memory_max_mib": 256.0, "readable": True},
                "home-web-gateway.service": {"active": "active", "n_restarts": 0, "memory_mib": 90.0,
                                             "memory_max_mib": None, "readable": True},
            },
            "brain": {"healthz_ok": True, "reachable": True, "online": True, "mode": "ambient" if present else "off",
                      "present": present, "people_count": 1 if present else 0, "occupancy_age_s": 12.0,
                      "activity_age_s": 3.0 if present else 400.0, "pose_age_s": 0.8 if present else None,
                      "health": {"jev": present, "deep_stack": present, "frigate": True, "camera": True},
                      "spot_on": False},
            "monitor": {"level": 0, "step": "normal", "latched": False, "age_s": 25.0},
        })
    return samples


PASSING_CHECKLIST = {"schema": "den-ops-soak-checklist-1", "results": [
    {"id": step[0], "result": "pass", "optional": step[5]} for step in soak.CHECKLIST]}


def results(criteria):
    return {c["id"]: c["result"] for c in criteria}


def test_a_clean_soak_with_the_checklist_passes():
    soak_cfg, monitor_cfg = configs()
    criteria = soak.evaluate(soak_cfg, monitor_cfg, make_samples(), checklist=PASSING_CHECKLIST)
    assert results(criteria) == {"duration": "pass", "faults": "pass", "temperatures": "pass",
                                 "resource_limits": "pass", "memory_flat": "pass", "restarts": "pass",
                                 "monitor": "pass", "brain_inputs": "pass", "mvp_checklist": "pass"}
    assert soak.verdict(criteria) == "pass"


def test_without_the_owner_checklist_the_soak_is_incomplete():
    soak_cfg, monitor_cfg = configs()
    criteria = soak.evaluate(soak_cfg, monitor_cfg, make_samples())
    assert results(criteria)["mvp_checklist"] == "pending" and soak.verdict(criteria) == "incomplete"


def test_a_skipped_required_step_fails_but_an_optional_skip_is_fine():
    soak_cfg, monitor_cfg = configs()
    checklist = copy.deepcopy(PASSING_CHECKLIST)
    for row in checklist["results"]:
        if row["id"] == "second_person":
            row["result"] = "skipped"
    assert results(soak.evaluate(soak_cfg, monitor_cfg, make_samples(), checklist=checklist))["mvp_checklist"] == "pass"
    for row in checklist["results"]:
        if row["id"] == "manual_pause":
            row["result"] = "skipped"
    assert results(soak.evaluate(soak_cfg, monitor_cfg, make_samples(), checklist=checklist))["mvp_checklist"] == "fail"


def test_shorter_than_three_hours_fails():
    soak_cfg, monitor_cfg = configs()
    criteria = soak.evaluate(soak_cfg, monitor_cfg, make_samples(hours=2.5), checklist=PASSING_CHECKLIST)
    assert results(criteria)["duration"] == "fail" and soak.verdict(criteria) == "fail"


def test_gaps_in_sampling_fail_the_duration():
    soak_cfg, monitor_cfg = configs()
    samples = make_samples()
    del samples[20:40]
    assert results(soak.evaluate(soak_cfg, monitor_cfg, samples, checklist=PASSING_CHECKLIST))["duration"] == "fail"


@pytest.mark.parametrize("fault", [
    {"class": "oom", "level": 2, "message": "Out of memory: Killed process 1", "at": START},
    {"class": "thermal", "level": 2, "message": "CPU3: Core temperature above threshold", "at": START},
    {"class": "mce", "level": 3, "message": "mce: [Hardware Error]", "at": START},
])
def test_any_new_fault_record_fails(fault):
    soak_cfg, monitor_cfg = configs()
    samples = make_samples()
    samples[100]["faults"] = [fault]
    criterion = [c for c in soak.evaluate(soak_cfg, monitor_cfg, samples) if c["id"] == "faults"][0]
    assert criterion["result"] == "fail" and criterion["measured"]["by_class"] == {fault["class"]: 1}


def test_a_reboot_fails():
    soak_cfg, monitor_cfg = configs()
    samples = make_samples()
    samples[50]["rebooted"] = True
    assert results(soak.evaluate(soak_cfg, monitor_cfg, samples))["faults"] == "fail"


def test_temperature_over_the_existing_limit_fails():
    soak_cfg, monitor_cfg = configs()
    samples = make_samples()
    samples[70]["signals"]["gpu.temp_c"] = 74.5          # the LA ladder's first rung is 74 °C
    criteria = results(soak.evaluate(soak_cfg, monitor_cfg, samples))
    assert criteria["temperatures"] == "fail" and criteria["resource_limits"] == "pass"


def test_gpu_memory_below_the_free_floor_fails_resource_limits():
    soak_cfg, monitor_cfg = configs()
    samples = make_samples()
    samples[70]["signals"]["gpu.mem_free_mib"] = 15000.0  # the existing >= 16 GiB free rule
    assert results(soak.evaluate(soak_cfg, monitor_cfg, samples))["resource_limits"] == "fail"


def test_a_slow_leak_fails_memory_flat():
    soak_cfg, monitor_cfg = configs()
    samples = make_samples()
    for i, sample in enumerate(samples):
        sample["units"]["lighting-brain.service"]["memory_mib"] = 40.0 + i * 0.5   # 30 MiB/h
    criterion = [c for c in soak.evaluate(soak_cfg, monitor_cfg, samples) if c["id"] == "memory_flat"][0]
    assert criterion["result"] == "fail"
    assert criterion["measured"]["lighting-brain.service"]["slope_mib_per_h"] == pytest.approx(30.0, rel=0.01)


def test_warm_up_growth_is_ignored():
    soak_cfg, monitor_cfg = configs()
    samples = make_samples()
    for sample in samples[:25]:                              # the first 25 minutes climb, then flat
        sample["units"]["lighting-brain.service"]["memory_mib"] = 20.0 + (sample["at"] - START) / 60
    assert results(soak.evaluate(soak_cfg, monitor_cfg, samples))["memory_flat"] == "pass"


def test_gpu_memory_drift_and_host_memory_fall_fail():
    soak_cfg, monitor_cfg = configs()
    samples = make_samples()
    samples[-1]["signals"]["gpu.mem_used_mib"] = 72000.0
    assert results(soak.evaluate(soak_cfg, monitor_cfg, samples))["memory_flat"] == "fail"
    samples = make_samples()
    for i, sample in enumerate(samples):
        sample["signals"]["mem.available_mib"] = 98304.0 - i * 10   # 600 MiB/h
    assert results(soak.evaluate(soak_cfg, monitor_cfg, samples))["memory_flat"] == "fail"


def test_peak_near_memory_max_fails():
    soak_cfg, monitor_cfg = configs()
    samples = make_samples()
    samples[100]["units"]["lighting-brain.service"]["memory_mib"] = 230.0      # > 85 % of 256
    assert results(soak.evaluate(soak_cfg, monitor_cfg, samples))["memory_flat"] == "fail"


def test_restarts_beyond_the_bound_fail():
    soak_cfg, monitor_cfg = configs()
    samples = make_samples()
    for sample in samples[90:]:
        sample["units"]["lighting-brain.service"]["n_restarts"] = 1
    criterion = [c for c in soak.evaluate(soak_cfg, monitor_cfg, samples) if c["id"] == "restarts"][0]
    assert criterion["result"] == "fail" and criterion["measured"]["lighting-brain.service"]["restarts"] == 1
    soak_cfg["units"][0]["max_restarts"] = 1
    assert results(soak.evaluate(soak_cfg, monitor_cfg, samples))["restarts"] == "pass"


def test_a_monitor_back_off_or_a_silent_monitor_fails():
    soak_cfg, monitor_cfg = configs()
    samples = make_samples()
    samples[10]["monitor"]["level"] = 1
    assert results(soak.evaluate(soak_cfg, monitor_cfg, samples))["monitor"] == "fail"
    samples = make_samples()
    for sample in samples[:20]:
        sample["monitor"] = None
    assert results(soak.evaluate(soak_cfg, monitor_cfg, samples))["monitor"] == "fail"


def test_brain_offline_or_stale_inputs_fail():
    soak_cfg, monitor_cfg = configs()
    samples = make_samples()
    for sample in samples[:5]:
        sample["brain"] = {"healthz_ok": False, "reachable": False}
    criterion = [c for c in soak.evaluate(soak_cfg, monitor_cfg, samples) if c["id"] == "brain_inputs"][0]
    assert criterion["result"] == "fail" and "online" in criterion["measured"]["failed"]

    samples = make_samples()
    for sample in samples:
        if sample["brain"]["present"]:
            sample["brain"]["health"]["jev"] = sample["at"] % 120 == 0   # half the occupied time
    criterion = [c for c in soak.evaluate(soak_cfg, monitor_cfg, samples) if c["id"] == "brain_inputs"][0]
    assert criterion["result"] == "fail" and criterion["measured"]["failed"] == ["jev+deep stack ok while occupied"]


def test_the_first_minute_of_occupancy_is_excused_and_the_pose_gap_is_measured():
    soak_cfg, monitor_cfg = configs()
    samples = make_samples()
    first = next(i for i, s in enumerate(samples) if s["brain"]["present"])
    samples[first]["brain"]["health"]["jev"] = False                       # waking: not counted
    for i in range(first + 10, first + 13):                                # a 3-sample stale pose at a roll
        samples[i]["brain"]["pose_age_s"] = 14.0
    criterion = [c for c in soak.evaluate(soak_cfg, monitor_cfg, samples) if c["id"] == "brain_inputs"][0]
    assert criterion["result"] == "pass"
    assert criterion["measured"]["worst_pose_gap_s"] == 180.0   # from the last fresh sample: an upper bound


def test_receipt_markdown_and_combine(tmp_path):
    soak_cfg, monitor_cfg = configs()
    samples = make_samples()
    la = soak.receipt(soak_cfg, soak.evaluate(soak_cfg, monitor_cfg, samples, checklist=PASSING_CHECKLIST),
                      samples, PASSING_CHECKLIST)
    soak.write_receipt(str(tmp_path), la, "soak-la-host")
    text = (tmp_path / "soak-la-host.md").read_text(encoding="utf-8")
    assert "Verdict: **PASS**" in text and "| Owner MVP behaviour checklist | pass |" in text
    assert json.loads((tmp_path / "soak-la-host.json").read_text(encoding="utf-8"))["schema"] == "den-ops-soak-receipt-1"

    jetson_cfg = soak.load_soak_config(os.path.join(CONFIG, "soak.jetson.example.json"))
    jetson_monitor = mon.load_config(os.path.join(CONFIG, "monitor.jetson.example.json"))
    jetson_samples = make_samples()
    for sample in jetson_samples:
        sample["signals"] = {"temp.zone_max": 52.0, "mem.available_mib": 5000.0, "swap.used_pct": 10.0,
                             "disk.recordings.free_gib": 200.0}
        sample["units"] = {"victoria-ha-bridge.service": {"active": "active", "n_restarts": 2, "memory_mib": 18.0,
                                                          "memory_max_mib": 128.0},
                           "perception-lab-managed.service": {"active": "active", "n_restarts": 5, "memory_mib": 600.0,
                                                              "memory_max_mib": 2048.0}}
        sample.pop("brain")
    jetson = soak.receipt(jetson_cfg, soak.evaluate(jetson_cfg, jetson_monitor, jetson_samples), jetson_samples)
    assert jetson["verdict"] == "pass"
    assert "mvp_checklist" not in results(jetson["criteria"])            # the LA host runs the checklist
    jetson["release"] = la["release"]
    combined = soak.combine([la, jetson])
    assert combined["verdict"] == "pass" and combined["hosts"] == ["la-host", "victoria-jetson"]
    jetson["release"] = "something-else"
    assert soak.combine([la, jetson])["verdict"] == "fail"               # one release per receipt


def scripted_states(sequence):
    """fetch_state returning each state in turn, repeating the last."""
    items = list(sequence)

    def fetch():
        return items.pop(0) if len(items) > 1 else items[0]
    return fetch


def test_checklist_verifies_each_step_against_brain_state():
    state = fixture_json("brain-state.json")
    off = copy.deepcopy(state)
    off.update(mode="off", occupancy={"present": False, "people_count": 0, "zones": {"sofa": False, "desk": False}})
    for fixture in off["fixtures"]:
        fixture["ambient"].update(radial_level=0, down_level=0)
        fixture["spot"]["on"] = False
    ambient = copy.deepcopy(state)
    ambient["mode"] = "ambient"
    for fixture in ambient["fixtures"]:
        fixture["spot"]["on"] = False
    paused = copy.deepcopy(ambient)
    paused["fixtures"][0]["paused_until"] = START + 1800
    degraded = copy.deepcopy(ambient)
    degraded["mode"] = "degraded_ambient"
    degraded["inputs"]["health"]["deep_stack"] = False

    clock = {"t": 0.0}
    tick = lambda: clock["t"]                                             # noqa: E731

    def sleep(seconds):
        clock["t"] += seconds

    plan = {"empty_off": [off], "sofa_ambient": [off, ambient], "reading_spot": [ambient, ambient, state],
            "reading_stop": [ambient], "manual_pause": [paused], "jev_loss": [state, degraded],
            "leave_off": [ambient, off]}
    answers = iter(["", "", "", "", "s", "", "", "y", "", "y"])
    fetchers = {}
    current = {"id": None}

    def fetch_state():
        return fetchers[current["id"]]()

    steps = []
    for step in soak.CHECKLIST:
        fetchers[step[0]] = scripted_states(plan.get(step[0], [state]))
        steps.append(step)

    def log(text):
        if text.startswith("\n["):
            current["id"] = text[2:text.index("]")]

    out = soak.run_checklist(fetch_state, lambda prompt: next(answers), clock=tick, sleep=sleep, steps=steps, log=log)
    got = {r["id"]: r["result"] for r in out["results"]}
    assert got == {"empty_off": "pass", "sofa_ambient": "pass", "reading_spot": "pass", "reading_stop": "pass",
                   "second_person": "skipped", "manual_pause": "pass", "jev_loss": "pass", "brain_offline": "pass",
                   "leave_off": "pass", "why_log": "pass"}
    by_id = {r["id"]: r for r in out["results"]}
    assert by_id["empty_off"]["elapsed_s"] >= 60                          # it had to hold for 60 s
    assert by_id["reading_spot"]["elapsed_s"] == 2.0 and by_id["reading_spot"]["evidence"]["last_rule"] == "reading_spot_on"
    assert soak._checklist_criterion(out)["result"] == "pass"


def test_checklist_step_times_out_when_the_brain_never_gets_there():
    state = fixture_json("brain-state.json")
    clock = {"t": 0.0}

    def sleep(seconds):
        clock["t"] += seconds
    step = [s for s in soak.CHECKLIST if s[0] == "leave_off"]
    out = soak.run_checklist(lambda: state, lambda prompt: "", clock=lambda: clock["t"], sleep=sleep, steps=step,
                             log=lambda text: None)
    assert out["results"][0]["result"] == "fail" and out["results"][0]["elapsed_s"] > 180


def test_sample_once_and_run_with_a_fake_host(tmp_path):
    soak_cfg, monitor_cfg = configs()
    monitor_cfg = json.loads(json.dumps(monitor_cfg))
    monitor_cfg["sources"].update(thermal_zones=None, hwmon=None, disks={}, faults={"baseline": str(tmp_path / "missing.json")})
    host = FakeHost(now=START)
    host.files["/proc/meminfo"] = fixture_text("meminfo.la.txt")
    host.files["/proc/sys/kernel/random/boot_id"] = "b1"
    host.files["/var/lib/den-ops-monitor/status.json"] = json.dumps({"level": 0, "step": "normal", "checked_at": START - 20})
    host.commands[("nvidia-smi",)] = (0, fixture_text("nvidia-smi.normal.csv"))
    host.commands[("journalctl",)] = (0, "")
    host.commands[("systemctl", "show", "lighting-brain.service")] = (
        0, "ActiveState=active\nNRestarts=0\nMemoryCurrent=44040192\nMemoryMax=268435456\n")
    host.commands[("systemctl", "show", "home-web-gateway.service")] = (
        0, "ActiveState=active\nNRestarts=0\nMemoryCurrent=[not set]\nMemoryMax=infinity\n")
    host.responses[("GET", soak_cfg["brain"]["healthz_url"])] = (200, {"ok": True})
    host.responses[("GET", soak_cfg["brain"]["state_url"])] = (200, fixture_json("brain-state.json"))

    sample = soak.sample_once(soak_cfg, monitor_cfg, host, {"journal": {}})
    assert sample["signals"]["gpu.temp_c"] == 52 and sample["signals"]["mem.available_mib"] == 98304
    assert sample["units"]["lighting-brain.service"] == {"active": "active", "n_restarts": 0, "memory_mib": 42.0,
                                                         "memory_max_mib": 256.0, "readable": True}
    assert sample["units"]["home-web-gateway.service"]["memory_mib"] is None
    brain = sample["brain"]
    assert brain["online"] and brain["present"] and brain["spot_on"] and brain["health"]["jev"]
    assert sample["monitor"]["age_s"] == 20 and sample["monitor"]["level"] == 0
    assert any("fault baseline unreadable" in n for n in sample["notices"])

    def sleep(seconds):
        host.advance(seconds)
    path = soak.run(soak_cfg, monitor_cfg, host, str(tmp_path / "out"), sleep=sleep, duration_s=600, log=lambda t: None)
    samples = soak.read_samples(path)
    assert len(samples) == 11 and samples[-1]["at"] - samples[0]["at"] == 600
    assert all(cmd[0] in ("nvidia-smi", "journalctl", "systemctl") for cmd in host.ran)
    assert {cmd[1] for cmd in host.ran if cmd[0] == "systemctl"} == {"show"}


def test_soak_config_validation(tmp_path):
    path = tmp_path / "soak.json"
    path.write_text(json.dumps({"schema": "den-ops-soak-1", "host": "x", "units": [{"name": "x.timer"}]}))
    with pytest.raises(ValueError):
        soak.load_soak_config(str(path))
