"""Fake clock, fake brain and a timeline replayer for offline tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from victoria_ha_bridge.core import Bridge, BridgeConfig, PostResult

FIXTURES = Path(__file__).resolve().parent / "fixtures"
T0 = 1_760_000_000.0  # fixed epoch for every run; tests talk in seconds from T0
DEN_ZONES = {"desk": "den_desk", "sofa": "den_sofa"}


class FakeClock:
    def __init__(self, start: float = T0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def rel(self) -> float:
        return self.now - T0


class FakeBrain:
    """Records accepted batches. `down(t)` decides, per attempt, if the brain is unreachable."""

    def __init__(self, clock: FakeClock, down: Callable[[float], bool] | None = None,
                 reject_status: int | None = None) -> None:
        self.clock = clock
        self.down = down or (lambda _t: False)
        self.reject_status = reject_status
        self.batches: list[tuple[float, dict[str, Any]]] = []
        self.attempts: list[float] = []

    def post(self, body: dict[str, Any]) -> PostResult:
        t = self.clock.rel()
        self.attempts.append(t)
        if self.down(t):
            return PostResult(ok=False, retryable=True, detail="ConnectionRefusedError")
        if self.reject_status:
            return PostResult(ok=False, status=self.reject_status, detail="refused")
        self.batches.append((t, json.loads(json.dumps(body))))
        return PostResult(ok=True, status=202)

    @property
    def events(self) -> list[dict[str, Any]]:
        return [event for _t, body in self.batches for event in body["events"]]

    def of(self, type_: str, **match: Any) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] == type_ and all(e.get(k) == v for k, v in match.items())]


def rel(event: dict[str, Any], key: str = "observed_at") -> float:
    return round(event[key] - T0, 3)


def load_timeline(name: str) -> list[dict[str, Any]]:
    stats = (FIXTURES / "frigate_stats_den_oak.json").read_text(encoding="utf-8")
    steps = []
    for step in json.loads((FIXTURES / "mqtt" / name).read_text(encoding="utf-8")):
        if step.get("payload") == "@stats":
            step = dict(step, payload=stats)
        if "every" in step:
            start, end, interval = step["every"]
            t = start
            while t <= end:
                steps.append({k: v for k, v in step.items() if k != "every"} | {"at": t})
                t += interval
        else:
            steps.append(step)
    return sorted(steps, key=lambda s: s["at"])


def make_bridge(zones: dict[str, str] | None = None, down: Callable[[float], bool] | None = None,
                **config: Any) -> tuple[Bridge, FakeClock, FakeBrain]:
    clock = FakeClock()
    brain = FakeBrain(clock, down)
    bridge = Bridge(BridgeConfig(zones=dict(zones or {}), **config), clock, brain)
    return bridge, clock, brain


def apply(bridge: Bridge, step: dict[str, Any]) -> None:
    if "connect" in step:
        bridge.on_connection(bool(step["connect"]))
    else:
        bridge.on_message(step["topic"], step["payload"].encode("utf-8"), retained=step.get("retain", False))


def replay(bridge: Bridge, clock: FakeClock, steps: list[dict[str, Any]], until: float,
           tick_s: float = 1.0) -> None:
    """Feed steps at their times and tick once a second, like the runner's loop."""
    i = 0
    t = clock.rel()
    while t <= until:
        while i < len(steps) and steps[i]["at"] <= t:
            clock.now = T0 + steps[i]["at"]
            apply(bridge, steps[i])
            i += 1
        clock.now = T0 + t
        bridge.tick()
        t += tick_s
