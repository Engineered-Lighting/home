"""Explicit config9 upgrade for the same commissioned producer and sole worker.

No I/O except a bounded local worker-description process. Does not grant device
authority, manufacture capture qualification or modify an input configuration.
"""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[2]


from journal import digest as _digest


def upgrade_configuration(configuration,worker_config,room_policy,*,clarification_policy=None,manual_policy=None):
    """Return new acquisition/ingress and worker configurations; keep source pins.

    Callers build capture/geometry policies with the same explicitly modeled room
    registration before this upgrade. Secret grants are separately re-pinned by
    their existing producer owner after the immutable manifest is computed.
    """
    result,worker=deepcopy(configuration),deepcopy(worker_config)
    config=result.get('live_config',result)
    if (config.get('schema')!='home-live-ingress-config-7' or config.get('version')!=7
            or worker.get('delivery_policy',{}).get('version')!=4
            or room_policy.get('registration')!=worker['delivery_policy'].get('registration')):
        raise ValueError('explicit_room_successor_base_required')
    for source in config['manifest']['sources'].values():
        if source.get('registration') is not None and source['registration']!=room_policy['registration']:
            raise ValueError('room_source_registration_mismatch')
    tasks=[fixture.get('task_policy') for fixture in room_policy.get('fixtures',[]) if fixture.get('task_policy') is not None]
    if tasks:
        # This explicit new-profile commissioning binds the existing inspection
        # procedure to the supplied assessed room. It does not perform an
        # inspection or clear the initial/unresolved person hazards.
        if len(tasks)!=4 or any(task.get('room')!=tasks[0].get('room') for task in tasks):
            raise ValueError('same_assessed_room_for_all_task_candidates_required')
        worker['delivery_policy']=deepcopy(tasks[0])
        clearance=worker.get('lifecycle_policy',{}).get('operator_clearance')
        if clearance is not None:
            clearance['room_assessment_ref']=deepcopy(tasks[0]['room']['assessment_ref'])
    worker['room_policy']=deepcopy(room_policy)
    if clarification_policy is not None:
        worker['clarification_policy']=deepcopy(clarification_policy)
    if manual_policy is not None:
        if clarification_policy is None:
            raise ValueError('manual_requires_combined_clarification_profile')
        worker['manual_policy']=deepcopy(manual_policy)
    worker['source_pins']={name:_digest(source) for name,source in config['manifest']['sources'].items()}
    node=shutil.which('node')
    if not node:raise ValueError('existing_node_runtime_required')
    script="const fs=require('node:fs');process.stdout.write(JSON.stringify(require('./tools/home-live-context-worker.cjs').describe(JSON.parse(fs.readFileSync(0,'utf8')))))"
    run=subprocess.run([node,'--max-old-space-size=384','-e',script],cwd=ROOT,input=json.dumps(worker,allow_nan=False),
                       capture_output=True,text=True,timeout=12)
    if run.returncode or len(run.stdout)>65536:
        raise ValueError('room_worker_description_refused')
    description=json.loads(run.stdout)
    manifest=config['manifest']
    version=11 if manual_policy is not None else 10 if clarification_policy is not None else 9
    manifest.update(schema='home-live-ingress-manifest-'+str(version),version=version,room_interface_digest=description['room_interface_digest'])
    manifest['worker'].update(config_digest=description['config_digest'],source_pins=deepcopy(worker['source_pins']),
                              room_policy=deepcopy(room_policy),room_schema_pins=description['room_schema_pins'],
                              delivery_policy=deepcopy(worker['delivery_policy']),lifecycle_policy=deepcopy(worker['lifecycle_policy']))
    if clarification_policy is not None:
        manifest['room_clarification_interface_digest']=description['room_clarification_interface_digest']
        manifest['worker'].update(clarification_policy=deepcopy(clarification_policy),
                                  room_clarification_schema_pins=description['room_clarification_schema_pins'])
    if manual_policy is not None:
        manifest['room_manual_interface_digest']=description['room_manual_interface_digest']
        manifest['worker'].update(manual_policy=deepcopy(manual_policy),
                                 room_manual_schema_pins=description['room_manual_schema_pins'])
    config.update(schema='home-live-ingress-config-'+str(version),version=version)
    config['manifest_ref']={'id':('room-manual-manifest:' if manual_policy is not None else 'room-clarification-manifest:' if clarification_policy is not None else 'room-manifest:')+_digest(manifest)[:48],'digest':_digest(manifest)}
    worker.update(manifest_ref=deepcopy(config['manifest_ref']),live_manifest=deepcopy(manifest))
    return result,worker
