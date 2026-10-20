"""Explicit config9 -> config12 upgrade; no I/O authority or qualification.

Inputs must already contain the commissioned sources and independently prepared
rule/clock/association records. This binds their exact bytes to the sole room
worker. It does not observe a person or mint passed qualification claims.
"""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess

from journal import digest
from live_pc_support import validate_pc_support_policy

ROOT = Path(__file__).resolve().parents[2]


def upgrade_configuration(configuration, worker_config, pc_support_policy):
    result, worker = deepcopy(configuration), deepcopy(worker_config)
    config = result.get('live_config', result)
    if (config.get('schema') != 'home-live-ingress-config-9' or config.get('version') != 9
            or not worker.get('room_policy') or worker.get('delivery_policy', {}).get('version') != 4
            or 'manual_policy' in worker or 'clarification_policy' in worker):
        raise ValueError('explicit_pc_room9_successor_base_required')
    validate_pc_support_policy(pc_support_policy, config['manifest']['sources'])
    worker['pc_support_policy'] = deepcopy(pc_support_policy)
    worker['source_pins'] = {name: digest(source) for name, source in config['manifest']['sources'].items()}
    node = shutil.which('node')
    if not node:
        raise ValueError('existing_node_runtime_required')
    script = "const fs=require('node:fs');process.stdout.write(JSON.stringify(require('./tools/home-live-context-worker.cjs').describe(JSON.parse(fs.readFileSync(0,'utf8')))))"
    run = subprocess.run([node, '--max-old-space-size=384', '-e', script], cwd=ROOT,
                         input=json.dumps(worker, allow_nan=False), capture_output=True, text=True, timeout=12)
    if run.returncode or len(run.stdout) > 65536:
        raise ValueError('pc_support_worker_description_refused:' + run.stderr[:1000])
    description = json.loads(run.stdout)
    manifest = config['manifest']
    manifest.update(schema='home-live-ingress-manifest-12', version=12,
                    pc_support_interface_digest=description['pc_support_interface_digest'])
    manifest['worker'].update(config_digest=description['config_digest'], source_pins=deepcopy(worker['source_pins']),
                              engagement_policy=deepcopy(worker['engagement_policy']),
                              pc_support_policy=deepcopy(pc_support_policy),
                              pc_support_schema_pins=description['pc_support_schema_pins'])
    config.update(schema='home-live-ingress-config-12', version=12)
    config['manifest_ref'] = {'id': 'pc-room-manifest:' + digest(manifest)[:48], 'digest': digest(manifest)}
    worker.update(manifest_ref=deepcopy(config['manifest_ref']), live_manifest=deepcopy(manifest))
    return result, worker
