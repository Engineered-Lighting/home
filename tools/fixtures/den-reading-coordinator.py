"""Authored source observations through the real loop, publisher and brain.

No camera/model call, network connection, household geometry or device output.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'stack/services/perception-lab'), str(ROOT/'stack/services/lighting-brain')]
from activity_loop import ActivityLoop
from brain_publisher import BrainPublisher
from lighting_brain.brain import Brain
from lighting_brain.geometry import load_geometry
from lighting_brain.settings import Settings

START = 1792411200.0

class Clock:
    value = START
    def __call__(self): return self.value

class Deep:
    seen = {}
    def start(self): pass
    def stop(self, reason): pass
    def tick(self): pass
    def observe(self): return self.seen

def binding(at, number):
    return dict(schema='den-sole-reader-source-1', status='available', reason=None,
        session_id='authored-session', generation=1, capture_epoch='authored-camera',
        engine_epoch='authored-engine', reader_epoch='authored-reader', interval_start=START,
        instance_id='authored-crop-'+str(number), source_frame_id='frame-'+str(number),
        source_request_id='request-'+str(number), source_event_id=number,
        observed_at=at, available_at=at, valid_until=at+2.8)

def main():
    clock=Clock();deep=Deep();published=[]
    geometry=load_geometry(ROOT/'stack/services/lighting-brain/config/geometry.example.json')
    brain=Brain(geometry,Settings(timezone='UTC'),clock)
    def post(raw):
        body=json.loads(raw);published.append(body)
        result=brain.ingest('victoria',body)
        assert not result['ignored'],result
        return 200
    publisher=BrainPublisher(origin='http://127.0.0.1',home='victoria',token=lambda:'authored-not-a-token',
        post=post,clock=clock,monotonic=clock)
    loop=ActivityLoop(deep=deep,occupancy=lambda:'on',publisher=publisher,clock=clock)
    brain.ingest('victoria',dict(schema='activity-lighting-events-1',events=[
        dict(type='occupancy',home='victoria',room='den',source='authored-bridge',observed_at=START,
             sent_at=START,ttl_s=90,present=True,people_count=1)]))
    states=[]
    for number,offset in enumerate([0,1,2,3,4],1):
        clock.value=START+offset;b=binding(clock.value,number);dx=.22 if offset==4 else 0
        person=dict(track_id='anonymous-reader', shoulders_m={'left':[1.42+dx,.75,.95],'right':[1.78+dx,.75,.95]},
                    head_m=[1.6+dx,.78,1.18],head_pitch_deg=20.,approximate=True)
        deep.seen=dict(running=True,person_count=1,health={k:(True,None) for k in ['jev','deep_stack','camera']},
            activity=dict(event_id=number,label='reading',confidence=None,model='authored-jev',facts_key='authored',
                          observed_at=clock.value,binding=b,valid_until=b['valid_until']),
            pose=dict(key=number,people=[person],observed_at=clock.value,binding=b,valid_until=b['valid_until']))
        loop.tick();states.append({'phase':'moved' if offset==4 else 'reading' if offset==3 else 'qualifying','state':brain.state()})
    clock.value=START+7;brain.tick();states.append({'phase':'expired','state':brain.state()})
    # Requalify, then replace the current reader epoch: no prior hold may carry.
    for number,offset in enumerate([8,9,10,11],6):
        clock.value=START+offset;b=binding(clock.value,number)
        deep.seen['activity'].update(event_id=number,observed_at=clock.value,binding=b,valid_until=b['valid_until'])
        deep.seen['pose'].update(key=number,observed_at=clock.value,binding=b,valid_until=b['valid_until'])
        loop.tick()
    assert brain.state()['mode']=='reading'
    clock.value=START+12;b=binding(clock.value,10);b['reader_epoch']='replacement-reader'
    deep.seen['activity'].update(event_id=10,observed_at=clock.value,binding=b,valid_until=b['valid_until'])
    deep.seen['pose'].update(key=10,observed_at=clock.value,binding=b,valid_until=b['valid_until'])
    loop.tick();states.append({'phase':'reader_changed','state':brain.state()})
    assert all(body['schema']=='activity-lighting-events-2' for body in published)
    assert all(row['state']['mode']=='ambient' for row in states if row['phase'] in ['qualifying','expired','reader_changed'])
    assert [row['state']['mode'] for row in states if row['phase'] in ['reading','moved']]==['reading','reading']
    print(json.dumps({'authored':True,'camera_calls':0,'model_calls':0,'events':sum(len(x['events']) for x in published),
                      'schemas':sorted({x['schema'] for x in published}),'states':states}))

if __name__=='__main__':main()
