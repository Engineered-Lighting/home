import copy
import json

import pytest

from den_ops import deployment, soak
from test_soak import manifest_fixture


def la_only_manifest():
    doc = manifest_fixture()
    doc['execution_profile'] = deployment.LA_ONLY_PROFILE
    jetson = doc['hosts']['victoria-jetson']
    jetson['units'] = [u for u in jetson['units'] if u['name'] != 'lab-jetson-detector-trial.service']
    jetson['components'].pop('detector')
    jetson['artifacts'] = [a for a in jetson['artifacts'] if a['component'] != 'detector']
    la = doc['hosts']['la-host']
    la['units'].append(dict(name='lab-la-detector-trial.service', scope='user', working_directory='/worker', exec_start='python worker.py'))
    la['components']['detector'] = 'actual-la-worker'
    la['artifacts'].append(dict(path='/worker/worker.py', sha256='a'*64, component='detector'))
    return doc


def test_la_only_inventory_requires_the_actual_la_worker_and_artifacts():
    doc = la_only_manifest()
    deployment.validate(doc)
    for key in ('units', 'components', 'artifacts'):
        broken = copy.deepcopy(doc)
        if key == 'units':
            broken['hosts']['la-host'][key].pop()
        elif key == 'components':
            broken['hosts']['la-host'][key].pop('detector')
        else:
            broken['hosts']['la-host'][key].pop()
        with pytest.raises(ValueError):
            deployment.validate(broken)


def test_execution_profile_is_explicit_and_unknown_profiles_fail_closed():
    doc = la_only_manifest()
    doc.pop('execution_profile')
    with pytest.raises(ValueError):
        deployment.validate(doc)
    with pytest.raises(ValueError):
        deployment.required_inventory('unknown')


def test_soak_config_uses_the_same_exact_unit_scope_profile(tmp_path):
    doc = la_only_manifest()
    cfg = dict(schema='den-ops-soak-1', host='la-host', execution_profile=deployment.LA_ONLY_PROFILE,
               units=doc['hosts']['la-host']['units'])
    path = tmp_path / 'soak.json'
    path.write_text(json.dumps(cfg))
    assert soak.load_soak_config(path)['execution_profile'] == deployment.LA_ONLY_PROFILE
    cfg['units'][-1]['scope'] = 'system'
    path.write_text(json.dumps(cfg))
    with pytest.raises(ValueError, match='required deployed units'):
        soak.load_soak_config(path)
