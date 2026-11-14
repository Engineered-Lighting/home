"""The exact fixture command for the coordinator's Decision: computed, shown, never sent.

Real compile_target on a FICTIONAL profile, with its pivot and basis taken from
the fixture the Decision lit, on the Decision's target_m. Then the real plan and
wire codec, read by path because it is on no git ref, sealed with the contract's
public test key for a fictional device, carrying the Decision's level. Then the
literal Home Assistant POST with a placeholder ieee, and the two CAN A4 frames.
Nothing here can send: there is no token, no route and no socket. Prints one JSON
document to stdout. Home's own angles are read back at landing by the aim check
(hop 24), never here.

Reads JSON on stdin: {"take", "decision", "codec_tools", "codec_contracts"}, where
take is the take's folder id. "decision" is null when the Decision lit nothing, or
else {"channel", "fixture_id", "aim_origin_m", "horizontal_world_reference",
"target_m", "level", "cct_k", "decision_id"}.
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


# prepare.py's list: every connect, bind and send, and every name lookup, which can send DNS on its own.
NETWORK = ('socket.connect', 'socket.bind', 'socket.sendto', 'socket.sendmsg', 'socket.getaddrinfo',
           'socket.gethostbyname', 'socket.gethostbyaddr', 'socket.getnameinfo')


def refuse_network(event, _args):
    if event in NETWORK:
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
NOT_GENERATED = 'Clock exchange is not generated: the real host brackets four timestamps against a live device.'


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def inverse_kinematics(decision):
    """Fixture x is the virtual fixture's forward, y its left and z up, which is the
    right-handed basis the compiler requires. So fixture pan is minus Home's pan, and
    fixture tilt is measured from straight down, where Home's is from level: the aim
    check compares them so at landing."""
    fx, fy = decision['horizontal_world_reference'][:2]
    length = math.hypot(fx, fy)
    fx, fy = fx / length, fy / length
    profile = fictional_profile()
    profile['geometry'].update(optical_pivot_m=list(decision['aim_origin_m']), fixture_to_room_basis=[fx, -fy, 0, fy, fx, 0, 0, 0, 1])
    try:
        target = compile_target(profile, decision['target_m'], SPEED_FIXTURE_DEG_S, now_home_us=FICTIONAL_NOW_US)
    except FixtureProfileRefusal as refusal:
        return dict(ok=False, reason=str(refusal))
    return dict(ok=True, status='FICTIONAL', profile_id=profile['profile_id'], profile_sha256=digest(profile),
                physical_eligible=target['physical_eligible'], fixture_angles_deg=list(target['fixture_angles_deg']),
                target_centideg=target['target_centideg'], speed_deg_s=target['speed_deg_s'],
                quantization_error_rad=target['quantization_error_rad'], range_m=target['range_m'])


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

    # Trace the Decision's aim into the bytes that would carry it.
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


def main():
    request = json.load(sys.stdin)
    decision = request['decision']
    out = dict(sent=False, send_path=None, take=request['take'], decision=decision, ik=None, spot=None, plan=None, wire=None, can=None,
               codec=dict(found=False, tools=request['codec_tools']))
    tools = Path(request['codec_tools'])
    if (tools / 'c12_runtime_plan.py').is_file() and (tools / 'c12_runtime_wire.py').is_file():
        codec, plan_sha = load_by_path(tools, 'c12_runtime_plan')
        wire, wire_sha = load_by_path(tools, 'c12_runtime_wire')
        out['codec'].update(found=True, c12_runtime_plan_sha256=plan_sha, c12_runtime_wire_sha256=wire_sha)
    if decision is None:
        print(json.dumps(out, allow_nan=False))
        return
    out['ik'] = inverse_kinematics(decision)
    if not out['ik']['ok']:
        print(json.dumps(out, allow_nan=False))
        return
    duty = round(decision['level'] * FULL_DUTY)
    out['spot'] = dict(raw_duty=duty, full_duty=FULL_DUTY, channel=SPOT_CHANNEL, decision_level=decision['level'], decision_cct_k=decision['cct_k'],
                       semantic_refusal=semantic_spot_refusal(), semantic_cct_mapping=None)
    out['can'] = can_frames(out['ik'], request['codec_contracts'])
    if out['codec']['found']:
        out['plan'], out['wire'] = seal_everything(codec, wire, out['ik'], duty)
    print(json.dumps(out, allow_nan=False))


if __name__ == '__main__':
    main()
