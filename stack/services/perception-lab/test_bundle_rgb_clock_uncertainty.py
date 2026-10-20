"""Actual installed causal header -> shared parser -> anchor; no devices/I/O.

Regression for the duplicate same-host uncertainty addition. Authored
clock/frame points invoke the actual immutable installed camera implementation.
"""
import base64
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
import threading
import time
import types
import unittest

HERE=Path(__file__).resolve().parent
ROOT=next(p for p in HERE.parents if (p/'tools/victoria-camera/capture.py').is_file() and (p/'stack/services/perception-lab').is_dir())
for name in ('tools/shared-home','stack/services/perception-lab','stack/services/home-context'):
    if str(ROOT/name) not in sys.path:sys.path.append(str(ROOT/name))
SHA=lambda raw:hashlib.sha256(raw).hexdigest()
ENC=lambda v:json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
CAMERA=ROOT/'tools/victoria-camera/capture.py'
CAMERA_SHA='d052fc217b2d412cb877cfb49608e7dcef4a52526932b8d036a51380accb6132'
BOOT='00000000-0000-0000-0000-000000000001'
NOW=1800000000
TRACE=[]

def installed():
    raw=CAMERA.read_bytes()
    name='_offline_exact_installed_causal_header'
    module=types.ModuleType(name);module.__file__=str(CAMERA);sys.modules[name]=module
    exec(compile(raw,str(CAMERA),'exec'),module.__dict__)
    return module

C=installed()

def causal(u,*,offset=0.,lifetime=3):
    from live_capture_fixtures import jpeg
    clock=C.HostClockEvidence(1,'commissioned_camera_host_clock_v1',BOOT,'authored-same-host',
        9_990_000_000,NOW*10**9-10_000_000,9_990_000_000+int(lifetime*10**9),u)
    def point(seconds):
        ns=10_000_000_000+int(seconds*10**9)
        return C.HostPoint(ns,ns,NOW*10**9+int(seconds*10**9),ns)
    nv12=bytes(640*360*3//2);sent=point(offset);received=point(offset+.02)
    frame_device=50_200_000_000+int(offset*10**9)
    preview=C.Preview('authored-clock-epoch',10+int(offset*100),frame_device/1e9,
        received.after_ns/1e9,nv12,.01,0)
    frame=C.CausalFrame(preview,'0'*32,50_000_000_000+int(offset*10**9),
        frame_device,sent,received,1000,SHA(nv12),clock)
    metadata=C.causal_timing(frame,point(offset+.03),BOOT)
    data=jpeg();metadata['jpeg_sha256']=SHA(data)
    header=base64.urlsafe_b64encode(ENC(metadata)).decode()
    return data,header,metadata,clock

class BundledClockUncertaintyTests(unittest.TestCase):
    def test_01_full_point55_clock_error_is_counted_once_in_anchor(self):
        from assessment_existing_api import _receipt
        from bundle_rgb_input import anchor_metadata
        data,header,original,_=causal(.55)
        receipt=_receipt(data,header)
        self.assertEqual(receipt,original)
        self.assertAlmostEqual(receipt['uncertainty_seconds'],.570001)
        TRACE.append({'case':'same_bound_point55','clock_u':.55,'full_validated_header_u':receipt['uncertainty_seconds'],
            'current_formula_u':receipt['uncertainty_seconds']+receipt['host_clock_uncertainty_seconds'],
            'correct_oracle':'complete validated header uncertainty once; no bound subtraction'})
        metadata=anchor_metadata(receipt,site_id='victoria',camera_id='den',available_at=NOW+.03,started_at=NOW-.5)
        self.assertEqual(metadata['uncertainty_seconds'],receipt['uncertainty_seconds'])
        self.assertEqual(metadata['valid_until'],metadata['observed_at']+3-receipt['uncertainty_seconds'])

    def test_02_low_uncertainty_is_not_silently_charged_twice_either(self):
        from assessment_existing_api import _receipt
        from bundle_rgb_input import anchor_metadata
        data,header,_,_=causal(.2);receipt=_receipt(data,header)
        metadata=anchor_metadata(receipt,site_id='victoria',camera_id='den',available_at=NOW+.03,started_at=NOW-.5)
        TRACE.append({'case':'low_bound','full_validated_header_u':receipt['uncertainty_seconds'],
            'current_anchor_u':metadata['uncertainty_seconds']})
        self.assertEqual(metadata['uncertainty_seconds'],receipt['uncertainty_seconds'])

    def test_03_header_host_error_mutation_still_fails_exact_parser(self):
        from assessment_existing_api import _receipt
        data,_,metadata,_=causal(.55);metadata['host_clock_uncertainty_seconds']=.1
        header=base64.urlsafe_b64encode(ENC(metadata)).decode()
        with self.assertRaisesRegex(ValueError,'causal frame derived timing mismatch'):_receipt(data,header)

    def test_04_receiver_proof_is_same_original_identity_not_a_second_bound(self):
        from assessment_existing_api import _receipt
        from bundle_rgb_input import receipt_upper
        data,header,_,clock=causal(.55);receipt=_receipt(data,header)
        def p(offset):
            ns=10_000_000_000+int(offset*10**9)
            return {'before_ns':ns,'boot_ns':ns,'unix_ns':NOW*10**9+int(offset*10**9),'after_ns':ns+100}
        proof={'evidence':asdict(clock),'before':p(.025),'after':p(.035),'boot_id':BOOT,'validator_source_sha256':CAMERA_SHA}
        self.assertGreater(receipt_upper(proof,receipt),NOW*10**9)
        for key,value in (('host_clock_validation_id','foreign-clock'),('host_boot_id','00000000-0000-0000-0000-000000000002'),('host_clock_uncertainty_seconds',.2)):
            foreign=deepcopy(receipt);foreign[key]=value
            with self.subTest(field=key),self.assertRaisesRegex(ValueError,'bundled_receiver_proof_invalid'):
                receipt_upper(proof,foreign)

    def test_05_observed_three_second_error_fits_only_honest_close_anchors(self):
        from assessment_existing_api import _receipt
        from bundle_rgb_input import anchor_metadata,mapped_timing,receipt_upper
        from live_capture_fixtures import frame_metadata
        # Retained aggregate from the read-only sample, not a new host measurement.
        u=.7031523344500001
        for gap in (.5,1.580456):
            with self.subTest(gap=gap):
                a,ah,_,clock=causal(u);b,bh,_,_=causal(u,offset=gap)
                ra,rb=_receipt(a,ah),_receipt(b,bh)
                def point(offset):
                    ns=10_000_000_000+int(offset*10**9)
                    return {'before_ns':ns,'boot_ns':ns,'unix_ns':NOW*10**9+int(offset*10**9),'after_ns':ns+100}
                proof={'evidence':asdict(clock),'before':point(gap+.05),'after':point(gap+.09),
                    'boot_id':BOOT,'validator_source_sha256':CAMERA_SHA}
                receipt_upper(proof,rb)
                # Slowed common-offer p95 is a proxy, not historical exposure timing.
                trace={'case':'actual_observed_3s_aggregate_u','clock_u':u,'authored_anchor_gap_s':gap,
                    'gap_basis':'authored500ms' if gap==.5 else 'retained_common_offer_p95_proxy_not_retained_exposure',
                    'actual_validated_header_u':ra['uncertainty_seconds'],'source_data':'authored exact camera originals with retained aggregate uncertainty'}
                TRACE.append(trace)
                anchors=[{'metadata':anchor_metadata(r,site_id='victoria',camera_id='den',
                    available_at=NOW+gap+.09,started_at=NOW-.5)} for r in (ra,rb)]
                self.assertEqual(anchors[0]['metadata']['uncertainty_seconds'],ra['uncertainty_seconds'])
                meta=frame_metadata(sequence=13,rgb_device_s=50.45,depth_device_s=50.44,epoch='authored-clock-epoch')
                if gap==.5:
                    timing=mapped_timing(*anchors,meta,proof)
                    self.assertLessEqual(timing['uncertainty_seconds'],1)
                    trace.update(mapped_uncertainty_s=timing['uncertainty_seconds'],mapped_original_deadline=timing['valid_until'])
                else:
                    with self.assertRaisesRegex(ValueError,'bundled_rgb_clock_uncertainty_excessive'):
                        mapped_timing(*anchors,meta,proof)
                    trace['expected_refusal']='bundled_rgb_clock_uncertainty_excessive'

    def test_06_previous_producer_lease_is_not_reinterpreted_or_extended(self):
        import inspect
        from unittest.mock import patch
        import apartment_viewing_metric as metric
        import bundle_rgb_input as bundled
        import live_capture_fixtures as capture
        from bundle_ingest import calibration_content_id
        # Re-execute the actual previous producer function, whose only changed
        # expression was fullU + sameHostU. No lease/metadata is rewritten.
        source=inspect.getsource(bundled.anchor_metadata)
        current="    total = receipt['uncertainty_seconds']\n"
        previous="    total = receipt['uncertainty_seconds']+receipt['host_clock_uncertainty_seconds']\n"
        if current in source:
            source=source.replace(current,previous)
        namespace=dict(bundled.__dict__)
        exec(compile(source,'<previous-anchor-producer>','exec'),namespace)
        data,ah,_,clock=causal(.2);_,bh,_,_=causal(.2,offset=.5)
        meta=capture.frame_metadata(sequence=13,rgb_device_s=50.45,depth_device_s=50.44,epoch='authored-clock-epoch')
        calibration=capture.owner_calibration();calibration['session']='authored-clock-epoch'
        originals={'npz':capture.bundle(meta),'calibration':ENC(calibration),'anchor_a':data,'anchor_b':data}
        headers={'a':ah,'b':bh}
        def point(offset):
            ns=10_000_000_000+int(offset*10**9)
            return {'before_ns':ns,'boot_ns':ns,'unix_ns':NOW*10**9+int(offset*10**9),'after_ns':ns+100}
        proof={'evidence':asdict(clock),'before':point(.57),'after':point(.59),
            'boot_id':BOOT,'validator_source_sha256':CAMERA_SHA}
        binding={'session':{'id':'authored-previous-lease','site_id':'victoria','camera_id':'den'},
            'arm':'b','model_generation':1,'media_generation':0,'sampling_hz':1,
            'lab_profile':{'profile_id':'authored','engine_epoch':'authored-engine',
                'model_version':'authored-model','artifact_sha256':'a'*64}}
        pins={'originals':{k:{'size':len(v),'sha256':SHA(v)} for k,v in originals.items()},
            'anchor_headers':{k:SHA(v.encode()) for k,v in headers.items()},'receiver_proof':SHA(ENC(proof)),
            'capture_epoch':'authored-clock-epoch','timing_generation':0,'calibration_content_id':calibration_content_id(calibration)}
        args=dict(originals=originals,anchor_headers=headers,receiver_proof=proof,binding=binding,pins=pins,
            available_at=NOW+.59,started_at=NOW-.5,now_s=NOW+.59)
        with patch.object(metric,'anchor_metadata',namespace['anchor_metadata']):
            old=metric.prepare_metric_frame(**args)
        before=(old._descriptor_json,old._metadata_json,old._proof_json,old.image_bytes)
        now=old.descriptor['retirement_deadline']+.05
        # Corrected recomputation remains fresh, but its identity differs from
        # the previous immutable descriptor. The real lift must refuse that.
        checked=metric._checked(originals,headers,proof,binding,pins,args['available_at'],args['started_at'],now)
        self.assertGreater(checked[4]['valid_until'],now)
        with self.assertRaisesRegex(ValueError,'metric_prepared_original_changed'):
            metric.lift_supported_joints(old,event={},image_record={},now_s=now)
        self.assertEqual(before,(old._descriptor_json,old._metadata_json,old._proof_json,old.image_bytes))


if __name__=='__main__':unittest.main()
