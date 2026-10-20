"""Explicit, private Lab household binding. No inference during construction."""
import os
import time
from pathlib import Path

from live_detector_app import private_json
from jev_policy import LabJevPolicy
from jev_provider import JevProvider, history_windows
from live_reasoning import LiveReasoning
from reasoning import finite


def session_policy(policy, session=None, *, now):
    """Standing opt-in permission yields a lease anchored to recording start."""
    if policy.get('schema_version')!=2:return dict(policy)
    fields={'schema_version','enabled','scope','revision','generation','site_id','camera_ids',
            'model_selector','max_calls_per_minute','session_seconds','max_session_calls'}
    # Optional explicit caption/ambient/presence/device egress grants: a
    # projection schema name, never a flag; plus the optional list of granted
    # structured-source schemas (the continuous Qwen scene).
    from jev_policy import GRANT_KEYS,SOURCES_KEY,structured_sources
    grants=set(GRANT_KEYS)
    if (set(policy)-grants-{SOURCES_KEY}!=fields or type(policy['enabled']) is not bool
            or any(not isinstance(policy.get(k,''),str) for k in grants)
            or policy['scope']!='household_structured'
            or not isinstance(policy['revision'],str) or not policy['revision']
            or type(policy['generation']) is not int or policy['generation']<0
            or not isinstance(policy['site_id'],str) or not policy['site_id']
            or not isinstance(policy['camera_ids'],list) or not policy['camera_ids']
            or not all(isinstance(c,str) and c for c in policy['camera_ids'])
            or policy['model_selector']!='jev-1.13.0'
            or type(policy['session_seconds']) is not int or not 1<=policy['session_seconds']<=300
            or type(policy['max_session_calls']) is not int or not 1<=policy['max_session_calls']<=600
            or type(policy['max_calls_per_minute']) is not int or not 2<=policy['max_calls_per_minute']<=120):
        raise ValueError('Invalid bounded session policy')
    try:
        structured_sources(policy)
    except ValueError:
        raise ValueError('Invalid bounded session policy') from None
    if session is None:return dict(policy)
    start=session.get('started_at')
    if (session.get('state')!='recording' or session.get('reasoning_enabled') is not True
            or session.get('site_id')!=policy['site_id'] or session.get('camera_id') not in policy['camera_ids']
            or not finite(start) or not start<=now<start+policy['session_seconds']):
        raise ValueError('Session reasoning grant revoked')
    return {**{k:v for k,v in policy.items() if k!='session_seconds'},
            'schema_version':1,'expires_at':start+policy['session_seconds']}


def reasoning_factory(config_path):
    config = private_json(config_path)
    if set(config)-{'ha_bindings','zone_bindings','site_timezone','shadow_wire','wire','monitor_side','desk_seat','questions','facts_key','smoothing'} != {'schema_version','policy_file','credential_file','response_version'} or config['schema_version'] != 1:
        raise ValueError('Exact Lab JEV provisioning required')
    # Optional live packet wire: absent is v5 (structured-semantic-7, with its
    # pre-send budget guard); 'den-facts-6' sends the facts-only v6 wire,
    # 'den-facts-7' the same wire with now.position (it needs desk_seat below)
    # and 'den-facts-8' the same wire with the room facts (room-facts-1; the
    # live config's room_facts block provisions them, desk_seat is optional,
    # its no-3D-fact fallback). The household policy must also grant that
    # exact id in structured_sources. Rolling back is setting 'wire' to
    # 'den-facts-7' or 'den-facts-6' (desk_seat may stay).
    from jev_wire import WIRE_VERSION,WIRE_VERSION_7,WIRE_VERSIONS
    wire=config.get('wire')
    person_mode=wire=='den-person-facts-1'
    if wire not in (None,*WIRE_VERSIONS,'den-person-facts-1'):
        raise ValueError('Exact Lab JEV provisioning required')
    # Optional v6 shadow: builds and verifies den-facts-6 beside every live v5
    # request and records a local verdict; it never sends v6. It is off while
    # v6 is the live wire (nothing to shadow).
    shadow_wire=config.get('shadow_wire')
    if shadow_wire not in (None,WIRE_VERSION):
        raise ValueError('Exact Lab JEV provisioning required')
    if wire is not None:
        shadow_wire=None
    # Optional levers on the v6 wire, each a registered name (absent: today's):
    # 'questions' v6 (den-activity-8-facts), v7 (den-activity-9-fanout, fused
    # in code) or v8 (den-activity-10-facts, den-facts-7 only); 'facts_key'
    # facts-key-1 or facts-key-2 (the coarse key); and
    # 'smoothing', a registered jev_smoothing parameter set for the shown
    # timeline. questions and facts_key need the v6 wire.
    from jev_wire import FACTS_KEY_VERSIONS,QUESTION_CHOICES,question_wire_ok
    from jev_smoothing import PARAMETER_SETS
    questions=config.get('questions','v6')
    facts_key=config.get('facts_key')
    smoothing=config.get('smoothing')
    if person_mode and any(k in config for k in ('questions','facts_key','smoothing','shadow_wire')):
        raise ValueError('Person judgments have a fixed wire and no legacy temporal smoothing')
    if not person_mode and (not isinstance(questions,str) or questions not in QUESTION_CHOICES
            or (facts_key is not None and (not isinstance(facts_key,str) or facts_key not in FACTS_KEY_VERSIONS))
            or (smoothing is not None and (not isinstance(smoothing,str) or smoothing not in PARAMETER_SETS))
            or (wire is None and (questions!='v6' or facts_key is not None))
            or (wire is not None and not question_wire_ok(QUESTION_CHOICES[questions],wire))):
        raise ValueError('Exact Lab JEV provisioning required')
    question_version=QUESTION_CHOICES[questions]
    from ambient_context import ZONE
    # Optional IANA site zone for ambient context. Resolution happens where it
    # is used; a zone that cannot load yields 'unknown', never another zone.
    site_timezone=config.get('site_timezone')
    if site_timezone is not None and (not isinstance(site_timezone,str) or not ZONE.fullmatch(site_timezone)):
        raise ValueError('Registered semantic source bindings required')
    # Optional site monitor side (head-direction-1): which image side the
    # off-camera monitor is on ('image_left' or 'image_right'). With it the
    # head block gains head_toward; never sent, only mapped.
    from jev_evidence import MONITOR_SIDES
    monitor_side=config.get('monitor_side')
    if monitor_side is not None and (not isinstance(monitor_side,str) or monitor_side not in MONITOR_SIDES):
        raise ValueError('Registered semantic source bindings required')
    # Optional site desk_seat region (desk-seat-1): the image edge the desk seat
    # touches and the box fractions that place the one person in it. Used by
    # the den-facts-7 wire (now.position) and, only while no 3D room fact is
    # current, by den-facts-8's position fallback; never sent, only the word.
    from jev_evidence import desk_seat_config
    desk_seat=config.get('desk_seat')
    try:
        desk_seat=None if desk_seat is None else desk_seat_config(desk_seat)
    except ValueError:
        raise ValueError('Registered semantic source bindings required') from None
    if wire==WIRE_VERSION_7 and desk_seat is None:
        raise ValueError('Exact Lab JEV provisioning required')
    from jev_evidence import DERIVED_HA,HA_FACTS,ZONE_TYPES
    import re
    ha=config.get('ha_bindings',{});zones=config.get('zone_bindings',{})
    # A 'derived.' id binds only its own fact; the HA source file must declare it.
    if (not isinstance(ha,dict) or len(ha)>8 or len(set(ha.values()))!=len(ha)
            or any(not re.fullmatch(r'[a-z_]+\.[a-z0-9_]{1,100}',k) or v not in HA_FACTS
                   or (k.split('.',1)[0]=='derived' and DERIVED_HA.get(k)!=v)
                   or (v=='owner_home' and k.split('.',1)[0]!='person') for k,v in ha.items())
            or not isinstance(zones,dict) or len(zones)>16 or any(v not in ZONE_TYPES for v in zones.values())):
        raise ValueError('Registered semantic source bindings required')

    def create(store, sid):
        session = store.get_session(sid)
        revision = session['reasoning_revision']

        def read_policy():
            policy = private_json(config['policy_file'])
            current = store.get_session(sid)
            if (current['state'] != 'recording' or not current.get('reasoning_enabled')
                    or current['reasoning_revision'] != revision):
                raise ValueError('Session reasoning grant revoked')
            policy=session_policy(policy,current,now=store.clock())
            # The operator policy and session setting must both remain current.
            policy['revision'] = f"{policy['revision']}:session:{sid}:{revision}"
            return policy

        def toggle():
            # This is a fresh read of a durable operator switch, not a synthetic
            # heartbeat for a remote or cached HA state.
            policy = private_json(config['policy_file'])
            return {'state':'on' if policy.get('enabled') is True else 'off',
                    'received_at':store.clock()}

        def credential():
            path = Path(config['credential_file'])
            if path.is_symlink() or path.stat().st_uid != os.geteuid() or path.stat().st_mode & 0o077:
                raise ValueError('Private Lab credential required')
            with path.open(encoding='utf-8') as stream:
                token = stream.read(4098).strip()
            if not 1 <= len(token) <= 4096:
                raise ValueError('Invalid Lab credential')
            return token

        policy = read_policy()
        gate_type=LabJevPolicy
        provider_type=JevProvider
        if person_mode:
            from jev_person_policy import PersonJevPolicy
            from jev_person_provider import JevPersonProvider
            gate_type,provider_type=PersonJevPolicy,JevPersonProvider
        gate=gate_type(store, read_policy, toggle,ha_bindings=ha,zone_bindings=zones,site_timezone=site_timezone,
                          monitor_side=monitor_side,desk_seat=desk_seat)
        shadow=None
        if shadow_wire is not None:
            from jev_shadow import ShadowV6
            shadow=ShadowV6(store,gate,monitor_side=monitor_side)
        provider = provider_type(model_selector=policy['model_selector'],
            allow_recent_history=True,
            semantic=True,ha_bindings=ha,zone_bindings=zones,
            response_version=config['response_version'],
            authorize=gate, credential=credential, clock=store.clock, shadow=shadow, wire=wire, monitor_side=monitor_side,
            questions=question_version if wire is not None else None, facts_key=facts_key, desk_seat=desk_seat)
        return LiveReasoning(store, sid, provider, smoothing=smoothing)

    from jev_evidence import FACT_GRANTS,fact_granted
    # HA facts bound here whose egress needs its own household grant.
    gated=sorted({f for f in ha.values() if f in FACT_GRANTS})

    def zone_loaded():
        from ambient_context import zone
        return site_timezone is not None and zone(site_timezone) is not None

    def grant_states(policy):
        from jev_evidence import CAPTION_EGRESS,PRESENCE_EGRESS,QWEN_EGRESS,VJEPA_EGRESS
        from jev_policy import structured_sources
        caption='granted' if policy.get('historical_caption')==CAPTION_EGRESS else 'not_granted'
        presence=('not_bound' if 'owner_home' not in ha.values()
                  else 'granted' if policy.get('owner_presence')==PRESENCE_EGRESS else 'not_granted')
        devices=[f for f in gated if f!='owner_home']
        device=('not_bound' if not devices else 'granted' if all(fact_granted(f,policy) for f in devices) else 'not_granted')
        try:
            qwen='granted' if QWEN_EGRESS in structured_sources(policy) else 'not_granted'
            vjepa='granted' if VJEPA_EGRESS in structured_sources(policy) else 'not_granted'
        except ValueError:
            qwen=vjepa='invalid'
        # zone_loaded: whether the configured site zone loads on this host
        # (tzdata); false means ambient clock facts are timezone_unavailable.
        return {'historical_caption':caption,'ambient_context':ambient_state(policy),'owner_presence':presence,
                'device_context':device,'qwen_scene':qwen,'vjepa_scene':vjepa,'zone_loaded':zone_loaded()}

    def readiness():
        try:
            policy=private_json(config['policy_file'])
            states=grant_states(policy)
            if policy.get('schema_version')==2:
                session_policy(policy,now=time.time())
                if policy['enabled'] is not True:return {'available':False,'reason':'jev_policy_disabled'}
                return {'available':True,'reason':None,'mode':'explicit_bounded_session',
                    'session_seconds':policy['session_seconds'],'max_session_calls':policy['max_session_calls'],
                    'max_calls_per_minute':policy['max_calls_per_minute'],**states}
            if policy.get('enabled') is not True:return {'available':False,'reason':'jev_policy_disabled'}
            if not isinstance(policy.get('expires_at'),(int,float)) or policy['expires_at']<=time.time():
                return {'available':False,'reason':'jev_policy_expired'}
            return {'available':True,'reason':None,'policy_expires_at':policy['expires_at'],'mode':'bounded_lab_comparison',
                    **states}
        except Exception:
            return {'available':False,'reason':'jev_policy_unavailable'}

    def ambient_state(policy):
        from jev_evidence import AMBIENT_EGRESS
        if site_timezone is None:return 'not_provisioned'
        return 'granted' if isinstance(policy,dict) and policy.get('ambient_context')==AMBIENT_EGRESS else 'not_granted'

    def current_policy():
        try:
            policy=private_json(config['policy_file'])
            return policy if isinstance(policy,dict) else None
        except Exception:
            return None

    def ambient_provisioned():
        # Read per offer tick: ambient joins the shared context only while the
        # operator zone is set and the household policy grants its schema, so a
        # missing grant is explicit coverage, never a refused dispatch.
        return ambient_state(current_policy())=='granted'

    def ambient_tick_state():
        # 'granted', 'not_granted' (zone set, no household grant) or 'not_provisioned'.
        policy=current_policy()
        return ambient_state(policy) if policy is not None else ('not_provisioned' if site_timezone is None else 'not_granted')

    def fact_grants():
        # Per tick: {gated fact: granted?}. An absent grant drops that fact
        # from the packet and marks it not_granted; dispatch continues.
        policy=current_policy()
        return {f:policy is not None and fact_granted(f,policy) for f in gated}

    def qwen_granted():
        # Per tick: the household lists the continuous Qwen scene schema.
        from jev_evidence import QWEN_EGRESS
        from jev_policy import structured_sources
        policy=current_policy()
        try:
            return policy is not None and QWEN_EGRESS in structured_sources(policy)
        except ValueError:
            return False
    def vjepa_granted():
        # Per tick: the household lists the V-JEPA den grant (vjepa-den-1).
        # Off: the pump sends nothing and no V-JEPA record reaches JEV.
        from jev_evidence import VJEPA_EGRESS
        from jev_policy import structured_sources
        policy=current_policy()
        try:
            return policy is not None and VJEPA_EGRESS in structured_sources(policy)
        except ValueError:
            return False
    def qwen_person_granted():
        # Separate opt-in input policy. The old scene grant does not authorize
        # person labels/boxes, and no new record enters an old JEV wire.
        from jev_policy import structured_sources
        policy=current_policy()
        try:
            return (policy is not None and policy.get('enabled') is True
                    and policy.get('scope') == 'household_structured' and policy.get('site_id') == 'victoria'
                    and isinstance(policy.get('camera_ids'), list) and 'den' in policy['camera_ids']
                    and 'lab-live-person-scene-1' in structured_sources(policy))
        except ValueError:
            return False
    create.readiness=readiness
    create.site_timezone=site_timezone
    create.ambient_provisioned=ambient_provisioned
    create.ambient_state=ambient_tick_state
    create.fact_grants=fact_grants
    create.qwen_granted=qwen_granted
    create.qwen_person_granted=qwen_person_granted
    create.vjepa_granted=vjepa_granted
    # Startup cross-checks: HA source allowlist/provenance and recipe windows.
    create.ha_bindings=dict(ha)
    create.shadow_wire=shadow_wire
    create.wire=wire
    create.person_mode=person_mode
    create.monitor_side=monitor_side
    create.desk_seat=desk_seat
    create.questions=questions
    create.facts_key=facts_key
    create.smoothing=smoothing
    create.check_windows=history_windows
    return create
