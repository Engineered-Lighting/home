"""An occupied recording is not proof that its always-on Qwen worked."""
import copy

import pytest

from den_ops import soak
from test_evidence_admission import prepare_pair


PROFILE = dict(model_version="qwen-scene-1", served_model="qwen", artifact_sha256="a" * 64,
               engine_epoch="engine-a", runtime_revision="runtime-a", adapter_revision="prompt-a")


def occupied_rows():
    return [dict(at=i * 60, loop=dict(session_id="recording", producer_epoch="loop-a", rolls=i // 4, state="running", occupancy_age_s=0.,
                qwen_workload=dict(schema="den-qwen-workload-1", producer_epoch="qwen-a", state="running",
                                   blocked=False, completed=i * 12, model=dict(PROFILE)))) for i in range(80)]


def test_an_occupied_recording_cannot_qualify_without_actual_qwen_evidence():
    rows = occupied_rows()
    for row in rows:
        row["loop"].pop("qwen_workload")
    assert soak._roll_exposure({"warmup_s": 0, "loop": {"qwen_profile": PROFILE}}, rows)["result"] != "pass"


@pytest.mark.parametrize("failure", ["stalled", "blocked", "stopped", "different_model", "restart", "counter_reset", "missing_sample"])
def test_qwen_failure_or_discontinuity_breaks_the_occupied_hour(failure):
    rows = occupied_rows()
    for i, row in enumerate(rows):
        qwen = row["loop"]["qwen_workload"]
        if failure == "stalled": qwen["completed"] = 100
        if failure == "blocked": qwen["blocked"] = True
        if failure == "stopped": qwen["state"] = "stopped"
        if failure == "different_model": qwen["model"]["artifact_sha256"] = "b" * 64
        if i >= 40:
            if failure == "restart": qwen["producer_epoch"] = "qwen-b"
            if failure == "counter_reset": qwen["completed"] -= 40 * 12
        if failure == "missing_sample" and i == 40: row["loop"].pop("qwen_workload")
    assert soak._roll_exposure({"warmup_s": 0, "loop": {"qwen_profile": PROFILE}}, rows)["result"] != "pass"


def test_qwen_completions_qualify_workload_without_asserting_reading_accuracy():
    result = soak._roll_exposure({"warmup_s": 0, "loop": {"qwen_profile": PROFILE}}, occupied_rows())
    assert result["result"] == "pass"
    assert result["measured"]["qwen_completed"] > 0


def test_common_occupied_window_uses_the_same_qwen_and_roll_evidence():
    cfg = {'host': 'victoria-jetson', 'warmup_s': 0, 'loop': {'qwen_profile': PROFILE}}
    rows = occupied_rows()
    assert soak._occupied_window(cfg, rows) == {'started_at': 0, 'ended_at': 79 * 60}
    for row in rows:
        row['loop']['qwen_workload']['completed'] = 100
    assert soak._occupied_window(cfg, rows) is None


@pytest.mark.parametrize('mutation', [
    lambda q: q.pop('model'), lambda q: q.update(completed=True), lambda q: q.update(completed=-1),
    lambda q: q.update(state='unknown'), lambda q: q.update(blocked=0),
    lambda q: q.update(producer_epoch=''), lambda q: q.update(model=None, completed=1),
])
def test_malformed_qwen_telemetry_cannot_be_used(mutation):
    qwen = copy.deepcopy(occupied_rows()[0]['loop']['qwen_workload'])
    mutation(qwen)
    with pytest.raises(ValueError): soak._qwen_workload(qwen)


def test_sampler_does_not_accept_legacy_roll_only_status(tmp_path, monkeypatch):
    pairs, _ = prepare_pair(tmp_path, monkeypatch)
    cfg, monitor, host, _ = pairs["jetson"]
    host.responses[("GET", cfg["loop"]["status_url"])] = (200, {"deep_stack": {
        "session_id": "s", "producer_epoch": "loop-a", "rolls": 10}})
    sample = soak.sample_once(cfg, monitor, host, {"journal": {}})
    assert not sample["evidence"]["ok"]
    assert any(error["source"] == "loop" for error in sample["evidence"]["errors"])
