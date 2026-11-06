"""Replay check (ladder rung 0 in miniature): a logged run re-runs from its logged
inputs, code and config to the same bytes, header included.

python -B tools/den-core/check.py LOG  prints OK or the first mismatch; exits 0 or 1. A file it can't read at all, the log or a
logged light model, or one whose header, config or input lines are the wrong shape to replay, is one sentence and exit 2.
"""
import sys

import events
import runner


def _refuse(reason, unreadable=False):
    return {"ok": False, "reason": reason, "first_mismatch": None, **({"unreadable": True} if unreadable else {})}


def config_changed(entry, policy):
    """True unless a header's config, its hash and what the policy rebuilt from it all agree."""
    return len({entry["config_sha256"], events.sha256_json(entry["config"]), events.sha256_json(policy.config)}) > 1


def check(log_path, resolve=None, on_policy=None):
    """{ok, reason, first_mismatch: {line, logged, replayed} | None}, and unreadable: True when a file could not be read, which
    a command line refuses rather than fails; lines count from 1 (the header).
    on_policy(policy), if given, sees each rebuilt policy before the replay; score.py times the coordinator with it."""
    try:
        header, logged = events.read_run(log_path)
    except events.BadFile as error:
        return _refuse(str(error), unreadable=True)
    except (OSError, ValueError) as error:
        return _refuse("unreadable log: %s" % error, unreadable=True)
    inputs = [(e["t"], e["kind"], e["data"], e["source"]) for e in logged if e["source"] in events.ADAPTER_SOURCES]
    policies = []
    for entry in header["policies"]:
        name = entry["module"]
        try:
            module = (resolve or {}).get(name) or runner.import_module(name)
        except (ValueError, ImportError) as error:
            return _refuse("the log names a policy this tool does not have: %s" % error, unreadable=True)
        if events.sha256_file(module.__file__) != entry["code_sha256"]:
            return _refuse("code changed: %s" % name)
        try:
            policy = module.make_policy(entry["config"])
        except events.BadFile as error:
            return _refuse(str(error), unreadable=True)
        except ValueError as error:
            return _refuse("%s refused its logged config: %s" % (name, error))
        except events.SHAPE_ERRORS as error:
            return _refuse("%s can't be rebuilt from its logged config, which is the wrong shape: %s" % (name, events.named(error)), unreadable=True)
        if config_changed(entry, policy):
            return _refuse("config changed: %s" % name)
        if on_policy:
            on_policy(policy)
        policies.append(policy)
    params = {key: header[key] for key in ("tick_s", "tail_s", "plant", "shown", "synthetic", "assume_occupied", "actuation")}
    try:
        replay_header, replayed = runner.run(inputs, policies, sources=header["sources"], **params)
    except ValueError as error:
        return _refuse("the log's header does not replay: %s" % error)
    except events.SHAPE_ERRORS as error:
        return _refuse("the log does not replay: an input line or a logged config is the wrong shape, or a policy has a bug: %s" % events.named(error),
                       unreadable=True)
    want = events.log_lines(log_path)
    got = [events.dumps(value) for value in [replay_header, *replayed]]
    for index in range(max(len(want), len(got))):
        a = want[index] if index < len(want) else None
        b = got[index] if index < len(got) else None
        if a != b:
            return {"ok": False, "reason": "line %d differs" % (index + 1),
                    "first_mismatch": {"line": index + 1, "logged": a, "replayed": b}}
    return {"ok": True, "reason": "%d lines replayed byte-identically" % len(got), "first_mismatch": None}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        sys.exit("usage: python -B tools/den-core/check.py LOG")
    try:
        result = check(argv[0])
    except OSError as error:  # a checkout with no den_lighting, say
        print("check.py: %s" % error, file=sys.stderr)
        return 2
    except Exception as error:
        print(events.stopped("check.py", error), file=sys.stderr)
        return 2
    if result.get("unreadable"):
        print(result["reason"], file=sys.stderr)
        return 2
    if result["ok"]:
        print("OK: " + result["reason"])
    else:
        print("FAIL: " + result["reason"])
        if result["first_mismatch"]:
            print("  logged:   %s\n  replayed: %s" % (result["first_mismatch"]["logged"],
                                                    result["first_mismatch"]["replayed"]))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
