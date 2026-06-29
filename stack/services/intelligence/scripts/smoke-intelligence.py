from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


def fetch_json(base: str, path: str) -> dict:
    with urllib.request.urlopen(base.rstrip("/") + path, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test Home Intelligence Core.")
    parser.add_argument("--base", default="http://127.0.0.1:8095")
    parser.add_argument("--min-raw-events", type=int, default=0)
    parser.add_argument("--min-observations", type=int, default=0)
    parser.add_argument("--expect-scheduler", action="store_true")
    args = parser.parse_args()

    try:
        health = fetch_json(args.base, "/healthz")
        scheduler = fetch_json(args.base, "/api/intelligence/scheduler")
        decisions = fetch_json(args.base, "/api/intelligence/decisions?limit=1")
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"smoke failed: {exc}", file=sys.stderr)
        return 1

    counts = health.get("counts") or {}
    failures: list[str] = []
    if not health.get("ok"):
        failures.append("healthz ok=false")
    if counts.get("raw_events", 0) < args.min_raw_events:
        failures.append(f"raw_events below expected: {counts.get('raw_events', 0)} < {args.min_raw_events}")
    if counts.get("preference_observations", 0) < args.min_observations:
        failures.append(
            "preference_observations below expected: "
            f"{counts.get('preference_observations', 0)} < {args.min_observations}"
        )
    if "decisions" not in counts:
        failures.append("decisions table count missing from healthz")
    if not decisions.get("ok"):
        failures.append("decisions endpoint ok=false")
    if args.expect_scheduler:
        sched = scheduler.get("scheduler") or {}
        if not sched.get("enabled"):
            failures.append("scheduler disabled")
        if not sched.get("running"):
            failures.append("scheduler not running")

    summary = {
        "ok": not failures,
        "counts": counts,
        "scheduler": scheduler.get("scheduler"),
        "decision_sample_count": len(decisions.get("decisions") or []),
        "failures": failures,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
