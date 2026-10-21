from den_ops import policy as ladder

GPU = {"id": "gpu.temp", "signal": "gpu.temp_c", "op": ">=", "levels": [74, 77, 79], "clear_margin": 4,
       "label": "GPU", "unit": "°C"}
MEM = {"signal": "mem.available_mib", "op": "<=", "levels": [4096, 3584, 3072], "clear_margin": 512, "label": "RAM"}
POLICY = {"escalate_checks": 2, "recover_after_s": 600, "fault_hold_s": 1800, "latch_level": 3}


def reasons_at(gpu):
    return ladder.evaluate_rules({"gpu.temp_c": gpu}, [GPU])


def run(state, gpu, now):
    return ladder.step(state, reasons_at(gpu), now, POLICY)


def test_rule_levels_and_hysteresis_hold():
    assert ladder.evaluate_rule({"gpu.temp_c": 70}, GPU)["level"] == 0
    r = ladder.evaluate_rule({"gpu.temp_c": 77.5}, GPU)
    # hold: the highest step this value would keep if already there (79 - 4 = 75)
    assert r["level"] == 2 and r["hold"] == 3 and r["detail"] == "GPU 77.5°C ≥ 77°C"
    held = ladder.evaluate_rule({"gpu.temp_c": 75.5}, GPU)   # under 77 and 79 but within their 4 °C margins
    assert held["level"] == 1 and held["hold"] == 3
    cleared = ladder.evaluate_rule({"gpu.temp_c": 69.9}, GPU)
    assert cleared["level"] == 0 and cleared["hold"] == 0
    assert ladder.evaluate_rule({"gpu.temp_c": 71}, GPU)["hold"] == 1


def test_lower_is_worse_rules():
    assert ladder.evaluate_rule({"mem.available_mib": 3500}, MEM)["level"] == 2
    assert ladder.evaluate_rule({"mem.available_mib": 4000}, MEM)["hold"] == 2  # within 512 of 3584
    assert ladder.evaluate_rule({"mem.available_mib": 5000}, MEM)["hold"] == 0


def test_missing_required_signal_latches_but_optional_does_not():
    r = ladder.evaluate_rule({}, GPU)
    assert r["level"] == 3 and r["notice"] and r["immediate"] and r["evidence_error"] and r["detail"] == "GPU unreadable"
    assert not ladder.evaluate_rule({}, {**GPU, "required": False})["notice"]


def test_escalation_needs_two_checks_then_jumps_to_the_worst():
    state, t = run({}, 79.5, 0)
    assert state["level"] == 0 and t is None and state["pending"] == 1
    state, t = run(state, 79.5, 60)
    assert state["level"] == 3 and t == {"kind": "escalate", "from": 0, "to": 3} and state["latched"]


def test_a_single_spike_does_not_escalate():
    state, _ = run({}, 78, 0)
    state, _ = run(state, 60, 60)
    state, _ = run(state, 78, 120)
    assert state["level"] == 0


def test_faults_escalate_at_once_and_hold():
    state = {}
    fault = [{"class": "oom", "level": 2, "message": "Out of memory: Killed process 1", "at": 0}]
    reasons, held = ladder.fault_reasons(state, fault, 0, POLICY)
    state["fault_hold"] = held
    state, t = ladder.step(state, reasons, 0, POLICY)
    assert state["level"] == 2 and t["kind"] == "escalate"
    # No new record: the hold keeps level 2 for fault_hold_s, then recovery starts.
    for now in range(60, 1800, 60):
        reasons, state["fault_hold"] = ladder.fault_reasons(state, [], now, POLICY)
        state, t = ladder.step(state, reasons, now, POLICY)
        assert state["level"] == 2, now
    now = 1800
    while state["level"] == 2 and now < 4000:
        reasons, state["fault_hold"] = ladder.fault_reasons(state, [], now, POLICY)
        state, t = ladder.step(state, reasons, now, POLICY)
        now += 60
    assert state["level"] == 1 and 1800 + 600 <= now <= 1800 + 720


def test_recovery_is_one_step_per_quiet_period():
    state, _ = run({}, 78, 0)
    state, _ = run(state, 78, 60)
    assert state["level"] == 2
    now, seen = 60, []
    while state["level"] and now < 5000:
        now += 60
        state, t = run(state, 60, now)
        if t:
            seen.append((now, t["to"]))
    assert [level for _, level in seen] == [1, 0]
    assert seen[0][0] - 60 >= 600 and seen[1][0] - seen[0][0] >= 600


def test_hysteresis_blocks_recovery_inside_the_margin():
    state, _ = run({}, 78, 0)
    state, _ = run(state, 78, 60)
    for now in range(120, 3000, 60):
        state, t = run(state, 74, now)   # below 77, but within 4 °C of it
        assert state["level"] == 2 and t is None


def test_level_three_latches_until_release():
    state, _ = run({}, 80, 0)
    state, _ = run(state, 80, 60)
    assert state["level"] == 3 and state["latched"]
    for now in range(120, 7200, 60):
        state, t = run(state, 40, now)
        assert state["level"] == 3 and t is None
    state = ladder.release(state, POLICY)
    assert state["level"] == 2 and not state["latched"] and state["fault_hold"] == {}
    now, levels = 7200, []
    while state["level"] and now < 10000:
        now += 60
        state, t = run(state, 40, now)
        if t:
            levels.append(t["to"])
    assert levels == [1, 0]


def test_release_does_not_drop_below_the_current_level_if_lower():
    assert ladder.release({"level": 1, "latched": False})["level"] == 1


def test_messages():
    assert ladder.message(0, False, []) == "Den lighting ops normal"
    notice = [{"signal": "unit.x", "level": 0, "notice": True, "detail": "system unit x.service failed"}]
    assert ladder.message(0, False, notice) == "Den lighting notice: system unit x.service failed"
    text = ladder.message(2, False, reasons_at(78))
    assert text == "Den lighting backed off — deep stack paused, ambient only: GPU 78°C ≥ 77°C"
    assert ladder.message(3, True, reasons_at(80)).endswith("held until the owner releases it")
    assert "recovering" in ladder.message(1, False, reasons_at(60))
