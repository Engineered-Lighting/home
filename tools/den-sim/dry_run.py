"""The exact fixture command for the brain's target: computed, shown, never sent.

Real compile_target on a FICTIONAL profile, with its pivot and basis taken from
the fixture the brain chose. Then the real plan and wire codec, read by path
because it is on no git ref, sealed with the contract's public test key for a
fictional device. Then the literal Home Assistant POST with a placeholder ieee,
the two CAN A4 frames, and the Hue form of what decide v0 asked for in chain.py.
Nothing here can send: there is no token, no route and no socket. Prints one JSON
document to stdout.

Reads JSON on stdin: {"scenario", "brain", "decide", "codec_tools",
"codec_contracts"}. "brain" is null when the brain lit nothing, or else
{"fixture_id", "aim_origin_m", "horizontal_world_reference", "target_m", "level",
"cct_k", "pan_deg", "tilt_deg"}.
"""
import hashlib
import importlib.util
import itertools
import json
import math
import struct
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # The codec's folder is read only: nothing lands beside it.


def refuse_network(event, _args):
    if event in ('socket.connect', 'socket.bind', 'socket.sendto', 'socket.getaddrinfo'):
        raise RuntimeError('the den simulation dry run has no network')


sys.addaudithook(refuse_network)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'stack/services/home-context'))
from fixture_execution import compile_target  # noqa: E402
from fixture_joint_execution import JointFixtureCompiler  # noqa: E402
from fixture_profile import FixtureProfileRefusal, digest  # noqa: E402
from test_fixture_profile import fictional_profile  # noqa: E402

FICTIONAL_NOW_US = 1_000_000  # Inside the fictional profile's evidence window.
SPEED_FIXTURE_DEG_S = [30, 30]
PUBLIC_TEST_KEY = bytes(range(32))  # runtime-wire-v1's golden vector: never a provisioned key.
FICTIONAL_DEVICE_ID = bytes(range(0xa0, 0xb0))
PLACEHOLDER_IEEE = 'UNCOMMISSIONED'
ZHA_ROUTE = '/api/services/zha/issue_zigbee_cluster_command'
SPOT_CHANNEL, FULL_DUTY = 21, 4096
ANGLE_TOLERANCE_DEG = .05
NOT_GENERATED = 'Clock exchange is not generated: the real host brackets four timestamps against a live device.'


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def inverse_kinematics(brain):
    """Fixture x is the virtual fixture's forward, y its left and z up, which is the
    right-handed basis the compiler requires. So fixture pan is minus Home's pan, and
    fixture tilt is measured from straight down, where Home's is from level."""
    fx, fy = brain['horizontal_world_reference'][:2]
    length = math.hypot(fx, fy)
    fx, fy = fx / length, fy / length
    profile = fictional_profile()
    profile['geometry'].update(optical_pivot_m=list(brain['aim_origin_m']), fixture_to_room_basis=[fx, -fy, 0, fy, fx, 0, 0, 0, 1])
    try:
        target = compile_target(profile, brain['target_m'], SPEED_FIXTURE_DEG_S, now_home_us=FICTIONAL_NOW_US)
    except FixtureProfileRefusal as refusal:
        return dict(ok=False, reason=str(refusal))
    pan, tilt = target['fixture_angles_deg']
    pan_gap, tilt_gap = abs(pan + brain['pan_deg']), abs(tilt + brain['tilt_deg'] - 90)
    return dict(ok=True, status='FICTIONAL', profile_id=profile['profile_id'], profile_sha256=digest(profile),
                physical_eligible=target['physical_eligible'], fixture_angles_deg=[pan, tilt],
                target_centideg=target['target_centideg'], speed_deg_s=target['speed_deg_s'],
                quantization_error_rad=target['quantization_error_rad'], range_m=target['range_m'],
                home_angles_deg=[brain['pan_deg'], brain['tilt_deg']], angle_gap_deg=[pan_gap, tilt_gap],
                agrees_with_home=pan_gap < ANGLE_TOLERANCE_DEG and tilt_gap < ANGLE_TOLERANCE_DEG)


def semantic_spot_refusal():
    try:
        JointFixtureCompiler.compile_semantic_spotlight(None)
    except FixtureProfileRefusal as refusal:
        return refusal.code
    return None


def load_by_path(tools, name):
    path = Path(tools) / (name + '.py')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclass looks its module up here.
    spec.loader.exec_module(module)
    return module, sha256(path.read_bytes())


def zha_post(wire, packet):
    """The fields room_production_canonical_manual.py requires, field by field. The
    SDK adds the LVBytes length back, so params.packet leaves it off."""
    return dict(ieee=PLACEHOLDER_IEEE, endpoint_id=wire.ENDPOINT, cluster_id=wire.CLUSTER, cluster_type='in',
                command=0, command_type='server', manufacturer=-1, params=dict(packet=list(packet[1:])))


def seal_everything(codec, wire, ik, duty):
    context = codec.example_context()
    context_bytes = codec.encode_context(context)
    active = hashlib.sha256(context_bytes).digest()
    plan = codec.example_plan()
    plan.update(target_centideg=ik['target_centideg'], speed_deg_s=ik['speed_deg_s'], frame=[0] * SPOT_CHANNEL + [duty, 0, 0])
    plan_bytes = codec.encode_plan(plan)
    boot, session, owner = (int(context[key]) for key in ('boot_generation', 'session_id', 'ownership_generation'))
    sequence, packets = itertools.count(1), []

    def add(stage, operation, bound_to, body):
        packet = wire.seal_packet(PUBLIC_TEST_KEY, FICTIONAL_DEVICE_ID, boot, bound_to, wire.Direction.REQUEST, operation, session, next(sequence), body)
        assert wire.open_packet(PUBLIC_TEST_KEY, FICTIONAL_DEVICE_ID, boot, bound_to, wire.Direction.REQUEST, packet).body == body
        packets.append(dict(n=len(packets) + 1, stage=stage, packet_hex=packet.hex(), route=ZHA_ROUTE, body=zha_post(wire, packet)))

    for body in wire.session_open_bodies(context_bytes, owner):
        add('session_open', wire.Operation.SESSION_OPEN, wire.BOOTSTRAP_CONTEXT, body)
    prepare_start = len(packets)
    for body in wire.plan_prepare_bodies(plan_bytes, 1):
        add('plan_prepare', wire.Operation.PLAN_PREPARE, active, body)
    add('plan_commit', wire.Operation.PLAN_COMMIT, active, wire.plan_commit_body(plan_bytes))

    # Trace the brain's aim into the bytes that would carry it.
    aim = struct.pack('<2i2H', *ik['target_centideg'], *ik['speed_deg_s'])
    offset = plan_bytes.find(aim)
    assert offset > 0 and plan_bytes.find(aim, offset + 1) < 0
    chunk = wire.PLAN_CHUNK
    aim_chunks = list(range(offset // chunk + 1, (offset + len(aim) - 1) // chunk + 2))
    counts = dict(session_open=prepare_start, plan_prepare=len(packets) - prepare_start - 1, plan_commit=1, total=len(packets))
    return (dict(bytes=len(plan_bytes), sha256=sha256(plan_bytes), hex=plan_bytes.hex(), status='fictional context, draft format',
                 aim=dict(offset=offset, hex=aim.hex(), prepare_chunks=aim_chunks,
                          packets=[prepare_start + number for number in aim_chunks])),
            dict(key='public test key from runtime-wire-v1, never a provisioned key', device_id=FICTIONAL_DEVICE_ID.hex(), ieee=PLACEHOLDER_IEEE,
                 route=ZHA_ROUTE, counts=counts, time_exchange=NOT_GENERATED, packets=packets))


def can_a4_frame(target_centideg, speed_deg_s):
    """V4.2 A4 fields, encoded here: the firmware's own encoder is native code."""
    return struct.pack('<BBHi', 0xA4, 0, speed_deg_s, target_centideg)


def can_frames(ik, contracts):
    frames = [dict(axis=name, id=id_, id_hex='0x%03x' % id_, data_hex=can_a4_frame(target, speed).hex(), target_centideg=target, speed_deg_s=speed)
              for name, id_, target, speed in zip(('pan', 'tilt'), (0x141, 0x142), ik['target_centideg'], ik['speed_deg_s'])]
    result = dict(frames=frames, oracle_ok=None, allowlist_entries=None, note='The motor allowlist decides which units may ever receive these.')
    try:
        plan_contract = (Path(contracts) / 'runtime-plan-v1.json').read_bytes()
        allowlist = json.loads((Path(contracts) / 'motor-protocol-allowlist-v1.json').read_bytes())
    except OSError:
        return result
    oracles = json.loads(plan_contract)['independent_motor_oracles']
    result['oracle_ok'] = bool(oracles) and all(can_a4_frame(row['target_centideg'], row['speed_deg_s']).hex() == row['a4_hex'] for row in oracles)
    result['allowlist_entries'] = len(allowlist['entries'])
    return result


def hue_form(scene_by_light):
    calls = []
    for entity, scene in scene_by_light.items():
        if scene['on']:
            calls.append(dict(route='/api/services/light/turn_on', body=dict(entity_id=entity, brightness=scene['brightness'], color_temp_kelvin=scene['kelvin'])))
        else:
            calls.append(dict(route='/api/services/light/turn_off', body=dict(entity_id=entity)))
    return calls


def main():
    request = json.load(sys.stdin)
    brain, decide = request['brain'], request['decide']
    out = dict(sent=False, send_path=None, scenario=request['scenario'], brain=brain, ik=None, spot=None, plan=None, wire=None, can=None,
               decide={**decide, 'calls': hue_form(decide['requested_scene'] or {})}, codec=dict(found=False, tools=request['codec_tools']))
    tools = Path(request['codec_tools'])
    if (tools / 'c12_runtime_plan.py').is_file() and (tools / 'c12_runtime_wire.py').is_file():
        codec, plan_sha = load_by_path(tools, 'c12_runtime_plan')
        wire, wire_sha = load_by_path(tools, 'c12_runtime_wire')
        out['codec'].update(found=True, c12_runtime_plan_sha256=plan_sha, c12_runtime_wire_sha256=wire_sha)
    if brain is None:
        print(json.dumps(out, allow_nan=False))
        return
    out['ik'] = inverse_kinematics(brain)
    if not out['ik']['ok']:
        print(json.dumps(out, allow_nan=False))
        return
    duty = round(brain['level'] * FULL_DUTY)
    out['spot'] = dict(raw_duty=duty, full_duty=FULL_DUTY, channel=SPOT_CHANNEL, brain_level=brain['level'], brain_cct_k=brain['cct_k'],
                       semantic_refusal=semantic_spot_refusal(), semantic_cct_mapping=None)
    out['can'] = can_frames(out['ik'], request['codec_contracts'])
    if out['codec']['found']:
        out['plan'], out['wire'] = seal_everything(codec, wire, out['ik'], duty)
    print(json.dumps(out, allow_nan=False))


if __name__ == '__main__':
    main()
