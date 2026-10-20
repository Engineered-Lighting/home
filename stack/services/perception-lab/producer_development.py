"""Bounded producer-only development composition; no camera or worker ownership.

The trusted caller constructs the production graph and supplies local runtime
capabilities. No request or record selects executable code, models or endpoints.
The commissioning kit checks owner records; it does not manufacture measurements.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
import stat
import subprocess


_KIT = Path(__file__).resolve().parents[3] / 'tools' / 'den-commissioning' / 'commission.mjs'
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}\Z')
_SHA = re.compile(r'[a-f0-9]{64}\Z')
_CONFIG_KEYS = {'schema', 'version', 'session_ref', 'source_id', 'configuration_digest',
                'max_runtime_s', 'max_cycles', 'interval_s', 'commissioning_record',
                'commissioning_freeze', 'measurement_summary'}
_CYCLE_KEYS = {'schema', 'version', 'status', 'reason', 'capture', 'detector', 'pose',
               'spatial', 'interaction', 'operational_geometry', 'focused_output'}
_COUNT_KEYS = ('cycles', 'observed', 'blocked', 'failed', 'stopped', 'qualified_capture_results',
               'detector_completed_results', 'pose_completed_results', 'spatial_completed_results',
               'interaction_completed_results')
_GRAPH_COUNTERS = {'cycles', 'captures', 'detector_runs', 'pose_runs', 'refusals', 'source_gaps'}


class DevelopmentRefusal(ValueError):
    pass


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _config(config):
    if not isinstance(config, dict) or set(config) != _CONFIG_KEYS:
        raise DevelopmentRefusal('development_config_invalid')
    scope = config['session_ref']
    if (config['schema'] != 'home-producer-development-1' or type(config['version']) is not int
            or config['version'] != 1 or not isinstance(scope, dict)
            or set(scope) != {'site_id', 'room_id', 'session_id', 'domain_epoch'}
            or not all(isinstance(v, str) and _ID.fullmatch(v) for v in scope.values())
            or not isinstance(config['source_id'], str) or not _ID.fullmatch(config['source_id'])
            or not isinstance(config['configuration_digest'], str) or not _SHA.fullmatch(config['configuration_digest'])
            or not _finite(config['max_runtime_s']) or not 0 < config['max_runtime_s'] <= 120
            or type(config['max_cycles']) is not int or not 1 <= config['max_cycles'] <= 24
            or not _finite(config['interval_s']) or not .5 <= config['interval_s'] <= 120):
        raise DevelopmentRefusal('development_config_invalid')
    for name in ('commissioning_record', 'commissioning_freeze', 'measurement_summary'):
        value = config[name]
        if name != 'commissioning_record' and value is None:
            continue
        if not isinstance(value, str) or not value or len(value) > 4096 or not Path(value).is_absolute():
            raise DevelopmentRefusal('owner_local_absolute_path_required')
    if (config['commissioning_freeze'] is None) != (config['measurement_summary'] is None):
        raise DevelopmentRefusal('aggregate_artifact_pair_required')


def _kit(node_runtime, command, *arguments):
    """Only a fixed repository program and its closed owner-record operations."""
    if not isinstance(node_runtime, str) or not node_runtime or len(node_runtime) > 4096:
        raise DevelopmentRefusal('node_runtime_unavailable')
    try:
        completed = subprocess.run([node_runtime, str(_KIT), command, *arguments],
                                   capture_output=True, timeout=10, shell=False)
    except (OSError, subprocess.TimeoutExpired):
        raise DevelopmentRefusal('commissioning_tool_unavailable') from None
    if (completed.returncode not in (0, 2, 3) or completed.stderr
            or len(completed.stdout) > 1024 * 1024):
        raise DevelopmentRefusal('commissioning_tool_refused')
    try:
        result = json.loads(completed.stdout)
    except (ValueError, UnicodeError):
        raise DevelopmentRefusal('commissioning_tool_invalid_report') from None
    if not isinstance(result, dict) or result.get('physical_authority') is not False:
        raise DevelopmentRefusal('commissioning_tool_invalid_report')
    # These are aggregate report fields, never original records or filenames.
    names = ('schema', 'status', 'errors', 'blockers', 'counts', 'next_steps',
             'runtime_qualification', 'physical_authority', 'reason') if command == 'validate' else (
             'schema', 'freeze_digest', 'assessment_digest', 'assessment_partition_ref', 'units',
             'duration_s', 'source_validity', 'work_center_error_m', 'reference_uncertainty_m',
             'containment', 'latency_s', 'missing_groups', 'status', 'origin',
             'runtime_qualification', 'physical_authority', 'reason')
    return completed.returncode, {key: result[key] for key in names if key in result}


def _owner_json(filename):
    try:
        path = Path(filename)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_size > 1024 * 1024:
            raise ValueError()
        def pairs(items):
            value = {}
            for key, item in items:
                if key in value:
                    raise ValueError()
                value[key] = item
            return value
        with path.open('rb') as source:
            raw = source.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError()
        return json.loads(raw, object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (OSError, ValueError, RecursionError):
        raise DevelopmentRefusal('commissioning_artifact_unavailable') from None


def _owner_bindings(config):
    record = _owner_json(config['commissioning_record'])
    try:
        pins = record['survey']['pins']
        for key in ('site_id', 'room_id'):
            if pins[key] is not None and pins[key] != config['session_ref'][key]:
                raise DevelopmentRefusal('commissioning_scope_mismatch')
        if config['commissioning_freeze'] is not None:
            frozen = _owner_json(config['commissioning_freeze'])
            if frozen.get('record') != record:
                raise DevelopmentRefusal('commissioning_record_freeze_mismatch')
    except (KeyError, TypeError, AttributeError):
        raise DevelopmentRefusal('commissioning_artifact_unavailable') from None


def _cycle(value):
    if (not isinstance(value, dict) or set(value) != _CYCLE_KEYS
            or value['schema'] != 'home-acquisition-cycle-1' or type(value['version']) is not int
            or value['version'] != 1 or value['status'] not in ('observed', 'blocked', 'failed', 'stopped')
            or not isinstance(value['reason'], str) or not _ID.fullmatch(value['reason'])
            or value['capture'] not in ('qualified', 'unavailable')
            or value['detector'] not in ('completed', 'unavailable', 'not_run')
            or value['pose'] not in ('completed', 'unavailable', 'not_run')
            or value['spatial'] not in ('completed', 'missing', 'unavailable')
            or value['interaction'] not in ('completed', 'missing', 'unavailable')
            or value['operational_geometry'] is not False or value['focused_output'] is not False):
        raise DevelopmentRefusal('acquisition_cycle_invalid')
    return value


def _cleanup(graph, reason):
    try:
        result = graph.close(reason=reason)
        if (not isinstance(result, dict) or set(result) != {'status', 'reason', 'withdrawal', 'buffers_cleared'}
                or result['status'] not in ('stopped', 'unconfirmed')
                or not isinstance(result['reason'], str) or not _ID.fullmatch(result['reason'])
                or result['withdrawal'] not in ('recorded', 'not_recorded')
                or type(result['buffers_cleared']) is not bool):
            raise ValueError('invalid cleanup result')
        return result
    except Exception:
        return {'status': 'unconfirmed', 'reason': 'development_cleanup_unconfirmed',
                'withdrawal': 'not_recorded', 'buffers_cleared': False}


def _graph_identity(graph, config):
    value = graph.status()
    if (not isinstance(value, dict) or any(value.get(key) != config[key]
            for key in ('session_ref', 'source_id', 'configuration_digest'))):
        raise DevelopmentRefusal('acquisition_identity_mismatch')
    if (set(value) != {'schema', 'version', 'session_ref', 'source_id', 'configuration_digest',
                       'counters', 'stage_presence', 'closed', 'inflight'}
            or value['schema'] != 'home-acquisition-status-1' or type(value['version']) is not int
            or value['version'] != 1 or not isinstance(value['counters'], dict)
            or set(value['counters']) != _GRAPH_COUNTERS
            or any(type(v) is not int or not 0 <= v <= 1000000 for v in value['counters'].values())
            or not isinstance(value['stage_presence'], dict)
            or set(value['stage_presence']) != {'spatial', 'interaction'}
            or any(type(v) is not bool for v in value['stage_presence'].values())
            or type(value['closed']) is not bool or type(value['inflight']) is not bool):
        raise DevelopmentRefusal('acquisition_status_invalid')
    return value


def run_development_session(graph, run_config, *, clock, sleep, stop_requested, node_runtime):
    """Run a trusted production graph with bounded cycles and explicit cleanup.

    ``clock`` measures monotonic duration only; the graph separately owns UTC
    observation/publication clocks. Graph I/O must have its own resource/time
    bounds: this loop does not pretend it can force-cancel a native model SDK.
    An incomplete commissioning record permits development, never qualification.
    """
    report = {'schema': 'home-producer-development-report-1', 'version': 1,
              'status': 'refused', 'reason': 'development_config_invalid',
              'session_ref': None, 'source_id': None, 'configuration_digest': None,
              'commissioning': None, 'measurement_aggregate': None,
              'counts': {key: 0 for key in _COUNT_KEYS}, 'elapsed_s': 0.0,
              'graph_counters': None,
              'cycle_duration_s': {'samples': 0, 'max': None},
              'cycle_start_gap_s': {'samples': 0, 'max': None},
              'missing_stages': [], 'operational_geometry': False, 'focused_output': False,
              'runtime_qualification': 'not_assessed', 'physical_authority': False}
    started = previous = None
    missing = set()
    try:
        _config(run_config)
        report.update({key: run_config[key].copy() if isinstance(run_config[key], dict) else run_config[key]
                       for key in ('session_ref', 'source_id', 'configuration_digest')})
        if not all(callable(f) for f in (clock, sleep, stop_requested)):
            raise DevelopmentRefusal('development_runtime_invalid')
        state = _graph_identity(graph, run_config)
        if state['closed'] or state['inflight']:
            raise DevelopmentRefusal('acquisition_graph_not_ready')
        missing.update(name for name, present in state['stage_presence'].items() if not present)
        started = clock()
        if not _finite(started):
            raise DevelopmentRefusal('development_clock_unavailable')
        code, report['commissioning'] = _kit(node_runtime, 'validate', '--record', run_config['commissioning_record'])
        if code == 2:
            raise DevelopmentRefusal('commissioning_record_refused')
        # Incomplete pins remain missing; supplied pins must refer to this room.
        # A supplied assessment freeze cannot belong to a different record.
        _owner_bindings(run_config)
        if run_config['commissioning_freeze'] is not None:
            code, report['measurement_aggregate'] = _kit(node_runtime, 'aggregate', '--freeze',
                run_config['commissioning_freeze'], '--summary', run_config['measurement_summary'])
            if code == 2:
                raise DevelopmentRefusal('commissioning_aggregate_refused')
        report.update(status='development_complete', reason='development_complete')
        for _ in range(run_config['max_cycles']):
            now = clock()
            if not _finite(now) or now < started or previous is not None and now < previous:
                raise DevelopmentRefusal('development_clock_unavailable')
            if stop_requested():
                report.update(status='stopped', reason='development_stop_requested')
                break
            if now - started >= run_config['max_runtime_s']:
                report.update(status='stopped', reason='development_runtime_limit')
                break
            if previous is not None:
                timing = report['cycle_start_gap_s']
                timing.update(samples=timing['samples'] + 1, max=max(timing['max'] or 0, now - previous))
            previous = now
            result = _cycle(graph.step())
            finished = clock()
            if not _finite(finished) or finished < now:
                raise DevelopmentRefusal('development_clock_unavailable')
            timing = report['cycle_duration_s']
            timing.update(samples=timing['samples'] + 1, max=max(timing['max'] or 0, finished - now))
            counts = report['counts']
            counts['cycles'] += 1
            counts[result['status']] += 1
            counts['qualified_capture_results'] += result['capture'] == 'qualified'
            for stage in ('detector', 'pose', 'spatial', 'interaction'):
                counts[stage + '_completed_results'] += result[stage] == 'completed'
                if result[stage] == 'missing':
                    missing.add(stage)
            if result['status'] in ('stopped', 'failed'):
                report.update(status=result['status'], reason=result['reason'])
                break
            if finished - started >= run_config['max_runtime_s']:
                report.update(status='stopped', reason='development_runtime_limit')
                break
            if counts['cycles'] < run_config['max_cycles']:
                sleep(min(run_config['interval_s'], run_config['max_runtime_s'] - (finished - started)))
    except DevelopmentRefusal as error:
        report.update(status='refused', reason=str(error))
    except KeyboardInterrupt:
        report.update(status='stopped', reason='development_stop_requested')
    except Exception:
        report.update(status='failed', reason='development_operation_failed')
    finally:
        report['cleanup'] = _cleanup(graph, report['reason'])
        if report['cleanup']['status'] != 'stopped' or not report['cleanup']['buffers_cleared']:
            report.update(status='cleanup_unconfirmed', reason='development_cleanup_unconfirmed')
        report['missing_stages'] = [name for name in ('spatial', 'interaction') if name in missing]
        try:
            report['graph_counters'] = _graph_identity(graph, run_config)['counters'].copy()
        except Exception:
            pass
        if started is not None:
            try:
                finished = clock()
                if _finite(finished) and finished >= started:
                    report['elapsed_s'] = finished - started
            except Exception:
                pass
    return report
