"""Explicit config12 -> config13 assessed-room upgrade; no qualification grant.

The caller supplies independently commissioned policy2 and literal opt-in.
Existing sources, PC originals, clocks and the sole worker authority remain.
Only the existing bounded local Node description process performs I/O.
"""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess

from journal import digest
from live_ingress_validation import validate_config
from room_delivery import room_policy_view
from schema_registry import SchemaRegistry

ROOT = Path(__file__).resolve().parents[2]


def upgrade_configuration(configuration, worker_config, room_policy, *, allow_assessed_subset=False):
    result, worker = deepcopy(configuration), deepcopy(worker_config)
    config = result.get('live_config', result)
    if (allow_assessed_subset is not True or config.get('schema') != 'home-live-ingress-config-12'
            or type(config.get('version')) is not int or config['version'] != 12
            or not isinstance(room_policy, dict) or room_policy.get('schema') != 'home-live-room-policy-2'
            or type(room_policy.get('version')) is not int or room_policy['version'] != 2
            or worker.get('profile') != 'home-live-shadow-1'
            or 'room_subset_mode' in worker or 'manual_policy' in worker or 'clarification_policy' in worker):
        raise ValueError('explicit_assessed_subset_config12_base_and_opt_in_required')
    registry = SchemaRegistry((), profile='home-live-shadow-1')
    validate_config(config, registry)
    manifest = config['manifest']
    base = manifest['worker']
    source_pins = {name: digest(source) for name, source in manifest['sources'].items()}
    raw_fields = ('worker_id', 'producer_id', 'producer_epoch', 'revision', 'max_output_age_s',
                  'semantic_interval_s', 'engagement_policy', 'scene_policy', 'delivery_policy',
                  'lifecycle_policy', 'room_policy', 'pc_support_policy')
    if (worker.get('identity') != manifest['identity'] or worker.get('live_manifest') != manifest
            or worker.get('manifest_ref') != config['manifest_ref']
            or worker.get('source_pins') != source_pins or base['source_pins'] != source_pins
            or any(worker.get(key) != base[key] for key in raw_fields)
            or worker.get('delivery_policy', {}).get('version') != 4
            or worker.get('pc_support_policy', {}).get('schema') != 'home-live-pc-support-policy-1'
            or 'manual_policy' in base or 'clarification_policy' in base):
        raise ValueError('exact_config12_worker_manifest_and_PC_base_required')
    registry.validate(room_policy)
    view = room_policy_view(room_policy, manifest['sources'], worker['scene_policy'], allow_assessed_subset=True)
    if room_policy['registration'] != worker['delivery_policy']['registration'] or any(
            source.get('registration') is not None and source['registration'] != room_policy['registration']
            for source in manifest['sources'].values()):
        raise ValueError('assessed_subset_source_registration_mismatch')
    tasks = [fixture['task_policy'] for fixture in view['fixtures'] if fixture['task_policy'] is not None]
    if tasks:
        # Bind the same existing inspection procedure to the supplied assessed
        # room, exactly as the predecessor builder does. This clears no hazard.
        worker['delivery_policy'] = deepcopy(tasks[0])
        clearance = worker['lifecycle_policy'].get('operator_clearance')
        if clearance is not None:
            clearance['room_assessment_ref'] = deepcopy(tasks[0]['room']['assessment_ref'])
    worker['room_policy'] = deepcopy(room_policy)
    worker['room_subset_mode'] = True
    node = shutil.which('node')
    if not node:
        raise ValueError('existing_node_runtime_required')
    script = "const fs=require('node:fs');process.stdout.write(JSON.stringify(require('./tools/home-live-context-worker.cjs').describe(JSON.parse(fs.readFileSync(0,'utf8')))))"
    run = subprocess.run([node, '--max-old-space-size=384', '-e', script], cwd=ROOT,
        input=json.dumps(worker, allow_nan=False), capture_output=True, text=True, timeout=12)
    if run.returncode or len(run.stdout) > 65536:
        raise ValueError('room_subset_worker_description_refused')
    description = json.loads(run.stdout)
    if description.get('room_subset_mode') is not True:
        raise ValueError('literal_assessed_subset_description_required')
    manifest.update(schema='home-live-ingress-manifest-13', version=13,
                    room_subset_interface_digest=description['room_subset_interface_digest'])
    manifest['worker'].update(config_digest=description['config_digest'], source_pins=deepcopy(source_pins),
        delivery_policy=deepcopy(worker['delivery_policy']), lifecycle_policy=deepcopy(worker['lifecycle_policy']),
        room_policy=deepcopy(room_policy), room_subset_mode=True,
        room_subset_schema_pins=deepcopy(description['room_subset_schema_pins']))
    config.update(schema='home-live-ingress-config-13', version=13)
    config['manifest_ref'] = {'id': 'room-subset-manifest:' + digest(manifest)[:48], 'digest': digest(manifest)}
    validate_config(config, registry)
    worker.update(manifest_ref=deepcopy(config['manifest_ref']), live_manifest=deepcopy(manifest))
    return result, worker
