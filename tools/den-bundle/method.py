"""Read METHOD-V1.md and ROUTER-V0.md: one fenced JSON block each.

    ```json den-bundle-method          ```json den-bundle-router
    { ... }                            { ... }
    ```                                ```

A slot the contracts leave open is written

    {"open": true, "proposed": <value>, "why": "<what decides it>"}

Practice runs (synthetic fixtures, fakes) use the proposed value. A scored run
refuses while any slot is open. The block's sha256 is taken over its canonical
JSON (sorted keys, no spaces), so CRLF or LF, and the prose around the block,
never change it.
"""
import hashlib
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
METHOD_PATH = os.path.join(HERE, 'METHOD-V1.md')
ROUTER_PATH = os.path.join(HERE, 'ROUTER-V0.md')
METHOD_FENCE = '```json den-bundle-method'
ROUTER_FENCE = '```json den-bundle-router'

METHOD_KEYS = ('method', 'version', 'window_s', 'max_gap_s', 'depth_skew_s',
               'pad_fraction', 'pad_min_px', 'frame_px')
ROUTER_KEYS = ('rule', 'version', 'min_interval_s', 'max_per_minute', 'minute_s',
               'max_in_flight', 'backoff_s', 'order', 'eligible_states', 'timeout_s')


class MethodError(Exception):
    pass


def read_block(path, fence):
    with open(path, encoding='utf-8', newline=None) as f:   # universal newlines: CRLF reads as LF
        lines = f.read().split('\n')
    starts = [i for i, line in enumerate(lines) if line.strip() == fence]
    if len(starts) != 1:
        raise MethodError('%s must hold exactly one "%s" block, found %d' % (path, fence, len(starts)))
    body = []
    for line in lines[starts[0] + 1:]:
        if line.strip() == '```':
            break
        body.append(line)
    else:
        raise MethodError('the block in %s is not closed' % path)
    try:
        block = json.loads('\n'.join(body))
    except ValueError as e:
        raise MethodError('the block in %s is not valid JSON: %s' % (path, e))
    if not isinstance(block, dict):
        raise MethodError('the block in %s must be a JSON object' % path)
    return block


def is_open(value):
    return isinstance(value, dict) and value.get('open') is True


def canonical_sha256(block):
    text = json.dumps(block, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


class Settings:
    """A resolved block: values (proposed ones for open slots), the open slot names, the hash."""

    def __init__(self, block, keys, name_key, source='(inline)'):
        missing = [k for k in keys if k not in block]
        extra = [k for k in block if k not in keys]
        if missing or extra:
            raise MethodError('%s: keys differ from method.py (missing %s, extra %s)' % (source, missing, extra))
        self.block = block
        self.source = source
        self.sha256 = canonical_sha256(block)
        self.version = '%s-%s' % (block[name_key], block['version'])
        self.open = []
        self.values = {}
        for key in keys:
            value = block[key]
            if is_open(value):
                if 'proposed' not in value:
                    raise MethodError('%s: open slot %s needs a proposed value' % (source, key))
                self.open.append(key)
                value = value['proposed']
            self.values[key] = value

    def __getitem__(self, key):
        return self.values[key]

    def stamp(self):
        """What every record names: which settings, which exact text, and what was still open."""
        return {'version': self.version, 'sha256': self.sha256, 'open': list(self.open)}

    def require_closed(self, what='a scored run'):
        if self.open:
            raise MethodError('%s refuses: %s has open slots: %s' % (what, self.source, ', '.join(self.open)))


def method_from(block, source='(inline)'):
    s = Settings(block, METHOD_KEYS, 'method', source)
    for key in ('window_s', 'max_gap_s', 'depth_skew_s', 'pad_fraction', 'pad_min_px'):
        if not isinstance(s[key], (int, float)) or isinstance(s[key], bool) or s[key] < 0:
            raise MethodError('%s: %s must be a number >= 0' % (source, key))
    if s['window_s'] <= 0:
        raise MethodError('%s: window_s must be > 0' % source)
    for cam, size in s['frame_px'].items():
        if (not isinstance(size, list) or len(size) != 2
                or any(not isinstance(v, int) or v <= 0 or v % 2 for v in size)):
            raise MethodError('%s: frame_px.%s must be [width, height], even whole pixels' % (source, cam))
    return s


def router_from(block, source='(inline)'):
    s = Settings(block, ROUTER_KEYS, 'rule', source)
    for key in ('min_interval_s', 'minute_s', 'backoff_s', 'timeout_s'):
        if not isinstance(s[key], (int, float)) or isinstance(s[key], bool) or s[key] <= 0:
            raise MethodError('%s: %s must be a number > 0' % (source, key))
    for key in ('max_per_minute', 'max_in_flight'):
        if not isinstance(s[key], int) or s[key] < 1:
            raise MethodError('%s: %s must be a whole number >= 1' % (source, key))
    if s['max_in_flight'] != 1:
        raise MethodError('%s: router v0 supports one request in flight only' % source)
    if s['order'] != 'round-robin':
        raise MethodError('%s: router v0 knows only round-robin order' % source)
    return s


def load_method(path=METHOD_PATH, scored=False):
    s = method_from(read_block(path, METHOD_FENCE), os.path.basename(path))
    if scored:
        s.require_closed()
    return s


def load_router(path=ROUTER_PATH, scored=False):
    s = router_from(read_block(path, ROUTER_FENCE), os.path.basename(path))
    if scored:
        s.require_closed()
    return s
