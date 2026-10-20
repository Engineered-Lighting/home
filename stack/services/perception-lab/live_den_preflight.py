"""Fixed local checks for an explicitly provisioned Den start, never remote I/O.

The owner supplies a recent independent inspection and control-clock assessment.
This module rechecks actual local resource/unit/kernel state; it cannot clear
failures, select commands, or qualify a new camera clock from wall time alone.
"""
from decimal import Decimal
import math
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time

CONTEXT = Path(__file__).resolve().parents[1] / 'home-context'
if str(CONTEXT) not in sys.path:
    sys.path.append(str(CONTEXT))
from journal import digest
from live_detector_app import private_json

FAULT = re.compile(r'\b(?:mce|hardware[- ]error|(?:soft |hard )?lockup|thermal|out of memory|oom(?:-kill)?|panic|I/O error|segfault)\b', re.I)


def local_probe(directory, inspected_at, boundary):
    def command(args):
        result = subprocess.run(args, capture_output=True, text=True, timeout=2)
        if result.returncode or len(result.stdout) > 256 * 1024:
            raise ValueError('local_probe_unavailable')
        return result.stdout
    manager = ['--user'] if boundary['service_manager'] == 'user' else []
    failed = command(['systemctl', *manager, '--failed', '--plain', '--no-legend', '--no-pager'])
    records = command(['journalctl', '-k', '--since', '@' + str(inspected_at), '--no-pager', '--output=cat', '--lines=512'])
    if len(records.splitlines()) >= 512:
        raise ValueError('kernel_inspection_bound')
    units = sorted(line.split()[0] for line in failed.splitlines() if line.strip())
    memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    temperatures = [int(p.read_text()) / 1000 for p in Path('/sys/class/thermal').glob('thermal_zone*/temp')]
    if not temperatures:
        raise ValueError('temperature_unavailable')
    group = Path('/sys/fs/cgroup') / Path(boundary['cgroup']).relative_to('/')
    current_group = next(line.split('::', 1)[1] for line in Path('/proc/self/cgroup').read_text().splitlines() if line.startswith('0::'))
    cpu = [int(v) for v in (group / 'cpu.max').read_text().split()]
    observed = {'cgroup': current_group, 'memory_max_bytes': int((group / 'memory.max').read_text()),
                'pids_max': int((group / 'pids.max').read_text()), 'cpu_quota_us': cpu[0], 'cpu_period_us': cpu[1]}
    unit = command(['systemctl', *manager, 'show', boundary['service_unit'], '--property=ControlGroup', '--no-pager'])
    unit = dict(line.split('=', 1) for line in unit.splitlines() if '=' in line)
    observed['service_unit'] = boundary['service_unit']
    observed['service_manager'] = boundary['service_manager']
    observed['unit_group'] = unit['ControlGroup']
    services_healthy = all(command(['systemctl', *manager, 'is-active', name]).strip() == 'active' for name in boundary['required_units'])
    containers_healthy = all(command(['docker', 'inspect', '--format', '{{.State.Health.Status}}', name]).strip() == 'healthy' for name in boundary['required_containers'])
    return {'host': socket.gethostname(), 'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'failed_units': units, 'new_fault': bool(FAULT.search(records)),
            'available_mb': int(memory['MemAvailable'].split()[0]) / 1024,
            'free_bytes': shutil.disk_usage(directory).free, 'temperature_c': max(temperatures),
            'load_per_cpu': os.getloadavg()[0] / (os.cpu_count() or 1), 'boundary': observed,
            'services_healthy': services_healthy, 'containers_healthy': containers_healthy}


class LocalDenPreflight:
    def __init__(self, filename, *, configuration_digest, control_policy, archive_directory,
                 clock=time.time, monotonic=time.monotonic, probe=local_probe, reader=private_json):
        self.filename, self.configuration_digest = filename, configuration_digest
        self.policy, self.directory = dict(control_policy), archive_directory
        self.clock, self.monotonic, self.probe, self.reader = clock, monotonic, probe, reader
        self.identity = None
        self.cached_at, self.cached = -math.inf, None

    def receipt(self):
        r = self.reader(self.filename)
        keys = {'schema', 'version', 'configuration_digest', 'host', 'boot_id', 'inspected_at', 'valid_until',
                'allowed_failed_units', 'limits', 'control_clock', 'qualification_current', 'execution_boundary'}
        if (not isinstance(r, dict) or set(r) != keys or r['schema'] != 'home-den-local-preflight-1'
                or type(r['version']) is not int or r['version'] != 1
                or r['configuration_digest'] != self.configuration_digest
                or not isinstance(r['host'], str) or not isinstance(r['boot_id'], str)
                or not all(type(r[k]) in (int, float) and math.isfinite(r[k]) for k in ('inspected_at', 'valid_until'))
                or not r['inspected_at'] <= self.clock() < r['valid_until'] <= r['inspected_at'] + 180
                or type(r['qualification_current']) is not bool
                or not isinstance(r['allowed_failed_units'], list)
                or any(not isinstance(v, str) or not re.fullmatch(r'[A-Za-z0-9_.@:-]+', v) for v in r['allowed_failed_units'])):
            raise ValueError('independent_preflight_unavailable')
        limits = r['limits']
        if (not isinstance(limits, dict) or set(limits) != {'min_available_mb', 'min_free_bytes', 'max_temperature_c', 'max_load_per_cpu'}
                or any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in limits.values())
                or limits['min_available_mb'] < 512 or limits['min_free_bytes'] < 64 * 1024**2
                or limits['max_temperature_c'] > 85 or limits['max_load_per_cpu'] > 1):
            raise ValueError('bounded_resource_limits_required')
        b = r['execution_boundary']
        if (not isinstance(b, dict) or set(b) != {'cgroup', 'service_unit', 'service_manager', 'memory_max_bytes', 'pids_max', 'cpu_quota_us', 'cpu_period_us', 'required_units', 'required_containers'}
                or b['service_manager'] not in {'system', 'user'}
                or not isinstance(b['cgroup'], str) or not re.fullmatch(r'/[A-Za-z0-9_./@:-]+', b['cgroup']) or '..' in b['cgroup']
                or not isinstance(b['service_unit'], str) or not re.fullmatch(r'[A-Za-z0-9_.@:-]+\.service', b['service_unit'])
                or any(type(b[k]) is not int or b[k] <= 0 for k in ('memory_max_bytes', 'pids_max', 'cpu_quota_us', 'cpu_period_us'))
                or b['memory_max_bytes'] > 2 * 1024**3 or b['pids_max'] > 256 or b['cpu_quota_us'] / b['cpu_period_us'] > 4
                or any(not isinstance(b[k], list) or any(not isinstance(v, str) or not re.fullmatch(r'[A-Za-z0-9_.@:-]+', v) for v in b[k]) for k in ('required_units', 'required_containers'))
                or not b['required_units']):
            raise ValueError('enforced_execution_boundary_required')
        c = r['control_clock']
        if (not isinstance(c, dict) or set(c) != {'policy', 'uncertainty_s'} or c['policy'] != self.policy
                or self.policy.get('original_unit') != 'unix_ns' or self.policy.get('epoch_origin_s') != 0
                or type(c['uncertainty_s']) not in (int, float) or not math.isfinite(c['uncertainty_s'])
                or not 0 <= c['uncertainty_s'] <= self.policy['max_uncertainty_s']):
            raise ValueError('independent_control_clock_unavailable')
        identity = digest({k: r[k] for k in ('configuration_digest', 'host', 'boot_id', 'control_clock', 'execution_boundary')})
        if self.identity is None:
            self.identity = identity
        if identity != self.identity:
            raise ValueError('preflight_generation_changed')
        return r

    def check(self):
        r = self.receipt()
        tick = self.monotonic()
        age = tick - self.cached_at
        if not math.isfinite(tick) or age < 0 or math.isnan(age):
            self.cached = False
            return False
        if age >= 1:
            p = self.probe(self.directory, r['inspected_at'], r['execution_boundary'])
            limits = r['limits']
            boundary = r['execution_boundary']
            expected = {k: v for k, v in boundary.items() if k not in {'required_units', 'required_containers'}}
            expected['unit_group'] = boundary['cgroup']
            self.cached = (p['host'] == r['host'] and p['boot_id'] == r['boot_id'] and p['new_fault'] is False
                           and p['boundary'] == expected and p['services_healthy'] is True and p['containers_healthy'] is True
                           and set(p['failed_units']) <= set(r['allowed_failed_units'])
                           and p['available_mb'] >= limits['min_available_mb'] and p['free_bytes'] >= limits['min_free_bytes']
                           and p['temperature_c'] <= limits['max_temperature_c'] and p['load_per_cpu'] <= limits['max_load_per_cpu'])
            self.cached_at = tick
        return self.cached is True

    def control_clock(self):
        r = self.receipt()
        ns = time.time_ns()
        return {'observed_at': float(Decimal(ns) / Decimal(1000000000)), 'clock': {'status': 'commissioned',
            **{k: self.policy[k] for k in ('mapping_id', 'mapping_digest', 'source_epoch', 'original_unit')},
            'original_value': str(ns), 'uncertainty_s': r['control_clock']['uncertainty_s']}}

    def supervisor(self, config):
        try:
            r = self.receipt()
            return {'status': 'ready' if self.check() else 'refused', 'qualification_current': r['qualification_current']}
        except (ValueError, OSError, subprocess.SubprocessError):
            return {'status': 'refused', 'qualification_current': False}
