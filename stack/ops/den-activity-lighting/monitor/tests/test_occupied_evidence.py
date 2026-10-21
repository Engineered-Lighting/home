"""A running recording/Qwen cannot substitute for fresh occupied evidence."""
import pytest

from den_ops import soak
from test_qwen_soak import occupied_rows, PROFILE
from test_evidence_admission import prepare_pair


def rows_with_occupancy():
    rows=occupied_rows()
    for row in rows:
        row['loop'].update(state='running',occupancy_age_s=0.)
    return rows


@pytest.mark.parametrize('age',[None,True,-1,91,float('nan'),float('inf'),'0'])
def test_occupied_recording_needs_fresh_readable_occupancy(age):
    rows=rows_with_occupancy()
    for row in rows:row['loop']['occupancy_age_s']=age
    assert soak._roll_exposure({'warmup_s':0,'loop':{'qwen_profile':PROFILE}},rows)['result']!='pass'
    assert soak._occupied_window({'host':'victoria-jetson','warmup_s':0,'loop':{'qwen_profile':PROFILE}},rows) is None


@pytest.mark.parametrize('state',[None,'paused','stopped','unknown'])
def test_recording_left_active_cannot_hide_a_stopped_loop(state):
    rows=rows_with_occupancy()
    for row in rows:row['loop']['state']=state
    assert soak._roll_exposure({'warmup_s':0,'loop':{'qwen_profile':PROFILE}},rows)['result']!='pass'


def test_la_occupied_window_cannot_span_a_missing_sample():
    rows=[{'at':i*60,'brain':{'present':True,'occupancy_age_s':0}} for i in range(80)]
    rows.pop(40)
    assert soak._occupied_window({'host':'la-host','warmup_s':0},rows) is None


def test_jetson_loaded_hour_cannot_span_a_missing_sample():
    rows=rows_with_occupancy();rows.pop(40)
    assert soak._roll_exposure({'warmup_s':0,'loop':{'qwen_profile':PROFILE}},rows)['result']!='pass'


@pytest.mark.parametrize('age',[None,True,-1,91,float('nan'),float('inf'),'0'])
def test_la_occupied_window_needs_fresh_occupancy(age):
    rows=[{'at':i*60,'brain':{'present':True,'occupancy_age_s':age}} for i in range(80)]
    assert soak._occupied_window({'host':'la-host','warmup_s':0},rows) is None


@pytest.mark.parametrize('field,value',[('state','unknown'),('state',None),
    ('last_occupied_age_s',True),('last_occupied_age_s',-1),('last_occupied_age_s',float('nan'))])
def test_real_sampler_refuses_missing_or_malformed_loop_evidence(tmp_path,monkeypatch,field,value):
    pairs,_=prepare_pair(tmp_path,monkeypatch)
    cfg,monitor,host,_=pairs['jetson'];key=('GET',cfg['loop']['status_url'])
    status=host.responses[key](None)[1];status[field]=value
    host.responses[key]=(200,status)
    sample=soak.sample_once(cfg,monitor,host,{'journal':{}})
    assert not sample['evidence']['ok']
    assert any(error['source']=='loop' for error in sample['evidence']['errors'])


def test_real_sampler_carries_original_loop_occupancy_age(tmp_path,monkeypatch):
    pairs,_=prepare_pair(tmp_path,monkeypatch)
    cfg,monitor,host,_=pairs['jetson'];key=('GET',cfg['loop']['status_url'])
    status=host.responses[key](None)[1];status['last_occupied_age_s']=12.5
    host.responses[key]=(200,status)
    sample=soak.sample_once(cfg,monitor,host,{'journal':{}})
    assert sample['loop']['occupancy_age_s']==12.5
    assert sample['loop']['state']=='running'
