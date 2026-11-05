"""probe_atk_open_slot_enforcement: does den-track refuse a scored run while a
METHOD.md slot is open?

Spec README: "A field or number the contracts leave open goes into the tool's
own method or gate file as an empty slot, marked open, with a proposed value.
The tool refuses a scored run while a slot is open."

Checks, against tools/den-track/method.py (copied to a temp dir):
  1. the branch ships no METHOD.md, and nothing outside method.py calls
     require_closed() (no scored-run entry point exists);
  2. a METHOD.md (written to a temp dir) whose open slot sits inside a JSON
     list, e.g. per-window-kind thresholds, is it found and refused?
  3. a slot marked "open": "yes" / 1 (not the JSON literal true), is it
     treated as open, or as a closed value that is the slot dict itself?

Run:  python3 -B probe_atk_open_slot_enforcement.py <branch checkout root>
Synthetic files in temp dirs only.
"""
import os
import re
import shutil
import sys
import tempfile

sys.dont_write_bytecode = True


def load(root):
    tmp = tempfile.mkdtemp(prefix='r3atk-')
    shutil.copytree(os.path.join(root, 'tools', 'den-track'), os.path.join(tmp, 'den-track'))
    sys.path.insert(0, os.path.join(tmp, 'den-track'))
    import method
    return method, os.path.join(tmp, 'den-track')


def write_method(body):
    d = tempfile.mkdtemp(prefix='r3atk-method-')
    p = os.path.join(d, 'METHOD.md')
    with open(p, 'w', encoding='utf-8', newline='\r\n') as f:   # the owner edits on Windows
        f.write('# METHOD\n\n```json den-track-method\n' + body + '\n```\n')
    return p


def main(root):
    method, tool = load(root)
    bad = 0
    shipped = os.path.exists(os.path.join(root, 'tools', 'den-track', 'METHOD.md'))
    callers = []
    for name in sorted(os.listdir(tool)):
        if name.endswith('.py') and name != 'method.py':
            with open(os.path.join(tool, name), encoding='utf-8') as f:
                if re.search(r'require_closed|import method|from method', f.read()):
                    callers.append(name)
    print('1. METHOD.md shipped: %s; modules that use method.py / require_closed: %s'
          % (shipped, callers or 'none'))
    if not shipped and not callers:
        print('FAIL 1: no method file and no scored-run path that could refuse')
        bad += 1

    p = write_method('{"version": "den-track-method-0", "scoring": {"idf1_gate": 0.9, '
                     '"confident_at_by_kind": [{"kind": "crossing", '
                     '"confident_at": {"open": true, "proposed": 0.9, "why": "set on development"}}]}}')
    m = method.Method(p)
    try:
        m.require_closed()
        refused = False
    except method.MethodError as e:
        refused = True
    val = m.scoring['confident_at_by_kind'][0]['confident_at']
    print('2. open slot inside a list: open_slots=%s refused=%s resolved value=%r' % (m.open, refused, val))
    if not refused:
        print('FAIL 2: a scored run would go ahead with an open slot (and with the slot dict as its value)')
        bad += 1

    p = write_method('{"version": "den-track-method-0", "scoring": {"confident_at": '
                     '{"open": "yes", "proposed": 0.9, "why": "set on development"}}}')
    m = method.Method(p)
    try:
        m.require_closed()
        refused = False
    except method.MethodError:
        refused = True
    print('3. "open": "yes": open_slots=%s refused=%s resolved value=%r' % (m.open, refused, m.scoring['confident_at']))
    if not refused:
        print('FAIL 3: a slot the owner marked open is taken as closed')
        bad += 1
    print('RESULT: %s (%d problem(s))' % ('FAIL' if bad else 'PASS', bad))
    return 1 if bad else 0


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit('usage: python3 -B probe_atk_open_slot_enforcement.py <branch checkout root>')
    sys.exit(main(sys.argv[1]))
