"""The frozen numbers live in METHOD.md, in one fenced block:

    ```json den-track-method
    { ... }
    ```

This module reads that block (CRLF or LF, it reads the same), resolves open
slots and fingerprints the block and the tool's own source, so a score always
names the exact method and code it ran under. A slot is open when its value is

    {"open": true, "proposed": <value>, "why": "<what decides it>"}

Practice runs on synthetic scenes use the proposed value. A scored run
refuses while any slot is open.
"""
import hashlib
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
METHOD_PATH = os.path.join(HERE, 'METHOD.md')
CODE_DIR = HERE
FENCE = '```json den-track-method'


class MethodError(Exception):
    pass


def read_block(path=METHOD_PATH):
    with open(path, encoding='utf-8-sig', newline=None) as f:   # universal newlines: CRLF == LF
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


def code_fingerprint(folder=None):
    """sha256 over the tool's own source: every .py file but the tests, as text, so CRLF and LF hash alike."""
    folder = folder or CODE_DIR
    digest = hashlib.sha256()
    for name in sorted(os.listdir(folder)):
        if name.endswith('.py') and not name.startswith('test_'):
            with open(os.path.join(folder, name), encoding='utf-8-sig', newline=None) as f:
                digest.update(('%s\0%s\0' % (name, f.read())).encode('utf-8'))
    return 'sha256:' + digest.hexdigest()


class Method:
    def __init__(self, path=METHOD_PATH):
        self.path = path
        self.block = read_block(path)
        self.open = open_slots(self.block)
        self.values = resolve(self.block)
        self.fingerprint = fingerprint(self.block)
        self.code_fingerprint = code_fingerprint()
        self.version = self.block.get('version')

    @property
    def tracker(self):
        return self.values['tracker']

    @property
    def scoring(self):
        return self.values['scoring']

    @property
    def room(self):
        return self.values['room']

    def override(self, dotted, value):
        """Practice only, for tuning: this changes the values, not the block or its fingerprint."""
        section, key = dotted.split('.', 1)
        if section not in self.values or key not in self.values[section]:
            raise MethodError('no such value in the method: %s' % dotted)
        self.values[section][key] = value

    def require_closed(self):
        if self.open:
            names = ', '.join(name for name, _ in self.open)
            raise MethodError('refusing a scored run: METHOD.md has %d open slot(s): %s. '
                              'The owner closes each one in METHOD.md first.' % (len(self.open), names))
