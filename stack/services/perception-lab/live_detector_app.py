"""Provisioned Victoria paired-detector trial; browsing never loads a model.

Workers run in separate, explicitly bounded services. A private operator lease
records their verified readiness; dispatch still requires each worker's admission.
"""
from copy import deepcopy
import http.client
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import ssl
import time
from urllib.parse import urlsplit

from jetson_detector_app import CHECKPOINT, PREPROCESSING
from reasoning import digest, finite

# Imported before the refresh publisher loads its protected configuration, so a
# caption generated after this time was published under that loaded revision.
PROCESS_STARTED_AT = time.time()
_la_live_log = logging.getLogger("perception_lab.la_live")


def private_json(path):
    path=Path(path)
    if path.is_symlink() or path.stat().st_uid!=os.geteuid() or path.stat().st_mode&0o077:
        raise ValueError('Private operator file required')
    with path.open('rb') as source:raw=source.read(65537)
    if len(raw)>65536:raise ValueError('Operator file exceeds bound')
    return json.loads(raw)


OPTIONAL_KEYS=frozenset({'pose_profiles','ha_source_file','caption_history','spatial_file','qwen_profile','qwen_transport',
                         'reasoning_min_offer_interval_s','reasoning_cadence','qwen_live','vjepa_live','room_facts',
                         'occupancy_reader','la_live_perceiver','apartment_viewing'})
# Always-on LA live perceiver ('la_live_perceiver': {enabled, transport_file,
# artifact_sha256, runtime_revision, fact_log_directory, plus optional pump,
# fact-log and re-probe settings}; la_live_perceiver.py, speed plan S1.3): a
# managed-controller component that sends den frames to LA Qwen whenever the
# occupancy_reader gate is open and the household policy lists
# la-live-inference-1, outside recordings too, and writes la-fact-log-1 words.
# It needs the occupancy_reader block. Absent or 'enabled': false: nothing is
# built. It refuses to start while the qwen_live block is enabled, and a
# recording gets no qwen_live pump while it runs (one live sender per LA).
# Victoria-LAN occupancy reader ('occupancy_reader': {origin, credential_file,
# entity?, api?, ca_file?, expected_hostname?, hold_seconds?, grace_seconds?,
# interval_seconds?}; occupancy_reader.py): one 2 Hz poll of the bound
# camera-derived occupancy entity, kept in memory, that the live pumps' gate
# prefers over the recording's ha.observation rows and that wakes them at an
# occupancy edge. Absent: the ledger gate, as before. With it, its entity,
# hold and grace apply to every live pump: an enabled qwen_live or vjepa_live
# block naming a different occupancy_entity, occupancy_hold_seconds or
# occupancy_grace_seconds is refused. A reader whose entity is not the HA
# source's bound camera-derived occupancy entity is passed over (one WARNING
# line per recording) for the ledger gate, so it never ungates anything.
# Room facts ('room_facts': {'enabled', 'site_map_file'}): the saved den site
# map (room_mapper save, private) that turns each arm's detections plus a 2 Hz
# depth sample into room.facts words (room_facts.py) for the den-facts-8 wire.
# Absent or 'enabled': false: nothing is sampled and nothing changes.
# Continuous Qwen perceiver ('qwen_live'). Absent means not provisioned: no
# pump, qwen_scene coverage not_provisioned, everything else unchanged.
# artifact_sha256/runtime_revision pin the LA model the pump's exact profile
# names (the engine epoch is probed from the LA); the pump settings default to
# qwen_live.DEFAULTS. The pump sends only while the camera-derived Frigate
# person-occupancy entity of the HA source says occupied (owner requirement);
# 'occupancy_entity' names it explicitly (else the HA source's one bound
# camera-derived binary sensor), and the hold/grace settings default to
# occupancy_gate.DEFAULTS. No HA source or entity: the pump refuses to run.
QWEN_LIVE_REQUIRED=frozenset({'transport_file','artifact_sha256','runtime_revision','enabled'})
QWEN_LIVE_PUMP=frozenset({'interval_seconds','frames','span_seconds','max_per_minute','horizon_seconds','timezone'})
QWEN_LIVE_OCCUPANCY=frozenset({'occupancy_entity','occupancy_hold_seconds','occupancy_grace_seconds'})
QWEN_LIVE_OPTIONAL=QWEN_LIVE_PUMP|QWEN_LIVE_OCCUPANCY|{'protocol','arm'}


def check_qwen_live(value):
    """Exact 'qwen_live' block; returns the validated pump settings."""
    if not isinstance(value,dict) or set(value)-QWEN_LIVE_OPTIONAL!=QWEN_LIVE_REQUIRED:
        raise ValueError('Exact Qwen live perceiver provisioning required')
    if type(value['enabled']) is not bool:
        raise ValueError('Explicit Qwen live perceiver switch required')
    protocol=value.get('protocol','lab-live-scene-1')
    if protocol not in ('lab-live-scene-1','lab-live-person-scene-1'):
        raise ValueError('Fixed Qwen live protocol required')
    if ('arm' in value and protocol!='lab-live-person-scene-1') or value.get('arm','b') not in ('a','b'):
        raise ValueError('Person scene requires one fixed detector arm')
    if not isinstance(value['transport_file'],str) or not PurePosixPath(value['transport_file']).is_absolute():
        raise ValueError('Absolute Qwen live transport path required')
    if not isinstance(value['artifact_sha256'],str) or not re.fullmatch(r'[a-f0-9]{64}',value['artifact_sha256']):
        raise ValueError('Pinned Qwen artifact digest required')
    if not isinstance(value['runtime_revision'],str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,191}',value['runtime_revision']):
        raise ValueError('Pinned Qwen runtime revision required')
    from ambient_context import ZONE
    if 'timezone' in value and (not isinstance(value['timezone'],str) or not ZONE.fullmatch(value['timezone'])):
        raise ValueError('IANA time zone required')
    from occupancy_gate import gate_config
    gate_config({k:value[k] for k in QWEN_LIVE_OCCUPANCY if k in value})
    from qwen_live import pump_config
    return pump_config({k:value[k] for k in QWEN_LIVE_PUMP if k in value})


def qwen_live_factory(config,*,reasoning_factory=None,transport_factory=None):
    """Per-recording perceiver factory from the 'qwen_live' block, or None.

    Reads the private transport file now (no connection); each recording gets
    its own transport, closed when its pump stops. The pump sends only while
    the household policy lists lab-live-scene-1 (a per-tick kill switch the
    owner can flip without a restart) and the session's reasoning is enabled;
    'enabled': false leaves the perceiver unprovisioned."""
    block=config.get('qwen_live')
    if block is None or block.get('enabled') is not True:
        return None
    settings=check_qwen_live(block)
    import qwen_scene as scene
    from qwen_live import MAX_DEADLINE_SECONDS, LiveSceneTransport, QwenLivePump
    person_mode=block.get('protocol')=='lab-live-person-scene-1'
    if person_mode:
        import qwen_person_scene as scene
        from qwen_person_live import PersonSceneTransport as LiveSceneTransport, QwenPersonPump as QwenLivePump
        # The existing RAM owner retires originals at their own expiry. Keep
        # the first subject context inside it, without retaining extra frames.
        if 'frames' not in block:settings['frames']=2
        if 'span_seconds' not in block:settings['span_seconds']=1.
    from qwen_transport import load_config
    transport_config=load_config(private_json(block['transport_file']),check_files=transport_factory is None)
    if not 5<=transport_config['deadline_seconds']<=MAX_DEADLINE_SECONDS:
        raise ValueError('Qwen live perceiver deadline must be 5-8 seconds')
    profile={'model_version':scene.MODEL,'served_model':scene.SERVED_MODEL,'artifact_sha256':block['artifact_sha256'],
             'engine_epoch':'unprobed','runtime_revision':block['runtime_revision'],'adapter_revision':scene.PROMPT_VERSION}

    def create(store,session,*,active,enabled,occupied):
        if person_mode and (session.get('site_id')!='victoria' or session.get('camera_id')!='den'):
            raise ValueError('Person scene requires the fixed Victoria Den scope')
        transport=(transport_factory or LiveSceneTransport)(transport_config)
        return QwenLivePump(store,session,transport=transport,profile=dict(profile),config=settings,active=active,enabled=enabled,
                            occupied=occupied,**({'arm':block.get('arm','b')} if person_mode else {}))

    def kill_switch():
        check=getattr(reasoning_factory,'qwen_person_granted' if person_mode else 'qwen_granted',None)
        return callable(check) and check() is True
    create.kill_switch=kill_switch
    create.horizon_seconds=settings['horizon_seconds']
    from occupancy_gate import explicit_settings, gate_config
    create.occupancy=gate_config({k:block[k] for k in QWEN_LIVE_OCCUPANCY if k in block})
    # The settings the block names itself: with an occupancy reader they must
    # equal the reader's (validate_config; service_runtime checks again).
    create.occupancy_explicit=explicit_settings(block)
    return create


# V-JEPA 2.1 den evidence ('vjepa_live'). Absent (or 'enabled': false) means
# not provisioned: no pump, temporal_encoder coverage not_provisioned. The block
# pins the model identity (artifact_sha256 must be vjepa_scene.ARTIFACT_SHA256)
# and the staged LA worker tree (runtime_revision, the digest vjepa_launcher
# reports); the private transport file names the LA supervisor origin, its two
# credentials and the trust anchor. Pump settings default to vjepa_live.DEFAULTS
# and the occupancy gate settings to occupancy_gate.DEFAULTS. The LA worker is
# leased per recording (vjepa_lease) only after the paired detector is ready,
# only for a recipe granting temporal_encoder, and only while the household
# policy lists vjepa-den-1 (per-tick kill switch) and the room is occupied.
VJEPA_LIVE_REQUIRED=frozenset({'transport_file','artifact_sha256','runtime_revision','enabled'})
VJEPA_LIVE_PUMP=frozenset({'interval_seconds','max_per_minute','min_box_frames','min_confidence','ntu_confidence',
                           'prior_max_gap_seconds','box_match_seconds'})
VJEPA_LIVE_OCCUPANCY=QWEN_LIVE_OCCUPANCY
VJEPA_LIVE_OPTIONAL=VJEPA_LIVE_PUMP|VJEPA_LIVE_OCCUPANCY
VJEPA_TRANSPORT=frozenset({'schema_version','origin','site_id','ca_file','lifecycle_token_file','execution_token_file'})
# Optional: 'keep_alive' false restores one clip connection per request (default true).
VJEPA_TRANSPORT_OPTIONAL=frozenset({'keep_alive'})
VJEPA_ORIGIN='https://192.168.0.100:9447'


def check_vjepa_live(value):
    """Exact 'vjepa_live' block; returns the validated pump settings."""
    if not isinstance(value,dict) or set(value)-VJEPA_LIVE_OPTIONAL!=VJEPA_LIVE_REQUIRED:
        raise ValueError('Exact V-JEPA live provisioning required')
    if type(value['enabled']) is not bool:
        raise ValueError('Explicit V-JEPA live switch required')
    if not isinstance(value['transport_file'],str) or not PurePosixPath(value['transport_file']).is_absolute():
        raise ValueError('Absolute V-JEPA transport path required')
    import vjepa_scene
    if value['artifact_sha256']!=vjepa_scene.ARTIFACT_SHA256:
        raise ValueError('Pinned V-JEPA artifact digest required')
    if not isinstance(value['runtime_revision'],str) or not re.fullmatch(r'[a-f0-9]{64}',value['runtime_revision']):
        raise ValueError('Pinned V-JEPA worker runtime revision required')
    from occupancy_gate import gate_config
    gate_config({k:value[k] for k in VJEPA_LIVE_OCCUPANCY if k in value})
    from vjepa_live import pump_config
    return pump_config({k:value[k] for k in VJEPA_LIVE_PUMP if k in value})


def check_vjepa_transport(value):
    """Exact private V-JEPA transport file: the fixed LA supervisor origin,
    its lifecycle and execution credentials and the CA, as absolute paths."""
    if not isinstance(value,dict) or set(value)-VJEPA_TRANSPORT_OPTIONAL!=VJEPA_TRANSPORT or value['schema_version']!=1:
        raise ValueError('Exact V-JEPA transport provisioning required')
    if 'keep_alive' in value and type(value['keep_alive']) is not bool:
        raise ValueError('V-JEPA keep_alive must be true or false')
    if value['origin']!=VJEPA_ORIGIN or value['site_id']!='echo':
        raise ValueError('Provisioned LA V-JEPA origin required')
    paths=[value[k] for k in ('ca_file','lifecycle_token_file','execution_token_file')]
    if any(not isinstance(p,str) or not PurePosixPath(p).is_absolute() for p in paths) or len(set(paths[1:]))!=2:
        raise ValueError('Absolute distinct V-JEPA credential/trust paths required')
    return dict(value)


def private_token(path):
    """A private (0600, owner) bearer credential of 32-512 characters."""
    file=Path(path)
    if file.is_symlink() or file.stat().st_uid!=os.geteuid() or file.stat().st_mode&0o077:
        raise ValueError('Private worker credential required')
    with file.open('r') as stream:token=stream.read(514).strip()
    if not 32<=len(token)<=512:raise ValueError('Invalid worker credential')
    return token


def vjepa_live_factory(config,*,reasoning_factory=None,client_factory=None,transport_factory=None,check_files=True):
    """Per-recording V-JEPA pump factory from the 'vjepa_live' block, or None.

    Reads and checks the private transport file now (no connection); each
    recording gets its own lifecycle client, clip transport and lease, all
    closed or released when its pump stops."""
    block=config.get('vjepa_live')
    if block is None or block.get('enabled') is not True:
        return None
    settings=check_vjepa_live(block)
    transport=check_vjepa_transport(private_json(block['transport_file']))
    if check_files:
        for key in ('lifecycle_token_file','execution_token_file'):
            private_token(transport[key])
        if not Path(transport['ca_file']).is_file():
            raise ValueError('V-JEPA trust anchor required')
    from vjepa_lease import LeasedVjepaPump, VjepaLease
    from vjepa_live import VjepaTransport
    from worker_lifecycle_client import LifecycleClient
    revision=block['runtime_revision']

    def create(store,session,*,active,enabled,occupied):
        client=(client_factory or LifecycleClient)(transport['origin'],lambda:private_token(transport['lifecycle_token_file']),
                                                    transport['ca_file'],transport['site_id'])
        clip=(transport_factory or VjepaTransport)(transport['origin'],lambda:private_token(transport['execution_token_file']),
                                                  transport['ca_file'],
                                                  **({'keep_alive':transport['keep_alive']} if 'keep_alive' in transport else {}))
        lease=VjepaLease(store,session,client,runtime_revision=revision)
        return LeasedVjepaPump(store,session,lease=lease,transport=clip,runtime_revision=revision,config=settings,
                               active=active,enabled=enabled,occupied=occupied)

    def kill_switch():
        check=getattr(reasoning_factory,'vjepa_granted',None)
        return callable(check) and check() is True
    create.kill_switch=kill_switch
    create.runtime_revision=revision
    from occupancy_gate import explicit_settings, gate_config
    create.occupancy=gate_config({k:block[k] for k in VJEPA_LIVE_OCCUPANCY if k in block})
    create.occupancy_explicit=explicit_settings(block)
    return create


def check_qwen_provisioning(profile,transport):
    """Saved-analysis keys: provisioned together, each an object or an absolute
    private-file path. Objects get the adapter's exact LA profile check and the
    transport's exact schema here; saved_analysis re-reads both at startup and
    then also checks the trust and credential files."""
    for item in (profile,transport):
        if isinstance(item,str):
            if not PurePosixPath(item).is_absolute():raise ValueError('Absolute Qwen provisioning paths required')
        elif not isinstance(item,dict):
            raise ValueError('Qwen provisioning object or path required')
    if isinstance(profile,dict):
        from qwen_adapter import QwenAdapter
        QwenAdapter(profile,None,None)
    if isinstance(transport,dict):
        from qwen_transport import load_config
        load_config(transport,check_files=False)


def validate_config(value):
    paired=isinstance(value,dict) and type(value.get('schema_version')) is int and value['schema_version']==2
    assessment=isinstance(value,dict) and type(value.get('schema_version')) is int and value['schema_version']==3
    bundled=isinstance(value,dict) and type(value.get('schema_version')) is int and value['schema_version']==4
    required=({'schema_version','capture_config','camera_env','receiver_clock','lease_file','arms'}
              |({'paired_preview'} if paired else set())
              |({'existing_api_assessment'} if assessment else set())
              |({'bundled_rgb_input'} if bundled else set()))
    if set(value)-OPTIONAL_KEYS!=required or (not paired and not assessment and not bundled and value['schema_version']!=1):
        raise ValueError('Exact live detector provisioning required')
    if paired:
        from paired_preview_client import policy
        policy(value['paired_preview'])
    if assessment:
        from assessment_existing_api import policy
        policy(value['existing_api_assessment'])
    if bundled:
        from bundle_rgb_input import policy
        policy(value['bundled_rgb_input'])
    if 'apartment_viewing' in value:
        from apartment_viewing_source import policy
        policy(value['apartment_viewing'])
        if 'pose_profiles' not in value:raise ValueError('Apartment viewing requires the existing pose profiles')
    if 'caption_history' in value and value['caption_history'] is not True:
        raise ValueError('Explicit caption history opt-in required')
    if 'reasoning_min_offer_interval_s' in value:
        # Controller setting, independent of the recipe: 1-10 s, default 2 s.
        from service_runtime import offer_interval
        if value['reasoning_min_offer_interval_s'] is None:
            raise ValueError('Explicit reasoning offer interval required')
        offer_interval(value['reasoning_min_offer_interval_s'])
    if 'reasoning_cadence' in value:
        # Controller setting: the facts-key cadence (default on) or new_evidence.
        from service_runtime import cadence_settings
        if value['reasoning_cadence'] is None:
            raise ValueError('Explicit reasoning cadence required')
        cadence_settings(value['reasoning_cadence'])
    if 'spatial_file' in value and (not isinstance(value['spatial_file'],str) or not PurePosixPath(value['spatial_file']).is_absolute()):
        raise ValueError('Absolute spatial provisioning path required')
    if ('qwen_profile' in value)!=('qwen_transport' in value):
        raise ValueError('Qwen profile and admission transport must be provisioned together')
    if 'qwen_profile' in value:
        check_qwen_provisioning(value['qwen_profile'],value['qwen_transport'])
    if 'qwen_live' in value:
        check_qwen_live(value['qwen_live'])
    if 'vjepa_live' in value:
        check_vjepa_live(value['vjepa_live'])
    if 'room_facts' in value:
        from room_facts import check_config
        check_config(value['room_facts'])
    if 'occupancy_reader' in value:
        from occupancy_reader import check_config as check_occupancy_reader
        reader=check_occupancy_reader(value['occupancy_reader'])
        # The reader's entity, hold and grace apply to every live pump: an
        # enabled pump block's own value that differs would be silently
        # replaced (a stricter hold or grace widened), so it is refused.
        from occupancy_gate import explicit_settings, reader_mismatch
        for name in ('qwen_live','vjepa_live'):
            block=value.get(name)
            mismatch=reader_mismatch(reader,explicit_settings(block)) if (block or {}).get('enabled') is True else None
            if mismatch:
                raise ValueError(f"{name} occupancy_{mismatch} differs from the occupancy_reader block's {mismatch}; "
                                 "with the reader, set it there (it applies to every live pump)")
    if 'la_live_perceiver' in value:
        from la_live_perceiver import check_config as check_la_live
        check_la_live(value['la_live_perceiver'])
        if value['la_live_perceiver']['enabled'] is True and 'occupancy_reader' not in value:
            raise ValueError('The LA live perceiver requires the occupancy_reader block')
    if set(value['arms'])!={'a','b'}:raise ValueError('Two arms required')
    for key in ('capture_config','camera_env','lease_file'):
        if not PurePosixPath(value[key]).is_absolute():raise ValueError('Absolute operator paths required')
    if 'ha_source_file' in value and not PurePosixPath(value['ha_source_file']).is_absolute():
        raise ValueError('Absolute HA provisioning path required')
    clock=value['receiver_clock']
    if set(clock)!={'validation_id','uncertainty_seconds'} or not clock['validation_id'] or not finite(clock['uncertainty_seconds']) or not 0<=clock['uncertainty_seconds']<=1:
        raise ValueError('Measured receiver clock required')
    devices=set()
    for arm,binding in value['arms'].items():
        if set(binding)!={'profile','origin','token_file','ca_file'}:raise ValueError('Exact worker binding required')
        profile=binding['profile']
        if (profile.get('checkpoint')!='yolo26s' or profile.get('artifact_sha256')!=CHECKPOINT
                or profile.get('sha256')!=CHECKPOINT or profile.get('precision')!='fp32'
                or profile.get('preprocessing')!=PREPROCESSING or profile.get('task')!='detect'
                or profile.get('runtime_version')!='8.4.0'
                or any(not isinstance(profile.get(k),str) or not profile[k] for k in
                       ('profile_id','model_version','engine_epoch','device_id','runtime_revision'))):
            raise ValueError('Pinned matching detector profiles required')
        origin=urlsplit(binding['origin'])
        if (origin.scheme!='https' or origin.hostname not in ({'192.168.1.168'} if arm=='a' else {'192.168.0.100'})
                or origin.username or origin.password or origin.query or origin.fragment or origin.path not in {'','/'}):
            raise ValueError('Provisioned device HTTPS origin required')
        for key in ('token_file','ca_file'):
            if not PurePosixPath(binding[key]).is_absolute():raise ValueError('Absolute credential/trust paths required')
        devices.add(profile['device_id'])
    if len(devices)!=2 or value['arms']['a']['profile']['device_id']!='victoria-den-jetson':
        raise ValueError('Distinct Jetson and LA placement required')
    if 'pose_profiles' in value:
        if set(value['pose_profiles'])!={'a','b'}:raise ValueError('Two pose profiles required')
        for p in value['pose_profiles'].values():
            if p.get('model_version')!='detector-pose-v1' or p.get('profile_id')!='yolo26s-rtmpose-cpu-v1':
                raise ValueError('Pinned pose composition required')
    return deepcopy(value)


class TrialLease:
    def __init__(self,path,profiles,*,read=private_json,clock=time.time):
        self.path,self.profiles,self.read,self.clock=path,deepcopy(profiles),read,clock

    def ready(self):
        try:
            value=self.read(self.path);now=self.clock()
            return (set(value)=={'schema_version','ready_at','expires_at','profiles_sha256','gate_references'}
                and value['schema_version']==1 and finite(value['ready_at']) and finite(value['expires_at'])
                and value['ready_at']<=now<value['expires_at'] and 0<value['expires_at']-value['ready_at']<=120
                and value['profiles_sha256']==digest(self.profiles)
                and set(value['gate_references'])=={'technical','health_resource','workload_authorization'}
                and all(isinstance(v,str) and 0<len(v)<=256 for v in value['gate_references'].values()))
        except Exception:return False


SPATIAL_FIELDS=frozenset({'schema_version','association_revision','calibration','registration','room_map'})
SPATIAL_OPTIONAL=frozenset({'depth_clock','max_age_s','max_skew_s'})


def _stereo_calibration(calibration):
    """The stereo association fields (depth_association.camera_model), checked once."""
    import numpy as np
    from depth_association import _array,_intrinsics,_rigid
    projection=calibration.get('reprojection')
    if (not isinstance(projection,dict) or not isinstance(projection.get('receipt'),str) or not projection['receipt']
            or not finite(projection.get('p95_px')) or not 0<=projection['p95_px']<=5):
        raise ValueError('Validated RGB-depth reprojection required')
    reference=calibration.get('reference_intrinsics')
    if not isinstance(reference,dict) or set(reference)!={'rectified_left','rgb'}:
        raise ValueError('Reference intrinsics required')
    for name in ('rectified_left','rgb'):_intrinsics(reference[name])
    extrinsics=calibration.get('rgb_from_rectified_left')
    if not isinstance(extrinsics,dict):raise ValueError('RGB-from-rectified-left extrinsics required')
    transform=np.eye(4)
    transform[:3,:3]=_array(extrinsics.get('rotation'),(3,3))
    transform[:3,3]=_array(extrinsics.get('translation_m'),(3,))
    _rigid(transform)
    intrinsics=calibration.get('intrinsics')
    if (not isinstance(intrinsics,dict) or not all(finite(intrinsics.get(k)) for k in ('fx','fy','cx','cy'))
            or intrinsics['fx']<=0 or intrinsics['fy']<=0 or calibration.get('frame')!='rectified_left'
            or (calibration.get('width'),calibration.get('height'))!=(640,400)
            or not finite(calibration.get('error_bound_m')) or calibration['error_bound_m']<0):
        raise ValueError('Validated rectified-left depth calibration required')


def spatial_provisioning(value,*,site_id,camera_id):
    """Validate the private spatial file; return (value, per-localization reasons).

    {schema_version:1, association_revision, calibration, registration, room_map,
    depth_clock?, max_age_s?, max_skew_s?}. The consumer's own checks bind the
    registration, map and calibration; depth_clock is DepthClock commissioning.
    A localization maps to None when provisioned, else to a controlled reason:
    stereo needs the commissioned depth clock and the validated stereo
    calibration, floor_projection the registration's validated floor homography.
    Freshness bounds may only tighten (the consumer refuses anything looser).
    """
    if not isinstance(value,dict) or set(value)-SPATIAL_OPTIONAL!=SPATIAL_FIELDS or value['schema_version']!=1:
        raise ValueError('Exact spatial provisioning required')
    calibration,registration,room_map=value['calibration'],value['registration'],value['room_map']
    if not all(isinstance(v,dict) for v in (calibration,registration,room_map)):
        raise ValueError('Exact spatial provisioning required')
    if any((item.get('site_id'),item.get('camera_id'))!=(site_id,camera_id) for item in (calibration,registration)):
        raise ValueError('Spatial provisioning belongs to another camera')
    from spatial_consumer import SpatialConsumer
    SpatialConsumer(None,calibration,registration,room_map,association_revision=value['association_revision'],
                    **{k:value[k] for k in ('max_age_s','max_skew_s') if k in value})
    reasons={'floor_projection':None if registration.get('floor_homography') is not None else 'floor_homography_not_provisioned'}
    if 'depth_clock' in value:
        from depth_clock import DepthClock
        DepthClock(None,site_id=site_id,camera_id=camera_id,clock=value['depth_clock'],calibration=calibration)
        _stereo_calibration(calibration)
        reasons['stereo']=None
    else:
        reasons['stereo']='depth_clock_not_commissioned'
    return deepcopy(value),reasons


def check_spatial_provisioning(settings,source,config):
    """Fail closed at startup on an invalid spatial file; None when absent."""
    if not config.get('spatial_file'):
        return None
    return spatial_provisioning(private_json(config['spatial_file']),site_id=settings.site_id,camera_id=source.camera_id)[1]


def room_facts_map(config,*,site_id,camera_id,read=private_json):
    """The validated room model of an enabled 'room_facts' block, else None."""
    block=config.get('room_facts')
    if block is None or block.get('enabled') is not True:
        return None
    from room_facts import load_site_map
    return load_site_map(read(block['site_map_file']),site_id=site_id,camera_id=camera_id)


def check_room_facts_provisioning(settings,source,config):
    """Fail closed at startup on an enabled room_facts block whose site map is
    missing, unbound or unregistered; None when absent or disabled."""
    room=room_facts_map(config,site_id=settings.site_id,camera_id=source.camera_id)
    return None if room is None else {'map_id':room['map_id'],'revision':room['revision']}


def check_caption_provisioning(recipe,config,reasoning_factory):
    """Reject, never clamp, a recipe granting captions that cannot be honoured.

    Without the caption reader nothing could be attached although the activation
    records a grant; without the household caption egress grant the JEV policy
    would refuse that arm's whole packets, visual evidence included.
    """
    granted=[arm for arm in ('a','b') if isinstance(recipe.get(arm),dict) and recipe[arm].get('caption_evidence') is True]
    if not granted:return
    if config.get('caption_history') is not True:
        raise ValueError(f"Recipe {recipe['id']} grants captions without provisioned caption history")
    readiness=getattr(reasoning_factory,'readiness',None)
    if reasoning_factory is not None and (readiness is None or (readiness() or {}).get('historical_caption')!='granted'):
        raise ValueError(f"Recipe {recipe['id']} grants captions without household caption egress")


def build_runtime(store,source,config,*,transport_factory=None,lease=None,experiment_id='placement-same',profiles_override=None,reasoning_factory=None,la_live=None,apartment_metric_source=None,execution_arms=('a', 'b')):
    from runtime import validate_execution_arms
    execution_arms = validate_execution_arms(execution_arms)
    from recorder import StreamCopySupervisor
    from service_runtime import ServiceRuntime
    from profile_resolver import RegisteredPairs,recipe_digest
    from remote_specialist import RemoteSpecialist
    from remote_transport import RemoteTransport
    from preview_ingest import PreviewIngestor
    from preview_client import LocalPreviewClient
    from chrony_preview_client import ChronyPreviewClient
    from preview_app import camera_token
    profiles=deepcopy(profiles_override) if profiles_override is not None else {arm:item['profile'] for arm,item in config['arms'].items()}
    lease=lease or TrialLease(config['lease_file'],profiles)
    recipe=next(r for r in json.loads(Path(__file__).with_name('experiments.json').read_text())['experiments'] if r['id']==experiment_id)
    check_caption_provisioning(recipe,config,reasoning_factory)
    recipe_sha=recipe_digest(recipe)
    def readiness(_session,_recipe,proposed):
        return {'state':'ready','recipe_sha256':recipe_sha,'profiles':profiles} if proposed==profiles and lease.ready() else {'state':'blocked'}
    resolver=RegisteredPairs(site_id='victoria',camera_ids=[source.camera_id],
        bindings={experiment_id:{'recipe_sha256':recipe_sha,'profiles':profiles}},readiness=readiness)
    executors={}
    for arm,binding in config['arms'].items():
        if arm not in execution_arms:
            continue
        binding={**binding,'profile':profiles[arm]}
        def credential(path=binding['token_file']):
            file=Path(path)
            if file.is_symlink() or file.stat().st_uid!=os.geteuid() or file.stat().st_mode&0o077:raise ValueError('Private worker credential required')
            with file.open('r') as stream:token=stream.read(514).strip()
            if not 32<=len(token)<=512:raise ValueError('Invalid worker credential')
            return token
        if transport_factory is not None:transport=transport_factory(binding,credential)
        else:
            context=ssl.create_default_context(cafile=binding['ca_file'])
            def connection(host,port,timeout,context=context):
                return http.client.HTTPSConnection(host,port,timeout=timeout,context=context)
            transport=RemoteTransport(binding['origin'],credential,connection_factory=connection)
        def authorize(phase,request,profile=binding['profile']):
            frame=request.get('frame',request)
            return (lease.ready() and request.get('profile')==profile and frame.get('site_id')=='victoria'
                    and frame.get('camera_id')==source.camera_id and phase in {'prepare_media','dispatch'})
        executors[arm]=RemoteSpecialist(store,binding['profile'],transport,authorize)
    def execute(request):return executors[request['arm']](request)
    extra={}
    if config.get('ha_source_file'):
        from managed_sources import ha_sources,unavailable_ha
        # Validated at startup, re-read per session after workers are prepared.
        # A later invalid file leaves HA unknown for this recording; it never
        # fails the pair or falls back to the startup copy.
        try:
            extra=ha_sources(store,source,config['ha_source_file'],ha_bindings=getattr(reasoning_factory,'ha_bindings',None))
        except Exception:
            extra=unavailable_ha(store,source)
    requirements=recipe.get('requirements') or ()
    if 'depth' in requirements:
        # Only a recipe that needs depth samples the camera owner's numerical
        # bundles; placement recipes never ingest them.
        from bundle_ingest import BundleIngestor
        from preview_client import LocalBundleClient
        extra={**extra,'bundle_sources':{source.camera_id:BundleIngestor(store,site_id='victoria',camera_id=source.camera_id)},
               'bundle_clients':{source.camera_id:LocalBundleClient(lambda:camera_token(config['camera_env']))}}
    elif (config.get('room_facts') or {}).get('enabled') is True:
        # Placement recipes: with a validated site map the shared baseline
        # also samples the depth NPZ at 2 Hz for the room facts of both arms
        # (never a lever). A map that no longer reads or binds leaves room
        # facts off for this recording; it never fails the pair.
        try:
            from room_facts import RoomFactsStage
            room=room_facts_map(config,site_id='victoria',camera_id=source.camera_id)
            extra={**extra,'room_facts':RoomFactsStage(store,room,camera_id=source.camera_id,
                client=LocalPreviewClient(lambda:camera_token(config['camera_env'])))}
        except Exception:
            pass
    if any(recipe[arm].get('localization','none')!='none' for arm in ('a','b')):
        # Absent or invalid spatial provisioning leaves localization unavailable
        # for this recording (recorded at activation); it never fails the pair.
        spatial={'spatial_unavailable':'spatial_not_provisioned'}
        if config.get('spatial_file'):
            try:
                from spatial_consumer import SpatialConsumer
                from service_runtime import BundleSynchronizedAssociation
                value,reasons=spatial_provisioning(private_json(config['spatial_file']),site_id='victoria',camera_id=source.camera_id)
                if 'depth' not in requirements and reasons['stereo'] is None:
                    reasons['stereo']='depth_bundles_not_requested'
                bounds={k:value[k] for k in ('max_age_s','max_skew_s') if k in value}
                spatial={'spatial_sources':{source.camera_id:SpatialConsumer(store,value['calibration'],value['registration'],
                            value['room_map'],association_revision=value['association_revision'],**bounds)},
                         'spatial_associators':{source.camera_id:BundleSynchronizedAssociation(store,value,site_id='victoria',
                            camera_id=source.camera_id,unavailable=reasons)}}
            except Exception:
                spatial={'spatial_unavailable':'spatial_provisioning_invalid'}
        extra={**extra,**spatial}
    if config.get('caption_history'):
        from caption_history import CaptionHistory,UnavailableCaption,refresh_publisher_binding
        # Managed refresh composition only: the stored caption its publisher
        # wrote. Polling never requests a refresh; a binding that fails leaves
        # captions unavailable for this recording and never fails the pair.
        try:
            path,revision=refresh_publisher_binding(PROCESS_STARTED_AT)
            caption=CaptionHistory(store,path,source.camera_id,model_revision=revision,revision_since=PROCESS_STARTED_AT)
        except Exception:
            caption=UnavailableCaption(store,source.camera_id)
        extra={**extra,'caption_sources':{source.camera_id:caption}}
    if config.get('qwen_live') is not None and reasoning_factory is not None:
        # A transport file that no longer reads leaves the perceiver
        # unprovisioned for this runtime; it never fails the pair.
        try:
            qwen=qwen_live_factory(config,reasoning_factory=reasoning_factory)
        except Exception:
            qwen=None
        if qwen is not None and la_live is not None and la_live.running():
            # LA allows one outstanding live request: while the always-on
            # perceiver runs, this recording gets no second live sender.
            _la_live_log.warning('lab qwen_live refused for this recording: the la-live-perceiver is running and '
                                 'LA allows one outstanding live request')
            qwen=None
        if qwen is not None:
            extra={**extra,'qwen_live':qwen}
    if config.get('vjepa_live') is not None and reasoning_factory is not None:
        # Likewise for the V-JEPA pump: an unreadable transport or credential
        # leaves it unprovisioned (temporal_encoder not_provisioned).
        try:
            vjepa=vjepa_live_factory(config,reasoning_factory=reasoning_factory)
        except Exception:
            vjepa=None
        if vjepa is not None:
            extra={**extra,'vjepa_live':vjepa}
    if config.get('occupancy_reader') is not None and ('qwen_live' in extra or 'vjepa_live' in extra):
        # Only a provisioned live pump uses it. An unreadable trust file leaves
        # the pumps on the ledger gate; it never fails the pair.
        try:
            from occupancy_reader import from_config
            extra={**extra,'occupancy_readers':{source.camera_id:from_config(config['occupancy_reader'],clock=store.clock)}}
        except Exception:
            pass
    if (la_live is not None and la_live.running() and 'qwen_live' not in extra and reasoning_factory is not None
            and hasattr(getattr(la_live, 'fact_log', None), 'subscribe_receipts')):
        # The perceiver is the only live Qwen sender: its accepted receipts are
        # this runtime's Qwen facts for JEV (absent from JEV since 2026-10-09).
        from la_qwen_context import LaQwenReceipts
        extra={**extra,'la_qwen':LaQwenReceipts(la_live.fact_log)}
    if la_live is not None and la_live.running():
        # The recording's preview pump owns the capture slots while it runs;
        # each preview it accepts is also offered to the perceiver's RAM ring
        # (kept only while that gate is open), so captures stay at ~2 Hz.
        extra={**extra,'preview_taps':{source.camera_id:la_live.offer_preview}}
    preview=ChronyPreviewClient()
    if config.get('schema_version')==2:
        from paired_preview_client import PairedPreviewClient, policy
        from bundle_ingest import BundleIngestor
        paired_policy=policy(config['paired_preview'])
        clocks={}
        if config.get('spatial_file'):
            spatial,_=spatial_provisioning(private_json(config['spatial_file']),site_id='victoria',camera_id=source.camera_id)
            if 'depth_clock' in spatial:
                clocks[source.camera_id]=spatial['depth_clock']
        preview=PairedPreviewClient(clock=store.clock)
        extra={**extra,'paired_preview_policy':paired_policy,'paired_depth_clocks':clocks,
            'paired_preview_sources':{source.camera_id:BundleIngestor(store,site_id='victoria',camera_id=source.camera_id)}}
    assessment=None
    if config.get('schema_version')==3:
        # Opt-in private numerical assessment, after ordinary RGB admission and
        # model offer. Reuse the installed capture client and fixed endpoints;
        # this does not commission a depth clock or grant operational geometry.
        from assessment_existing_api import AssessmentExistingApiTap, policy
        from preview_client import LocalBundleClient
        assessment=AssessmentExistingApiTap(store,site_id='victoria',camera_id=source.camera_id,
            client=LocalBundleClient(lambda:camera_token(config['camera_env'])),
            policy=policy(config['existing_api_assessment']))
        previous=(extra.get('preview_taps') or {}).get(source.camera_id)
        def assessment_fanout(jpeg,header):
            if previous is not None:
                try:previous(jpeg,header)
                except Exception:pass  # Preserve independent assessment admission.
            assessment(jpeg,header)
        extra={**extra,'preview_taps':{**extra.get('preview_taps',{}),source.camera_id:assessment_fanout}}
    anchor_source=PreviewIngestor(store,site_id='victoria',camera_id=source.camera_id,
        clock_validation_id=config['receiver_clock']['validation_id'],clock_uncertainty_s=config['receiver_clock']['uncertainty_seconds'])
    bundled_source=None
    if config.get('schema_version')==4 and experiment_id!='apartment-viewing':
        from bundle_rgb_input import BundledRgbInputFactory
        from preview_client import LocalBundleClient
        bundled_source=BundledRgbInputFactory(store,site_id='victoria',camera_id=source.camera_id,
            client=LocalBundleClient(lambda:camera_token(config['camera_env'])), policy=config['bundled_rgb_input'],
            anchor_tap=(extra.get('preview_taps') or {}).get(source.camera_id))
        extra={**extra,'bundled_rgb_sources':{source.camera_id:bundled_source}}
    viewing_source=None
    if experiment_id=='apartment-viewing':
        from apartment_viewing_source import ViewingFactory, policy
        viewing_source=ViewingFactory(store,camera_id=source.camera_id,policy=policy(config.get('apartment_viewing')),metric_source=apartment_metric_source)
        extra={**extra,'apartment_viewing_sources':{source.camera_id:viewing_source}}
    if experiment_id=='apartment-viewing':
        from apartment_viewing_source import ViewingRecorder
        recorder=ViewingRecorder(store,source.camera_id)
    else:recorder=StreamCopySupervisor(store,source)
    runtime=ServiceRuntime(store,{source.camera_id:recorder},resolver,execute,reasoning_factory=reasoning_factory,execution_arms=execution_arms,**extra,
        reasoning_min_offer_interval_s=config.get('reasoning_min_offer_interval_s'),
        reasoning_cadence=config.get('reasoning_cadence'),
        preview_sources={source.camera_id:anchor_source},
        preview_clients={source.camera_id:preview})
    if assessment is not None:
        runtime.existing_api_assessment=assessment
        assessment.bind_runtime(runtime)
    if bundled_source is not None:
        bundled_source.bind_runtime(runtime)
    if viewing_source is not None:viewing_source.bind_runtime(runtime)
    return runtime


def create_live_detector_app():
    from capture_app import load_provisioning
    from app import create_app
    from live_video import LiveVideo
    from live_planes import LivePlanes
    from preview_client import LocalPreviewClient
    from preview_app import camera_token
    config=validate_config(private_json(os.environ['PERCEPTION_LAB_LIVE_CONFIG']))
    if 'qwen_profile' in config:
        # Saved analysis is owned by the managed controller; never silently ignored.
        raise ValueError('Saved Qwen analysis requires the managed controller')
    if 'qwen_live' in config:
        # The perceiver feeds live JEV, which only the managed controller binds.
        raise ValueError('The Qwen live perceiver requires the managed controller')
    if 'vjepa_live' in config:
        # Its LA lease follows the managed pair's detector lease.
        raise ValueError('The V-JEPA live pump requires the managed controller')
    if (config.get('la_live_perceiver') or {}).get('enabled') is True:
        # A controller-lifetime component of the managed controller only.
        raise ValueError('The LA live perceiver requires the managed controller')
    settings,source,_clock,_credential,_gates=load_provisioning(config['capture_config'])
    check_spatial_provisioning(settings,source,config)
    check_room_facts_provisioning(settings,source,config)
    if settings.max_session_seconds>120:raise ValueError('Bounded two-minute recording required')
    if config.get('ha_source_file'):
        from managed_sources import ha_source
        ha_source(settings.site_id,source.camera_id,config['ha_source_file'])
    return create_app(settings,runtime_factory=lambda store:build_runtime(store,source,config),
        live_videos={source.camera_id:LiveVideo(source,settings.site_id)},
        live_planes={source.camera_id:LivePlanes(LocalPreviewClient(lambda:camera_token(config['camera_env'])),settings.site_id,source.camera_id)})
