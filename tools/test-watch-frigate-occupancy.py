#!/usr/bin/env python3
"""Pure-function tests for watch-frigate-occupancy.py.

These tests avoid live Frigate/Home Assistant calls. They validate the report
classification logic that splits failures into Frigate camera health, zone
geometry, HA mirror, classifier, and actuator/cascade layers.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "watch_frigate_occupancy", HERE / "watch-frigate-occupancy.py"
)
wfo = importlib.util.module_from_spec(SPEC)
sys.modules["watch_frigate_occupancy"] = wfo
SPEC.loader.exec_module(wfo)


PASSES = 0
FAILS = 0


def assert_(condition: bool, label: str) -> None:
    global PASSES, FAILS
    if condition:
        PASSES += 1
        print(f"[OK] {label}")
    else:
        FAILS += 1
        print(f"[FAIL] {label}", file=sys.stderr)
        raise AssertionError(label)


def report_text(records: list[dict], camera: str = "living_room") -> str:
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        md_path = tmpdir / "report.md"
        jsonl_path = tmpdir / "raw.jsonl"
        wfo.summarize(records, camera, md_path, jsonl_path)
        return md_path.read_text(encoding="utf-8")


def write_jsonl(tmpdir: Path, rows: list[dict | str]) -> Path:
    path = tmpdir / "watch.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            if isinstance(row, str):
                handle.write(row + "\n")
            else:
                handle.write(wfo.json.dumps(row) + "\n")
    return path


def sample(
    labels: list[str],
    ha: dict[str, dict] | None = None,
    camera_stats: dict | None = None,
    person_zones: list[str] | None = None,
) -> dict:
    events = []
    zones = ["Whole_Living_Room"] if person_zones is None else person_zones
    for idx, label in enumerate(labels):
        events.append(
            {
                "id": f"event-{idx}",
                "camera": "living_room",
                "label": label,
                "zones": zones if label == "person" else [],
                "score": 0.7,
                "top_score": 0.8,
            }
        )
    return {
        "kind": "sample",
        "frigate": {
            "camera_stats": camera_stats
            if camera_stats is not None
            else {"camera_fps": 10.0, "process_fps": 10.0, "detection_fps": 8.0, "detection_enabled": True},
            "recent_events": events,
        },
        "ha": ha or {},
    }


def test_compact_state() -> None:
    state = {
        "state": "vacant",
        "last_changed": "2026-06-23T00:00:00+00:00",
        "last_updated": "2026-06-23T00:00:01+00:00",
        "attributes": {
            "friendly_name": "Living Room Sofa Lighting State",
            "predicted_brightness_pct": 20,
            "ignored": "not copied",
        },
    }
    compact = wfo.compact_state(state)
    assert_(compact["state"] == "vacant", "compact_state preserves state")
    assert_(compact["predicted_brightness_pct"] == 20, "compact_state keeps useful attrs")
    assert_("ignored" not in compact, "compact_state omits noisy attrs")
    assert_(wfo.compact_state(None) == {"missing": True}, "compact_state marks missing")


def test_event_summary() -> None:
    event = {
        "id": "abc",
        "camera": "living_room",
        "label": "person",
        "sub_label": "marcelo",
        "zones": ["Whole_Living_Room"],
        "data": {"score": 0.71, "top_score": 0.83, "box": [0.1, 0.2, 0.3, 0.4]},
    }
    summary = wfo.event_summary(event)
    assert_(summary["label"] == "person", "event_summary preserves label")
    assert_(summary["score"] == 0.71, "event_summary lifts score")
    assert_(summary["box"] == [0.1, 0.2, 0.3, 0.4], "event_summary lifts box")
    api_event = {
        "id": "api",
        "camera": "living_room",
        "label": "person",
        "data": {"current_zones": ["whole_living_room"]},
    }
    assert_(
        wfo.event_summary(api_event)["zones"] == ["whole_living_room"],
        "event_summary reads Frigate data.current_zones",
    )
    mqtt_event = {
        "id": "mqtt",
        "camera": "living_room",
        "label": "person",
        "data": {"entered_zones": ["sofa"]},
    }
    assert_(
        wfo.event_summary(mqtt_event)["zones"] == ["sofa"],
        "event_summary reads Frigate data.entered_zones",
    )


def test_load_jsonl_records() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        path = write_jsonl(
            tmpdir,
            [
                {"kind": "run_start", "camera": "kitchen"},
                "",
                sample(["person"]),
                {"kind": "ignored"},
            ],
        )
        camera, records = wfo.load_jsonl_records(path)
        assert_(camera == "kitchen", "load_jsonl_records reads run_start camera")
        assert_(len(records) == 1, "load_jsonl_records returns sample rows only")
        assert_(records[0]["kind"] == "sample", "load_jsonl_records preserves sample payload")


def test_load_jsonl_records_infers_camera() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        path = write_jsonl(tmpdir, [sample(["chair"])])
        camera, records = wfo.load_jsonl_records(path)
        assert_(camera == "living_room", "load_jsonl_records infers camera from events")
        assert_(len(records) == 1, "load_jsonl_records keeps inferred sample")


def test_load_jsonl_records_reports_bad_json_line() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        path = write_jsonl(tmpdir, [{"kind": "run_start", "camera": "living_room"}, "{bad"])
        try:
            wfo.load_jsonl_records(path)
        except ValueError as exc:
            message = str(exc)
        else:
            raise AssertionError("bad JSON should raise ValueError")
        assert_("watch.jsonl:2" in message, "load_jsonl_records reports bad JSON line")
        assert_("invalid JSON" in message, "load_jsonl_records names invalid JSON")


def test_offline_rerender_from_jsonl_records() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        jsonl_path = write_jsonl(tmpdir, [{"kind": "run_start", "camera": "living_room"}, sample(["chair"])])
        camera, records = wfo.load_jsonl_records(jsonl_path)
        md_path = tmpdir / "watch.md"
        wfo.summarize(records, camera or "living_room", md_path, jsonl_path)
        text = md_path.read_text(encoding="utf-8")
        assert_("Raw JSONL: `watch.jsonl`" in text, "offline re-render preserves JSONL reference")
        assert_("Camera stats looked healthy" in text, "offline re-render includes current diagnosis")


def test_camera_health_summary_healthy_stats() -> None:
    health = wfo.camera_health_summary([sample(["chair"])])
    assert_(health["healthy"] is True, "camera_health_summary marks normal stats healthy")
    assert_(health["camera_fps_avg"] == 10.0, "camera_health_summary averages camera fps")
    text = report_text([sample(["chair"])])
    assert_("Camera stats looked healthy" in text, "report includes healthy camera stats")


def test_detection_disabled_preempts_detector_diagnosis() -> None:
    text = report_text(
        [
            sample(
                ["chair"],
                camera_stats={
                    "camera_fps": 10.0,
                    "process_fps": 10.0,
                    "detection_fps": 0.0,
                    "detection_enabled": False,
                },
            )
        ]
    )
    assert_("detection is disabled" in text, "report calls out disabled detection")
    assert_("camera/detection pipeline looked unhealthy" in text, "diagnosis prioritizes camera health")


def test_zero_fps_preempts_no_evidence_diagnosis() -> None:
    text = report_text(
        [
            sample(
                [],
                camera_stats={
                    "camera_fps": 0.0,
                    "process_fps": 0.0,
                    "detection_fps": 0.0,
                    "detection_enabled": True,
                },
            )
        ]
    )
    assert_("camera FPS is zero" in text, "report calls out zero camera fps")
    assert_("process FPS is zero" in text, "report calls out zero process fps")
    assert_("detection FPS is zero" in text, "report calls out zero detection fps")


def test_person_without_ha_occupancy() -> None:
    text = report_text([sample(["person"])])
    assert_("Frigate produced person events in watched zones" in text, "diagnoses HA mirror gap")
    assert_("MQTT/Home Assistant integration" in text, "names integration layer")


def test_person_outside_watched_zones() -> None:
    text = report_text([sample(["person"], person_zones=[])])
    assert_("none landed in the watched lighting zones" in text, "diagnoses zone geometry gap")
    assert_("Frigate zone geometry, masks, and zone-name mapping" in text, "names zone config layer")
    assert_("whole_living_room" in text, "lists expected watched zones")


def test_person_zone_normalization() -> None:
    text = report_text([sample(["person"], person_zones=["Whole Living-Room"])])
    assert_("in watched zones" in text, "normalizes Frigate zone names before matching")
    assert_("MQTT/Home Assistant integration" in text, "normalized zone still reaches HA mirror branch")


def test_non_person_labels_only() -> None:
    text = report_text([sample(["chair", "potted plant", "chair"])])
    assert_("- chair: 2" in text, "counts duplicate labels once per event id")
    assert_("none were labeled person" in text, "diagnoses Frigate detector/filter layer")


def test_ha_occupancy_but_classifier_vacant() -> None:
    text = report_text(
        [
            sample(
                ["person"],
                ha={
                    "binary_sensor.sofa_person_occupancy": {"state": "on"},
                    "sensor.living_room_sofa_lighting_state": {"state": "vacant"},
                },
            )
        ]
    )
    assert_("classifier stayed vacant" in text, "diagnoses classifier layer")


def test_classifier_non_vacant() -> None:
    text = report_text(
        [
            sample(
                ["person"],
                ha={
                    "binary_sensor.sofa_person_occupancy": {"state": "on"},
                    "sensor.living_room_sofa_lighting_state": {"state": "present"},
                },
            )
        ]
    )
    assert_("focus on actuator/manual-override/cascade logic" in text, "diagnoses downstream layer")


def main() -> int:
    tests = [
        test_compact_state,
        test_event_summary,
        test_load_jsonl_records,
        test_load_jsonl_records_infers_camera,
        test_load_jsonl_records_reports_bad_json_line,
        test_offline_rerender_from_jsonl_records,
        test_camera_health_summary_healthy_stats,
        test_detection_disabled_preempts_detector_diagnosis,
        test_zero_fps_preempts_no_evidence_diagnosis,
        test_person_without_ha_occupancy,
        test_person_outside_watched_zones,
        test_person_zone_normalization,
        test_non_person_labels_only,
        test_ha_occupancy_but_classifier_vacant,
        test_classifier_non_vacant,
    ]
    for test in tests:
        test()
    print(f"{PASSES} pass . {FAILS} fail")
    return 0 if FAILS == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
