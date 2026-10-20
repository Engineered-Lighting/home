"""Approximate room-frame person facts for the activity loop (never qualified).

Inputs are the existing approximate placement packet (room-frame joints from
the reviewed view alignment) and the same arm-B model output. Outputs are only
the contract's person_pose fields, the merged person count and a zone hint.
Head pitch uses the head3d.prior_forward calibration in the contract's sign:
positive looks down, 0 is level, negative looks up (CONTRACT.md).
"""
import math

from jev_semantics import NOSE, EYES, EARS, SHOULDERS, head_measures

PITCH_LEVEL_RATIO, PITCH_DEG_PER_RATIO = 0.45, 250.0  # head3d.prior_forward calibration


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _xyz(value):
    return [float(v) for v in value] if isinstance(value, list) and len(value) == 3 and all(_finite(v) for v in value) else None


def _mid(a, b):
    return [(x + y) / 2 for x, y in zip(a, b)] if a is not None and b is not None else None


def head_pitch_deg(raw_pose):
    """Pitch from the 2D nose/shoulder measure, clamped like head3d; None without it.
    Positive looks down (the contract's sign; head3d's own measure is positive up)."""
    try:
        _, _, pitch = head_measures(raw_pose)
    except Exception:
        return None
    if pitch is None:
        return None
    return -max(-60.0, min(30.0, (pitch - PITCH_LEVEL_RATIO) * PITCH_DEG_PER_RATIO))


def placed_person(approximate, raw_pose=None, *, slot=0):
    """One person_pose entry from a renderable approximate packet, or None."""
    if not isinstance(approximate, dict) or approximate.get('renderable') is not True:
        return None
    joints = {}
    for joint in (approximate.get('approximate_pose') or {}).get('joints') or []:
        point = _xyz(joint.get('position_m')) if isinstance(joint, dict) else None
        if point is not None and type(joint.get('id')) is int:
            joints[joint['id']] = point
    left, right = joints.get(SHOULDERS[0]), joints.get(SHOULDERS[1])
    head = joints.get(NOSE) or _mid(joints.get(EARS[0]), joints.get(EARS[1])) or _mid(joints.get(EYES[0]), joints.get(EYES[1]))
    if left is None and right is None and head is None:
        return None
    return {'track_id': 'slot-%d' % slot, 'shoulders_m': {'left': left, 'right': right}, 'head_m': head,
            'head_pitch_deg': head_pitch_deg(raw_pose), 'approximate': True}


def positions(approximate):
    """person_position entries from a renderable approximate packet: every lifted
    person's room-frame chest point (approximate_people), else the instance-0
    pose's shoulder midpoint or head. [] when nothing is placed."""
    if not isinstance(approximate, dict) or approximate.get('renderable') is not True:
        return []
    people = approximate.get('approximate_people')
    if isinstance(people, list):
        out = []
        for slot, item in enumerate(people[:8]):
            point = _xyz(item.get('position_m')) if isinstance(item, dict) else None
            if point is not None:
                out.append({'track_id': 'slot-%d' % slot, 'position_m': point, 'approximate': True})
        return out
    person = placed_person(approximate)
    if person is None:
        return []
    shoulders = person['shoulders_m']
    point = _mid(shoulders['left'], shoulders['right']) or shoulders['left'] or shoulders['right'] or person['head_m']
    return [{'track_id': 'slot-0', 'position_m': point, 'approximate': True}] if point else []


def person_count(result):
    """People in one model result after the duplicate-box merge, or None."""
    from apartment_desk_preview import distinct_people
    try:
        return len(distinct_people(result))
    except (AttributeError, KeyError, TypeError):
        return None


def _inside(point, polygon):
    x, y, inside = point[0], point[1], False
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def zone_hint(person, zones):
    """'sofa' or 'desk' when the shoulder midpoint (else head), projected along
    the zone's normal, falls inside exactly one checked zone; None otherwise."""
    if not person or not zones:
        return None
    shoulders = person['shoulders_m']
    point = _mid(shoulders['left'], shoulders['right']) or shoulders['left'] or shoulders['right'] or person['head_m']
    if point is None:
        return None
    hits = [name for name, zone in sorted(zones.items()) if _inside(_in_plane(point, zone), zone['polygon'])]
    return hits[0] if len(hits) == 1 else None


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _unit(a):
    length = math.sqrt(_dot(a, a))
    return [x / length for x in a]


def _in_plane(point, zone):
    d = [p - o for p, o in zip(point, zone['origin'])]
    return [_dot(d, zone['u']), _dot(d, zone['v'])]


def _plane_zone(vertices, normal):
    """A zone polygon in its own plane: in-plane axes u, v and 2D vertices."""
    axis = min(([1., 0., 0.], [0., 1., 0.], [0., 0., 1.]), key=lambda a: abs(_dot(a, normal)))
    u = _unit(_cross(normal, axis))
    v = _cross(normal, u)
    zone = {'origin': vertices[0], 'u': u, 'v': v}
    zone['polygon'] = [_in_plane(p, zone) for p in vertices]
    return zone


EXPORT_SCHEMA = 'lighting-brain-geometry-1'  # the single private geometry file shared with the brain (AL-4)
RETIRED_SCHEMAS = ('lighting-brain-targets-1',)


def check_zones(value, *, home=None, room=None):
    """Checked zones from the private lighting-brain-geometry-1 file (zones.sofa
    and zones.desk: 3D zone vertices and normal in the room frame), or the
    inline form {'sofa'|'desk': [[x, y], ...]} on the z-up floor plane. None
    means no zones. The retired lighting-brain-targets-1 export is refused."""
    if value is None:
        return {}
    bad = ValueError('activity loop zones must be the lighting-brain-geometry-1 file or sofa/desk polygons')
    if not isinstance(value, dict):
        raise bad
    if 'schema' in value:
        if value['schema'] in RETIRED_SCHEMAS:
            raise ValueError('activity loop zones: %s is retired; point zones at the lighting-brain-geometry-1 file'
                             % value['schema'])
        targets = value.get('zones')
        if (value['schema'] != EXPORT_SCHEMA or not isinstance(targets, dict)
                or not set(targets) <= {'sofa', 'desk'} or (home is not None and value.get('home') != home)
                or (room is not None and value.get('room') != room)):
            raise bad
        zones = {}
        for name, target in targets.items():
            raw = target.get('zone_vertices_m') if isinstance(target, dict) else None
            vertices = [_xyz(v) for v in raw] if isinstance(raw, list) and 3 <= len(raw) <= 64 else None
            normal = _xyz(target.get('zone_normal_unit')) if isinstance(target, dict) else None
            if (vertices is None or None in vertices or any(abs(c) > 100 for p in vertices for c in p)
                    or normal is None or abs(math.sqrt(_dot(normal, normal)) - 1) > .01):
                raise bad
            zones[name] = _plane_zone(vertices, normal)
        return zones
    if (not set(value) <= {'sofa', 'desk'}
            or any(not isinstance(p, list) or not 3 <= len(p) <= 64
                   or any(not isinstance(v, list) or len(v) != 2 or not all(_finite(c) and abs(c) <= 100 for c in v) for v in p)
                   for p in value.values())):
        raise bad
    floor = {'origin': [0., 0., 0.], 'u': [1., 0., 0.], 'v': [0., 1., 0.]}
    return {k: dict(floor, polygon=[[float(x), float(y)] for x, y in v]) for k, v in value.items()}
