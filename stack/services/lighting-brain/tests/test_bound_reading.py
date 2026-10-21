"""Version migration and adversarial source/reader retirement, without hosts."""
from copy import deepcopy

import pytest

from conftest import activity, batch, healthy, occupancy, pose, NOON
from lighting_brain.inputs import EventError


def feed(brain, clock, at, *events):
    clock.set(at)
    return brain.ingest('victoria', batch(*events))


def qualify(brain, clock):
    feed(brain, clock, NOON, occupancy(NOON), activity(NOON), pose(NOON), *healthy(NOON))
    feed(brain, clock, NOON + 4, activity(NOON + 4), pose(NOON + 4))
    assert brain.state()['mode'] == 'reading'


def legacy(event):
    return {k: v for k, v in event.items() if k not in ('evidence_version', 'binding', 'valid_until')}


def test_old_producer_is_ambient_only_and_cannot_smuggle_bound_fields(brain, clock):
    old = [occupancy(NOON), legacy(activity(NOON)), legacy(pose(NOON)), *healthy(NOON)]
    brain.ingest('victoria', dict(schema='activity-lighting-events-1', events=old))
    clock.tick(4); brain.tick()
    assert brain.state()['mode'] == 'ambient'
    assert not any(f['spot']['on'] for f in brain.state()['fixtures'])
    with pytest.raises(EventError):
        brain.ingest('victoria', dict(schema='activity-lighting-events-1', events=[activity(NOON + 4)]))


def test_original_source_expiry_has_no_ten_second_outage_grace(brain, clock):
    qualify(brain, clock)
    answer = activity(NOON + 5, ttl=1)
    feed(brain, clock, NOON + 5, answer, pose(NOON + 5))
    clock.set(NOON + 6); brain.tick()
    assert brain.state()['mode'] == 'ambient'
    assert not brain.state()['reading_evidence']['bound']
    late = activity(NOON + 4, ttl=1)
    assert feed(brain, clock, NOON + 6, late)['ignored'][0]['reason'] == 'expired'


@pytest.mark.parametrize('changed', ['reader_epoch', 'capture_epoch', 'session_id', 'engine_epoch', 'generation'])
def test_changed_source_or_reader_resets_the_hold(brain, clock, changed):
    qualify(brain, clock)
    new_pose = pose(NOON + 5)
    new_pose['binding'][changed] = 2 if changed == 'generation' else 'replacement'
    feed(brain, clock, NOON + 5, new_pose)
    assert brain.state()['mode'] == 'ambient'
    assert brain.inputs.reading is None
    # A delayed old-subject answer cannot regain reading on the new pose.
    feed(brain, clock, NOON + 5.5, activity(NOON + 5.5))
    assert brain.state()['mode'] == 'ambient'


def test_ambiguous_pose_and_abstention_retire_reading(brain, clock):
    qualify(brain, clock)
    ambiguous = pose(NOON + 5, people=[], count=2)
    feed(brain, clock, NOON + 5, ambiguous)
    assert brain.inputs.reading is None
    feed(brain, clock, NOON + 6, activity(NOON + 6), pose(NOON + 6))
    assert brain.state()['mode'] == 'ambient'  # a new hold is required
    feed(brain, clock, NOON + 7, activity(NOON + 7, label='insufficient_evidence'))
    assert brain.inputs.reading is None


def test_invalid_bound_batch_is_atomic(brain, clock):
    malformed = activity(NOON)
    malformed['valid_until'] = NOON + 1  # TTL would launder eleven more seconds
    with pytest.raises(EventError):
        brain.ingest('victoria', batch(occupancy(NOON), malformed))
    assert brain.inputs.occupancy is None


def test_diagnostics_correlate_without_changing_decision_on_evidence_refresh(brain, clock):
    qualify(brain, clock)
    before = deepcopy(brain.state())
    feed(brain, clock, NOON + 5, activity(NOON + 5), pose(NOON + 5))
    after = brain.state()
    assert after['decision_id'] == before['decision_id']
    assert after['reading_evidence']['activity']['source_frame_id'] != before['reading_evidence']['activity']['source_frame_id']
    assert after['inputs']['at_sofa'] is True
    assert brain.health()['reading_requires_bound_evidence'] is True
