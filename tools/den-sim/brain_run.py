"""The real lighting brain on a scripted reader, for the den simulation.

The same wiring as tools/fixtures/den-reading-coordinator.py: the real
ActivityLoop, BrainPublisher and Brain under a fake clock, writing one JSON
timeline to stdout. Only the deep stack is fake: the joints and the activity
label are typed in here, no camera or model runs, and no socket can open.

Reads {"geometry", "scenario", "seat_m", "facing_xy"} as JSON on stdin.
"""
import dataclasses
import json
import math
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
from lighting_brain.brain import Brain  # noqa: E402
from lighting_brain.geometry import load_geometry  # noqa: E402
from lighting_brain.settings import Settings  # noqa: E402

START = 1792411200.0  # Noon UTC, so the night rule stays out of this.
OFFSETS = (0, 1, 2, 3)  # Reading must hold 3 s before the brain lights a spot.
LABELS = {'reading_on_couch': 'reading', 'not_reading': 'phone_use'}
SCRIPTED = 'SIMULATION-scripted'
SHOULDER_ABOVE_SEAT_M, HEAD_ABOVE_SHOULDER_M, SHOULDER_HALF_M, HEAD_PITCH_DEG = .5, .23, .18, 20.


class Clock:
    value = START

    def __call__(self):
        return self.value


class Deep:
    """The fake deep stack: it hands over whatever the script typed in."""
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


def scripted_reader(seat, facing_xy):
    """Person A, sitting at the seat. Without a camera the brain takes facing from
    the shoulder labels (facing = z x (right - left)), so the labels are set for it."""
    length = math.hypot(*facing_xy)
    fx, fy = facing_xy[0] / length, facing_xy[1] / length
    x, y, z = seat[0], seat[1], seat[2] + SHOULDER_ABOVE_SEAT_M
    dx, dy = fy * SHOULDER_HALF_M, -fx * SHOULDER_HALF_M
    return dict(track_id='person-A', shoulders_m={'left': [x - dx, y - dy, z], 'right': [x + dx, y + dy, z]},
                head_m=[x, y, z + HEAD_ABOVE_SHOULDER_M], head_pitch_deg=HEAD_PITCH_DEG, approximate=True)


def row(phase, brain, clock):
    evaluation = brain.evaluation
    facts = evaluation.facts
    return dict(phase=phase, offset_s=clock.value - START, state=brain.state(),
                refusal=evaluation.rule_whys.get('reading_spot_off'), reading_why=facts.reading_why,
                spot_plan=dataclasses.asdict(evaluation.spot_plan) if evaluation.spot_plan else None,
                reader=dataclasses.asdict(facts.reader) if facts.reader else None)


def main():
    request = json.load(sys.stdin)
    label = LABELS[request['scenario']]
    clock, deep, published = Clock(), Deep(), []
    brain = Brain(load_geometry(request['geometry']), Settings(timezone='UTC'), clock)
    rows = [row('empty', brain, clock)]

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
    person = scripted_reader(request['seat_m'], request['facing_xy'])
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
    print(json.dumps(dict(scenario=request['scenario'], label=label, scripted_reader=person, rows=rows,
                          events=sum(len(body['events']) for body in published),
                          schemas=sorted({body['schema'] for body in published}),
                          camera_calls=0, model_calls=0), allow_nan=False))


if __name__ == '__main__':
    main()
