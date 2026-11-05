"""Replay check (ladder rung 0 in miniature): a logged run re-runs from its logged
inputs, code and config to the same bytes, header included.

python -B tools/den-core/check.py LOG  prints OK or the first mismatch; exits 0 or 1.
"""
import sys
from pathlib import Path

import events
import runner


def _refuse(reason):
    return {"ok": False, "reason": reason, "first_mismatch": None}


def check(log_path, resolve=None, on_policy=None):
    """{ok, reason, first_mismatch: {line, logged, replayed} | None}; lines count from 1 (the header).
    on_policy(policy), if given, sees each rebuilt policy before the replay; score.py times the coordinator with it."""
    try:
        header, logged = events.read(log_path)
    except ValueError as error:
        return _refuse("unreadable log: %s" % error)
    inputs = [(e["t"], e["kind"], e["data"], e["source"]) for e in logged if e["source"] in events.ADAPTER_SOURCES]
    policies = []
    for entry in header["policies"]:
        name = entry["module"]
        module = (resolve or {}).get(name) or runner.import_module(name)
        if events.sha256_file(module.__file__) != entry["code_sha256"]:
            return _refuse("code changed: %s" % name)
        try:
            policy = module.make_policy(entry["config"])
        except ValueError as error:
            return _refuse("%s refused its logged config: %s" % (name, error))
        if len({entry["config_sha256"], events.sha256_json(entry["config"]), events.sha256_json(policy.config)}) > 1:
            return _refuse("config changed: %s" % name)
        if on_policy:
            on_policy(policy)
        policies.append(policy)
    params = {key: header[key] for key in ("tick_s", "tail_s", "plant", "shown", "synthetic", "assume_occupied", "actuation")}
    try:
        replay_header, replayed = runner.run(inputs, policies, sources=header["sources"], **params)
    except ValueError as error:
        return _refuse("the log's header does not replay: %s" % error)
    want = Path(log_path).read_bytes().decode("ascii").split("\n")[:-1]
    got = [events.dumps(value) for value in [replay_header, *replayed]]
    for index in range(max(len(want), len(got))):
        a = want[index] if index < len(want) else None
        b = got[index] if index < len(got) else None
        if a != b:
            return {"ok": False, "reason": "line %d differs" % (index + 1),
                    "first_mismatch": {"line": index + 1, "logged": a, "replayed": b}}
    return {"ok": True, "reason": "%d lines replayed byte-identically" % len(got), "first_mismatch": None}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -B tools/den-core/check.py LOG")
    result = check(sys.argv[1])
    if result["ok"]:
        print("OK: " + result["reason"])
    else:
        print("FAIL: " + result["reason"])
        if result["first_mismatch"]:
            print("  logged:   %s\n  replayed: %s" % (result["first_mismatch"]["logged"],
                                                    result["first_mismatch"]["replayed"]))
    sys.exit(0 if result["ok"] else 1)
