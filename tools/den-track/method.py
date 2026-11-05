"""The frozen numbers live in METHOD.md, in one fenced block:

    ```json den-track-method
    { ... }
    ```

This module reads that block (CRLF or LF, it reads the same), resolves open
slots and fingerprints the block, so a score always names the exact method
it ran under. A slot is open when its value is

    {"open": true, "proposed": <value>, "why": "<what decides it>"}

Practice runs on synthetic scenes use the proposed value. A scored run
refuses while any slot is open.
"""
import hashlib
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
METHOD_PATH = os.path.join(HERE, 'METHOD.md')
FENCE = '```json den-track-method'


class MethodError(Exception):
    pass


def read_block(path=METHOD_PATH):
    with open(path, encoding='utf-8', newline=None) as f:   # universal newlines: CRLF == LF
        lines = f.read().split('\n')
    starts = [i for i, line in enumerate(lines) if line.strip() == FENCE]
    if len(starts) != 1:
        raise MethodError('%s must hold exactly one "%s" block, found %d' % (path, FENCE, len(starts)))
    body = []
    for line in lines[starts[0] + 1:]:
        if line.strip() == '```':
            break
        body.append(line)
    else:
        raise MethodError('the method block in %s is not closed' % path)
    try:
        return json.loads('\n'.join(body))
    except ValueError as e:
        raise MethodError('the method block in %s is not valid JSON: %s' % (path, e))


def is_open(value):
    return isinstance(value, dict) and value.get('open') is True


def open_slots(block):
    """[(dotted.name, slot), ...] for every open slot, in file order."""
    out = []

    def walk(prefix, node):
        if is_open(node):
            out.append((prefix, node))
        elif isinstance(node, dict):
            for k, v in node.items():
                walk('%s.%s' % (prefix, k) if prefix else k, v)

    walk('', block)
    return out


def resolve(node):
    """The block with every open slot replaced by its proposed value."""
    if is_open(node):
        return node['proposed']
    if isinstance(node, dict):
        return {k: resolve(v) for k, v in node.items()}
    return node


def fingerprint(block):
    canon = json.dumps(block, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    return 'sha256:' + hashlib.sha256(canon.encode('ascii')).hexdigest()


class Method:
    def __init__(self, path=METHOD_PATH):
        self.path = path
        self.block = read_block(path)
        self.open = open_slots(self.block)
        self.values = resolve(self.block)
        self.fingerprint = fingerprint(self.block)
        self.version = self.block.get('version')

    @property
    def tracker(self):
        return self.values['tracker']

    @property
    def scoring(self):
        return self.values['scoring']

    def require_closed(self):
        if self.open:
            names = ', '.join(name for name, _ in self.open)
            raise MethodError('refusing a scored run: METHOD.md has %d open slot(s): %s. '
                              'The owner closes each one in METHOD.md first.' % (len(self.open), names))
