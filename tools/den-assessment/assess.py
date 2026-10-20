"""Owner-local reference/scoring CLI. Never opens cameras, endpoints or models."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'stack/services/perception-lab'))
from live_assessment_scoring import score_assessment, REQUIREMENTS


def read_json(path):
    filename = Path(path)
    if not filename.is_absolute() or not filename.is_file() or filename.is_symlink() or filename.stat().st_size > 9 * 1024**2:
        raise ValueError('bounded regular absolute owner-local input required')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON field refused')
            result[key] = value
        return result
    return json.loads(filename.read_text(encoding='utf-8'), object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON refused')))


def write(path, value):
    filename = Path(path)
    if not filename.is_absolute():
        raise ValueError('absolute owner-local output required')
    encoded = value.encode('utf-8') if isinstance(value, str) else json.dumps(value, indent=2, allow_nan=False).encode('utf-8')
    if len(encoded) > 9 * 1024**2:
        raise ValueError('bounded owner-local output required')
    descriptor = os.open(filename, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    template = sub.add_parser('reference-template')
    template.add_argument('--measurements', required=True)
    template.add_argument('--output', required=True)
    editor = sub.add_parser('reference-editor')
    editor.add_argument('--output', required=True)
    score = sub.add_parser('score')
    score.add_argument('--measurements', required=True)
    score.add_argument('--references', required=True)
    score.add_argument('--output', required=True)
    options = parser.parse_args(arguments)
    try:
        if options.command == 'reference-editor':
            write(options.output, (Path(__file__).parent / 'reference-editor.html').read_text(encoding='utf-8'))
            print(json.dumps({'status': 'created', 'kind': 'offline_reference_editor', 'physical_authority': False}))
            return 0
        measurements = read_json(options.measurements)
        if measurements.get('schema') != 'home-live-assessment-measurements-1' or measurements.get('version') != 1:
            raise ValueError('assessment measurements version required')
        if options.command == 'reference-template':
            write(options.output, {'schema': 'home-live-assessment-references-1', 'version': 1,
                'binding': measurements['binding'], 'truth_role': 'independent_evaluation_reference',
                'frame': 'z_up_metric_floor0', 'unit': 'm', 'reference_protocol_ref': {'id': '', 'digest': ''},
                'requirements': dict.fromkeys(REQUIREMENTS), 'windows': []})
            print(json.dumps({'status': 'created', 'kind': 'incomplete_independent_references', 'physical_authority': False}))
            return 3
        report = score_assessment(measurements, read_json(options.references))
        write(options.output, report)
        print(json.dumps({'status': 'scored', 'decision': report['decision'],
                          'missing_requirements': report['missing_requirements'],
                          'failed_requirements': report['failed_requirements'], 'physical_authority': False}))
        return 0 if report['decision'] == 'proceed' else 3
    except (ValueError, OSError, KeyError, TypeError, OverflowError, RecursionError):
        # No private values, file paths, coordinates or exceptions in stdout.
        print(json.dumps({'status': 'refused', 'reason': 'invalid_or_unavailable_owner_local_assessment',
                          'physical_authority': False}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
