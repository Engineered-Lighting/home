"""Read-only SSH preservation of a pinned checkout's selected dirty source.

Copies exact reviewed source classes into private local content-addressed blobs.
Does not copy configurations, credentials, databases, media or Git object closure.
No remote files are written and no production source is imported or executed.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import inspect
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import threading

from home_shared.preservation import save_snapshot


def run_bounded(command, *, input_bytes=None, output_limit, timeout):
    """Bound pipe storage during collection, not after communicate returns."""
    if input_bytes is not None and len(input_bytes) > 128 * 1024:
        raise ValueError('collector input exceeds bound')
    process = subprocess.Popen(command, stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    buffers, errors = [bytearray(), bytearray()], []
    def read(stream, index, limit):
        try:
            while True:
                block = stream.read(8192)
                if not block:
                    break
                if len(buffers[index]) + len(block) > limit:
                    errors.append('command output exceeds bound')
                    process.kill()
                    return
                buffers[index].extend(block)
        except (OSError, ValueError):
            errors.append('command pipe failed')
    workers = [threading.Thread(target=read, args=(process.stdout, 0, output_limit), daemon=True),
               threading.Thread(target=read, args=(process.stderr, 1, 64 * 1024), daemon=True)]
    if input_bytes is not None:
        def write():
            try:
                process.stdin.write(input_bytes)
                process.stdin.close()
            except (OSError, ValueError):
                errors.append('command input failed')
        workers.append(threading.Thread(target=write, daemon=True))
    try:
        for worker in workers:
            worker.start()
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            raise ValueError('command deadline exceeded') from None
        for worker in workers:
            worker.join(timeout=2)
        if any(worker.is_alive() for worker in workers) or errors:
            raise ValueError('command transport failed or exceeded bound')
        return subprocess.CompletedProcess(command, returncode, bytes(buffers[0]), bytes(buffers[1]))
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        for stream in (process.stdout, process.stderr, process.stdin):
            if stream is not None:
                stream.close()


REMOTE_DRIVER = r'''
import subprocess
from datetime import datetime, timezone
root = Path(CAPTURE_ROOT)
def git(*args):
    result = run_bounded(['git', '--no-optional-locks', '-c', 'core.fsmonitor=false', '-C', str(root), *args],
                         output_limit=1024 * 1024, timeout=15)
    if result.returncode:
        raise ValueError('checkout metadata unavailable')
    return result.stdout
filter_config = run_bounded(['git', '--no-optional-locks', '-C', str(root),
    'config', '--get-regexp', r'^filter\..*\.(clean|process)$'], output_limit=65536, timeout=10)
if filter_config.returncode != 1:
    raise ValueError('configured source filters require separate review before capture')
head = git('rev-parse', 'HEAD').decode('ascii').strip()
if head != EXPECTED_HEAD:
    raise ValueError('deployed checkout revision changed')
before = git('status', '--porcelain=v1', '-z', '--untracked-files=all', '--no-renames')
entries = [entry.decode('utf-8') for entry in before.split(b'\0') if entry]
if len(entries) > 1024:
    raise ValueError('dirty source inventory exceeds bound')
selected, skipped, statuses, inspected_bytes = [], [], {}, 0
for entry in entries:
    status, relative = entry[:2], entry[3:]
    if entry[2:3] != ' ' or status[0] not in {' ', '?'}:
        raise ValueError('staged or unsupported state requires separate index preservation')
    statuses[relative] = status
    if 'D' in status:
        skipped.append({'path': relative, 'status': status, 'reason': 'deleted_at_source'})
        continue
    try:
        source_path(relative)
        size = (root / relative).lstat().st_size
        if size > MAX_FILE_BYTES:
            skipped.append({'path': relative, 'status': status, 'reason': 'oversized_source'})
            continue
        inspected_bytes += size
        if inspected_bytes > MAX_TOTAL_BYTES:
            raise RuntimeError('source preflight byte budget exceeded')
        snapshot_sources(root, [relative])
    except (ValueError, OSError) as exc:
        # Only fixed policy category is retained; never include content or
        # filesystem exception messages which might reveal private details.
        skipped.append({'path': relative, 'status': status, 'reason': rejection_reason(exc)})
        continue
    selected.append(relative)
snapshot = snapshot_sources(root, selected)
after = git('status', '--porcelain=v1', '-z', '--untracked-files=all', '--no-renames')
if before != after or git('rev-parse', 'HEAD').decode('ascii').strip() != head:
    raise ValueError('checkout changed during preservation')
result = {'version': 1, 'snapshot': snapshot, 'provenance': {
    'site_id': 'echo', 'root': str(root), 'head': head,
    'captured_at_utc': datetime.now(timezone.utc).isoformat(),
    'git_status_sha256': hashlib.sha256(before).hexdigest(),
    'selected_status': {name: statuses[name] for name in selected},
    'skipped': skipped,
    'index_state': 'no staged changes observed before or after capture',
    'consistency': 'same HEAD and Git inventory before/after; source bytes stable across two passes; not atomic filesystem snapshot',
    'git_object_closure_copied': False,
    'runtime_or_database_recovery_set': False}}
print(json.dumps(result, separators=(',', ':')))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ssh-alias', required=True)
    parser.add_argument('--root', required=True)
    parser.add_argument('--expected-head', required=True)
    parser.add_argument('--output-parent', type=Path, required=True,
                        help='private local directory; source content is not publication-safe')
    args = parser.parse_args()
    if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', args.ssh_alias) is None:
        parser.error('fixed SSH alias required')
    source_root = PurePosixPath(args.root)
    if not source_root.is_absolute() or '..' in source_root.parts or str(source_root) != args.root:
        parser.error('explicit normalized absolute checkout root required')
    if re.fullmatch(r'[0-9a-f]{40}', args.expected_head) is None:
        parser.error('full expected Git commit required')
    collector_path = Path(__file__).parent / 'home_shared/preservation.py'
    collector = collector_path.read_text(encoding='utf-8')
    driver = (collector + '\nimport subprocess, threading\n' + inspect.getsource(run_bounded)
              + '\nCAPTURE_ROOT = ' + repr(args.root) + '\nEXPECTED_HEAD = '
              + repr(args.expected_head) + '\n' + REMOTE_DRIVER)
    command = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
               '-o', 'StrictHostKeyChecking=yes', args.ssh_alias, 'python3 -I -S -B -']
    result = run_bounded(command, input_bytes=driver.encode('utf-8'), output_limit=24 * 1024 * 1024, timeout=90)
    if result.returncode != 0:
        raise SystemExit('Read-only source capture failed; no local recovery manifest written. Inspect SSH access or source policy without printing source bodies.')
    if len(result.stdout) > 24 * 1024 * 1024:
        raise SystemExit('Source transfer exceeded its bound; no recovery manifest written.')
    payload = json.loads(result.stdout)
    provenance = payload.get('provenance', {})
    if (payload.get('version') != 1 or provenance.get('site_id') != 'echo'
            or provenance.get('head') != args.expected_head or provenance.get('root') != args.root):
        raise SystemExit('Source transfer provenance mismatch; no recovery manifest written.')
    parent = args.output_parent.resolve()
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    ignore = parent / '.gitignore'
    if not ignore.exists():
        ignore.write_text('*\n', encoding='utf-8')
    name = 'echo-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    destination = parent / name
    manifest = save_snapshot(payload['snapshot'], destination)
    (destination / 'collector.source').write_text(collector, encoding='utf-8', newline='\n')
    (destination / 'ssh-driver.source').write_text(driver, encoding='utf-8', newline='\n')
    provenance.update(ssh_alias=args.ssh_alias, collector_sha256=hashlib.sha256(collector.encode('utf-8')).hexdigest(),
                      driver_sha256=hashlib.sha256(driver.encode('utf-8')).hexdigest(),
                      manifest_sha256=hashlib.sha256((destination / 'manifest.json').read_bytes()).hexdigest(),
                      complete_deployed_baseline=False, deployment_authorized=False)
    (destination / 'provenance.json').write_text(json.dumps(provenance, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'path': str(destination), 'files': len(manifest['files']),
                      'bytes': sum(item['bytes'] for item in manifest['files']),
                      'skipped': len(provenance['skipped']), 'head': provenance['head'],
                      'manifest_sha256': provenance['manifest_sha256'], 'complete_deployed_baseline': False}))


if __name__ == '__main__':
    main()
