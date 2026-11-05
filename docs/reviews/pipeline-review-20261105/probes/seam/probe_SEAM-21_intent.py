"""probe_SEAM-21_intent.py: what den-n2's score.py does when its default
--den-core (tools/den-core beside tools/den-n2) is absent from the checkout.

Verifier probe (intent/spec lens) for finding SEAM-21. No data, no network.

    python3 -B probe_SEAM-21_intent.py <root of a claude/den-n2 checkout>

1. Lists whether tools/den-core exists in the checkout.
2. Calls score.load_checker(score.DEFAULT_DEN_CORE) (the step run() takes right
   after N1's freeze lines are read) and prints what it raises.
3. Runs score.main() with --den-core set to an empty temp folder and a fake
   freeze-free N1 to show the refusal order (exit code and message).
"""
import contextlib
import io
import os
import sys
import tempfile

sys.dont_write_bytecode = True


def main():
    root = sys.argv[1]
    here = os.path.join(root, 'tools', 'den-n2')
    sys.path.insert(0, here)
    import score
    print('tools/den-core present: %s' % os.path.isdir(os.path.join(root, 'tools', 'den-core')))
    print('DEFAULT_DEN_CORE = %s' % score.DEFAULT_DEN_CORE)
    try:
        score.load_checker(score.DEFAULT_DEN_CORE)
        print('RESULT load_checker(default): loaded (no failure)')
    except score.Refused as e:
        print('RESULT load_checker(default): Refused: %s' % e)
    except Exception as e:  # anything else would be a crash, not a refusal
        print('RESULT load_checker(default): CRASH %s: %s' % (type(e).__name__, e))
    with tempfile.TemporaryDirectory(prefix='seam21-') as tmp:
        err, out = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            try:
                rc = score.main(['--split', 'development', '--takes', tmp, '--n1', os.path.join(tmp, 'n1'),
                                 '--n2', os.path.join(tmp, 'n2'), '--den-core', os.path.join(tmp, 'no-den-core')])
            except SystemExit as e:
                rc = e.code
        print('RESULT main with absent --den-core: rc=%s stderr=%r stdout=%r' % (rc, err.getvalue().strip()[-300:],
                                                                                 out.getvalue().strip()[-300:]))


if __name__ == '__main__':
    main()
