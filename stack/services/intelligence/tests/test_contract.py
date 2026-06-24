from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("INTELLIGENCE_SCHEDULER_ENABLED", "0")

from fastapi.testclient import TestClient  # noqa: E402

from app.aggregation import aggregate_lighting_preferences  # noqa: E402
from app.db import connect, init_db  # noqa: E402
from app.evidence_sources import EvidencePullError, list_evidence_sources, pull_ha_evidence  # noqa: E402
from app.episodes import build_preference_episodes  # noqa: E402
from app.ingest import canonical_json, ingest_jsonl_file, ingest_payload  # noqa: E402
from app.lighting_activity import list_recent_lighting_activity, rebuild_lighting_change_episodes  # noqa: E402
from app.main import app  # noqa: E402
from app.multimodal import build_prompt_contract  # noqa: E402


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class EvidenceContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="home-intel-contract-")
        self.db_path = Path(self.tmp.name) / "intelligence.sqlite"
        os.environ["INTELLIGENCE_DB"] = str(self.db_path)
        os.environ.pop("INTELLIGENCE_HA_BASE_URL", None)
        os.environ.pop("INTELLIGENCE_HA_TOKEN", None)
        os.environ.pop("INTELLIGENCE_MULTIMODAL_RING_ENABLED", None)
        os.environ.pop("INTELLIGENCE_MULTIMODAL_PILOT_ENABLED", None)
        os.environ.pop("INTELLIGENCE_MULTIMODAL_PILOT_ZONES", None)
        os.environ.pop("INTELLIGENCE_MULTIMODAL_CAMERAS", None)
        self.conn = connect(self.db_path)
        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def ingest_fixture(self, name: str) -> dict:
        payload = load_fixture(name)
        return ingest_payload(
            self.conn,
            source="fixture",
            source_id=name,
            payload=payload,
            raw_text=canonical_json(payload),
            source_path=str(FIXTURES / name),
            line_no=1,
        )

    def test_live_office_contract_preserves_nested_context(self) -> None:
        self.ingest_fixture("office_override_event.json")
        self.ingest_fixture("office_pending_preference.json")
        self.ingest_fixture("office_lighting_decision.json")
        build_preference_episodes(self.conn)
        aggregate_lighting_preferences(self.conn)

        observations = self.conn.execute(
            """
            SELECT kind, zone, primary_zone, shadow_mode, weight,
                   context_id, evidence_id, dedupe_key, dedupe_window_s,
                   capture_suppressed_count, related_override_context_id,
                   occupancy_flicker_count_5min,
                   predictions_by_zone_json, capture_provenance_json,
                   prediction_consistency_json, occupancy_json, context_json
            FROM preference_observations
            ORDER BY kind
            """
        ).fetchall()
        self.assertEqual(len(observations), 2)
        for row in observations:
            predictions = json.loads(row["predictions_by_zone_json"])
            context = json.loads(row["context_json"])
            self.assertEqual(row["zone"], "office")
            self.assertEqual(row["shadow_mode"], 0)
            self.assertIsInstance(predictions["office"], dict)
            self.assertIn("predicted_pct", predictions["office"])
            self.assertIn("profile", context)
            self.assertIn("tv_playing", context)
            self.assertIn("user_at_home", context)
            if row["kind"] == "pending_preference":
                capture = json.loads(row["capture_provenance_json"])
                consistency = json.loads(row["prediction_consistency_json"])
                occupancy = json.loads(row["occupancy_json"])
                self.assertEqual(row["primary_zone"], "office")
                self.assertEqual(row["evidence_id"], "pp-1779823314-087834")
                self.assertEqual(row["dedupe_key"], "office|midday|present|F|F|F|T")
                self.assertEqual(row["dedupe_window_s"], 600)
                self.assertEqual(row["capture_suppressed_count"], 0)
                self.assertEqual(row["related_override_context_id"], "ovr-1779822169-431424")
                self.assertEqual(row["occupancy_flicker_count_5min"], 1)
                self.assertEqual(capture["capture_trigger"], "vacancy_settle")
                self.assertEqual(consistency["prediction_mismatch_pct"], 0)
                self.assertEqual(occupancy["entity_id"], "binary_sensor.office_person_occupancy")
                self.assertLess(row["weight"], 1.5)
            if row["kind"] == "override_event":
                self.assertEqual(row["context_id"], "ovr-1779822169-431424")

        decision = self.conn.execute(
            "SELECT zone, predicted_brightness_pct FROM lighting_decisions"
        ).fetchone()
        self.assertEqual(decision["zone"], "office")
        self.assertEqual(decision["predicted_brightness_pct"], 80)

        episode = self.conn.execute("SELECT summary_json FROM episodes").fetchone()
        summary = json.loads(episode["summary_json"])
        self.assertEqual(summary["pending"]["capture_provenance"]["capture_trigger"], "vacancy_settle")
        self.assertEqual(summary["pending"]["evidence_id"], "pp-1779823314-087834")
        self.assertEqual(summary["pending"]["dedupe_key"], "office|midday|present|F|F|F|T")
        self.assertEqual(summary["pending"]["related_override_context_id"], "ovr-1779822169-431424")
        self.assertEqual(summary["link_method"], "related_override_context_id")
        self.assertEqual(len(summary["override_observation_ids"]), 1)

        present_candidate = self.conn.execute(
            """
            SELECT id, evidence_ids_json, quality_warnings_json,
                   supporting_evidence_count, linked_override_count
            FROM preference_candidates
            WHERE context_json LIKE '%"state":"present"%'
            """
        ).fetchone()
        evidence_ids = json.loads(present_candidate["evidence_ids_json"])
        warning_codes = {w["code"] for w in json.loads(present_candidate["quality_warnings_json"])}
        self.assertEqual(
            len(warning_codes),
            len(json.loads(present_candidate["quality_warnings_json"])),
        )
        self.assertEqual(len(evidence_ids), 2)
        self.assertEqual(present_candidate["supporting_evidence_count"], 1)
        self.assertEqual(present_candidate["linked_override_count"], 1)
        self.assertNotIn("no_override_event", warning_codes)

        client = TestClient(app)
        response = client.get(f"/api/intelligence/candidates/{present_candidate['id']}")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        roles = {item["kind"]: item["evidence_role"] for item in body["evidence"]}
        self.assertEqual(roles["pending_preference"], "primary")
        self.assertEqual(roles["override_event"], "supporting")
        self.assertEqual(len(body["episodes"]), 1)

    def test_candidate_quality_warnings_and_detail_endpoint(self) -> None:
        self.ingest_fixture("office_pending_preference_manual_mismatch.json")
        build_preference_episodes(self.conn)
        aggregate_lighting_preferences(self.conn)

        candidate = self.conn.execute(
            "SELECT id, quality_warnings_json FROM preference_candidates"
        ).fetchone()
        warnings = json.loads(candidate["quality_warnings_json"])
        codes = {w["code"] for w in warnings}
        self.assertIn("prediction_mismatch", codes)
        self.assertIn("occupancy_unstable", codes)
        self.assertIn("manual_capture_trigger", codes)
        self.assertIn("occupancy_flicker", codes)
        self.assertNotIn("capture_provenance_missing", codes)

        client = TestClient(app)
        response = client.get(f"/api/intelligence/candidates/{candidate['id']}")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["candidate"]["id"], candidate["id"])
        self.assertEqual(len(body["evidence"]), 1)
        self.assertEqual(
            body["evidence"][0]["capture_provenance"]["capture_trigger"],
            "manual_automation_trigger",
        )
        self.assertEqual(
            body["evidence"][0]["prediction_consistency"]["prediction_mismatch_pct"],
            30,
        )
        self.assertEqual(body["evidence"][0]["evidence_id"], "pp-1779823202-309424")
        self.assertEqual(body["evidence"][0]["capture_suppressed_count"], 2)
        self.assertEqual(body["evidence"][0]["dedupe_window_s"], 600)
        self.assertIn("redacted_payload", body["evidence"][0]["raw_event"])

        readiness = client.get(f"/api/intelligence/candidates/{candidate['id']}/readiness")
        self.assertEqual(readiness.status_code, 200)
        self.assertFalse(readiness.json()["readiness"]["ready"])

        preview = client.get(f"/api/intelligence/proposal-preview/{candidate['id']}")
        self.assertEqual(preview.status_code, 200)
        self.assertFalse(preview.json()["proposal_preview"]["ready"])
        self.assertEqual(preview.json()["proposal_preview"]["status"], "preview_only")

        proposals = client.post("/api/intelligence/proposals/run")
        self.assertEqual(proposals.status_code, 200)
        proposal_body = proposals.json()
        self.assertEqual(proposal_body["ready_count"], 0)
        self.assertGreaterEqual(proposal_body["blocked_count"], 1)
        listed = client.get("/api/intelligence/proposals")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["proposals"], [])

    def test_soak_review_surfaces_m4_hygiene(self) -> None:
        self.ingest_fixture("office_override_event.json")
        self.ingest_fixture("office_pending_preference.json")
        build_preference_episodes(self.conn)
        aggregate_lighting_preferences(self.conn)
        self.conn.commit()

        client = TestClient(app)
        response = client.get("/api/intelligence/soak-review?hours=48")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["summary"]["override_count"], 1)
        self.assertEqual(body["summary"]["pending_count"], 1)
        self.assertEqual(body["summary"]["m4_record_count"], 1)
        self.assertEqual(body["summary"]["direct_link_episode_count"], 1)

        office = next(z for z in body["zones"] if z["zone"] == "office")
        self.assertEqual(office["m4_record_count"], 1)
        self.assertEqual(office["dedupe_key_count"], 1)
        self.assertEqual(office["direct_link_episode_count"], 1)
        self.assertEqual(office["latest_pending"]["evidence_id"], "pp-1779823314-087834")
        self.assertEqual(office["latest_pending"]["related_override_context_id"], "ovr-1779822169-431424")
        self.assertEqual(office["latest_natural_pending"]["capture_trigger"], "vacancy_settle")
        self.assertIn(office["health"], {"ok", "watch", "needs_attention"})
        self.assertEqual(
            body["latest_pending_preferences"][0]["dedupe_key"],
            "office|midday|present|F|F|F|T",
        )

    def test_daily_memory_writes_journal_and_wiki_claims(self) -> None:
        self.ingest_fixture("office_override_event.json")
        self.ingest_fixture("office_pending_preference.json")
        build_preference_episodes(self.conn)
        aggregate_lighting_preferences(self.conn)
        self.conn.commit()

        client = TestClient(app)
        response = client.post("/api/intelligence/memory/run?date=2026-05-26")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["journal"]["date"], "2026-05-26")
        self.assertIn("Home Intelligence Journal - 2026-05-26", body["journal"]["body_md"])
        self.assertIn("office", body["journal"]["body_md"])
        self.assertIn("pending preferences: 1", body["journal"]["body_md"])
        self.assertIn("prefobs:pending_preference:pp-1779823314-087834", body["journal"]["evidence_ids"])
        self.assertGreaterEqual(body["claims"]["inserted"], 1)

        journals = client.get("/api/intelligence/journals?limit=1")
        self.assertEqual(journals.status_code, 200)
        journal = journals.json()["journals"][0]
        self.assertEqual(journal["date"], "2026-05-26")
        self.assertIn("Candidate State", journal["body_md"])

        claims = client.get("/api/intelligence/wiki-claims?limit=10")
        self.assertEqual(claims.status_code, 200)
        claim_rows = claims.json()["claims"]
        self.assertTrue(any(claim["claim_type"] == "hypothesis" for claim in claim_rows))
        preference_claim = next(
            claim for claim in claim_rows
            if claim["page_path"] == "runtime/intelligence/wiki/rooms/office.md"
        )
        self.assertEqual(preference_claim["review_state"], "unreviewed")
        self.assertIn("office lighting may prefer", preference_claim["claim"])
        self.assertIn("prefobs:pending_preference:pp-1779823314-087834", preference_claim["evidence_ids"])
        self.assertGreater(preference_claim["confidence"], 0)

        detail = client.get(f"/api/intelligence/wiki-claims/{preference_claim['id']}")
        self.assertEqual(detail.status_code, 200)
        detail_body = detail.json()
        self.assertEqual(detail_body["claim"]["id"], preference_claim["id"])
        self.assertGreaterEqual(len(detail_body["evidence"]), 1)
        self.assertGreaterEqual(len(detail_body["candidates"]), 1)
        self.assertGreaterEqual(len(detail_body["episodes"]), 1)
        self.assertEqual(detail_body["decisions"], [])

        claim_decision = client.post(
            f"/api/intelligence/wiki-claims/{preference_claim['id']}/decisions",
            json={
                "decision": "reject",
                "actor": "test",
                "reason": "fixture review",
                "note": "not enough evidence yet",
            },
        )
        self.assertEqual(claim_decision.status_code, 200)
        self.assertEqual(claim_decision.json()["decision"]["prior_state"], "unreviewed")
        self.assertEqual(claim_decision.json()["decision"]["new_state"], "rejected")
        rejected_detail = client.get(f"/api/intelligence/wiki-claims/{preference_claim['id']}").json()
        self.assertEqual(rejected_detail["claim"]["review_state"], "rejected")
        self.assertEqual(len(rejected_detail["decisions"]), 1)

        open_claims = client.get("/api/intelligence/wiki-claims?review_state=open_question")
        self.assertEqual(open_claims.status_code, 200)
        self.assertTrue(all(c["review_state"] == "open_question" for c in open_claims.json()["claims"]))

        scheduler = client.get("/api/intelligence/scheduler")
        self.assertEqual(scheduler.status_code, 200)
        self.assertFalse(scheduler.json()["scheduler"]["enabled"])
        jobs = client.get("/api/intelligence/jobs?limit=5")
        self.assertEqual(jobs.status_code, 200)
        self.assertTrue(any(job["name"] == "daily_memory" for job in jobs.json()["jobs"]))

    def test_context_suppression_requires_context_or_candidate(self) -> None:
        client = TestClient(app)
        response = client.post(
            "/api/intelligence/suppressions",
            json={"zone": "office", "reason": "too broad"},
        )
        self.assertEqual(response.status_code, 400)

    def test_ha_api_evidence_pull_persists_cursors_and_is_idempotent(self) -> None:
        override = load_fixture("office_override_event.json")
        pending = load_fixture("office_pending_preference.json")
        decision = load_fixture("office_lighting_decision.json")

        class FakeHaClient:
            def __init__(self) -> None:
                self.files = {
                    "preferences": (
                        canonical_json(override).encode("utf-8") + b"\n"
                        + canonical_json(pending).encode("utf-8") + b"\n"
                    ),
                    "decisions": canonical_json(decision).encode("utf-8") + b"\n",
                    "activity": b"",
                }

            def get_json(self, path: str, params: dict | None = None) -> dict:
                if path.endswith("/status"):
                    return {
                        "ok": True,
                        "generated_at": "2026-05-26T20:00:00+00:00",
                        "sources": {
                            "preferences": {
                                "exists": True,
                                "size": len(self.files["preferences"]),
                                "mtime_iso": "2026-05-26T20:00:00+00:00",
                                "fingerprint": "dev:preferences",
                                "latest": {"ts": pending["ts"]},
                            },
                            "decisions": {
                                "exists": True,
                                "size": len(self.files["decisions"]),
                                "mtime_iso": "2026-05-26T20:00:00+00:00",
                                "fingerprint": "dev:decisions",
                                "latest": {"ts": decision["ts"]},
                            },
                            "activity": {
                                "exists": True,
                                "size": len(self.files["activity"]),
                                "mtime_iso": "2026-05-26T20:00:00+00:00",
                                "fingerprint": "dev:activity",
                                "latest": None,
                            },
                        },
                    }
                source = (params or {})["source"]
                offset = int((params or {}).get("offset") or 0)
                max_bytes = int((params or {}).get("max_bytes") or 1024)
                data = self.files[source]
                chunk = data[offset:offset + max_bytes]
                lines = []
                pos = 0
                consumed = 0
                while True:
                    nl = chunk.find(b"\n", pos)
                    if nl < 0:
                        break
                    lines.append({
                        "byte_start": offset + pos,
                        "byte_end": offset + nl + 1,
                        "raw": chunk[pos:nl].decode("utf-8"),
                    })
                    pos = nl + 1
                    consumed = pos
                offset_out = offset + consumed
                return {
                    "ok": True,
                    "source": source,
                    "offset_in": offset,
                    "offset_out": offset_out,
                    "eof": offset_out >= len(data),
                    "reset_required": False,
                    "lines": lines,
                    "file": {
                        "size": len(data),
                        "mtime_iso": "2026-05-26T20:00:00+00:00",
                        "fingerprint": f"dev:{source}",
                    },
                }

        first = pull_ha_evidence(self.conn, client=FakeHaClient())
        self.assertTrue(first["ok"])
        self.assertEqual(first["transport"], "ha_api")
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS c FROM preference_observations").fetchone()["c"],
            2,
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS c FROM lighting_decisions").fetchone()["c"],
            1,
        )
        sources = list_evidence_sources(self.conn)
        by_source = {row["source"]: row for row in sources["sources"]}
        self.assertEqual(by_source["preferences"]["status"], "fresh_ingested")
        self.assertEqual(by_source["preferences"]["lag_bytes"], 0)
        self.assertEqual(by_source["preferences"]["latest_ingested_ts"], pending["ts"])

        second = pull_ha_evidence(self.conn, client=FakeHaClient())
        self.assertTrue(second["ok"])
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS c FROM preference_observations").fetchone()["c"],
            2,
        )

    def test_ha_api_auth_failure_records_redacted_source_status(self) -> None:
        class AuthFailClient:
            def get_json(self, path: str, params: dict | None = None) -> dict:
                raise EvidencePullError("auth_failed", "bad token super-secret-token")

        os.environ["INTELLIGENCE_HA_TOKEN"] = "super-secret-token"
        body = pull_ha_evidence(self.conn, client=AuthFailClient())
        self.assertFalse(body["ok"])
        self.assertEqual(body["status"], "auth_failed")
        sources = list_evidence_sources(self.conn)
        self.assertEqual(sources["overall_status"], "auth_failed")
        for row in sources["sources"]:
            self.assertEqual(row["status"], "auth_failed")
            self.assertNotIn("super-secret-token", row["last_error"] or "")

    def test_universal_lighting_activity_ingests_and_collapses_to_episode(self) -> None:
        command_id = "cmd-1779900000-123456"
        command = {
            "schema_version": 1,
            "kind": "lighting_command_event",
            "raw_event_kind": "lighting_command_event",
            "event_id": command_id,
            "command_id": command_id,
            "ts": "2026-05-27T08:00:00.000000-07:00",
            "zone": "office",
            "entity_id": "input_text.living_lights_override_text_office",
            "source": "voice",
            "source_text": "make the office brighter",
            "requested": {"brightness_pct": 60, "color_temp_kelvin": 3000},
            "baseline": {"brightness_pct": 20, "color_temp_kelvin": 2700},
        }
        ingest_payload(
            self.conn,
            source="fixture",
            source_id="activity-command",
            payload=command,
            raw_text=canonical_json(command),
            source_path="activity",
            line_no=1,
        )
        steps = [
            (0, 20, 35),
            (1, 35, 50),
            (2, 50, 60),
        ]
        for idx, before, after in steps:
            change = {
                "schema_version": 1,
                "kind": "lighting_change_event",
                "raw_event_kind": "lighting_change_event",
                "event_id": f"lightchg-test-{idx}",
                "ts": f"2026-05-27T08:00:0{idx}.000000-07:00",
                "zone": "office",
                "entity_id": "light.office",
                "light_entity": "light.office",
                "from": {
                    "state": "on",
                    "brightness_pct": before,
                    "color_temp_kelvin": 2700,
                },
                "to": {
                    "state": "on",
                    "brightness_pct": after,
                    "color_temp_kelvin": 3000,
                },
                "predicted": {"brightness_pct": 30, "color_temp_kelvin": 2700},
                "command_id": command_id,
                "source_hint": "home_app_ai",
                "source_confidence": "high",
                "ha_context": {"id": f"ctx-{idx}", "parent_id": None, "user_id": None},
                "context": {"profile": "morning", "state": "present"},
            }
            ingest_payload(
                self.conn,
                source="fixture",
                source_id=f"activity-change-{idx}",
                payload=change,
                raw_text=canonical_json(change),
                source_path="activity",
                line_no=idx + 2,
            )

        summary = rebuild_lighting_change_episodes(self.conn)
        self.assertEqual(summary["raw_event_count"], 3)
        self.assertEqual(summary["episode_count"], 1)
        self.assertEqual(summary["events_collapsed"], 2)
        self.conn.commit()

        body = list_recent_lighting_activity(self.conn, zone="office", hours=24, include_raw=True)
        self.assertTrue(body["ok"])
        self.assertEqual(body["summary"]["episode_count"], 1)
        episode = body["episodes"][0]
        self.assertEqual(episode["event_count"], 3)
        self.assertEqual(episode["command_id"], command_id)
        self.assertEqual(episode["source_class"], "home_app_ai")
        self.assertEqual(episode["source_confidence"], "medium")
        self.assertEqual(episode["interpretation"], "explicit_command")
        self.assertEqual(episode["first_state"]["brightness_pct"], 20)
        self.assertEqual(episode["last_state"]["brightness_pct"], 60)
        self.assertEqual(len(episode["raw_events"]), 3)

        client = TestClient(app)
        response = client.get("/api/intelligence/lighting-activity?zone=office&hours=24&include_raw=true")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["episodes"][0]["last_state"]["brightness_pct"], 60)

    def test_ingest_recovers_concatenated_jsonl_objects(self) -> None:
        first = load_fixture("office_lighting_decision.json")
        second = dict(first)
        second["ts"] = "2026-05-26T12:30:27.000000-07:00"
        path = Path(self.tmp.name) / "concat.log"
        path.write_text(canonical_json(first) + canonical_json(second) + "\n", encoding="utf-8")

        stats = ingest_jsonl_file(self.conn, path, source="fixture_concat")
        self.assertEqual(stats["read"], 1)
        self.assertEqual(stats["ingested"], 2)
        self.assertEqual(stats["split_lines"], 1)
        self.assertEqual(stats["errors"], 0)

        count = self.conn.execute("SELECT COUNT(*) AS c FROM lighting_decisions").fetchone()["c"]
        self.assertEqual(count, 2)

    def test_duplicate_pending_density_becomes_quality_warning(self) -> None:
        payload = load_fixture("office_pending_preference.json")
        for idx in range(6):
            item = json.loads(json.dumps(payload))
            item["ts"] = f"2026-05-26T12:2{idx}:00.000000-07:00"
            item["evidence_id"] = f"pp-duplicate-{idx}"
            item["occupancy"]["age_s"] = 15
            ingest_payload(
                self.conn,
                source="fixture",
                source_id=f"duplicate-{idx}",
                payload=item,
                raw_text=canonical_json(item),
                source_path="duplicate",
                line_no=idx + 1,
            )
        build_preference_episodes(self.conn)
        aggregate_lighting_preferences(self.conn)

        candidate = self.conn.execute(
            """
            SELECT blockers_json, quality_warnings_json, duplicate_pending_ratio
            FROM preference_candidates
            """
        ).fetchone()
        blockers = json.loads(candidate["blockers_json"])
        warnings = json.loads(candidate["quality_warnings_json"])
        warning_by_code = {w["code"]: w for w in warnings}
        self.assertIn("duplicate-heavy pending captures", blockers)
        self.assertGreater(candidate["duplicate_pending_ratio"], 0.6)
        self.assertIn("duplicate_pending_density", warning_by_code)
        self.assertGreater(
            warning_by_code["duplicate_pending_density"]["details"]["duplicate_count"],
            0,
        )

    def test_override_burst_counts_as_one_effective_evidence_unit(self) -> None:
        payload = load_fixture("office_override_event.json")
        actuals = [20, 30, 40, 55, 70, 50, 35, 45, 55, 60]
        for idx, actual in enumerate(actuals):
            item = json.loads(json.dumps(payload))
            item["context_id"] = f"ovr-burst-{idx}"
            item["ts"] = f"2026-05-26T21:23:0{idx // 3}.{idx:06d}-07:00"
            item["context"]["profile"] = "late_evening"
            item["context"]["state"] = "vacant"
            item["observed"]["brightness_pct"] = actual
            item["predicted"]["brightness_pct"] = 20
            item["delta_pct"] = actual - 20
            item["predictions_by_zone"]["office"]["predicted_pct"] = 20
            item["predictions_by_zone"]["office"]["state"] = "vacant"
            ingest_payload(
                self.conn,
                source="fixture",
                source_id=f"burst-override-{idx}",
                payload=item,
                raw_text=canonical_json(item),
                source_path="burst",
                line_no=idx + 1,
            )

        build_preference_episodes(self.conn)
        aggregate_lighting_preferences(self.conn)

        candidate = self.conn.execute(
            """
            SELECT id, evidence_count, raw_evidence_count, raw_override_count,
                   override_session_count, burst_event_count,
                   max_override_burst_size, override_burst_ratio,
                   pending_evidence_count, session_intent_count,
                   automation_tail_session_count, evidence_tier,
                   median_actual_brightness_pct, median_predicted_brightness_pct,
                   blockers_json, quality_warnings_json, evidence_ids_json
            FROM preference_candidates
            """
        ).fetchone()
        self.assertEqual(candidate["evidence_count"], 1)
        self.assertEqual(candidate["raw_evidence_count"], 10)
        self.assertEqual(candidate["raw_override_count"], 10)
        self.assertEqual(candidate["override_session_count"], 1)
        self.assertEqual(candidate["pending_evidence_count"], 0)
        self.assertEqual(candidate["session_intent_count"], 1)
        self.assertEqual(candidate["automation_tail_session_count"], 0)
        self.assertEqual(candidate["evidence_tier"], "session_intent")
        self.assertEqual(candidate["burst_event_count"], 9)
        self.assertEqual(candidate["max_override_burst_size"], 10)
        self.assertGreater(candidate["override_burst_ratio"], 0.8)
        self.assertEqual(candidate["median_actual_brightness_pct"], 60)
        self.assertEqual(candidate["median_predicted_brightness_pct"], 20)
        self.assertIn("fewer than 5 non-shadow observations", json.loads(candidate["blockers_json"]))

        warnings = json.loads(candidate["quality_warnings_json"])
        warning_by_code = {w["code"]: w for w in warnings}
        self.assertIn("override_burst_collapsed", warning_by_code)
        self.assertEqual(
            warning_by_code["override_burst_collapsed"]["details"]["burst_event_count"],
            9,
        )
        evidence_ids = json.loads(candidate["evidence_ids_json"])
        self.assertEqual(len(evidence_ids), 1)
        self.assertTrue(evidence_ids[0].endswith("ovr-burst-9"))

        client = TestClient(app)
        sessions = client.get("/api/intelligence/override-sessions?zone=office&hours=24&include_events=true")
        self.assertEqual(sessions.status_code, 200)
        body = sessions.json()
        self.assertEqual(body["summary"]["raw_override_count"], 10)
        self.assertEqual(body["summary"]["override_session_count"], 1)
        self.assertEqual(body["summary"]["burst_event_count"], 9)
        self.assertEqual(body["sessions"][0]["representative"]["context_id"], "ovr-burst-9")
        self.assertFalse(body["sessions"][0]["automation_tail_detected"])
        self.assertEqual(body["sessions"][0]["learning_value_pct"], 60)
        self.assertEqual(body["sessions"][0]["learning_value_source"], "final_actual")
        self.assertEqual(body["sessions"][0]["session_tail_path"], [])
        self.assertEqual(len(body["sessions"][0]["events"]), 10)

        detail = client.get(f"/api/intelligence/candidates/{candidate['id']}")
        self.assertEqual(detail.status_code, 200)
        detail_body = detail.json()
        self.assertEqual(detail_body["override_sessions"][0]["size"], 10)
        self.assertEqual(
            detail_body["override_sessions"][0]["final_actual_brightness_pct"],
            60,
        )

    def test_automation_tail_uses_user_landed_value_for_learning(self) -> None:
        payload = load_fixture("office_override_event.json")
        actuals = [35, 58, 58, 0, 0]
        for idx, actual in enumerate(actuals):
            item = json.loads(json.dumps(payload))
            item["context_id"] = f"ovr-tail-{idx}"
            item["ts"] = f"2026-05-26T21:23:0{idx}.{idx:06d}-07:00"
            item["context"]["profile"] = "late_evening"
            item["context"]["state"] = "vacant"
            item["observed"]["brightness_pct"] = actual
            item["observed"]["light_state"] = "off" if actual == 0 else "on"
            item["predicted"]["brightness_pct"] = 20
            item["delta_pct"] = actual - 20
            item["predictions_by_zone"]["office"]["predicted_pct"] = 20
            item["predictions_by_zone"]["office"]["state"] = "vacant"
            ingest_payload(
                self.conn,
                source="fixture",
                source_id=f"tail-override-{idx}",
                payload=item,
                raw_text=canonical_json(item),
                source_path="tail",
                line_no=idx + 1,
            )

        build_preference_episodes(self.conn)
        aggregate_lighting_preferences(self.conn)
        candidate = self.conn.execute(
            """
            SELECT id, evidence_count, raw_evidence_count, burst_event_count,
                   pending_evidence_count, session_intent_count,
                   automation_tail_session_count, evidence_tier,
                   median_actual_brightness_pct, median_predicted_brightness_pct,
                   evidence_ids_json, context_json
            FROM preference_candidates
            """
        ).fetchone()
        self.assertEqual(candidate["evidence_count"], 1)
        self.assertEqual(candidate["raw_evidence_count"], 5)
        self.assertEqual(candidate["burst_event_count"], 4)
        self.assertEqual(candidate["pending_evidence_count"], 0)
        self.assertEqual(candidate["session_intent_count"], 1)
        self.assertEqual(candidate["automation_tail_session_count"], 1)
        self.assertEqual(candidate["evidence_tier"], "session_intent")
        self.assertEqual(candidate["median_actual_brightness_pct"], 58)
        self.assertEqual(candidate["median_predicted_brightness_pct"], 20)
        self.assertEqual(json.loads(candidate["context_json"])["light_state"], "on")
        evidence_ids = json.loads(candidate["evidence_ids_json"])
        self.assertEqual(len(evidence_ids), 1)
        self.assertTrue(evidence_ids[0].endswith("ovr-tail-2"))

        client = TestClient(app)
        sessions = client.get("/api/intelligence/override-sessions?zone=office&hours=24&include_events=true")
        self.assertEqual(sessions.status_code, 200)
        session = sessions.json()["sessions"][0]
        self.assertTrue(session["automation_tail_detected"])
        self.assertEqual(session["session_final_actual_pct"], 0)
        self.assertEqual(session["session_user_landed_pct"], 58)
        self.assertEqual(session["learning_value_pct"], 58)
        self.assertEqual(session["learning_value_source"], "user_landed")
        self.assertEqual(session["session_tail_path"], [0.0, 0.0])

        detail = client.get(f"/api/intelligence/candidates/{candidate['id']}")
        self.assertEqual(detail.status_code, 200)
        detail_session = detail.json()["override_sessions"][0]
        self.assertTrue(detail_session["automation_tail_detected"])
        self.assertEqual(detail_session["learning_value_pct"], 58)

        readiness = client.get(f"/api/intelligence/candidates/{candidate['id']}/readiness")
        self.assertEqual(readiness.status_code, 200)
        readiness_body = readiness.json()["readiness"]
        blocker_codes = {blocker["code"] for blocker in readiness_body["hard_blockers"]}
        self.assertFalse(readiness_body["ready"])
        self.assertIn("durable_pending_preference", blocker_codes)

        preview = client.get(f"/api/intelligence/proposal-preview/{candidate['id']}")
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(
            preview.json()["proposal_preview"]["blocked_reason"],
            "needs durable pending preference confirmation",
        )

    def test_session_intent_only_candidate_needs_pending_preference_before_proposal(self) -> None:
        payload = load_fixture("office_override_event.json")
        timestamps = [
            "2026-05-25T08:00:00.000000-07:00",
            "2026-05-25T09:00:00.000000-07:00",
            "2026-05-25T10:00:00.000000-07:00",
            "2026-05-26T08:00:00.000000-07:00",
            "2026-05-26T09:00:00.000000-07:00",
        ]
        for idx, ts in enumerate(timestamps):
            item = json.loads(json.dumps(payload))
            item["context_id"] = f"ovr-session-only-{idx}"
            item["ts"] = ts
            item["context"]["profile"] = "midday"
            item["context"]["state"] = "present"
            item["observed"]["brightness_pct"] = 30
            item["predicted"]["brightness_pct"] = 80
            item["delta_pct"] = -50
            item["predictions_by_zone"]["office"]["predicted_pct"] = 80
            item["predictions_by_zone"]["office"]["state"] = "present"
            ingest_payload(
                self.conn,
                source="fixture",
                source_id=f"session-only-override-{idx}",
                payload=item,
                raw_text=canonical_json(item),
                source_path="session-only",
                line_no=idx + 1,
            )

        build_preference_episodes(self.conn)
        aggregate_lighting_preferences(self.conn)
        candidate = self.conn.execute(
            """
            SELECT id, evidence_count, pending_evidence_count,
                   session_intent_count, evidence_tier
            FROM preference_candidates
            WHERE context_json LIKE '%"state":"present"%'
            ORDER BY evidence_count DESC
            LIMIT 1
            """
        ).fetchone()
        self.assertEqual(candidate["evidence_count"], 5)
        self.assertEqual(candidate["pending_evidence_count"], 0)
        self.assertEqual(candidate["session_intent_count"], 5)
        self.assertEqual(candidate["evidence_tier"], "session_intent")

        self.conn.execute(
            """
            UPDATE preference_candidates
            SET status = 'candidate',
                blockers_json = '[]',
                quality_warnings_json = '[]',
                linked_override_count = 1
            WHERE id = ?
            """,
            (candidate["id"],),
        )
        self.conn.commit()

        client = TestClient(app)
        readiness = client.get(f"/api/intelligence/candidates/{candidate['id']}/readiness")
        self.assertEqual(readiness.status_code, 200)
        readiness_body = readiness.json()["readiness"]
        gates = {gate["code"]: gate for gate in readiness_body["gates"]}
        self.assertTrue(gates["candidate_not_blocked"]["passed"])
        self.assertTrue(gates["enough_primary_evidence"]["passed"])
        self.assertTrue(gates["enough_days_seen"]["passed"])
        self.assertTrue(gates["linked_manual_override"]["passed"])
        self.assertFalse(gates["durable_pending_preference"]["passed"])
        self.assertFalse(readiness_body["ready"])

        preview = client.get(f"/api/intelligence/proposal-preview/{candidate['id']}").json()
        self.assertEqual(
            preview["proposal_preview"]["blocked_reason"],
            "needs durable pending preference confirmation",
        )
        self.assertEqual(
            preview["proposal_preview"]["why_now"]["evidence_tier"],
            "session_intent",
        )
        self.assertEqual(
            preview["proposal_preview"]["why_now"]["pending_evidence_count"],
            0,
        )

    def test_pending_linked_to_automation_tail_uses_session_learning_value(self) -> None:
        override = load_fixture("office_override_event.json")
        pending = load_fixture("office_pending_preference.json")
        actuals = [35, 58, 58, 0, 0]
        for idx, actual in enumerate(actuals):
            item = json.loads(json.dumps(override))
            item["context_id"] = f"ovr-tail-pending-{idx}"
            item["ts"] = f"2026-05-26T21:23:0{idx}.{idx:06d}-07:00"
            item["context"]["profile"] = "late_evening"
            item["context"]["state"] = "vacant"
            item["observed"]["brightness_pct"] = actual
            item["observed"]["light_state"] = "off" if actual == 0 else "on"
            item["predicted"]["brightness_pct"] = 20
            item["delta_pct"] = actual - 20
            item["predictions_by_zone"]["office"]["predicted_pct"] = 20
            item["predictions_by_zone"]["office"]["state"] = "vacant"
            ingest_payload(
                self.conn,
                source="fixture",
                source_id=f"tail-pending-override-{idx}",
                payload=item,
                raw_text=canonical_json(item),
                source_path="tail-pending",
                line_no=idx + 1,
            )

        pref = json.loads(json.dumps(pending))
        pref["ts"] = "2026-05-26T21:23:12.000000-07:00"
        pref["evidence_id"] = "pp-tail-pending-0"
        pref["related_override_context_id"] = "ovr-tail-pending-4"
        pref["context"]["profile"] = "late_evening"
        pref["context"]["state"] = "present"
        pref["captured_state"]["brightness_pct"] = 0
        pref["captured_state"]["light_state"] = "off"
        pref["what_automation_would_have_done"]["brightness_pct"] = 20
        pref["prediction_consistency"]["primary_prediction_pct"] = 20
        pref["prediction_consistency"]["would_have_done_brightness_pct"] = 20
        pref["prediction_consistency"]["prediction_mismatch_pct"] = 0
        pref["predictions_by_zone"]["office"]["predicted_pct"] = 20
        pref["predictions_by_zone"]["office"]["state"] = "present"
        pref["capture_suppressed_count"] = 0
        pref["occupancy"]["age_s"] = 15
        pref["occupancy"]["flicker_count_5min"] = 0
        ingest_payload(
            self.conn,
            source="fixture",
            source_id="tail-pending-capture",
            payload=pref,
            raw_text=canonical_json(pref),
            source_path="tail-pending",
            line_no=10,
        )

        build_preference_episodes(self.conn)
        aggregate_lighting_preferences(self.conn)
        candidate = self.conn.execute(
            """
            SELECT id, evidence_count, pending_evidence_count,
                   session_intent_count, automation_tail_session_count,
                   evidence_tier, median_actual_brightness_pct,
                   median_predicted_brightness_pct, median_delta_pct,
                   context_json, quality_warnings_json, evidence_ids_json
            FROM preference_candidates
            WHERE context_json LIKE '%"state":"vacant"%'
              AND context_json LIKE '%"light_state":"on"%'
            ORDER BY pending_evidence_count DESC, evidence_count DESC
            LIMIT 1
            """
        ).fetchone()
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["evidence_tier"], "pending_preference")
        self.assertEqual(candidate["pending_evidence_count"], 1)
        self.assertEqual(candidate["session_intent_count"], 1)
        self.assertEqual(candidate["automation_tail_session_count"], 1)
        self.assertEqual(candidate["median_actual_brightness_pct"], 58)
        self.assertEqual(candidate["median_predicted_brightness_pct"], 20)
        self.assertEqual(candidate["median_delta_pct"], 38)
        self.assertEqual(json.loads(candidate["context_json"])["state"], "vacant")
        self.assertEqual(json.loads(candidate["context_json"])["light_state"], "on")

        warnings = json.loads(candidate["quality_warnings_json"])
        warning_codes = {warning["code"] for warning in warnings}
        self.assertIn("pending_intent_reconciled", warning_codes)
        evidence_ids = json.loads(candidate["evidence_ids_json"])
        self.assertTrue(any(eid.endswith("ovr-tail-pending-2") for eid in evidence_ids))
        self.assertTrue(any(eid.endswith("ovr-tail-pending-4") for eid in evidence_ids))

        client = TestClient(app)
        audit = client.get("/api/intelligence/evidence-link-audit?zone=office&hours=24")
        self.assertEqual(audit.status_code, 200)
        audit_body = audit.json()
        self.assertEqual(audit_body["summary"]["pending_count"], 1)
        self.assertEqual(audit_body["summary"]["automation_tail_linked_count"], 1)
        self.assertEqual(audit_body["summary"]["reconciled_count"], 1)
        item = audit_body["items"][0]
        self.assertEqual(item["status"], "reconciled")
        self.assertEqual(item["aggregation_value_pct"], 58)
        self.assertEqual(item["aggregation_value_source"], "session_learning_value")
        self.assertEqual(item["captured_actual_brightness_pct"], 0)
        self.assertIn("automation_tail_capture_gap", item["flags"])
        self.assertEqual(item["matched_session"]["match_role"], "last")
        self.assertEqual(item["matched_session"]["session_last_context_id"], "ovr-tail-pending-4")
        self.assertEqual(item["matched_session"]["learning_context_id"], "ovr-tail-pending-2")
        self.assertEqual(item["matched_session"]["session_tail_path"], [0.0, 0.0])

    def test_intentional_off_session_remains_off_learning_value(self) -> None:
        payload = load_fixture("office_override_event.json")
        for idx in range(2):
            item = json.loads(json.dumps(payload))
            item["context_id"] = f"ovr-off-{idx}"
            item["ts"] = f"2026-05-26T21:24:0{idx}.{idx:06d}-07:00"
            item["context"]["profile"] = "late_evening"
            item["context"]["state"] = "vacant"
            item["observed"]["brightness_pct"] = 0
            item["observed"]["light_state"] = "off"
            item["predicted"]["brightness_pct"] = 20
            item["delta_pct"] = -20
            ingest_payload(
                self.conn,
                source="fixture",
                source_id=f"off-override-{idx}",
                payload=item,
                raw_text=canonical_json(item),
                source_path="off",
                line_no=idx + 1,
            )

        build_preference_episodes(self.conn)
        aggregate_lighting_preferences(self.conn)
        candidate = self.conn.execute(
            """
            SELECT median_actual_brightness_pct, context_json
            FROM preference_candidates
            """
        ).fetchone()
        self.assertEqual(candidate["median_actual_brightness_pct"], 0)
        self.assertEqual(json.loads(candidate["context_json"])["light_state"], "off")

        client = TestClient(app)
        session = client.get(
            "/api/intelligence/override-sessions?zone=office&hours=24"
        ).json()["sessions"][0]
        self.assertFalse(session["automation_tail_detected"])
        self.assertEqual(session["learning_value_pct"], 0)
        self.assertEqual(session["learning_value_source"], "off_session")
        self.assertEqual(session["session_tail_path"], [])

    def test_gaming_active_splits_session_key_from_non_gaming_events(self) -> None:
        """MC.1 — Steam-driven gaming context should separate override
        sessions that occurred during a game from otherwise-identical
        non-gaming overrides. Five events on the same light/zone/profile
        but split 3 gaming + 2 non-gaming should produce 2 override
        sessions (one per gaming_active value), NOT 1 folded session."""
        payload = load_fixture("office_override_event.json")
        # All same zone/profile/state — only gaming_active differs.
        # Three gaming events 30s apart (force separate sessions within
        # gaming=true). Two non-gaming events 30s apart, also separate.
        cases = [
            ("ovr-gaming-0",  "2026-05-26T22:00:00.000000-07:00", True),
            ("ovr-gaming-1",  "2026-05-26T22:00:30.000000-07:00", True),
            ("ovr-gaming-2",  "2026-05-26T22:01:00.000000-07:00", True),
            ("ovr-nongame-0", "2026-05-26T22:01:30.000000-07:00", False),
            ("ovr-nongame-1", "2026-05-26T22:02:00.000000-07:00", False),
        ]
        for ctx_id, ts, gaming in cases:
            item = json.loads(json.dumps(payload))
            item["context_id"] = ctx_id
            item["ts"] = ts
            item["context"]["profile"] = "late_evening"
            item["context"]["state"] = "vacant"
            item["context"]["gaming_active"] = gaming
            item["observed"]["brightness_pct"] = 30 if gaming else 60
            item["predicted"]["brightness_pct"] = 50
            item["delta_pct"] = (30 if gaming else 60) - 50
            ingest_payload(
                self.conn,
                source="fixture",
                source_id=ctx_id,
                payload=item,
                raw_text=canonical_json(item),
                source_path="gaming_split",
                line_no=1,
            )

        # Confirm DB has the gaming_active column populated correctly.
        rows = self.conn.execute(
            "SELECT context_id, gaming_active FROM preference_observations ORDER BY ts"
        ).fetchall()
        self.assertEqual(len(rows), 5)
        self.assertEqual([r["gaming_active"] for r in rows], [1, 1, 1, 0, 0])

        # The session_key tuple must include gaming_active. Same-gaming
        # events still group as one session; different-gaming forms a
        # second session.
        from app.override_sessions import (
            build_override_sessions,
            session_key,
        )
        all_overrides = self.conn.execute(
            "SELECT * FROM preference_observations WHERE kind='override_event' ORDER BY ts"
        ).fetchall()
        keys = {session_key(r) for r in all_overrides}
        # Two distinct keys differing only on the gaming_active slot
        # (index 5: zone, light_entity, profile, state, tv_playing,
        # gaming_active, asleep, night_safe, user_at_home).
        self.assertEqual(len(keys), 2, f"expected 2 distinct session_keys, got {keys}")
        gaming_slot_values = {k[5] for k in keys}
        self.assertEqual(gaming_slot_values, {0, 1})

        # The build_override_sessions session bucketing must reflect
        # the same separation — gaming/non-gaming events stay apart even
        # if they're within the 10s adjacency window of each other.
        sessions = build_override_sessions(all_overrides)
        # Different `session_key` buckets => the within-key 30s gap > 10s
        # window puts each event into its own micro-session. So expect
        # 5 micro-sessions (3 gaming + 2 non-gaming). What matters is
        # that no session contains BOTH gaming=1 AND gaming=0 events.
        for sess in sessions:
            gaming_set = {r["gaming_active"] for r in sess}
            self.assertEqual(
                len(gaming_set), 1,
                f"session mixed gaming + non-gaming events: {gaming_set}"
            )

    def test_readiness_gate_allows_clean_multi_day_candidate_preview(self) -> None:
        pending = load_fixture("office_pending_preference.json")
        override = load_fixture("office_override_event.json")
        timestamps = [
            ("2026-05-25T08:00:00.000000-07:00", "2026-05-25T08:03:00.000000-07:00"),
            ("2026-05-25T09:00:00.000000-07:00", "2026-05-25T09:03:00.000000-07:00"),
            ("2026-05-25T10:00:00.000000-07:00", "2026-05-25T10:03:00.000000-07:00"),
            ("2026-05-26T08:00:00.000000-07:00", "2026-05-26T08:03:00.000000-07:00"),
            ("2026-05-26T09:00:00.000000-07:00", "2026-05-26T09:03:00.000000-07:00"),
        ]
        for idx, (override_ts, pending_ts) in enumerate(timestamps):
            ov = json.loads(json.dumps(override))
            ov["context_id"] = f"ovr-clean-{idx}"
            ov["ts"] = override_ts
            ov["observed"]["brightness_pct"] = 30
            ov["predicted"]["brightness_pct"] = 80
            ov["delta_pct"] = -50
            ov["context"]["state"] = "present"
            ov["predictions_by_zone"]["office"]["predicted_pct"] = 80
            ov["predictions_by_zone"]["office"]["state"] = "present"
            ingest_payload(
                self.conn,
                source="fixture",
                source_id=f"clean-override-{idx}",
                payload=ov,
                raw_text=canonical_json(ov),
                source_path="clean",
                line_no=idx + 1,
            )

            pref = json.loads(json.dumps(pending))
            pref["ts"] = pending_ts
            pref["evidence_id"] = f"pp-clean-{idx}"
            pref["related_override_context_id"] = f"ovr-clean-{idx}"
            pref["capture_suppressed_count"] = 0
            pref["occupancy"]["age_s"] = 15
            pref["occupancy"]["flicker_count_5min"] = 0
            pref["context"]["dwell_s"] = 15
            pref["prediction_consistency"]["prediction_mismatch_pct"] = 0
            pref["prediction_consistency"]["primary_prediction_pct"] = 80
            pref["prediction_consistency"]["would_have_done_brightness_pct"] = 80
            pref["what_automation_would_have_done"]["brightness_pct"] = 80
            pref["captured_state"]["brightness_pct"] = 30
            ingest_payload(
                self.conn,
                source="fixture",
                source_id=f"clean-pending-{idx}",
                payload=pref,
                raw_text=canonical_json(pref),
                source_path="clean",
                line_no=idx + 1,
            )

        build_preference_episodes(self.conn)
        aggregate_lighting_preferences(self.conn)
        candidate = self.conn.execute(
            """
            SELECT id, status, evidence_count, linked_override_count,
                   pending_evidence_count, session_intent_count, evidence_tier,
                   blockers_json
            FROM preference_candidates
            WHERE context_json LIKE '%"state":"present"%'
              AND context_json LIKE '%"light_state":null%'
            ORDER BY evidence_count DESC
            LIMIT 1
            """
        ).fetchone()
        self.assertEqual(candidate["status"], "candidate")
        self.assertEqual(candidate["evidence_count"], 5)
        self.assertEqual(candidate["pending_evidence_count"], 5)
        self.assertEqual(candidate["session_intent_count"], 0)
        self.assertEqual(candidate["evidence_tier"], "pending_preference")
        self.assertGreaterEqual(candidate["linked_override_count"], 1)
        self.assertEqual(json.loads(candidate["blockers_json"]), [])

        client = TestClient(app)
        readiness = client.get(f"/api/intelligence/candidates/{candidate['id']}/readiness").json()["readiness"]
        self.assertTrue(readiness["ready"])
        self.assertEqual(readiness["status"], "ready_for_proposal")
        gate_codes = {gate["code"]: gate for gate in readiness["gates"]}
        self.assertTrue(gate_codes["durable_pending_preference"]["passed"])

        preview = client.get(f"/api/intelligence/proposal-preview/{candidate['id']}").json()
        self.assertTrue(preview["proposal_preview"]["ready"])
        self.assertEqual(preview["proposal_preview"]["kind"], "lighting_preference_experiment")
        self.assertIn("manual override rate", preview["proposal_preview"]["success_metric"])
        self.assertEqual(
            preview["proposal_preview"]["why_now"]["evidence_tier"],
            "pending_preference",
        )
        self.assertEqual(
            preview["proposal_preview"]["why_now"]["pending_evidence_count"],
            5,
        )

        production_counts_before = {
            table: self.conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
            for table in ("proposals", "experiments", "decisions")
        }
        sandbox = client.post(
            f"/api/intelligence/sandbox/experiment-chain/run?candidate_id={candidate['id']}"
        )
        self.assertEqual(sandbox.status_code, 200)
        sandbox_body = sandbox.json()
        self.assertTrue(sandbox_body["ok"])
        self.assertTrue(sandbox_body["sandbox"])
        self.assertEqual(sandbox_body["status"], "completed")
        self.assertFalse(sandbox_body["persisted_to_production"])
        self.assertTrue(sandbox_body["production_unchanged"])
        self.assertEqual(sandbox_body["chain"]["candidate_id"], candidate["id"])
        self.assertTrue(sandbox_body["chain"]["candidate_ready"])
        self.assertTrue(sandbox_body["chain"]["synthetic_proposal_id"].startswith("sandbox-proposal:"))
        self.assertTrue(sandbox_body["chain"]["experiment_id"].startswith("experiment:"))
        self.assertTrue(sandbox_body["chain"]["review_ready"])
        self.assertFalse(sandbox_body["chain"]["can_activate"])
        self.assertFalse(sandbox_body["chain"]["can_apply"])
        self.assertEqual(
            sandbox_body["synthetic_proposal"]["status"],
            "approved_for_experiment",
        )
        self.assertTrue(sandbox_body["synthetic_proposal"]["body"]["sandbox"]["synthetic"])
        self.assertEqual(
            sandbox_body["experiment"]["proposal"]["id"],
            sandbox_body["chain"]["synthetic_proposal_id"],
        )
        self.assertFalse(sandbox_body["manifest"]["can_apply"])
        self.assertFalse(sandbox_body["manifest"]["safety"]["ha_writes"])
        self.assertFalse(sandbox_body["manifest"]["safety"]["policy_activation"])

        explanation = client.post(
            f"/api/intelligence/sandbox/experiment-chain/explain?candidate_id={candidate['id']}"
        )
        self.assertEqual(explanation.status_code, 200)
        explanation_body = explanation.json()
        self.assertTrue(explanation_body["ok"])
        self.assertTrue(explanation_body["sandbox"])
        self.assertEqual(explanation_body["status"], "completed")
        self.assertFalse(explanation_body["persisted_to_production"])
        self.assertTrue(explanation_body["production_unchanged"])
        self.assertFalse(explanation_body["ready_now"])
        self.assertEqual(explanation_body["candidate"]["id"], candidate["id"])
        self.assertTrue(explanation_body["blocking_phases"]["candidate_ready"])
        self.assertTrue(explanation_body["blocking_phases"]["experiment_review_ready"])
        self.assertFalse(explanation_body["blocking_phases"]["activation_ready"])
        self.assertFalse(explanation_body["blocking_phases"]["apply_ready"])
        step_codes = {step["code"] for step in explanation_body["remediation_plan"]}
        self.assertIn("artifact:human_trial_activation_approval", step_codes)
        self.assertIn("artifact:activation_endpoint", step_codes)
        self.assertIn("artifact:implementation_manifest", step_codes)
        self.assertIn("artifact:rollback_checkpoint", step_codes)
        self.assertIn("artifact:monitor_job", step_codes)
        self.assertIn("manifest:apply_disabled", step_codes)
        self.assertIn("manifest:apply_endpoint_absent", step_codes)
        non_action_codes = {item["code"] for item in explanation_body["non_actions"]}
        self.assertIn("do_not_apply_manifest", non_action_codes)
        self.assertIn("do_not_edit_yaml", non_action_codes)
        self.assertIn("do_not_write_ha", non_action_codes)
        self.assertEqual(
            explanation_body["next_safe_milestone"]["code"],
            "activation_package_design",
        )
        production_counts_after = {
            table: self.conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
            for table in ("proposals", "experiments", "decisions")
        }
        self.assertEqual(production_counts_before, production_counts_after)

        generated = client.post("/api/intelligence/proposals/run")
        self.assertEqual(generated.status_code, 200)
        generated_body = generated.json()
        self.assertEqual(generated_body["ready_count"], 1)
        self.assertEqual(generated_body["created"], 1)
        self.assertEqual(generated_body["proposals"][0]["candidate_id"], candidate["id"])

        listed = client.get("/api/intelligence/proposals?status=draft")
        self.assertEqual(listed.status_code, 200)
        proposal = listed.json()["proposals"][0]
        self.assertEqual(proposal["candidate_id"], candidate["id"])
        self.assertEqual(proposal["status"], "draft")
        self.assertTrue(proposal["readiness"]["ready"])
        self.assertIn("rollback", proposal["body"])
        self.assertIn("success_metric", proposal["body"])
        self.assertEqual(
            proposal["body"]["candidate_revision"]["evidence_tier"],
            "pending_preference",
        )
        self.assertEqual(
            proposal["body"]["why_now"]["pending_evidence_count"],
            5,
        )
        self.assertTrue(proposal["body"]["safety"]["activation_requires_human"])
        self.assertFalse(proposal["body"]["safety"]["ha_writes"])
        self.assertGreaterEqual(len(proposal["evidence_ids"]), 5)

        detail = client.get(f"/api/intelligence/proposals/{proposal['id']}")
        self.assertEqual(detail.status_code, 200)
        detail_body = detail.json()
        self.assertEqual(detail_body["proposal"]["id"], proposal["id"])
        self.assertEqual(detail_body["candidate"]["id"], candidate["id"])
        self.assertGreaterEqual(len(detail_body["evidence"]), 5)
        self.assertEqual(detail_body["decisions"], [])

        generated_again = client.post("/api/intelligence/proposals/run")
        self.assertEqual(generated_again.status_code, 200)
        self.assertEqual(generated_again.json()["created"], 0)

        no_experiment_yet = client.post("/api/intelligence/experiments/design/run")
        self.assertEqual(no_experiment_yet.status_code, 200)
        self.assertEqual(no_experiment_yet.json()["approved_proposal_count"], 0)
        self.assertEqual(no_experiment_yet.json()["created"], 0)

        approved = client.post(
            f"/api/intelligence/proposals/{proposal['id']}/decisions",
            json={
                "decision": "approve",
                "actor": "test",
                "reason": "clean evidence",
                "note": "experiment design only",
            },
        )
        self.assertEqual(approved.status_code, 200)
        approved_body = approved.json()
        self.assertEqual(approved_body["decision"]["prior_state"], "draft")
        self.assertEqual(approved_body["decision"]["new_state"], "approved_for_experiment")
        self.assertFalse(approved_body["decision"]["body"]["safety"]["ha_writes"])

        approved_detail = client.get(f"/api/intelligence/proposals/{proposal['id']}").json()
        self.assertEqual(approved_detail["proposal"]["status"], "approved_for_experiment")
        self.assertEqual(len(approved_detail["decisions"]), 1)
        self.assertEqual(approved_detail["decisions"][0]["actor"], "test")
        self.assertEqual(
            approved_detail["proposal"]["body"]["approval_scope"]["experiment_design_only"],
            True,
        )

        after_approval_run = client.post("/api/intelligence/proposals/run")
        self.assertEqual(after_approval_run.status_code, 200)
        self.assertEqual(after_approval_run.json()["created"], 0)
        self.assertEqual(after_approval_run.json()["updated"], 0)
        still_approved = client.get(f"/api/intelligence/proposals/{proposal['id']}").json()
        self.assertEqual(still_approved["proposal"]["status"], "approved_for_experiment")

        experiments = client.post("/api/intelligence/experiments/design/run")
        self.assertEqual(experiments.status_code, 200)
        experiments_body = experiments.json()
        self.assertEqual(experiments_body["approved_proposal_count"], 1)
        self.assertEqual(experiments_body["created"], 1)
        experiment = experiments_body["experiments"][0]
        self.assertEqual(experiment["proposal_id"], proposal["id"])
        self.assertEqual(experiment["candidate_id"], candidate["id"])
        self.assertEqual(experiment["status"], "inactive_design")

        listed_experiments = client.get("/api/intelligence/experiments?status=inactive_design")
        self.assertEqual(listed_experiments.status_code, 200)
        self.assertEqual(len(listed_experiments.json()["experiments"]), 1)
        experiment_detail = client.get(f"/api/intelligence/experiments/{experiment['id']}")
        self.assertEqual(experiment_detail.status_code, 200)
        experiment_body = experiment_detail.json()
        self.assertEqual(experiment_body["experiment"]["status"], "inactive_design")
        self.assertEqual(experiment_body["proposal"]["id"], proposal["id"])
        self.assertGreaterEqual(len(experiment_body["evidence"]), 5)
        self.assertFalse(experiment_body["experiment"]["body"]["safety"]["ha_writes"])
        self.assertFalse(experiment_body["experiment"]["body"]["safety"]["automation_edits"])
        self.assertFalse(experiment_body["experiment"]["body"]["safety"]["policy_activation"])
        self.assertFalse(experiment_body["experiment"]["body"]["trial_design"]["activation_endpoint_present"])
        self.assertIn("manual override rate", experiment_body["experiment"]["body"]["success_metrics"]["primary"])

        evaluation = client.get(f"/api/intelligence/experiments/{experiment['id']}/evaluation")
        self.assertEqual(evaluation.status_code, 200)
        evaluation_body = evaluation.json()["evaluation"]
        self.assertTrue(evaluation_body["readiness"]["ready_for_activation_review"])
        self.assertEqual(evaluation_body["readiness"]["status"], "ready_for_activation_review")
        self.assertFalse(evaluation_body["activation"]["can_start_experiment"])
        self.assertFalse(evaluation_body["activation"]["activation_endpoint_present"])
        self.assertFalse(evaluation_body["safety"]["ha_writes"])
        self.assertEqual(evaluation_body["outcome"]["status"], "not_started")
        self.assertFalse(evaluation_body["outcome"]["comparison_ready"])
        self.assertGreaterEqual(evaluation_body["baseline"]["eligible_observation_count"], 5)
        self.assertGreaterEqual(evaluation_body["baseline"]["linked_override_count"], 1)
        self.assertGreaterEqual(len(evaluation_body["baseline"]["distinct_days"]), 2)

        evaluation_list = client.get("/api/intelligence/experiments/evaluations")
        self.assertEqual(evaluation_list.status_code, 200)
        self.assertEqual(len(evaluation_list.json()["evaluations"]), 1)
        self.assertEqual(
            evaluation_list.json()["evaluations"][0]["experiment"]["id"],
            experiment["id"],
        )
        self.assertFalse(
            evaluation_list.json()["evaluations"][0]["activation"]["can_start_experiment"],
        )

        preflight = client.get(f"/api/intelligence/experiments/{experiment['id']}/preflight")
        self.assertEqual(preflight.status_code, 200)
        preflight_body = preflight.json()["preflight"]
        self.assertEqual(preflight_body["experiment_id"], experiment["id"])
        self.assertEqual(preflight_body["status"], "blocked_missing_activation_artifacts")
        self.assertTrue(preflight_body["review_ready"])
        self.assertFalse(preflight_body["can_activate"])
        self.assertFalse(preflight_body["activation_api_present"])
        self.assertFalse(preflight_body["safety"]["ha_writes"])
        self.assertFalse(preflight_body["safety"]["policy_activation"])
        artifact_status = {
            artifact["artifact"]: artifact["status"]
            for artifact in preflight_body["required_artifacts"]
        }
        self.assertEqual(artifact_status["human_trial_activation_approval"], "missing_future_decision")
        self.assertEqual(artifact_status["activation_endpoint"], "missing_future_api")
        self.assertEqual(artifact_status["implementation_manifest"], "missing_future_artifact")
        self.assertEqual(artifact_status["rollback_checkpoint"], "missing_future_artifact")
        self.assertEqual(artifact_status["monitor_job"], "missing_future_artifact")
        self.assertEqual(artifact_status["baseline_snapshot"], "computed_read_only")
        self.assertGreaterEqual(preflight_body["baseline_snapshot"]["eligible_observations"], 5)
        self.assertGreaterEqual(preflight_body["baseline_snapshot"]["linked_overrides"], 1)
        self.assertFalse(preflight_body["rollback_package"]["checkpoint_present"])
        self.assertFalse(preflight_body["monitor_spec"]["job_present"])
        gate_by_code = {gate["code"]: gate for gate in preflight_body["gates"]}
        self.assertTrue(gate_by_code["evaluation_ready_for_activation_review"]["passed"])
        self.assertFalse(gate_by_code["explicit_activation_approval_present"]["passed"])
        self.assertFalse(gate_by_code["activation_endpoint_present"]["passed"])
        self.assertFalse(gate_by_code["implementation_manifest_present"]["passed"])
        self.assertFalse(gate_by_code["rollback_checkpoint_present"]["passed"])
        self.assertFalse(gate_by_code["monitor_job_present"]["passed"])

        preflight_list = client.get("/api/intelligence/experiments/preflights")
        self.assertEqual(preflight_list.status_code, 200)
        self.assertEqual(len(preflight_list.json()["preflights"]), 1)
        self.assertEqual(
            preflight_list.json()["preflights"][0]["preflight"]["id"],
            preflight_body["id"],
        )
        self.assertFalse(preflight_list.json()["preflights"][0]["preflight"]["can_activate"])

        manifest = client.get(f"/api/intelligence/experiments/{experiment['id']}/manifest")
        self.assertEqual(manifest.status_code, 200)
        manifest_body = manifest.json()["manifest"]
        self.assertEqual(manifest_body["experiment_id"], experiment["id"])
        self.assertEqual(manifest_body["preflight_id"], preflight_body["id"])
        self.assertEqual(manifest_body["status"], "draft_read_only")
        self.assertFalse(manifest_body["can_apply"])
        self.assertFalse(manifest_body["apply_endpoint_present"])
        self.assertFalse(manifest_body["safety"]["ha_writes"])
        self.assertFalse(manifest_body["safety"]["can_apply_manifest"])
        self.assertEqual(manifest_body["scope"]["zone"], "office")
        self.assertEqual(
            manifest_body["scope"]["context_matcher"]["match_type"],
            "exact_policy_context",
        )
        self.assertGreaterEqual(len(manifest_body["write_set"]), 5)
        self.assertTrue(all(item["hypothetical_only"] for item in manifest_body["write_set"]))
        self.assertTrue(all(item["requires_existing_entity"] for item in manifest_body["write_set"]))
        write_by_id = {item["id"]: item for item in manifest_body["write_set"]}
        self.assertEqual(write_by_id["write:target_brightness_pct"]["domain"], "input_number")
        self.assertEqual(write_by_id["write:target_brightness_pct"]["data"]["value"], 30)
        self.assertEqual(write_by_id["write:target_light_state"]["data"]["value"], "on")
        self.assertIn("input_boolean.", write_by_id["write:trial_enabled"]["target"]["entity_id"])
        self.assertTrue(manifest_body["shadow_parameter_patch"]["hypothetical_only"])
        self.assertEqual(
            manifest_body["shadow_parameter_patch"]["payload"]["experiment_id"],
            experiment["id"],
        )
        self.assertFalse(manifest_body["rollback_checkpoint_schema"]["checkpoint_present"])
        self.assertGreaterEqual(
            len(manifest_body["rollback_checkpoint_schema"]["helper_entity_states"]),
            len(manifest_body["write_set"]),
        )
        self.assertIn(
            "verify all helper entities exist",
            manifest_body["validation_plan"]["must_pass_before_apply"],
        )
        self.assertGreaterEqual(
            manifest_body["required_preflight_state"]["missing_artifact_count"],
            5,
        )

        manifest_list = client.get("/api/intelligence/experiments/manifests")
        self.assertEqual(manifest_list.status_code, 200)
        self.assertEqual(len(manifest_list.json()["manifests"]), 1)
        self.assertEqual(
            manifest_list.json()["manifests"][0]["manifest"]["id"],
            manifest_body["id"],
        )
        self.assertFalse(manifest_list.json()["manifests"][0]["manifest"]["can_apply"])

        experiments_again = client.post("/api/intelligence/experiments/design/run")
        self.assertEqual(experiments_again.status_code, 200)
        self.assertEqual(experiments_again.json()["created"], 0)
        self.assertEqual(experiments_again.json()["unchanged"], 1)

        self.conn.execute(
            "UPDATE experiments SET status = 'archived' WHERE id = ?",
            (experiment["id"],),
        )
        self.conn.commit()
        archived_again = client.post("/api/intelligence/experiments/design/run")
        self.assertEqual(archived_again.status_code, 200)
        self.assertEqual(archived_again.json()["created"], 0)
        self.assertEqual(archived_again.json()["updated"], 0)
        self.assertEqual(archived_again.json()["skipped"], 1)
        archived_detail = client.get(f"/api/intelligence/experiments/{experiment['id']}")
        self.assertEqual(archived_detail.status_code, 200)
        self.assertEqual(archived_detail.json()["experiment"]["status"], "archived")
        archived_eval = client.get(f"/api/intelligence/experiments/{experiment['id']}/evaluation")
        self.assertEqual(archived_eval.status_code, 200)
        self.assertFalse(archived_eval.json()["evaluation"]["readiness"]["ready_for_activation_review"])
        archived_preflight = client.get(f"/api/intelligence/experiments/{experiment['id']}/preflight")
        self.assertEqual(archived_preflight.status_code, 200)
        self.assertFalse(archived_preflight.json()["preflight"]["review_ready"])
        self.assertFalse(archived_preflight.json()["preflight"]["can_activate"])
        archived_manifest = client.get(f"/api/intelligence/experiments/{experiment['id']}/manifest")
        self.assertEqual(archived_manifest.status_code, 200)
        self.assertFalse(archived_manifest.json()["manifest"]["can_apply"])
        self.assertFalse(archived_manifest.json()["manifest"]["safety"]["policy_activation"])

        suppressed = client.post(
            f"/api/intelligence/proposals/{proposal['id']}/decisions",
            json={
                "decision": "do_not_learn",
                "actor": "test",
                "reason": "not representative",
            },
        )
        self.assertEqual(suppressed.status_code, 200)
        self.assertEqual(suppressed.json()["decision"]["new_state"], "suppressed")
        self.assertIsNotNone(suppressed.json()["suppression"])
        suppressions = client.get("/api/intelligence/suppressions").json()["suppressions"]
        self.assertEqual(len(suppressions), 1)
        suppressed_detail = client.get(f"/api/intelligence/proposals/{proposal['id']}").json()
        self.assertEqual(suppressed_detail["proposal"]["status"], "suppressed")
        self.assertEqual(len(suppressed_detail["decisions"]), 2)

        decision_rows = client.get(
            f"/api/intelligence/decisions?subject_type=proposal&subject_id={proposal['id']}"
        )
        self.assertEqual(decision_rows.status_code, 200)
        self.assertEqual(len(decision_rows.json()["decisions"]), 2)

    def test_multimodal_observation_packet_contract_is_read_only(self) -> None:
        self.ingest_fixture("office_pending_preference.json")
        self.conn.commit()
        client = TestClient(app)

        status = client.get("/api/intelligence/multimodal/status")
        self.assertEqual(status.status_code, 200)
        status_body = status.json()
        self.assertEqual(status_body["prompt_contract"]["prompt_version"], "home_observation_labeler_v1")
        self.assertEqual(status_body["prompt_contract"]["schema_version"], "home_observation_labels_v1")
        self.assertIn("desk_work", status_body["prompt_contract"]["allowed_labels"]["primary_activity"])
        self.assertFalse(status_body["prompt_contract"]["ring_buffer"]["running"] if "ring_buffer" in status_body["prompt_contract"] else False)

        observation = self.conn.execute(
            "SELECT id FROM preference_observations WHERE kind = 'pending_preference'"
        ).fetchone()
        capture = client.post(
            "/api/intelligence/observation-packets/capture",
            json={
                "observation_id": observation["id"],
                "capture_frames": False,
                "trigger": "contract_test",
            },
        )
        self.assertEqual(capture.status_code, 200)
        packet_id = capture.json()["packet"]["id"]
        self.assertTrue(capture.json()["created"])
        self.assertEqual(capture.json()["packet"]["event_type"], "pending_preference")
        self.assertEqual(capture.json()["packet"]["zone"], "office")
        self.assertEqual(capture.json()["packet"]["camera"], "living_room")
        self.assertEqual(capture.json()["packet"]["qwen_status"], "not_requested")
        self.assertEqual(capture.json()["frames"], [])
        self.assertEqual(capture.json()["packet"]["retention_policy"]["cloud_upload_allowed"], False)
        self.assertEqual(
            capture.json()["packet"]["ha_metadata"]["related_override_context_id"],
            "ovr-1779822169-431424",
        )

        packets = client.get("/api/intelligence/observation-packets?zone=office")
        self.assertEqual(packets.status_code, 200)
        self.assertEqual(len(packets.json()["observation_packets"]), 1)
        atlas = client.get("/api/intelligence/observation-packets/map")
        self.assertEqual(atlas.status_code, 200)
        self.assertEqual(atlas.json()["projection"], "deterministic_activity_layout_v1")
        self.assertEqual(atlas.json()["points"][0]["activity"], "unknown")

        skipped = client.post(f"/api/intelligence/observation-packets/{packet_id}/label")
        self.assertEqual(skipped.status_code, 200)
        self.assertFalse(skipped.json()["ok"])
        self.assertEqual(skipped.json()["status"], "skipped_no_frames")

        audit = client.post(
            f"/api/intelligence/observation-packets/{packet_id}/audits",
            json={"action": "label_looks_wrong", "actor": "test", "note": "fixture audit"},
        )
        self.assertEqual(audit.status_code, 200)
        detail = client.get(f"/api/intelligence/observation-packets/{packet_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["audits"][0]["action"], "label_looks_wrong")

    def test_observation_packet_human_activity_corrections_are_append_only(self) -> None:
        self.ingest_fixture("office_pending_preference.json")
        self.conn.commit()
        client = TestClient(app)
        observation = self.conn.execute(
            "SELECT id FROM preference_observations WHERE kind = 'pending_preference'"
        ).fetchone()
        capture = client.post(
            "/api/intelligence/observation-packets/capture",
            json={
                "observation_id": observation["id"],
                "capture_frames": False,
                "trigger": "contract_test",
            },
        )
        self.assertEqual(capture.status_code, 200)
        packet_id = capture.json()["packet"]["id"]
        labels = {
            "schema_version": "home_observation_labels_v1",
            "presence": {"label": "person_present", "confidence": 0.9, "evidence_frames": ["frame_2"]},
            "primary_activity": {"label": "unknown", "confidence": 0.1, "evidence_frames": ["frame_2"]},
            "secondary_activities": [],
            "motion_phase": {"label": "still", "confidence": 0.7, "evidence_frames": ["frame_2"]},
            "posture": {"label": "standing", "confidence": 0.7, "evidence_frames": ["frame_2"]},
            "task_surface": {"label": "floor", "confidence": 0.7, "evidence_frames": ["frame_2"]},
            "visual_focus": {"label": "uncertain", "confidence": 0.2, "evidence_frames": ["frame_2"]},
            "screen_visual_state": {"label": "no_screen_visible", "confidence": 0.8, "evidence_frames": ["frame_2"]},
            "room_lighting_visual": {"label": "bright", "confidence": 0.9, "evidence_frames": ["frame_2"]},
            "image_quality": {"label": "clear", "confidence": 0.9, "evidence_frames": ["frame_2"]},
            "privacy_sensitivity": {"label": "normal", "confidence": 0.9, "evidence_frames": ["frame_2"]},
            "metadata_consistency": {"label": "consistent", "confidence": 0.8, "evidence_frames": ["frame_2"]},
            "abstain_reason": None,
            "contradictions": [],
            "short_visible_summary": "A person is visible, but the activity is unclear.",
        }
        self.conn.execute(
            """
            INSERT INTO qwen_label_results
              (id, packet_id, status, prompt_version, schema_version, model,
               model_base_url, request_json, raw_output_text, labels_json,
               validation_errors_json, latency_ms, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "ql-human-correction",
                packet_id,
                "valid",
                "home_observation_labeler_v1",
                "home_observation_labels_v1",
                "qwen3-vl-30b",
                "local",
                "{}",
                canonical_json(labels),
                canonical_json(labels),
                "[]",
                12,
                "2026-05-27T12:00:00+00:00",
            ),
        )
        self.conn.commit()

        detail = client.get(f"/api/intelligence/observation-packets/{packet_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertTrue(detail.json()["packet"]["needs_review"]["primary_activity"])
        self.assertIn("unknown activity", detail.json()["packet"]["needs_review"]["reasons"])

        invalid = client.post(
            f"/api/intelligence/observation-packets/{packet_id}/audits",
            json={"action": "correct_primary_activity", "actor": "test", "body": {"label": "flying"}},
        )
        self.assertEqual(invalid.status_code, 400)

        correction = client.post(
            f"/api/intelligence/observation-packets/{packet_id}/audits",
            json={
                "action": "correct_primary_activity",
                "actor": "test",
                "body": {
                    "axis": "primary_activity",
                    "label": "reading",
                    "confidence": 1.0,
                    "source": "human",
                    "previous_qwen_label": "unknown",
                    "previous_qwen_confidence": 0.1,
                },
            },
        )
        self.assertEqual(correction.status_code, 200)
        correction_id = correction.json()["audit"]["id"]
        detail = client.get(f"/api/intelligence/observation-packets/{packet_id}")
        self.assertEqual(detail.json()["packet"]["latest_labels"]["primary_activity"]["label"], "unknown")
        self.assertEqual(detail.json()["packet"]["effective_labels"]["primary_activity"]["label"], "reading")
        self.assertFalse(detail.json()["packet"]["needs_review"]["primary_activity"])
        atlas = client.get("/api/intelligence/observation-packets/map")
        self.assertEqual(atlas.json()["points"][0]["activity"], "reading")
        self.assertTrue(atlas.json()["points"][0]["human_corrected"])

        retract = client.post(
            f"/api/intelligence/observation-packets/{packet_id}/audits",
            json={
                "action": "retract_label_audit",
                "actor": "test",
                "body": {"target_audit_id": correction_id},
            },
        )
        self.assertEqual(retract.status_code, 200)
        detail = client.get(f"/api/intelligence/observation-packets/{packet_id}")
        self.assertEqual(detail.json()["packet"]["effective_labels"]["primary_activity"]["label"], "unknown")
        self.assertTrue(detail.json()["packet"]["needs_review"]["primary_activity"])

        confirmed = client.post(
            f"/api/intelligence/observation-packets/{packet_id}/audits",
            json={"action": "confirm_unknown_activity", "actor": "test", "body": {"axis": "primary_activity"}},
        )
        self.assertEqual(confirmed.status_code, 200)
        detail = client.get(f"/api/intelligence/observation-packets/{packet_id}")
        self.assertEqual(
            detail.json()["packet"]["human_label_overrides"]["primary_activity"]["source"],
            "human_confirmed_unknown",
        )
        self.assertFalse(detail.json()["packet"]["needs_review"]["primary_activity"])

        bad_reason = client.post(
            f"/api/intelligence/observation-packets/{packet_id}/audits",
            json={"action": "not_labelable_activity", "actor": "test", "body": {"reason": "shrug"}},
        )
        self.assertEqual(bad_reason.status_code, 400)
        not_labelable = client.post(
            f"/api/intelligence/observation-packets/{packet_id}/audits",
            json={"action": "not_labelable_activity", "actor": "test", "body": {"reason": "ambiguous"}},
        )
        self.assertEqual(not_labelable.status_code, 200)
        detail = client.get(f"/api/intelligence/observation-packets/{packet_id}")
        self.assertEqual(
            detail.json()["packet"]["human_label_overrides"]["primary_activity"]["source"],
            "human_not_labelable",
        )
        self.assertEqual(
            detail.json()["packet"]["human_label_overrides"]["primary_activity"]["reason"],
            "ambiguous",
        )

    def test_qwen_label_schema_rejects_preference_like_lighting_labels(self) -> None:
        client = TestClient(app)
        base_axis = {"label": "uncertain", "confidence": 0.4, "evidence_frames": []}
        payload = {
            "schema_version": "home_observation_labels_v1",
            "presence": {"label": "person_present", "confidence": 0.8, "evidence_frames": ["frame_2"]},
            "primary_activity": {"label": "desk_work", "confidence": 0.7, "evidence_frames": ["frame_2"]},
            "secondary_activities": [],
            "motion_phase": {"label": "still", "confidence": 0.7, "evidence_frames": ["frame_2"]},
            "posture": {"label": "seated", "confidence": 0.7, "evidence_frames": ["frame_2"]},
            "task_surface": {"label": "desk", "confidence": 0.7, "evidence_frames": ["frame_2"]},
            "visual_focus": {"label": "laptop_or_keyboard", "confidence": 0.7, "evidence_frames": ["frame_2"]},
            "screen_visual_state": {"label": "screen_light_visible", "confidence": 0.6, "evidence_frames": ["frame_2"]},
            "room_lighting_visual": {"label": "comfortable", "confidence": 0.9, "evidence_frames": ["frame_2"]},
            "image_quality": {"label": "clear", "confidence": 0.9, "evidence_frames": ["frame_2"]},
            "privacy_sensitivity": {"label": "normal", "confidence": 0.9, "evidence_frames": ["frame_2"]},
            "metadata_consistency": {"label": "consistent", "confidence": 0.8, "evidence_frames": ["frame_2"]},
            "abstain_reason": None,
            "contradictions": [],
            "short_visible_summary": "Person seated at a desk with a laptop visible.",
        }
        response = client.post("/api/intelligence/qwen-labels/validate", json={"payload": payload})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["ok"])
        self.assertIn("room_lighting_visual.label invalid: comfortable", response.json()["errors"])

        payload["room_lighting_visual"] = {"label": "bright", "confidence": 0.9, "evidence_frames": ["frame_2"]}
        for key in (
            "motion_phase",
            "posture",
            "task_surface",
            "visual_focus",
            "screen_visual_state",
            "image_quality",
            "privacy_sensitivity",
            "metadata_consistency",
        ):
            payload.setdefault(key, base_axis)
        valid = client.post("/api/intelligence/qwen-labels/validate", json={"payload": payload})
        self.assertEqual(valid.status_code, 200)
        self.assertTrue(valid.json()["ok"])

    def test_qwen_prompt_contract_requires_top_level_axis_objects(self) -> None:
        prompt = build_prompt_contract(
            {"id": "op-test", "camera": "living_room", "zone": "office", "event_type": "override_event", "ha_metadata": {}},
            [{"frame_id": "frame_1", "relative_ts_s": -5, "frame_role": "pre_event"}],
        )
        user_prompt = prompt["user_prompt"]
        self.assertIn("Required JSON response shape:", user_prompt)
        self.assertIn('"schema_version":"home_observation_labels_v1"', user_prompt)
        self.assertIn('"presence":{', user_prompt)
        self.assertIn('"confidence":0.0', user_prompt)
        self.assertIn("Do not use an axes wrapper in the response.", user_prompt)

    def test_multimodal_pilot_is_narrow_and_does_not_use_current_frame_fallback(self) -> None:
        os.environ["INTELLIGENCE_MULTIMODAL_PILOT_ENABLED"] = "1"
        os.environ["INTELLIGENCE_MULTIMODAL_PILOT_ZONES"] = "office"
        os.environ["INTELLIGENCE_MULTIMODAL_CAMERAS"] = "living_room"
        os.environ["INTELLIGENCE_MULTIMODAL_PILOT_LOOKBACK_S"] = "999999999"
        os.environ["INTELLIGENCE_MULTIMODAL_PILOT_AUTO_LABEL"] = "1"
        self.ingest_fixture("office_pending_preference.json")
        self.conn.commit()

        client = TestClient(app)
        response = client.post("/api/intelligence/multimodal/pilot/run")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["pilot"]["created"], 1)
        self.assertEqual(body["pilot"]["labeled"], 0)
        packet = body["pilot"]["packets"][0]
        self.assertEqual(packet["zone"], "office")
        self.assertEqual(packet["camera"], "living_room")
        self.assertEqual(packet["frame_count"], 0)
        self.assertEqual(packet["status"], "frames_missing")

        detail = client.get(f"/api/intelligence/observation-packets/{packet['id']}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["capture_timing"]["quality"], "missing")
        self.assertFalse(detail.json()["label_usability"]["usable_for_clustering"])
        self.assertIn("capture timing is not event-aligned", detail.json()["label_usability"]["blockers"])

    def test_observation_packet_list_reports_real_capture_timing(self) -> None:
        self.ingest_fixture("office_pending_preference.json")
        self.conn.commit()
        client = TestClient(app)

        observation = self.conn.execute(
            "SELECT id FROM preference_observations WHERE kind = 'pending_preference'"
        ).fetchone()
        capture = client.post(
            "/api/intelligence/observation-packets/capture",
            json={
                "observation_id": observation["id"],
                "capture_frames": False,
                "trigger": "contract_test",
            },
        )
        self.assertEqual(capture.status_code, 200)
        packet_id = capture.json()["packet"]["id"]
        for frame_id, role, rel in (
            ("frame_1", "pre_event", -5.0),
            ("frame_2", "event", 0.0),
        ):
            self.conn.execute(
                """
                INSERT INTO observation_frames
                    (id, packet_id, frame_id, frame_role, relative_ts_s, captured_at,
                     camera, path, width, height, bytes, source, promoted_from_ring,
                     privacy_flags_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"{packet_id}:{frame_id}",
                    packet_id,
                    frame_id,
                    role,
                    rel,
                    "2026-05-26T19:02:49+00:00",
                    "living_room",
                    f"/tmp/{frame_id}.jpg",
                    640,
                    360,
                    100,
                    "ring_buffer",
                    1,
                    "[]",
                ),
            )
        self.conn.execute(
            "UPDATE observation_packets SET frame_count = 2, status = 'frames_ready' WHERE id = ?",
            (packet_id,),
        )
        self.conn.commit()

        packets = client.get("/api/intelligence/observation-packets?zone=office")
        self.assertEqual(packets.status_code, 200)
        packet = packets.json()["observation_packets"][0]
        self.assertTrue(packet["capture_timing"]["event_aligned"])
        self.assertEqual(packet["capture_timing"]["quality"], "aligned")
        self.assertNotIn(
            "capture timing is not event-aligned",
            packet["label_usability"]["blockers"],
        )

    def test_label_eval_set_seeds_audits_and_scores_heldout_packets(self) -> None:
        self.ingest_fixture("office_pending_preference.json")
        self.conn.commit()
        client = TestClient(app)
        observation = self.conn.execute(
            "SELECT id FROM preference_observations WHERE kind = 'pending_preference'"
        ).fetchone()
        capture = client.post(
            "/api/intelligence/observation-packets/capture",
            json={
                "observation_id": observation["id"],
                "capture_frames": False,
                "trigger": "contract_test",
            },
        )
        self.assertEqual(capture.status_code, 200)
        packet_id = capture.json()["packet"]["id"]
        for frame_id, role, rel in (
            ("frame_1", "pre_event", -5.0),
            ("frame_2", "event", 0.0),
        ):
            self.conn.execute(
                """
                INSERT INTO observation_frames
                    (id, packet_id, frame_id, frame_role, relative_ts_s, captured_at,
                     camera, path, width, height, bytes, source, promoted_from_ring,
                     privacy_flags_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"{packet_id}:{frame_id}",
                    packet_id,
                    frame_id,
                    role,
                    rel,
                    "2026-05-26T19:02:49+00:00",
                    "living_room",
                    f"/tmp/{frame_id}.jpg",
                    640,
                    360,
                    100,
                    "ring_buffer",
                    1,
                    "[]",
                ),
            )
        labels = {
            "schema_version": "home_observation_labels_v1",
            "presence": {"label": "person_present", "confidence": 0.8, "evidence_frames": ["frame_2"]},
            "primary_activity": {"label": "desk_work", "confidence": 0.8, "evidence_frames": ["frame_2"]},
            "secondary_activities": [],
            "motion_phase": {"label": "still", "confidence": 0.8, "evidence_frames": ["frame_2"]},
            "posture": {"label": "seated", "confidence": 0.8, "evidence_frames": ["frame_2"]},
            "task_surface": {"label": "desk", "confidence": 0.8, "evidence_frames": ["frame_2"]},
            "visual_focus": {"label": "laptop_or_keyboard", "confidence": 0.8, "evidence_frames": ["frame_2"]},
            "screen_visual_state": {"label": "screen_light_visible", "confidence": 0.8, "evidence_frames": ["frame_2"]},
            "room_lighting_visual": {"label": "bright", "confidence": 0.8, "evidence_frames": ["frame_2"]},
            "image_quality": {"label": "clear", "confidence": 0.8, "evidence_frames": ["frame_2"]},
            "privacy_sensitivity": {"label": "normal", "confidence": 0.8, "evidence_frames": ["frame_2"]},
            "metadata_consistency": {"label": "consistent", "confidence": 0.8, "evidence_frames": ["frame_2"]},
            "abstain_reason": None,
            "contradictions": [],
            "short_visible_summary": "Person seated at a desk.",
        }
        self.conn.execute(
            "UPDATE observation_packets SET frame_count = 2, status = 'labeled', qwen_status = 'valid' WHERE id = ?",
            (packet_id,),
        )
        self.conn.execute(
            """
            INSERT INTO qwen_label_results
                (id, packet_id, status, prompt_version, schema_version, model,
                 model_base_url, request_json, labels_json, validation_errors_json,
                 latency_ms, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "ql-eval-test",
                packet_id,
                "valid",
                "home_observation_labeler_v1",
                "home_observation_labels_v1",
                "qwen3-vl-30b",
                "http://qwen.test",
                "{}",
                canonical_json(labels),
                "[]",
                100,
                "2026-05-26T19:02:50+00:00",
            ),
        )
        self.conn.commit()

        seeded = client.post(
            "/api/intelligence/label-evals/seed",
            json={"name": "heldout_test", "target_size": 5, "require_valid_labels": True},
        )
        self.assertEqual(seeded.status_code, 200)
        body = seeded.json()
        self.assertEqual(body["inserted"], 1)
        self.assertEqual(len(body["items"]), 1)
        item = body["items"][0]
        self.assertEqual(item["status"], "unaudited")
        self.assertEqual(item["qwen_labels"]["primary_activity"]["label"], "desk_work")
        self.assertTrue(item["capture_timing"]["event_aligned"])

        audit = client.post(
            f"/api/intelligence/label-evals/items/{item['id']}/audit",
            json={"actor": "test", "human_labels": labels, "note": "matches fixture"},
        )
        self.assertEqual(audit.status_code, 200)
        self.assertEqual(audit.json()["item"]["status"], "audited")
        self.assertEqual(audit.json()["score"]["overall_accuracy"], 1.0)

        status = client.get("/api/intelligence/label-evals/status")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["items"], 1)
        self.assertEqual(status.json()["audited_items"], 1)

        gate = client.get("/api/intelligence/qwen-labels/prompt-gate")
        self.assertEqual(gate.status_code, 200)
        self.assertFalse(gate.json()["gate"]["ready"])
        self.assertIn("needs 50 audited items", gate.json()["gate"]["reason"])

        trial = client.post(
            "/api/intelligence/qwen-labels/prompt-trials/run",
            json={
                "candidate_name": "candidate_prompt_v2",
                "candidate_prompt": {"prompt_version": "candidate_v2"},
                "candidate_labels_by_item": {},
            },
        )
        self.assertEqual(trial.status_code, 200)
        self.assertEqual(trial.json()["trial"]["status"], "blocked_gate")
        self.assertFalse(trial.json()["trial"]["comparison"]["activation_allowed"])

        sets = client.get("/api/intelligence/label-evals")
        self.assertEqual(sets.status_code, 200)
        self.assertEqual(len(sets.json()["sets"]), 1)

        trials = client.get("/api/intelligence/qwen-labels/prompt-trials")
        self.assertEqual(trials.status_code, 200)
        self.assertEqual(trials.json()["trials"][0]["status"], "blocked_gate")

    def test_label_eval_queue_and_accept_qwen_fast_path(self) -> None:
        self.ingest_fixture("office_pending_preference.json")
        self.conn.commit()
        client = TestClient(app)
        observation = self.conn.execute(
            "SELECT id FROM preference_observations WHERE kind = 'pending_preference'"
        ).fetchone()
        capture = client.post(
            "/api/intelligence/observation-packets/capture",
            json={
                "observation_id": observation["id"],
                "capture_frames": False,
                "trigger": "contract_test",
            },
        )
        packet_id = capture.json()["packet"]["id"]
        self.conn.execute(
            "UPDATE observation_packets SET frame_count = 2, status = 'labeled', qwen_status = 'valid' WHERE id = ?",
            (packet_id,),
        )
        labels = {
            "schema_version": "home_observation_labels_v1",
            "presence": {"label": "person_present", "confidence": 0.8, "evidence_frames": ["frame_2"]},
            "primary_activity": {"label": "reading", "confidence": 0.7, "evidence_frames": ["frame_2"]},
            "secondary_activities": [],
            "motion_phase": {"label": "still", "confidence": 0.7, "evidence_frames": ["frame_2"]},
            "posture": {"label": "seated", "confidence": 0.7, "evidence_frames": ["frame_2"]},
            "task_surface": {"label": "sofa", "confidence": 0.7, "evidence_frames": ["frame_2"]},
            "visual_focus": {"label": "book_or_paper", "confidence": 0.7, "evidence_frames": ["frame_2"]},
            "screen_visual_state": {"label": "no_screen_visible", "confidence": 0.7, "evidence_frames": ["frame_2"]},
            "room_lighting_visual": {"label": "dim", "confidence": 0.7, "evidence_frames": ["frame_2"]},
            "image_quality": {"label": "clear", "confidence": 0.7, "evidence_frames": ["frame_2"]},
            "privacy_sensitivity": {"label": "normal", "confidence": 0.7, "evidence_frames": ["frame_2"]},
            "metadata_consistency": {"label": "consistent", "confidence": 0.7, "evidence_frames": ["frame_2"]},
            "abstain_reason": None,
            "contradictions": [],
            "short_visible_summary": "Person seated with paper visible.",
        }
        self.conn.execute(
            """
            INSERT INTO qwen_label_results
                (id, packet_id, status, prompt_version, schema_version, model,
                 model_base_url, request_json, labels_json, validation_errors_json,
                 created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "ql-eval-accept",
                packet_id,
                "valid",
                "home_observation_labeler_v1",
                "home_observation_labels_v1",
                "qwen3-vl-30b",
                "http://qwen.test",
                "{}",
                canonical_json(labels),
                "[]",
                "2026-05-26T19:02:50+00:00",
            ),
        )
        self.conn.commit()

        seeded = client.post("/api/intelligence/label-evals/seed", json={"target_size": 5})
        self.assertEqual(seeded.status_code, 200)
        queue = client.get("/api/intelligence/label-evals/queue")
        self.assertEqual(queue.status_code, 200)
        self.assertEqual(len(queue.json()["items"]), 1)
        item_id = queue.json()["items"][0]["id"]

        accepted = client.post(
            f"/api/intelligence/label-evals/items/{item_id}/accept-qwen",
            json={"actor": "test", "note": "fast audit"},
        )
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json()["item"]["human_labels"]["primary_activity"]["label"], "reading")
        self.assertEqual(accepted.json()["score"]["overall_accuracy"], 1.0)

        queue_after = client.get("/api/intelligence/label-evals/queue")
        self.assertEqual(queue_after.status_code, 200)
        self.assertEqual(queue_after.json()["items"], [])

    def test_training_clip_manifest_is_review_only_and_privacy_gated(self) -> None:
        self.ingest_fixture("office_pending_preference.json")
        self.conn.commit()
        client = TestClient(app)
        observation = self.conn.execute(
            "SELECT id FROM preference_observations WHERE kind = 'pending_preference'"
        ).fetchone()
        capture = client.post(
            "/api/intelligence/observation-packets/capture",
            json={
                "observation_id": observation["id"],
                "capture_frames": False,
                "trigger": "contract_test",
            },
        )
        packet_id = capture.json()["packet"]["id"]
        self.conn.execute(
            "UPDATE observation_packets SET frame_count = 3, status = 'labeled', qwen_status = 'valid' WHERE id = ?",
            (packet_id,),
        )
        labels = {
            "schema_version": "home_observation_labels_v1",
            "primary_activity": {"label": "watching_tv", "confidence": 0.8, "evidence_frames": ["frame_2"]},
            "privacy_sensitivity": {"label": "screen_content_visible", "confidence": 0.8, "evidence_frames": ["frame_2"]},
        }
        self.conn.execute(
            """
            INSERT INTO qwen_label_results
                (id, packet_id, status, prompt_version, schema_version, model,
                 model_base_url, request_json, labels_json, validation_errors_json,
                 created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "ql-clip-test",
                packet_id,
                "valid",
                "home_observation_labeler_v1",
                "home_observation_labels_v1",
                "qwen3-vl-30b",
                "http://qwen.test",
                "{}",
                canonical_json(labels),
                "[]",
                "2026-05-26T19:02:50+00:00",
            ),
        )
        self.conn.commit()

        manifest = client.post(
            "/api/intelligence/training-clips/manifests",
            json={"packet_id": packet_id, "tier": "short_clip", "actor": "test"},
        )
        self.assertEqual(manifest.status_code, 200)
        body = manifest.json()["manifest"]
        self.assertEqual(body["packet_id"], packet_id)
        self.assertEqual(body["frame_count_tier"], "short_clip")
        self.assertEqual(body["status"], "draft_review_required")
        self.assertFalse(body["quality"]["training_allowed"])
        self.assertFalse(body["quality"]["export_allowed"])
        self.assertIn("screen_content_visible", body["privacy_flags"])
        self.assertIn("privacy review required", body["quality"]["blockers"])

        listed = client.get("/api/intelligence/training-clips/manifests")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()["manifests"]), 1)


if __name__ == "__main__":
    unittest.main()
