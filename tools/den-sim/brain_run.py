"""The real lighting brain on what the pipeline produced, for the den simulation.

The same wiring as tools/fixtures/den-reading-coordinator.py: the real ActivityLoop, BrainPublisher and
Brain under a fake clock, writing one JSON timeline to stdout. Only the deep stack is fake: it hands over
the person and label chain.py's adapter made, with jev and deep_stack health asserted. No camera or model
runs, and no socket can open. The geometry copy has camera_m null, so facing comes from the shoulder
labels, and a refusal shows as no beam.

Reads JSON on stdin:
  {"geometry", "person", "label"}   one click: the brain's timeline
  {"geometry", "startup"}           page start: the empty room's row, and, when startup holds
                                    {"seat_m", "facing_unit", "spot_m"}, each fixture against the
                                    stated facing and the spot, through the brain's aim.py
"""
import dataclasses
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # Nothing is written beside the unchanged sources.


def refuse_network(event, _args):
    if event in ('socket.connect', 'socket.bind', 'socket.sendto', 'socket.getaddrinfo'):
        raise RuntimeError('the den simulation brain run has no network')


sys.addaudithook(refuse_network)

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'stack/services/perception-lab'), str(ROOT / 'stack/services/lighting-brain')]
from activity_loop import ActivityLoop  # noqa: E402
from brain_publisher import BrainPublisher  # noqa: E402
from lighting_brain import aim  # noqa: E402
from lighting_brain.brain import Brain  # noqa: E402
from lighting_brain.geometry import load_geometry  # noqa: E402
from lighting_brain.settings import Settings  # noqa: E402

START = 1792411200.0  # Noon UTC, so the night rule stays out of this.
OFFSETS = (0, 1, 2, 3)  # Reading must hold 3 s before the brain lights a spot.
SCRIPTED = 'SIMULATION-scripted'
SHOULDER_ABOVE_SEAT_M, HEAD_ABOVE_SHOULDER_M = .5, .23


class Clock:
    value = START

    def __call__(self):
        return self.value


class Deep:
    """The fake deep stack: it hands over what the adapter produced."""
    seen = {}

    def start(self):
        pass

    def stop(self, reason):
        pass

    def tick(self):
        pass

    def observe(self):
        return self.seen


def binding(at, number):
    return dict(schema='den-sole-reader-source-1', status='available', reason=None,
                session_id=SCRIPTED, generation=1, capture_epoch='SIMULATION-no-camera',
                engine_epoch='SIMULATION-no-engine', reader_epoch='SIMULATION-scripted-reader', interval_start=START,
                instance_id='SIMULATION-crop-%d' % number, source_frame_id='SIMULATION-frame-%d' % number,
                source_request_id='SIMULATION-request-%d' % number, source_event_id=number,
                observed_at=at, available_at=at, valid_until=at + 2.8)


def row(phase, brain, clock):
    evaluation = brain.evaluation
    facts = evaluation.facts
    return dict(phase=phase, offset_s=clock.value - START, state=brain.state(),
                refusal=evaluation.rule_whys.get('reading_spot_off'), reading_why=facts.reading_why,
                spot_plan=dataclasses.asdict(evaluation.spot_plan) if evaluation.spot_plan else None,
                reader=dataclasses.asdict(facts.reader) if facts.reader else None)


def startup_line(geometry, seat, facing, spot):
    """Each fixture against the stated facing and the spot, in words only: aim.py's own rule, on a reader
    seated at the mark's seat point facing the stated way, with the hand on the spot."""
    shoulder = (seat[0], seat[1], seat[2] + SHOULDER_ABOVE_SEAT_M)
    reader = aim.Reader(shoulder_mid=shoulder, facing=(facing[0], facing[1], 0.0), facing_source='stated',
                        head=(shoulder[0], shoulder[1], shoulder[2] + HEAD_ABOVE_SHOULDER_M), head_source='stated', hand=tuple(spot))
    settings = Settings(timezone='UTC')
    words = []
    for number, fixture in enumerate(geometry.fixtures, 1):
        candidate = aim.evaluate_fixture(fixture, reader, settings)
        words.append('F%d clear' % number if candidate.ok else 'F%d refused: %s' % (number, candidate.reason.split(':')[0].replace('_', ' ')))
    return words


def main():
    request = json.load(sys.stdin)
    geometry = load_geometry(request['geometry'])
    clock, deep, published = Clock(), Deep(), []
    brain = Brain(geometry, Settings(timezone='UTC'), clock)
    rows = [row('empty', brain, clock)]
    if 'startup' in request:
        stated = request['startup']
        print(json.dumps(dict(empty_row=rows[0], camera_m=geometry.camera_m,
                              startup=startup_line(geometry, stated['seat_m'], stated['facing_unit'], stated['spot_m']) if stated else None), allow_nan=False))
        return

    def post(raw):
        body = json.loads(raw)
        published.append(body)
        result = brain.ingest('victoria', body)
        assert not result['ignored'], result
        return 200

    publisher = BrainPublisher(origin='http://127.0.0.1', home='victoria', token=lambda: 'scripted-not-a-token',
                               post=post, clock=clock, monotonic=clock)
    loop = ActivityLoop(deep=deep, occupancy=lambda: 'on', publisher=publisher, clock=clock,
                        source=SCRIPTED + '/activity-loop')
    brain.ingest('victoria', dict(schema='activity-lighting-events-1', events=[
        dict(type='occupancy', home='victoria', room='den', source=SCRIPTED + '/occupancy', observed_at=START,
             sent_at=START, ttl_s=90, present=True, people_count=1)]))
    person, label = request['person'], request['label']
    for number, offset in enumerate(OFFSETS, 1):
        clock.value = START + offset
        source = binding(clock.value, number)
        deep.seen = dict(running=True, person_count=1, health={k: (True, None) for k in ('jev', 'deep_stack', 'camera')},
                         activity=dict(event_id=number, label=label, confidence=None, model=SCRIPTED, facts_key=SCRIPTED,
                                       observed_at=clock.value, binding=source, valid_until=source['valid_until']),
                         pose=dict(key=number, people=[person], observed_at=clock.value, binding=source,
                                   valid_until=source['valid_until']))
        loop.tick()
        rows.append(row('person_a', brain, clock))
    print(json.dumps(dict(label=label, person=person, rows=rows, camera_m=geometry.camera_m,
                          events=sum(len(body['events']) for body in published),
                          schemas=sorted({body['schema'] for body in published}),
                          camera_calls=0, model_calls=0), allow_nan=False))


if __name__ == '__main__':
    main()
