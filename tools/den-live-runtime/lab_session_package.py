"""Prepare one immutable incumbent-Lab Den package; never activate it.

Run on the target platform so existing absolute-path checks retain their meaning.
The trusted exporting owners supply inputs.json; this is not an evidence collector
or a substitute for that still-required export workflow. No credentials are read.

Entry: python -P tools/den-live-runtime/lab_session_package.py prepare
       --inputs ABS_OWNER_EXPORT_DIR --output ABS_NEW_PACKAGE_DIR
The safe-path flag avoids this directory's incumbent operator.py shadowing the
Python standard library. Preparation never changes controller provisioning.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import math
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
for _directory in (ROOT / 'stack/services/home-context', ROOT / 'stack/services/perception-lab'):
    if str(_directory) not in sys.path:
        sys.path.append(str(_directory))

from journal import Grant, LIVE_PROFILE, canonical, digest
from lab_live_runtime import lab_graph_configuration
from live_ingress import validate_grants
from live_den_preflight import LocalDenPreflight

INPUTS = {'schema', 'version', 'session', 'producer', 'worker_config', 'runtime',
          'operator', 'provisioning', 'evidence', 'preflight'}
EVIDENCE = ('registration', 'equipment', 'collision', 'fixtures', 'examples', 'qualifications', 'clocks')
BLOCKERS = ('live_home_entry_successor_unavailable', 'retired_session_rebinding_policy_unavailable',
            'geometry_qualification_requires_runtime_validation', 'fresh_runtime_preflight_required',
            'owner_input_export_and_start_generation_workflow_required')


class PackageRefusal(ValueError):
    pass


def _need(condition, reason):
    if not condition:
        raise PackageRefusal(reason)


def _exact(value, keys, reason):
    _need(isinstance(value, dict) and set(value) == set(keys), reason)


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value) is not None


def _path(value):
    return isinstance(value, str) and Path(value).is_absolute() and '..' not in Path(value).parts


def _no_secrets(value):
    if isinstance(value, dict):
        _need(not any(re.search(r'^(token|password|secret|authorization|cookie|private_key)$', k, re.I)
                      for k in value), 'inline_credentials_refused')
        for item in value.values():
            _no_secrets(item)
    elif isinstance(value, list):
        for item in value:
            _no_secrets(item)


def prepare_session_package(artifacts, *, now):
    """Pure preparation of mount2/bootstrap1/operator1, with no activation claim.

    `evidence` contains exact owner-exported JSON bodies by the seven fixed roles.
    Their hashes bind this package only; they do not establish qualification.
    Runtime still resolves/validates commissioned records and rechecks preflight.
    Missing dependencies return no installable files. Existing source/config,
    manifest, grants and policy bodies are preserved rather than re-commissioned.
    """
    result = {'schema': 'home-den-lab-session-package-1', 'version': 1,
              'status': 'refused', 'activation_ready': False, 'physical_authority': False,
              'missing_dependencies': [], 'activation_blockers': list(BLOCKERS), 'files': {}}
    if not isinstance(artifacts, dict):
        result['missing_dependencies'] = ['trusted_owner_inputs_required']
        return result
    missing = sorted(INPUTS - set(artifacts))
    if missing:
        result['missing_dependencies'] = ['input:' + name for name in missing]
        return result
    try:
        _exact(artifacts, INPUTS, 'closed_owner_inputs_required')
        _need(artifacts['schema'] == 'home-den-lab-session-inputs-1'
              and type(artifacts['version']) is int and artifacts['version'] == 1,
              'owner_input_version_required')
        _need(type(now) in (int, float) and math.isfinite(now), 'explicit_preparation_clock_required')
        _no_secrets(artifacts)
        # Freeze all borrowed bytes before calling validators; caller mutations
        # after return cannot alter the derived package or its binding receipt.
        inputs = deepcopy(artifacts)
        session, producer, worker = (inputs[k] for k in ('session', 'producer', 'worker_config'))
        _need(producer.get('schema') == 'home-incumbent-lab-producer-2'
              and producer.get('version') == 2
              and producer.get('live_config', {}).get('schema') == 'home-live-ingress-config-12'
              and producer['live_config'].get('version') == 12
              and producer.get('semantic_policy', {}).get('schema') == 'home-lab-semantic-admission-policy-2',
              'exact_producer2_config12_semantic2_required')
        graph = lab_graph_configuration(producer, session=session)
        live, scope = producer['live_config'], graph['session_ref']
        _need(session.get('state') == 'recording', 'current_recording_lab_session_required')
        grants = [Grant.from_dict(g, profile=LIVE_PROFILE) for g in producer['journal_grants']]
        validate_grants(grants, live)
        _need(len({g.token_sha256 for g in grants}) == len(grants), 'distinct_grant_credentials_required')
        manifest_worker = live['manifest']['worker']
        expected_worker = {k: deepcopy(v) for k, v in manifest_worker.items()
                           if k != 'config_digest' and not k.endswith('schema_pins')}
        expected_worker.update(profile=LIVE_PROFILE, identity=scope, manifest_ref=live['manifest_ref'],
                               live_manifest=live['manifest'])
        _need(isinstance(worker, dict) and _path(worker.get('token_file'))
              and {k: v for k, v in worker.items() if k != 'token_file'} == expected_worker,
              'exact_fixed_worker_policy_required')
        options, operator, provision = (inputs[k] for k in ('runtime', 'operator', 'provisioning'))
        _exact(options, {'node_runtime', 'archive_file', 'archive_limits', 'source_bundle_digest',
                         'max_cycles', 'max_runtime_s', 'interval_s', 'drain_timeout_s'}, 'runtime_options_required')
        _need(_path(options['node_runtime']) and _path(options['archive_file'])
              and _sha(options['source_bundle_digest']), 'fixed_runtime_paths_and_source_digest_required')
        _exact(options['archive_limits'], {'max_records', 'max_bytes', 'min_free_bytes'}, 'archive_limits_required')
        _need(all(type(v) is int and v > 0 for v in options['archive_limits'].values())
              and options['archive_limits']['max_records'] <= 100000
              and options['archive_limits']['max_bytes'] <= 64 * 1024**2, 'bounded_archive_limits_required')
        session_config = {'schema': 'home-live-session-config-1', 'version': 1,
                          'session_ref': scope, 'configuration_digest': digest(graph),
                          'mode': 'live_shadow_unqualified',
                          **{k: options[k] for k in ('max_cycles', 'max_runtime_s', 'interval_s', 'drain_timeout_s')}}
        # Reuse the inert constructor's exact existing bounds; no thread, port,
        # recorder or graph is created until its start() method (never called).
        from live_den_mount import _runtime_module
        _runtime_module()
        from home_den_runtime.supervisor import SessionSupervisor
        forbidden = lambda *a, **k: (_ for _ in ()).throw(AssertionError('prepare_is_inert'))
        SessionSupervisor(session_config, graph_factory=forbidden, recorder_factory=forbidden,
                          worker_factory=forbidden, preflight=forbidden)
        _need(options['max_cycles'] <= producer['limits']['max_cycles']
              and options['max_runtime_s'] <= producer['limits']['max_runtime_s'], 'producer_runtime_bounds_mismatch')
        _exact(provision, {'mount_file', 'bootstrap_file', 'operator_file', 'preflight_file',
                          'context_token_sha256', 'operator_token_sha256', 'publication_token_sha256',
                          'worker_token_sha256', 'lab_token_sha256'}, 'fixed_provisioning_required')
        _need(all(_path(provision[k]) for k in ('mount_file', 'bootstrap_file', 'operator_file', 'preflight_file')),
              'absolute_target_provisioning_paths_required')
        hashes = [provision[k] for k in ('context_token_sha256', 'operator_token_sha256',
                                       'publication_token_sha256', 'worker_token_sha256')]
        _need(all(_sha(v) for v in hashes) and len(set(hashes)) == 4
              and isinstance(provision['lab_token_sha256'], list)
              and all(_sha(v) for v in provision['lab_token_sha256'])
              and not set(hashes) & set(provision['lab_token_sha256']), 'disjoint_context_operator_lab_credentials_required')
        by_hash = {g.token_sha256: g for g in grants}
        _need(provision['operator_token_sha256'] not in by_hash
              and all(provision[k] in by_hash for k in ('context_token_sha256', 'publication_token_sha256', 'worker_token_sha256')),
              'exact_journal_credentials_required')
        context = by_hash[provision['context_token_sha256']]
        expected_bindings = [{'manifest_ref': live['manifest_ref'], 'source_id': sid, 'capabilities': ['read']}
                             for sid in sorted(live['manifest']['sources'])]
        context_raw = next(g for g in producer['journal_grants'] if g['token_sha256'] == context.token_sha256)
        _need(context.operations == frozenset({'read', 'intent'})
              and context.scopes == frozenset({(scope['site_id'], scope['room_id'])})
              and sorted(context_raw['live_bindings'], key=lambda b: b['source_id']) == expected_bindings,
              'narrow_context_grant_required')
        _need('event' in by_hash[provision['publication_token_sha256']].operations
              and by_hash[provision['worker_token_sha256']].operations == frozenset({'read', 'worker_commit'}),
              'separate_publication_and_worker_grants_required')
        paths = [provision[k] for k in ('mount_file', 'bootstrap_file', 'operator_file', 'preflight_file')]
        paths += [producer['journal_path'], producer['original_archive']['filename'], options['archive_file'],
                  producer['publication_token_file'], worker['token_file']]
        _need(len({str(Path(p)) for p in paths}) == len(paths), 'distinct_package_state_paths_required')
        _exact(operator, {'upstream', 'ca_file', 'token_file', 'timeout_s'}, 'fixed_private_operator_required')
        origin = urlsplit(operator['upstream'])
        _need(origin.scheme == 'https' and origin.hostname and origin.path in ('', '/')
              and not (origin.username or origin.password or origin.query or origin.fragment)
              and _path(operator['ca_file']) and _path(operator['token_file'])
              and operator['token_file'] not in paths
              and type(operator['timeout_s']) in (int, float) and math.isfinite(operator['timeout_s'])
              and 0 < operator['timeout_s'] <= 15, 'fixed_tls_operator_origin_and_paths_required')
        evidence = inputs['evidence']
        _exact(evidence, EVIDENCE, 'fixed_evidence_roles_required')
        absent = [role for role in EVIDENCE if evidence[role] is None or evidence[role] == []]
        if absent:
            result['missing_dependencies'] = ['evidence:' + role for role in absent]
            return result
        _need(evidence['registration'] == worker['room_policy']['registration']
              and evidence['fixtures'] == worker['room_policy']['fixtures']
              and evidence['examples'] == worker['scene_policy']['catalog'], 'exact_room_fixture_example_evidence_required')
        # The independently supplied receipt is structurally checked only. No
        # probe/check is called and no passed host health claim is minted.
        clock_policy = live['manifest']['sources'][producer['source_id']]['control_clock']
        try:
            LocalDenPreflight(provision['preflight_file'], configuration_digest=digest(graph),
                              control_policy=clock_policy, archive_directory=str(Path(options['archive_file']).parent),
                              clock=lambda: now, reader=lambda _: inputs['preflight'], probe=forbidden).receipt()
        except (ValueError, TypeError, KeyError):
            raise PackageRefusal('independent_preflight_record_unavailable') from None
        runtime = {'schema': 'home-live-runtime-config-1', 'version': 1, 'session': session_config,
                   'worker_config': worker, **{k: options[k] for k in ('node_runtime', 'archive_file', 'archive_limits', 'source_bundle_digest')}}
        mount = {'schema': 'home-managed-den-mount-2', 'version': 2, 'producer': producer, 'runtime': runtime,
                 'preflight_file': provision['preflight_file'],
                 **{k: provision[k] for k in ('context_token_sha256', 'operator_token_sha256')}}
        bootstrap = {'schema': 'home-den-lab-bootstrap-1', 'version': 1, 'mount_file': provision['mount_file'],
                     'operator_token_sha256': provision['operator_token_sha256'],
                     'scope': {k: scope[k] for k in ('site_id', 'room_id')}}
        operator_config = {'schema': 'home-live-operator-config-1', 'version': 1, **operator,
                           'session_ref': scope, 'configuration_digest': digest(graph)}
        files = {'mount.json': mount, 'bootstrap.json': bootstrap, 'operator.json': operator_config,
                 'lab-session.json': session,
                 'preflight.json': inputs['preflight'], 'evidence.json': evidence}
        bindings = {'source_bundle_digest': options['source_bundle_digest'], 'scope': scope,
                    'lab_binding': producer['lab_binding'], 'graph_configuration_digest': digest(graph),
                    'manifest_ref': live['manifest_ref'], 'source_pins': manifest_worker['source_pins'],
                    'grants_digest': digest(producer['journal_grants']), 'input_digest': digest(inputs),
                    'target_files': {k: provision[k] for k in ('mount_file', 'bootstrap_file', 'operator_file', 'preflight_file')},
                    'preflight_validation': 'supplied_record_shape_only_no_resource_probe',
                    'evidence_digests': {k: digest(evidence[k]) for k in EVIDENCE},
                    'file_digests': {k: digest(v) for k, v in files.items()}}
        result.update(status='prepared', bindings=bindings, files=files, package_digest=digest(bindings))
    except PackageRefusal as error:
        result['missing_dependencies'] = [str(error)]
    except (ValueError, TypeError, KeyError, OverflowError, AttributeError):
        # Do not echo malformed input/credentials in errors or logs.
        result['missing_dependencies'] = ['commissioned_lab_or_preflight_validator_refused']
    return deepcopy(result)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['prepare'])
    parser.add_argument('--inputs', required=True, help='Protected directory containing owner-exported inputs.json')
    parser.add_argument('--output', required=True, help='New directory; existing outputs are never overwritten')
    parser.add_argument('--now', type=float, help='Optional deterministic preparation clock, never changes evidence clocks')
    args = parser.parse_args(argv)
    directory, output = Path(args.inputs), Path(args.output)
    _need(directory.is_absolute() and output.is_absolute(), 'absolute_preparation_directories_required')
    source = directory / 'inputs.json'
    _need(not directory.is_symlink() and not source.is_symlink() and source.is_file()
          and source.stat().st_size <= 4 * 1024**2, 'bounded_owner_export_required')
    if sys.platform != 'win32':
        _need(not (source.stat().st_mode & 0o077), 'private_owner_export_required')
    result = prepare_session_package(json.loads(source.read_text(encoding='utf-8')),
                                     now=time.time() if args.now is None else args.now)
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    for name, body in {**result['files'], 'package.json': {k: v for k, v in result.items() if k != 'files'}}.items():
        with (output / name).open('x', encoding='utf-8') as handle:
            handle.write(canonical(body) + '\n')
        if sys.platform != 'win32':
            (output / name).chmod(0o600)
    print(json.dumps({'status': result['status'], 'activation_ready': False,
                      'missing_dependencies': result['missing_dependencies'], 'activation_blockers': result['activation_blockers']}))
    return 0 if result['status'] == 'prepared' else 2


if __name__ == '__main__':
    raise SystemExit(main())
