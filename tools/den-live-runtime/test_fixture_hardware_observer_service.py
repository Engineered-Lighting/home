"""Live observer TLS read path over the actual controlled Home authority."""
import http.client
from copy import deepcopy
import base64
import io
import importlib.util
import json
from decimal import Decimal
from pathlib import Path
import ssl
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location("controlled_fixture_observer_service",ROOT/"tools/fixtures/home-fixture-hardware-observer-service.py")
service_module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(service_module)


class HardwareObserverServiceTests(unittest.TestCase):
    def test_duplicate_command_keys_and_unsupported_fields_refuse(self):
        with self.assertRaisesRegex(ValueError,"duplicate_command_key"):
            service_module.parse_command('{"op":"tick","op":"execution"}')
        with self.assertRaises(ValueError):
            service_module.parse_command('{"op":"tick","seconds":NaN}')
        with self.assertRaisesRegex(ValueError,"byte_bound"):
            service_module.parse_command(" "*8193)

    def test_live_tls_observer_reads_real_commit_and_expiry_with_separate_read_authority(self):
        directory=Path(tempfile.mkdtemp(prefix="hardware-observer-service-test-",dir=ROOT/"artifacts"))
        service=service_module.LiveHardwareObserverService(directory)
        self.addCleanup(service.close)
        ready=service.ready
        port=service.hardware.join.edge_server.server_port
        token=Path(ready["observer"]["token_file"]).read_text()
        digest=ready["observer"]["binding_sha256"]
        scope=ready["observer"]["binding"]["home_scope"]
        ca=ssl.create_default_context(cafile=ready["ca_file"])
        route="/api/home-context/v1/fixture-execution?site_id="+scope["site_id"]+"&room_id="+scope["room_id"]
        def read(*,credential=token,path=route,binding=digest):
            connection=http.client.HTTPSConnection("127.0.0.1",port,context=ca,timeout=3)
            try:
                connection.request("GET",path,headers={"Authorization":"Bearer "+credential,
                    "X-Home-Fixture-Binding":binding,"X-Home-Fixture-Actor":"controlled-observer-owner"})
                response=connection.getresponse()
                return response.status,json.loads(response.read())
            finally:connection.close()
        self.assertEqual(read()[0],503,"no fabricated initial report")
        self.assertEqual(read(credential="Lab-token")[0],401)
        self.assertEqual(read(binding="00"*32)[0],409)
        self.assertEqual(read(path=route.replace("site_id=victoria","site_id=other-home"))[0],403)
        service.command({"op":"bootstrap"})
        service.command({"op":"advance","count":17})
        admitted=service.command({"op":"execution","kind":"MOVE","request_id":"observer-first-move"})
        self.assertEqual(admitted["execution"]["dispatch"]["outcome"]["device_disposition"],"ADMITTED")
        first=admitted["execution"]["native_snapshot"]
        self.assertEqual(first["phase"],"lit")
        self.assertEqual(first["applied"][21:],[1638,0,0])
        original_profile=json.loads((directory/"hardware-original-profile-assessment.json").read_text())
        self.assertEqual(original_profile["status"],"feasible_on_this_new_authored_cut")
        self.assertFalse(original_profile["operational_profile_changed"])
        self.assertEqual(ready["transport_mode"],"9b-native-runtime-endpoint-shared-core-backend")
        status,response=read()
        self.assertEqual(status,200,response)
        self.assertEqual(response["binding"],ready["observer"]["binding"])
        inspection=response["inspection"]
        self.assertIsNotNone(inspection["lease"])
        self.assertEqual(service.hardware.harness.now-int(inspection["commit"]["committed_home_us"]),10000)
        self.assertEqual(int(inspection["report"]["first_received_home_us"]),int(inspection["commit"]["committed_home_us"]))
        self.assertEqual(inspection["commit"]["report_id"],inspection["report"]["report_id"])
        row=service.hardware.join.graph.journal.db.execute("SELECT body FROM fixture_observer_reports WHERE cursor=?",(inspection["commit"]["cursor"],)).fetchone()
        self.assertEqual(json.loads(row[0])["commit"],inspection["commit"])
        before=len(service.hardware.harness.packets)
        duplicate=service.command({"op":"execution","kind":"MOVE","request_id":"observer-first-move"})
        self.assertEqual(duplicate,admitted)
        self.assertEqual(len(service.hardware.harness.packets),before)
        service.command({"op":"brightness","brightness_fraction":.25,"intent_id":"observer-brightness"})
        for _ in range(2):
            service.command({"op":"tick","seconds":.5})
            if service.hardware.join.decision()["scene"]["nominal"]["brightness_fraction"]==.25:break
        corrected=service.command({"op":"execution","kind":"LEVELS","request_id":"observer-levels"})
        second=corrected["execution"]["native_snapshot"]
        self.assertEqual(second["applied"][21:],[1024,0,0])
        for key in ("motion_id","motion_started_us","motion_deadline_us","support_expiry_home_us"):
            self.assertEqual(first[key],second[key],key)
        continued=service.command({"op":"continue","request_id":"observer-continue"})["execution"]["native_snapshot"]
        for key in ("motion_id","motion_started_us","motion_deadline_us","support_expiry_home_us","lighting_generation"):
            self.assertEqual(second[key],continued[key],key)
        self.assertEqual(len([row for row in service.hardware.native.last_response["can_writes"] if row["data"].startswith("a4")]),2)
        status,current=read()
        self.assertEqual(status,200,current)
        self.assertEqual(current["inspection"]["commit"]["cursor"],3)
        self.assertIsNotNone(current["inspection"]["lease"])
        inspection=current["inspection"]
        # NEW authored exposures continue on the current fake capture clock
        # after native/control-only time. They never redate retained originals
        # or renew the old hardware grants/caps/reports.
        old_plans=deepcopy(service.hardware.plans)
        old_report=deepcopy(service.hardware.reports[0])
        capture=service.hardware.join.graph.capture
        old_rgb_key=next(key for key,value in capture.originals.items() if value.get("component_id")=="rgb")
        old_rgb=deepcopy(capture.originals[old_rgb_key])
        for _ in range(4):
            row=service.command({"op":"advance"})
            self.assertEqual(row["cycle"]["status"],"observed")
        owner=service.hardware.join.graph.owner
        packet=owner.transport("fictional",timeout=1)
        header=json.loads(base64.urlsafe_b64decode(json.loads(packet)["header"]))
        self.assertAlmostEqual(float(Decimal(header["observed_at_unix_ns"])/1_000_000_000),service.hardware.join.io.now-.01-.198,places=5)
        self.assertEqual(header["frame_device_ns"],50_200_000_000+header["observed_at_unix_ns"]-1_800_000_000_000_000_000)
        import numpy as np
        with np.load(io.BytesIO(base64.b64decode(json.loads(packet)["bundle_b64"])),allow_pickle=False) as bundle:
            metadata=json.loads(bundle["metadata_json"].tobytes())
        self.assertAlmostEqual(metadata["frames"]["rgb"]["device_timestamp_seconds"]-metadata["frames"]["depth"]["device_timestamp_seconds"],.01,places=8)
        service.command({"op":"tick","seconds":0})
        self.assertEqual(owner.transport("fictional",timeout=1),packet,"same sequence is exact original bytes after time advances")
        self.assertEqual(service.hardware.plans,old_plans)
        self.assertEqual(service.hardware.reports[0],old_report)
        self.assertEqual(capture.originals[old_rgb_key],old_rgb)
        expired_tick=service.command({"op":"tick","seconds":2})
        withdrawal=expired_tick["hardware_withdrawal"]
        self.assertEqual(withdrawal["dispatch"]["state"],"terminal")
        self.assertEqual(withdrawal["dispatch"]["outcome"]["device_disposition"],"ADMITTED")
        self.assertEqual(withdrawal["native_snapshot"]["requested"][21:],[0,0,0])
        self.assertFalse(withdrawal["native_snapshot"]["stop_confirmed"])
        self.assertIsNone(withdrawal["report_publication"]["lease"])
        status,expired=read()
        self.assertEqual(status,200,expired)
        self.assertEqual(expired["inspection"]["commit"]["cursor"],4)
        self.assertEqual(json.loads(bytes.fromhex(expired["inspection"]["report"]["original_hex"]))["event"],"withdrawn")
        self.assertIsNone(expired["inspection"]["lease"])
        self.assertFalse(expired["inspection"]["physical_permission"])
        final=service.close()
        self.assertEqual(final["worker"]["withdrawal"],"recorded")
        self.assertEqual(final["worker"]["worker"]["status"],"settled")
        self.assertEqual(final["worker"]["recorder"]["status"],"drained")
        self.assertEqual(final["worker"]["replay"]["status"],"reproduced")
        self.assertEqual(final["native_ordinary_replay"]["status"],"reproduced")
        self.assertGreater(final["native_ordinary_replay"]["requests"],100)
        self.assertEqual(final["runtime_replay"]["attempts"],4)
        self.assertEqual(final["authority_replay"]["status"],"causal_archive_verified")
        self.assertEqual(final["authority_replay"]["reports"],4)

    def test_new_missing_protection_cut_withdraws_before_original_home_expiry(self):
        directory=Path(tempfile.mkdtemp(prefix="hardware-observer-protection-test-",dir=ROOT/"artifacts"))
        service=service_module.LiveHardwareObserverService(directory)
        self.addCleanup(service.close)
        service.command({"op":"bootstrap"})
        service.command({"op":"advance","count":17})
        admitted=service.command({"op":"execution","kind":"MOVE","request_id":"protection-move"})
        original_expiry=int(admitted["execution"]["native_snapshot"]["support_expiry_home_us"])
        result=service.command({"op":"advance","mode":"missing_protection"})
        self.assertLess(service.hardware.harness.now,original_expiry,"fresh-cut refusal, not Home lease timeout")
        withdrawal=result["hardware_withdrawal"]
        self.assertEqual(withdrawal["dispatch"]["outcome"]["device_disposition"],"ADMITTED")
        self.assertEqual(withdrawal["dispatch"]["outcome"]["domain_mask"],128)
        self.assertEqual(withdrawal["native_snapshot"]["requested"][21:],[0,0,0])
        self.assertIsNone(withdrawal["report_publication"]["lease"])
        native=json.loads(bytes.fromhex(withdrawal["report_publication"]["report"]["original_hex"]))
        self.assertEqual((native["event"],native["result"]),("withdrawn","revoked"))
        self.assertEqual(withdrawal["report_publication"]["report"]["category"],"unknown",
            "negative revoked event is status only, never pose/LED confirmation")
        before=len(service.hardware.harness.packets)
        again=service.command({"op":"tick","seconds":0})
        self.assertEqual(again["hardware_withdrawal"],withdrawal)
        self.assertEqual(len(service.hardware.harness.packets),before,"no blind withdrawal retry")
        with self.assertRaisesRegex(ValueError,"fresh_start_qualification"):
            service.command({"op":"execution","kind":"CONTINUE","request_id":"unsafe-resume"})
        control=service.hardware.join.graph.capture.control_clock()
        self.assertEqual(float(Decimal(control["clock"]["original_value"])/1_000_000_000),control["observed_at"])
        final=service.close()
        self.assertEqual(final["worker"]["withdrawal"],"recorded")
        self.assertEqual(final["worker"]["worker"]["status"],"settled")
        self.assertEqual(final["worker"]["recorder"]["status"],"drained")
        self.assertEqual(final["runtime"]["status"],"drained")
        self.assertEqual(final["native_ordinary_replay"]["status"],"reproduced")
        self.assertEqual(final["authority_replay"]["status"],"causal_archive_verified")


if __name__=="__main__":unittest.main()
