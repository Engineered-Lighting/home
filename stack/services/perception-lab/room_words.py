"""Closed word vocabularies of a room-facts-1 record and its projection.

Kept apart from room_facts (the controller stage: depth, maps, ledger) so the
workers' jev_evidence can validate the words without importing that stage.
No I/O and no imports."""

RULE = 'room-facts-1'
RECORD_SCHEMA = 'room-facts-1'
EVENT_KIND = 'room.facts'
MAX_SEGMENTS = 4

# --- Vocabularies (the only words a room.facts record or the wire may carry) ---
PEOPLE = ('none', 'one', 'multiple')
POSITIONS = ('desk_chair', 'armchair', 'sofa', 'bed', 'floor', 'doorway', 'standing_area', 'elsewhere_in_room',
             'unknown')
# How the wire's now.position was placed: the 3D room map, the image-edge
# stopgap rule (desk-seat-1, only without a current 3D fact), or neither.
POSITION_METHODS = ('room_map_3d', 'image_edge_rule', 'none')
LOOK_WORDS = ('monitor', 'tv', 'window', 'bookshelf', 'painting', 'door', 'fireplace', 'desk_surface', 'keyboard',
              'table', 'other_object', 'lap_or_hands', 'elsewhere', 'unknown')
STABILITY = ('steady', 'shifting', 'insufficient', 'unknown')
HAND_WORDS = ('keyboard', 'mouse', 'desk_surface', 'table_surface', 'bookshelf', 'book', 'phone', 'cup', 'bottle',
              'remote', 'laptop', 'bowl')
POSTURES = ('sitting', 'standing', 'lying', 'unknown')
MOTIONS = ('still', 'small', 'moving', 'unknown')
ORDERS = ('earliest', 'earlier', 'just_before', 'current')
SPANS = ('under_10_s', 'about_20_s', 'about_40_s', 'about_60_s', 'over_60_s')
HISTORY_WINDOW = 'last_60_s'


_WORD_FIELDS = {'people': PEOPLE, 'position': POSITIONS, 'looking_at': LOOK_WORDS, 'looking_at_stability': STABILITY,
                'posture': POSTURES, 'motion': MOTIONS}
_SEGMENT_FIELDS = {'order': ORDERS, 'position': POSITIONS, 'looking_at': LOOK_WORDS, 'span': SPANS}


def projection(value):
    """An exact copy of a room-facts-1 word record, else None: known keys,
    closed vocabularies, bounded lists; no number, null or free text."""
    if (not isinstance(value, dict) or set(value) != {'schema', 'hands_near', 'history', *_WORD_FIELDS}
            or value['schema'] != RECORD_SCHEMA):
        return None
    if any(type(value[k]) is not str or value[k] not in words for k, words in _WORD_FIELDS.items()):
        return None
    hands = value['hands_near']
    if (type(hands) is not list or len(hands) > len(HAND_WORDS) or len(set(map(str, hands))) != len(hands)
            or any(type(h) is not str or h not in HAND_WORDS for h in hands)):
        return None
    history = value['history']
    if (not isinstance(history, dict) or set(history) != {'window', 'segments', 'earlier_segments_omitted'}
            or history['window'] != HISTORY_WINDOW or type(history['earlier_segments_omitted']) is not bool
            or type(history['segments']) is not list or len(history['segments']) > MAX_SEGMENTS):
        return None
    for segment in history['segments']:
        if (not isinstance(segment, dict) or set(segment) != set(_SEGMENT_FIELDS)
                or any(type(segment[k]) is not str or segment[k] not in words for k, words in _SEGMENT_FIELDS.items())):
            return None
    return {**{k: value[k] for k in ('schema', *_WORD_FIELDS)}, 'hands_near': list(hands),
            'history': {'window': history['window'], 'earlier_segments_omitted': history['earlier_segments_omitted'],
                        'segments': [dict(s) for s in history['segments']]}}
