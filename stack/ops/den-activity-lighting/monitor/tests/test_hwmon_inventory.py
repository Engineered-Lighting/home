"""Aggregate temperatures cannot hide the loss of a commissioned input."""
from pathlib import Path

import pytest

from den_ops import monitor as mon
from test_evidence_admission import prepare_pair

INPUTS=[dict(path='hwmon0/temp1_input',chip='k10temp',label='Tctl'),
    dict(path='hwmon0/temp3_input',chip='k10temp',label='Tccd1'),
    dict(path='hwmon0/temp4_input',chip='k10temp',label='Tccd2'),
    dict(path='hwmon1/temp1_input',chip='nvme',label='Composite'),
    dict(path='hwmon1/temp2_input',chip='nvme',label='Sensor 1'),
    dict(path='hwmon2/temp1_input',chip='nvme',label='Composite')]


@pytest.mark.parametrize('missing',['hwmon0/temp3_input','hwmon1/temp1_input','hwmon2/temp1_input'])
def test_one_required_input_missing_is_not_hidden_by_another(tmp_path,monkeypatch,missing):
    pairs,_=prepare_pair(tmp_path,monkeypatch);_,cfg,host,_=pairs['la']
    cfg['sources']['hwmon']['required_inputs']=INPUTS
    (Path(cfg['sources']['hwmon']['root'])/missing).unlink()
    _,notices=mon.collect_signals(cfg,host)
    assert any(n.get('evidence_error') and 'hwmon' in n.get('signal','') for n in notices)


def test_required_hwmon_rules_need_an_explicit_inventory(tmp_path,monkeypatch):
    pairs,_=prepare_pair(tmp_path,monkeypatch);_,cfg,host,_=pairs['la']
    cfg['sources']['hwmon'].pop('required_inputs',None)
    _,notices=mon.collect_signals(cfg,host)
    assert any(n.get('evidence_error') and 'hwmon' in n.get('signal','') for n in notices)


@pytest.mark.parametrize('file,value',[('hwmon0/name','other-chip'),
    ('hwmon0/temp3_label','other-label'),('hwmon0/temp3_input','-55000'),('hwmon0/temp3_input','nan')])
def test_required_input_identity_and_validity_are_checked(tmp_path,monkeypatch,file,value):
    pairs,_=prepare_pair(tmp_path,monkeypatch);_,cfg,host,_=pairs['la']
    cfg['sources']['hwmon']['required_inputs']=INPUTS
    (Path(cfg['sources']['hwmon']['root'])/file).write_text(value)
    _,notices=mon.collect_signals(cfg,host)
    assert any(n.get('evidence_error') and 'hwmon' in n.get('signal','') for n in notices)


@pytest.mark.parametrize('path',['../temp1_input','/sys/class/hwmon/hwmon0/temp1_input','hwmon0/../temp1_input'])
def test_hwmon_inventory_refuses_path_escape(tmp_path,monkeypatch,path):
    pairs,_=prepare_pair(tmp_path,monkeypatch);_,cfg,host,_=pairs['la']
    cfg['sources']['hwmon']['required_inputs']=[dict(path=path,chip='k10temp',label='Tctl')]
    with pytest.raises(ValueError):mon.validate_config(cfg)
