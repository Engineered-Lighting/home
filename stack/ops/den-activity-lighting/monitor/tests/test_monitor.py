import json
import os
import re

import pytest

from conftest import CONFIG, FakeHost, fixture_text, write_tree
from den_ops import STEPS
from den_ops import monitor as mon
from den_ops import signals as sig
from den_ops.hostio import read_private

LAB = "https://lab.example.invalid/api/perception-lab/v1/activity-loop"
GATEWAY = "https://gateway.example.invalid/api/den-ops/v1"
HA = "https://ha.example.invalid"
JOURNAL = ("journalctl", "-k", "-o", "json", "-q", "--no-pager")


def test_first_user_scope_is_separate_and_cannot_override_history(jetson, private):
    j = jetson
    path = j.cfg['sources']['failed_units']['baseline']
    original = json.loads(read_private(path)); original['units'].pop('user')
    with open(path, 'w') as file: json.dump(original, file)
    before = read_private(path)
    extra = private('user-only.json', json.dumps({
        'schema': 'den-ops-failed-units-baseline-1', 'host': 'victoria-jetson', 'recorded_at': 1,
        'units': {'user': []}}))
    spec = j.cfg['sources']['failed_units']; spec['scope_baselines'] = {'user': extra}
    notices, _ = mon.failed_unit_notices(j.cfg, j.host)
    assert not any(n.get('evidence_error') for n in notices)
    assert read_private(path) == before
    spec['scope_baselines']['system'] = extra
    notices, _ = mon.failed_unit_notices(j.cfg, j.host)
    assert any(n['signal'] == 'units.baseline' for n in notices)


def test_jetson_metadata_failure_is_visible_but_unknown_and_user_failures_still_block(jetson):
    j = jetson
    j.host.commands[('systemctl',)] = (0, 'fwupd-refresh.service loaded failed failed metadata\n')
    notices, current = mon.failed_unit_notices(j.cfg, j.host)
    assert mon.evidence_report(notices)['ok'] is True
    assert any(n.get('notice') and 'fwupd-refresh' in n['detail'] for n in notices)
    j.host.commands[('systemctl', '--user')] = (0, 'fwupd-refresh.service loaded failed failed unexpected\n')
    notices, _ = mon.failed_unit_notices(j.cfg, j.host)
    assert mon.evidence_report(notices)['ok'] is False


def test_fresh_required_cleanup_is_accepted(jetson):
    j = jetson; j.cfg['require_media_cleanup'] = True
    j.host.responses[('GET', LAB)] = (200, {'deep_stack': {'media_cleanup':
        {'enabled': True, 'ok': True, 'checked_at': j.host.now(), 'age_s': 3600}}})
    mon.validate_config(j.cfg)
    _, status, _ = mon.check(j.cfg, j.host, dry_run=True)
    assert not any(r['signal'] == 'activity.media_cleanup' for r in status['reasons'])


def test_only_la_other_room_observer_is_outside_den_dependencies(jetson):
    j = jetson; j.cfg['host'] = 'la-host'
    path = j.cfg['sources']['failed_units']['baseline']
    original = json.loads(read_private(path)); original['host'] = 'la-host'
    with open(path, 'w') as f:json.dump(original, f)
    j.host.commands[('systemctl', '--user')] = (0, 'vjepa-live-prototype.service loaded failed failed other-room observer\n')
    notices, _ = mon.failed_unit_notices(j.cfg, j.host)
    assert mon.evidence_report(notices)['ok'] is True
    assert any('Other-room scene-memory' in n['detail'] for n in notices)
    j.host.commands[('systemctl', '--user')] = (0, 'lab-vjepa-worker.service loaded failed failed Den worker\n')
    notices, _ = mon.failed_unit_notices(j.cfg, j.host)
    assert mon.evidence_report(notices)['ok'] is False


@pytest.mark.parametrize('cleanup', [None, {'enabled': False},
    {'enabled': True, 'ok': False, 'checked_at': 1000, 'age_s': 3600},
    {'enabled': True, 'ok': True, 'checked_at': 0, 'age_s': 3600}])
def test_required_cleanup_failure_latches_monitor(jetson, cleanup):
    j = jetson; j.cfg['require_media_cleanup'] = True
    j.host.responses[('GET', LAB)] = (200, {'deep_stack': {'media_cleanup': cleanup}})
    _, status, _ = mon.check(j.cfg, j.host, dry_run=True)
    assert status['level'] == 3
    assert any(r['signal'] == 'activity.media_cleanup' for r in status['reasons'])


class FakeLoop:
    """Legacy AL-3 routes: a stop records its reason and overwrites an earlier one; a
    resume that names a reason refuses another reason's stop with 409; the
    cadence is normal or reduced; nothing survives a service restart."""

    def __init__(self):
        self.state, self.reason, self.cadence = "running", None, "normal"
        self.refuse_next_resume = False

    def view(self):
        return {"schema": "perception-lab-activity-loop-1", "state": self.state, "stopped_reason": self.reason,
                "cadence": self.cadence}

    def stop(self, body):
        body = body or {}
        if set(body) - {"reason"} or body.get("reason", "operator_stop") not in ("operator_stop", "monitor_backoff"):
            return 422, {"detail": "activity_loop_stop_reason_invalid"}
        self.state, self.reason = "stopped", body.get("reason", "operator_stop")
        return 200, self.view()

    def resume(self, body):
        only = (body or {}).get("reason")
        if self.refuse_next_resume or (only is not None and self.reason is not None and self.reason != only):
            self.refuse_next_resume = False
            return 409, {"detail": "activity_loop_stopped_by_" + (self.reason or "operator_stop")}
        self.state, self.reason = "running", None
        return 200, self.view()

    def set_cadence(self, body):
        if not isinstance(body, dict) or set(body) != {"level"} or body["level"] not in ("normal", "reduced"):
            return 422, {"detail": "activity_loop_cadence_level_invalid"}
        self.cadence = body["level"]
        return 200, {"level": self.cadence, "factor": 1 if self.cadence == "normal" else 2}

    def owner_stop(self):
        self.stop({})

    def restart(self):
        self.state, self.reason, self.cadence = "running", None, "normal"

    def serve(self, host):
        host.responses[("GET", LAB)] = lambda body: (200, self.view())
        host.responses[("POST", LAB + "/stop")] = self.stop
        host.responses[("POST", LAB + "/resume")] = self.resume
        host.responses[("POST", LAB + "/cadence")] = self.set_cadence


class ResourceControlledLoop(FakeLoop):
    """v2 contract: actual Qwen control and the owner's stop are independent."""
    def __init__(self):
        super().__init__()
        self.qwen_blocked = False
        self.qwen_factor = 1

    def view(self):
        return {**super().view(), 'monitor_control_version': 2,
                'deep_stack': {'qwen_control': {'blocked': self.qwen_blocked, 'factor': self.qwen_factor,
                                               'interval_seconds': 4. * self.qwen_factor}}}

    def set_cadence(self, body):
        code, reply = super().set_cadence(body)
        if code != 200:
            return code, reply
        self.qwen_factor = reply['factor']
        return code, {**reply, 'qwen': self.view()['deep_stack']['qwen_control']}

    def stop(self, body):
        if self.reason == 'operator_stop' and body == {'reason': 'monitor_backoff'}:
            self.qwen_blocked = True
            return 200, self.view()
        return super().stop(body)


def test_monitor_quiesces_actual_qwen_while_preserving_an_existing_owner_stop(jetson):
    jetson.loop = ResourceControlledLoop()
    jetson.loop.owner_stop()
    jetson.loop.serve(jetson.host)
    state = {'level': 3}
    effects, notices = mon.enforce(jetson.cfg, state, jetson.host)
    assert jetson.loop.qwen_blocked
    assert jetson.loop.reason == 'operator_stop'
    assert 'qwen_backoff' in effects and not state.get('stopped_by_monitor')
    effects, _ = mon.enforce(jetson.cfg, state, jetson.host)
    assert 'qwen_backoff' not in effects
    state['level'] = 0
    mon.enforce(jetson.cfg, state, jetson.host)
    assert not jetson.host.posts(LAB + '/resume')
    assert jetson.loop.qwen_blocked and jetson.loop.reason == 'operator_stop'


@pytest.mark.parametrize('blocked', [False, None, 'unknown'])
def test_monitor_does_not_accept_an_unconfirmed_qwen_backoff(jetson, blocked):
    jetson.loop = ResourceControlledLoop()
    jetson.loop.qwen_blocked = blocked
    jetson.loop.owner_stop()
    jetson.loop.serve(jetson.host)
    jetson.host.responses[('POST', LAB + '/stop')] = lambda body: (200, jetson.loop.view())
    effects, notices = mon.enforce(jetson.cfg, {'level': 3}, jetson.host)
    assert 'qwen_backoff' not in effects
    assert any(n['signal'] == 'actuator.qwen_backoff_failed' for n in notices)


def test_monitor_reasserts_cadence_when_actual_qwen_does_not_match_the_loop_label(jetson):
    jetson.loop = ResourceControlledLoop()
    jetson.loop.cadence = 'reduced'
    jetson.loop.serve(jetson.host)
    effects, _ = mon.enforce(jetson.cfg, {'level': 1}, jetson.host)
    assert jetson.loop.qwen_factor == 2
    assert 'cadence_reduce' in effects
    assert len(jetson.host.posts(LAB + '/cadence')) == 1


@pytest.mark.parametrize('confirmation', [None, {'factor': 1, 'interval_seconds': 4.},
    {'factor': True, 'interval_seconds': 8.}, {'factor': 2, 'interval_seconds': float('nan')}])
def test_monitor_requires_actual_qwen_cadence_confirmation(jetson, confirmation):
    jetson.loop = ResourceControlledLoop()
    jetson.loop.serve(jetson.host)
    jetson.host.responses[('POST', LAB + '/cadence')] = (200, {'level': 'reduced', 'qwen': confirmation})
    state = {'level': 1}
    effects, notices = mon.enforce(jetson.cfg, state, jetson.host)
    assert 'cadence_reduce' not in effects and not state.get('loop_cadence_reduced')
    assert any(n['signal'] == 'actuator.cadence_failed' for n in notices)


class Jetson:
    """A Jetson monitor wired to a FakeHost, with a writable fake sysfs."""

    def __init__(self, tmp_path, private, **overrides):
        self.root = str(tmp_path / "thermal")
        write_tree(self.root, {"thermal_zone0": {"type": "cpu-thermal", "temp": "50000"},
                               "thermal_zone1": {"type": "gpu-thermal", "temp": "48000"}})
        token = private("token", "t" * 40)
        baseline = private("units-baseline.json", json.dumps({
            "schema": "den-ops-failed-units-baseline-1", "host": "victoria-jetson", "recorded_at": 0,
            "units": {"system": ["fwupd-refresh.service"], "user": []}}))
        faults = private("fault-baseline.json", json.dumps({
            "schema": "den-ops-fault-baseline-1", "host": "victoria-jetson", "source": "test", "ignore": []}))
        self.cfg = {
            "schema": "den-ops-monitor-1", "host": "victoria-jetson", "role": "jetson",
            "state_dir": str(tmp_path / "state"),
            "policy": {"escalate_checks": 2, "recover_after_s": 600, "fault_hold_s": 1800},
            "sources": {"thermal_zones": {"root": self.root, "required_types": ["cpu-thermal", "gpu-thermal"]}, "meminfo": "/proc/meminfo",
                        "failed_units": {"scopes": ["system", "user"], "baseline": baseline},
                        "faults": {"baseline": faults}},
            "rules": [
                {"id": "soc.temp", "signal": "temp.zone_max", "op": ">=", "levels": [75, 78, 80],
                 "clear_margin": 5, "label": "Jetson max zone", "unit": "°C"},
                {"id": "mem.available", "signal": "mem.available_mib", "op": "<=", "levels": [4096, 3584, 3072],
                 "clear_margin": 512, "label": "RAM available", "unit": " MiB"},
            ],
            "loop": {"status_url": LAB, "stop_url": LAB + "/stop", "resume_url": LAB + "/resume",
                     "cadence_url": LAB + "/cadence", "token_file": token, "ca_file": None},
            "cadence": None,
            "remote": {"url": GATEWAY + "/status", "token_file": token},
            "gateway": {"status_url": GATEWAY + "/hosts/victoria-jetson/status", "token_file": token},
            "home_assistant": {"url": HA, "notify_service": "persistent_notification", "token_file": token,
                               "min_interval_s": 900},
        }
        self.cfg.update(overrides)
        mon.validate_config(self.cfg)
        self.host = FakeHost()
        self.host.files["/proc/meminfo"] = fixture_text("meminfo.jetson.txt")
        self.host.files["/proc/sys/kernel/random/boot_id"] = "00000000-0000-0000-0000-000000000001\n"
        self.host.commands[("systemctl",)] = (0, "")
        self.host.commands[("systemctl", "--user")] = (0, "")
        self.host.commands[JOURNAL] = (0, '{"__CURSOR":"anchor","__REALTIME_TIMESTAMP":"1800000000000000","MESSAGE":"Linux version"}\n')
        self.loop = FakeLoop()
        self.remote = {"schema": "den-ops-banner-1", "level": 0, "hosts": [{"host":"la-host","level":0,"stale":False}]}
        self.loop.serve(self.host)
        self.host.responses[("GET", GATEWAY + "/status")] = lambda body: (200, self.remote)
        self.host.responses[("POST", GATEWAY + "/hosts/victoria-jetson/status")] = (200, {"ok": True})
        self.host.responses[("POST", HA + "/api/services/notify/persistent_notification")] = (200, [])

    @property
    def loop_state(self):
        return self.loop.state

    def temp(self, celsius):
        with open(os.path.join(self.root, "thermal_zone0", "temp"), "w", encoding="utf-8") as out:
            out.write(str(int(celsius * 1000)))

    def check(self, advance=60, **kw):
        self.host.advance(advance)
        return mon.check(self.cfg, self.host, **kw)

    def ha_posts(self):
        return self.host.posts("/api/services/notify/")


@pytest.fixture
def jetson(tmp_path, private):
    return Jetson(tmp_path, private)


def test_heat_backs_off_stops_the_loop_then_recovers_and_resumes(jetson):
    state, status, report = jetson.check()
    assert status["level"] == 0 and status["message"] == "Den lighting ops normal"
    assert not jetson.ha_posts() and not jetson.host.posts(LAB)

    jetson.temp(78.5)
    state, status, report = jetson.check()
    assert status["level"] == 0 and state["pending"] == 1          # one hot reading is not enough
    state, status, report = jetson.check()
    assert status["level"] == 2 and status["step"] == "deep_stack_paused"
    assert report["effects"] == ["cadence_reduce", "loop_stop"] and jetson.loop_state == "stopped"
    assert jetson.loop.reason == "monitor_backoff" and jetson.loop.cadence == "reduced"
    assert "Jetson max zone 78.5°C ≥ 78°C" in status["message"]
    assert len(jetson.ha_posts()) == 1
    assert jetson.ha_posts()[0]["body"]["title"] == "Den lighting: deep stack paused"
    posted = jetson.host.posts("/hosts/victoria-jetson/status")[-1]["body"]
    assert posted["level"] == 2 and posted["step"] == "deep_stack_paused"

    state, status, report = jetson.check()                          # still hot: nothing new
    assert report["effects"] == [] and len(jetson.ha_posts()) == 1 and len(jetson.host.posts(LAB + "/stop")) == 1

    jetson.temp(69)                                                 # under 75 - 5: clear of every step
    transitions = []
    for _ in range(40):
        state, status, report = jetson.check()
        if report["transition"]:
            transitions.append((report["transition"]["to"], report["effects"]))
        if status["level"] == 0:
            break
    assert transitions == [(1, ["loop_resume"]), (0, ["cadence_restore"])]
    assert jetson.loop_state == "running" and jetson.loop.cadence == "normal"
    assert status["message"] == "Den lighting ops normal"
    assert len(jetson.ha_posts()) == 3                              # 2 -> 1 -> 0 each notified
    history = state["history"]
    assert [h["transition"]["to"] for h in history if h["transition"]] == [2, 1, 0]


def test_level_one_reduces_the_loop_cadence_and_recovery_restores_it(jetson):
    jetson.temp(76)
    jetson.check()
    _, status, report = jetson.check()
    assert status["level"] == 1 and report["effects"] == ["cadence_reduce"]
    assert jetson.loop.cadence == "reduced" and jetson.loop_state == "running"
    assert not any(r["signal"] == "actuator.cadence" for r in status["reasons"])
    jetson.loop.restart()                                            # the service came back at normal cadence
    _, _, report = jetson.check()
    assert report["effects"] == ["cadence_reduce"]
    _, _, report = jetson.check()
    assert report["effects"] == []                                  # already reduced: no repeat call
    jetson.temp(60)
    effects = []
    for _ in range(15):
        effects += jetson.check()[2]["effects"]
    assert effects == ["cadence_restore"] and jetson.loop.cadence == "normal"
    assert [c["body"] for c in jetson.host.posts(LAB + "/cadence")] == [
        {"level": "reduced"}, {"level": "reduced"}, {"level": "normal"}]


def test_an_owner_cadence_is_left_alone_at_level_zero(jetson):
    jetson.loop.cadence = "reduced"                                  # set by hand, not by the monitor
    for _ in range(3):
        _, status, _ = jetson.check()
    assert status["level"] == 0 and not jetson.host.posts(LAB) and jetson.loop.cadence == "reduced"


def test_level_one_without_a_cadence_route_records_and_alerts_only(jetson):
    del jetson.cfg["loop"]["cadence_url"]
    jetson.temp(76)
    jetson.check()
    state, status, report = jetson.check()
    assert status["level"] == 1 and report["effects"] == [] and jetson.loop_state == "running"
    assert any(r["signal"] == "actuator.cadence" for r in status["reasons"])
    assert len(jetson.ha_posts()) == 1   # the level change, not the quiet cadence note


def test_cadence_actions_run_once_when_configured(tmp_path, private):
    token = private("cadence-token", "c" * 40)
    other = "https://la.example.invalid/live/cadence"                # an extra, non-loop cadence control
    reduce = {"method": "POST", "url": other, "token_file": token, "body": {"level": "reduced"}}
    restore = {"method": "POST", "url": other, "token_file": token, "body": {"level": "normal"}}
    j = Jetson(tmp_path, private, cadence={"reduce": [reduce], "restore": [restore]},
               policy={"escalate_checks": 1, "recover_after_s": 60, "fault_hold_s": 1800})
    del j.cfg["loop"]["cadence_url"]
    j.host.responses[("POST", other)] = (200, {"ok": True})
    j.temp(76)
    _, status, report = j.check()
    assert status["level"] == 1 and report["effects"] == ["cadence_reduce"]
    _, _, report = j.check()
    assert report["effects"] == []
    j.temp(60)
    effects = []
    for _ in range(5):
        _, status, report = j.check()
        effects += report["effects"]
    assert status["level"] == 0 and effects == ["cadence_restore"]
    assert [c["body"]["level"] for c in j.host.posts(other)] == ["reduced", "normal"]


def test_kernel_mce_latches_level_three_until_the_owner_releases(jetson):
    jetson.check()                                  # first run on an empty journal: start watching from now
    assert any(a[-1].startswith("--since=@") for a in jetson.host.ran) is False  # nothing to anchor yet
    mce = '{"__CURSOR": "c2", "__REALTIME_TIMESTAMP": "1800000100000000", "MESSAGE": "mce: [Hardware Error]: CPU 0: Machine Check: 0 Bank 5"}\n'
    jetson.host.commands[JOURNAL] = (0, mce)
    state, status, report = jetson.check()
    assert any(a[-1] == "--after-cursor=anchor" for a in jetson.host.ran)
    assert status["level"] == 3 and status["latched"] and report["effects"] == ["cadence_reduce", "loop_stop"]
    assert report["faults"][0]["class"] == "mce"
    assert "held until the owner releases it" in status["message"]
    jetson.host.commands[JOURNAL] = (0, "")
    for _ in range(120):                                            # two quiet hours: still latched
        state, status, report = jetson.check()
    assert status["level"] == 3 and jetson.loop_state == "stopped"
    with pytest.raises(SystemExit):
        mon.release(jetson.cfg, confirm=False)
    released = mon.release(jetson.cfg, confirm=True)
    assert released["level"] == 2 and not released["latched"]
    levels = []
    for _ in range(40):
        state, status, report = jetson.check()
        levels.append(status["level"])
    assert levels[-1] == 0 and jetson.loop_state == "running"


def test_journal_cursor_is_followed_and_a_reboot_reads_the_whole_boot(jetson):
    jetson.host.commands[JOURNAL] = (0, '{"__CURSOR": "c1", "__REALTIME_TIMESTAMP": "1800000000000000", "MESSAGE": "Linux version"}\n')
    jetson.check()
    assert jetson.host.ran[[a[0] for a in jetson.host.ran].index("journalctl")][-1:] == ("-b",)
    jetson.check()
    assert any(a[-1] == "--after-cursor=c1" for a in jetson.host.ran)
    assert not any(a[-1].startswith("--since=") for a in jetson.host.ran)
    jetson.host.files["/proc/sys/kernel/random/boot_id"] = "00000000-0000-0000-0000-000000000002\n"
    jetson.host.ran.clear()
    _, status, _ = jetson.check()
    assert JOURNAL in jetson.host.ran
    assert any(r["signal"] == "host.rebooted" for r in status["reasons"])


def test_a_restarted_service_is_stopped_again_and_an_owner_stop_is_left_alone(jetson):
    jetson.temp(79)
    jetson.check()
    jetson.check()
    assert jetson.loop_state == "stopped"
    jetson.loop.restart()                                           # the managed service restarted
    _, _, report = jetson.check()
    assert report["effects"] == ["cadence_reduce", "loop_stop"] and jetson.loop.reason == "monitor_backoff"


def test_owner_stopped_loop_is_never_resumed(jetson):
    jetson.loop.owner_stop()                                        # the owner stopped it at level 0
    for _ in range(5):
        _, status, report = jetson.check()
    assert status["level"] == 0 and jetson.loop_state == "stopped"
    assert not jetson.host.posts(LAB + "/resume")


def back_off_then_cool(jetson, checks=40):
    jetson.temp(79)
    jetson.check()
    jetson.check()
    assert jetson.loop.reason == "monitor_backoff"
    jetson.temp(60)
    return [jetson.check() for _ in range(checks)]


def test_the_monitor_sends_the_exact_request_bodies(jetson):
    back_off_then_cool(jetson)
    assert [c["body"] for c in jetson.host.posts(LAB + "/stop")] == [{"reason": "monitor_backoff"}]
    assert [c["body"] for c in jetson.host.posts(LAB + "/resume")] == [{"reason": "monitor_backoff"}]
    assert [c["body"] for c in jetson.host.posts(LAB + "/cadence")] == [{"level": "reduced"}, {"level": "normal"}]
    assert all(c["token"] == "t" * 40 for c in jetson.host.calls if c["url"].startswith(LAB))


def test_an_owner_stop_during_a_back_off_is_never_resumed(jetson):
    jetson.temp(79)
    jetson.check()
    jetson.check()
    jetson.loop.owner_stop()                                         # the owner stops it too: the reason is now theirs
    jetson.temp(60)
    statuses = [jetson.check()[1] for _ in range(40)]
    assert statuses[-1]["level"] == 0
    assert jetson.loop_state == "stopped" and jetson.loop.reason == "operator_stop"
    assert not jetson.host.posts(LAB + "/resume")
    held = [r for r in statuses[-1]["reasons"] if r["signal"] == "actuator.owner_stop"]
    assert len(held) == 1 and "left stopped: the owner stopped it (activity_loop_stopped_by_operator_stop)" in held[0]["detail"]
    assert statuses[-1]["message"].startswith("Den lighting notice: activity loop left stopped")
    jetson.loop.resume({})                                           # the owner resumes it
    _, status, _ = jetson.check()
    assert not any(r["signal"] == "actuator.owner_stop" for r in status["reasons"])
    _, status, _ = jetson.check()
    assert status["reasons"] == [] and not jetson.host.posts(LAB + "/resume")
    saved = json.load(open(os.path.join(jetson.cfg["state_dir"], "state.json"), encoding="utf-8"))
    assert saved["stopped_by_monitor"] is False


def test_a_refused_resume_is_recorded_and_the_loop_left_stopped(jetson):
    jetson.loop.refuse_next_resume = True                           # e.g. the owner stopped it between GET and POST
    checks = back_off_then_cool(jetson)
    assert len(jetson.host.posts(LAB + "/resume")) == 1 and jetson.loop_state == "stopped"
    held = [r for r in checks[-1][1]["reasons"] if r["signal"] == "actuator.owner_stop"]
    assert len(held) == 1 and "activity_loop_stopped_by_monitor_backoff" in held[0]["detail"]
    assert not any("loop_resume" in report["effects"] for _, _, report in checks)
    assert any("owner stopped" in post["body"]["message"] for post in jetson.ha_posts())


def test_an_owner_stop_is_never_overwritten_by_a_back_off(jetson):
    jetson.loop.owner_stop()
    jetson.temp(79)
    jetson.check()
    _, status, report = jetson.check()
    assert status["level"] == 2 and "loop_stop" not in report["effects"]
    assert not jetson.host.posts(LAB + "/stop") and jetson.loop.reason == "operator_stop"
    assert any(r["signal"] == "actuator.owner_stop" for r in status["reasons"])
    jetson.temp(60)
    for _ in range(40):
        jetson.check()
    assert not jetson.host.posts(LAB + "/resume") and jetson.loop.reason == "operator_stop"


def test_a_monitor_stop_found_after_lost_state_is_adopted(jetson):
    jetson.loop.stop({"reason": "monitor_backoff"})                 # e.g. the state file was lost
    jetson.temp(79)
    jetson.check()
    _, _, report = jetson.check()
    assert "loop_stop" not in report["effects"]
    jetson.temp(60)
    for _ in range(40):
        jetson.check()
    assert jetson.loop_state == "running" and len(jetson.host.posts(LAB + "/resume")) == 1


def test_new_failed_units_latch_and_alert_once(jetson):
    jetson.check()
    jetson.host.commands[("systemctl",)] = (0, fixture_text("systemctl-failed.txt"))
    _, status, _ = jetson.check()
    assert status["level"] == 3 and status["latched"]
    assert [r["signal"] for r in status["reasons"] if r['level'] > 0] == ["unit.lighting-brain.service"]
    assert any(r['signal'] == 'unit.non_dependency.fwupd-refresh.service' for r in status['reasons'])
    assert "system unit lighting-brain.service failed (not in the recorded baseline)" in status["message"]
    assert len(jetson.ha_posts()) == 1 and jetson.ha_posts()[0]["body"]["title"] == "Den lighting: loop stopped"
    for _ in range(3):
        jetson.check()
    assert len(jetson.ha_posts()) == 1
    # The monitor only ever listed units: no restart, reset-failed, start or stop.
    verbs = {a[1] if a[1] != "--user" else a[2] for a in jetson.host.ran if a[0] == "systemctl"}
    assert verbs == {"list-units"}


def test_a_cleared_notice_alerts_again_when_it_returns_after_the_interval(jetson):
    jetson.check()
    failed = (0, "x.service loaded failed failed X\n")
    jetson.host.commands[("systemctl",)] = failed
    jetson.check()
    jetson.host.commands[("systemctl",)] = (0, "")
    jetson.check()
    jetson.host.commands[("systemctl",)] = failed
    jetson.check(advance=60)
    assert len(jetson.ha_posts()) == 1                              # inside min_interval_s
    jetson.host.commands[("systemctl",)] = (0, "")
    jetson.check(advance=1000)
    jetson.host.commands[("systemctl",)] = failed
    jetson.check()
    assert len(jetson.ha_posts()) == 1
    assert jetson.check()[1]["latched"]  # a cleared failed unit cannot release its own latch


def test_the_la_level_reaches_the_acting_monitor_through_the_gateway(jetson):
    jetson.remote = {"schema": "den-ops-banner-1", "level": 2, "hosts": [
        {"host": "la-host", "level": 2, "stale": False, "message": "Den lighting backed off — GPU 78°C"},
        {"host": "victoria-jetson", "level": 0, "stale": False, "message": "ok"}]}
    _, status, report = jetson.check()
    assert status["level"] == 2 and report["effects"] == ["cadence_reduce", "loop_stop"]
    assert status["reasons"][0]["signal"] == "remote.la-host"
    jetson.remote["hosts"][0]["stale"] = True                       # a silent LA monitor does not hold the level
    levels = [jetson.check()[1]["level"] for _ in range(30)]
    assert levels[-1] == 3  # a blind remote monitor cannot release the acting host


def test_status_matches_the_gateway_schema(jetson):
    jetson.temp(79)
    jetson.host.commands[("systemctl",)] = (0, "weird@unit:x.service loaded failed failed\n")
    jetson.check()
    _, status, _ = jetson.check()
    assert status["schema"] == "den-ops-status-1" and status["host"] == "victoria-jetson"
    assert status["step"] == STEPS[status["level"]] and isinstance(status["latched"], bool)
    assert len(status["message"]) <= 240 and len(status["reasons"]) <= 20
    for reason in status["reasons"]:
        assert re.match(r"^[a-z0-9][a-z0-9_.:-]{0,63}$", reason["signal"]), reason
        assert 0 <= reason["level"] <= 3 and len(reason["detail"]) <= 240
    on_disk = json.load(open(os.path.join(jetson.cfg["state_dir"], "status.json"), encoding="utf-8"))
    assert on_disk["level"] == status["level"] and "temp.zone_max" in on_disk["signals"]


def test_dry_run_has_no_side_effects(jetson):
    jetson.temp(79)
    jetson.check(dry_run=True)
    _, status, report = jetson.check(dry_run=True)
    assert not [c for c in jetson.host.calls if c["method"] != "GET"]
    assert not os.path.exists(os.path.join(jetson.cfg["state_dir"], "state.json"))


def test_unreachable_loop_and_unreadable_sensors_are_notices(jetson):
    jetson.host.responses[("GET", LAB)] = (0, None)
    jetson.host.files.pop("/proc/meminfo")
    jetson.temp(79)
    jetson.check()
    _, status, report = jetson.check()
    signals = [r["signal"] for r in status["reasons"]]
    assert status["level"] == 3 and report["effects"] == []
    assert "actuator.loop_unreachable" in signals and "mem.unreadable" in signals


def test_failed_unit_baseline_is_recorded_once(tmp_path, private):
    j = Jetson(tmp_path, private)
    target = str(tmp_path / "new-baseline.json")
    j.cfg["sources"]["failed_units"]["baseline"] = target
    j.host.commands[("systemctl",)] = (0, fixture_text("systemctl-failed.txt"))
    doc = mon.record_units_baseline(j.cfg, j.host, write=True, now=1.0, reviewed_units=["system:fwupd-refresh.service", "system:lighting-brain.service"])
    assert doc["units"]["system"] == ["fwupd-refresh.service", "lighting-brain.service"]
    with pytest.raises(SystemExit):
        mon.record_units_baseline(j.cfg, j.host, write=True, now=2.0)
    assert json.load(open(target, encoding="utf-8"))["recorded_at"] == 1.0


@pytest.mark.parametrize("name", ["monitor.la.example.json", "monitor.jetson.example.json"])
def test_example_configs_validate(name):
    cfg = mon.load_config(os.path.join(CONFIG, name))
    assert cfg["host"] in ("la-host", "victoria-jetson")


@pytest.mark.parametrize("change", [
    {"rules": [{"signal": "gpu.temp_c", "op": ">", "levels": [1, 2, 3]}]},
    {"rules": [{"signal": "gpu.temp_c", "op": ">=", "levels": [80, 77, 79]}]},
    {"rules": [{"signal": "gpu.temp_c", "op": ">=", "levels": [74, 77]}]},
    {"host": "LA Host"},
    {"surprise": True},
    {"policy": {"escalate_after": 1}},
    {"cadence": {"reduce": [{"method": "GET", "url": "https://x"}]}},
    {"cadence": {"reduce": [{"method": "POST", "url": "http://x"}]}},
    {"gateway": {"status_url": "http://gateway.example.invalid/api/den-ops/v1/hosts/x/status", "token_file": "t"}},
    {"loop": {"status_url": "http://lab.example.invalid/x", "stop_url": "https://x", "resume_url": "https://x"}},
    {"home_assistant": {"url": "http://ha.example.invalid:8123", "token_file": "t"}},
])
def test_bad_configs_are_refused(change):
    cfg = json.load(open(os.path.join(CONFIG, "monitor.jetson.example.json"), encoding="utf-8"))
    cfg.update(change)
    with pytest.raises(ValueError):
        mon.validate_config(cfg)


def test_plain_http_home_assistant_needs_an_explicit_opt_in():
    cfg = json.load(open(os.path.join(CONFIG, "monitor.jetson.example.json"), encoding="utf-8"))
    cfg["home_assistant"].update(url="http://ha.example.invalid:8123", allow_plain_http=True)
    mon.validate_config(cfg)
    from den_ops.hostio import Host
    with pytest.raises(ValueError):
        Host().http("POST", "http://ha.example.invalid:8123/api/services/notify/x")


def test_an_unreadable_cadence_token_is_a_notice_not_a_crash(tmp_path, private):
    action = {"method": "POST", "url": "https://la.example.invalid/live/cadence", "token_file": str(tmp_path / "missing")}
    j = Jetson(tmp_path, private, cadence={"reduce": [action], "restore": [action]},
               policy={"escalate_checks": 1, "recover_after_s": 60, "fault_hold_s": 1800})
    del j.cfg["loop"]["cadence_url"]
    j.temp(76)
    _, status, report = j.check()
    assert status["level"] == 1 and report["effects"] == []
    assert any(r["signal"] == "actuator.cadence_failed" for r in status["reasons"])


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_private_files_must_not_be_group_readable(tmp_path):
    path = tmp_path / "token"
    path.write_text("t" * 40)
    os.chmod(path, 0o640)
    with pytest.raises(PermissionError):
        read_private(str(path))


def test_example_fault_baseline_loads():
    assert sig.load_fault_baseline(os.path.join(CONFIG, "fault-baseline.example.json")) == []
