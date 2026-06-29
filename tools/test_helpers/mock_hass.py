"""Shared MockHass + supporting mocks for the test suite.

Drop-in substitute for `homeassistant.core.HomeAssistant` that supports:

- `hass.states.get(entity_id)` / `async_all()` / `set(...)`
- `hass.bus.async_fire(event_type, data)` / `async_listen(event_type, cb)`
- `hass.services.async_call(domain, service, data, blocking)` (records)
- `hass.async_add_executor_job(fn, *args)` (sync)
- `hass.add_job(target, *args)` (schedules onto current loop; the key
  primitive that makes `bus.async_fire` work when called from a worker
  thread — this is the regression class from Addendum 16 S4)
- `hass.data` (dict)

Plus convenience helpers:
- `hass.set_state(entity_id, state, attrs)` — single-call seed
- `hass.add_area(area_id, name=None)` — registers an area
- `hass.add_entity(entity_id, state, attrs, area_id=None)` — combo seed
- `hass.fire_state_changed(entity_id, new_state, old_state=None)` —
  fires a `state_changed` bus event with the standard payload shape
- `hass.service_calls_for(domain, service, **filters)` — filter list

The mock has its OWN smoke test at the bottom of this file — run it
with `py -3 mock_hass.py` to verify the helper is healthy before using
it in downstream tests.
"""

from __future__ import annotations

import asyncio
import sys
import threading
from dataclasses import dataclass, field
from typing import Any, Callable


# ───────────────────────────────────────────────────────────────────
# Minimal State + Bus + Services
# ───────────────────────────────────────────────────────────────────

class State:
    """Mirrors homeassistant.core.State minimally."""

    def __init__(self, entity_id: str, state: str, attributes: dict | None = None):
        self.entity_id = entity_id
        self.state = state
        self.attributes = attributes or {}
        self.last_changed = None
        self.last_updated = None

    def __repr__(self) -> str:
        return f"<State {self.entity_id}={self.state}>"


class MockStates:
    """In-memory state store. Mimics hass.states.{get, async_all, set}."""

    def __init__(self) -> None:
        self._states: dict[str, State] = {}

    def get(self, entity_id: str) -> State | None:
        return self._states.get(entity_id)

    def async_all(self) -> list[State]:
        return list(self._states.values())

    def set(self, entity_id: str, new_state: str, attributes: dict | None = None) -> None:
        """Replace state without firing state_changed. Use
        `hass.fire_state_changed(...)` if you want to trigger listeners."""
        self._states[entity_id] = State(entity_id, new_state, attributes or {})

    def remove(self, entity_id: str) -> None:
        self._states.pop(entity_id, None)


@dataclass
class FiredEvent:
    event_type: str
    data: Any


class MockBus:
    """Records fired events + dispatches them to registered listeners.

    Listeners registered via `async_listen(event_type, cb)` receive an
    object with `.data` attribute (mimicking HA's Event shape).

    CRITICAL semantics for the bus.async_fire-from-executor-thread bug
    (Addendum 16 S4): `async_fire` ALWAYS appends to `.fired` regardless
    of which thread calls it. But it ONLY dispatches to listeners on the
    main thread. If called from a worker thread, listeners aren't
    notified — exactly the production behavior. Use `hass.add_job(
    bus.async_fire, type, data)` from worker threads to schedule the
    dispatch onto the main loop.

    This makes the mock a TRUE regression instrument for the
    cross-thread bug.
    """

    def __init__(self) -> None:
        self.fired: list[FiredEvent] = []
        self._listeners: dict[str, list[Callable]] = {}
        self._main_thread_id = threading.get_ident()

    def async_fire(self, event_type: str, data: Any | None = None) -> None:
        evt = FiredEvent(event_type, data or {})
        self.fired.append(evt)
        # Dispatch to listeners ONLY when called from the main thread.
        # Cross-thread calls hit the same drop pattern as real HA.
        if threading.get_ident() == self._main_thread_id:
            self._dispatch(event_type, evt)

    def _dispatch(self, event_type: str, evt: FiredEvent) -> None:
        # Build a tiny event-shape that listeners expect.
        class _EventStub:
            def __init__(self, data):
                self.data = data
        ev = _EventStub(evt.data)
        for cb in self._listeners.get(event_type, []):
            try:
                cb(ev)
            except Exception as exc:
                # Match HA behavior: listener exceptions don't
                # propagate up out of the bus.
                print(f"[mock_hass] listener error for {event_type}: {exc}", file=sys.stderr)

    def async_listen(self, event_type: str, callback: Callable) -> Callable:
        """Returns an unsubscribe function (mirrors HA)."""
        self._listeners.setdefault(event_type, []).append(callback)

        def _unsub() -> None:
            if event_type in self._listeners:
                try:
                    self._listeners[event_type].remove(callback)
                except ValueError:
                    pass
        return _unsub

    def reset_main_thread(self) -> None:
        """Re-pin the main thread to the current thread. Useful when
        tests are restructured (e.g., asyncio.run starts a new thread)."""
        self._main_thread_id = threading.get_ident()


@dataclass
class ServiceCall:
    domain: str
    service: str
    data: dict
    blocking: bool


class MockServices:
    """Records every async_call. Tests assert against .calls."""

    def __init__(self) -> None:
        self.calls: list[ServiceCall] = []
        # Optional async hook — if set, runs in addition to recording.
        self._async_hook: Callable | None = None

    async def async_call(
        self,
        domain: str,
        service: str,
        service_data: dict | None = None,
        blocking: bool = False,
        **kwargs: Any,
    ) -> Any:
        call = ServiceCall(domain, service, dict(service_data or {}), blocking)
        self.calls.append(call)
        if self._async_hook is not None:
            return await self._async_hook(call)
        return None

    def set_async_hook(self, hook: Callable) -> None:
        """Provide a custom async hook that runs after recording. Used
        for tests that need a specific return value or side-effect."""
        self._async_hook = hook


# ───────────────────────────────────────────────────────────────────
# Registry stubs (area / entity)
# ───────────────────────────────────────────────────────────────────

@dataclass
class AreaEntry:
    id: str
    name: str
    aliases: set[str] = field(default_factory=set)


@dataclass
class EntityRegistryEntry:
    entity_id: str
    area_id: str | None = None
    device_id: str | None = None
    name: str | None = None


class MockAreaRegistry:
    def __init__(self) -> None:
        self.areas: dict[str, AreaEntry] = {}

    def async_get(self, area_id: str) -> AreaEntry | None:
        return self.areas.get(area_id)

    def async_list_areas(self) -> list[AreaEntry]:
        return list(self.areas.values())

    def add(self, area_id: str, name: str | None = None) -> AreaEntry:
        entry = AreaEntry(id=area_id, name=name or area_id)
        self.areas[area_id] = entry
        return entry


class MockEntityRegistry:
    def __init__(self) -> None:
        self.entities: dict[str, EntityRegistryEntry] = {}

    def async_get(self, entity_id: str) -> EntityRegistryEntry | None:
        return self.entities.get(entity_id)

    def entries_for_area(self, area_id: str) -> list[EntityRegistryEntry]:
        return [e for e in self.entities.values() if e.area_id == area_id]

    def add(
        self,
        entity_id: str,
        area_id: str | None = None,
        device_id: str | None = None,
        name: str | None = None,
    ) -> EntityRegistryEntry:
        entry = EntityRegistryEntry(entity_id, area_id, device_id, name)
        self.entities[entity_id] = entry
        return entry


# ───────────────────────────────────────────────────────────────────
# MockHass — the actual root
# ───────────────────────────────────────────────────────────────────

class MockHass:
    """Test-only HomeAssistant surrogate."""

    def __init__(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self.states = MockStates()
        self.bus = MockBus()
        self.services = MockServices()
        self.data: dict[str, Any] = {}
        self.area_registry = MockAreaRegistry()
        self.entity_registry = MockEntityRegistry()
        self._loop = loop  # set automatically by add_job if needed
        self._unload_callbacks: list[Callable] = []

    # ── Convenience seeders ──────────────────────────────────────

    def set_state(
        self, entity_id: str, state: str, attrs: dict | None = None
    ) -> None:
        self.states.set(entity_id, state, attrs)

    def add_area(self, area_id: str, name: str | None = None) -> AreaEntry:
        return self.area_registry.add(area_id, name)

    def add_entity(
        self,
        entity_id: str,
        state: str = "off",
        attrs: dict | None = None,
        area_id: str | None = None,
        device_id: str | None = None,
        name: str | None = None,
    ) -> None:
        """Combo seed: registers the entity in the entity_registry AND
        seeds its state. Most tests want both."""
        merged_attrs = dict(attrs or {})
        if name and "friendly_name" not in merged_attrs:
            merged_attrs["friendly_name"] = name
        self.states.set(entity_id, state, merged_attrs)
        self.entity_registry.add(entity_id, area_id=area_id, device_id=device_id, name=name)

    def fire_state_changed(
        self,
        entity_id: str,
        new_state: State | dict | None,
        old_state: State | None = None,
    ) -> None:
        """Fire a state_changed bus event with the canonical payload
        shape. If `new_state` is a dict, it's wrapped in a State."""
        if isinstance(new_state, dict):
            new_state = State(
                entity_id,
                new_state.get("state", "on"),
                new_state.get("attributes", {}),
            )
        # Update the state store too so subsequent .get() reflects it.
        if new_state is not None:
            self.states._states[entity_id] = new_state
        self.bus.async_fire(
            "state_changed",
            {"entity_id": entity_id, "new_state": new_state, "old_state": old_state},
        )

    def service_calls_for(
        self,
        domain: str,
        service: str | None = None,
        **filter_data: Any,
    ) -> list[ServiceCall]:
        """Filter recorded service calls by domain + optional service +
        optional service_data fields."""
        out = []
        for c in self.services.calls:
            if c.domain != domain:
                continue
            if service is not None and c.service != service:
                continue
            if all(c.data.get(k) == v for k, v in filter_data.items()):
                out.append(c)
        return out

    # ── HA-API surface ──────────────────────────────────────────

    async def async_add_executor_job(self, fn: Callable, *args: Any) -> Any:
        """Run in 'executor'. For tests we just run synchronously — the
        observable behavior matches what tests need."""
        return fn(*args)

    def add_job(self, target: Callable, *args: Any) -> None:
        """Schedule `target(*args)` onto the event loop.

        This is the cross-thread bridge: when a worker thread calls
        `hass.add_job(bus.async_fire, type, data)`, the bus call runs
        on the main loop and listeners get notified.

        Implementation: if we have a running loop, schedule via
        call_soon_threadsafe; otherwise run synchronously (single-
        threaded test). The latter is fine because the bug we're
        guarding against (silent drop) requires the call to happen ON
        the main thread, which synchronous execution satisfies.
        """
        loop = self._loop
        if loop is None:
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = None
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(target, *args)
        else:
            target(*args)

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Pin a specific loop for add_job dispatch."""
        self._loop = loop

    def register_unload(self, callback: Callable) -> None:
        """Track callbacks that simulated async_unload_entry should
        invoke. Used by lifecycle tests."""
        self._unload_callbacks.append(callback)

    async def simulate_unload(self) -> None:
        """Run every registered unload callback in order. Used by tests
        that simulate `async_unload_entry`."""
        for cb in list(self._unload_callbacks):
            res = cb()
            if asyncio.iscoroutine(res):
                await res

    def reset(self) -> None:
        """Re-pin main thread + clear fired/calls/unload registrations.
        Useful between scenarios when a test class reuses the mock."""
        self.bus.fired.clear()
        self.bus._listeners.clear()
        self.bus.reset_main_thread()
        self.services.calls.clear()
        self._unload_callbacks.clear()


# ───────────────────────────────────────────────────────────────────
# Self-test — run with `py -3 mock_hass.py`
# ───────────────────────────────────────────────────────────────────

def _selftest() -> int:
    import sys as _sys

    pass_count = 0
    fail_count = 0
    failures: list[str] = []

    def _assert(name: str, cond: bool, detail: Any = "") -> None:
        nonlocal pass_count, fail_count
        if cond:
            pass_count += 1
            print(f"  PASS  {name}")
        else:
            fail_count += 1
            failures.append(f"{name}: {detail!r}")
            print(f"  FAIL  {name}  ({detail!r})")

    print("\n[mock_hass self-test]\n")

    # ── states ──
    h = MockHass()
    h.set_state("light.kitchen", "on", {"brightness": 200})
    s = h.states.get("light.kitchen")
    _assert("state set + get", s is not None and s.state == "on")
    _assert("state attrs preserved", s.attributes["brightness"] == 200)
    _assert("missing state returns None", h.states.get("light.nope") is None)
    _assert("async_all returns the seeded state",
            any(x.entity_id == "light.kitchen" for x in h.states.async_all()))

    # ── area + entity registry ──
    h.add_area("kitchen", "Kitchen")
    h.add_entity("media_player.kitchen_stereo", "off",
                 attrs={"friendly_name": "Kitchen Stereo"},
                 area_id="kitchen")
    h.add_entity("media_player.kitchen_sub", "off", area_id="kitchen")
    h.add_entity("light.living", "off", area_id="living_room")
    _assert("area registered",
            h.area_registry.async_get("kitchen") is not None)
    entries = h.entity_registry.entries_for_area("kitchen")
    _assert("entity registry filters by area",
            len(entries) == 2 and
            {e.entity_id for e in entries} ==
            {"media_player.kitchen_stereo", "media_player.kitchen_sub"})
    _assert("entity registry get",
            h.entity_registry.async_get("media_player.kitchen_stereo") is not None)

    # ── bus listener (main thread) ──
    received: list = []
    unsub = h.bus.async_listen("test_event", lambda ev: received.append(ev.data))
    h.bus.async_fire("test_event", {"x": 1})
    _assert("main-thread fire dispatches to listener",
            len(received) == 1 and received[0]["x"] == 1)
    _assert("fired log contains event",
            len(h.bus.fired) == 1 and h.bus.fired[0].event_type == "test_event")
    unsub()
    h.bus.async_fire("test_event", {"x": 2})
    _assert("unsub stops delivery", len(received) == 1)

    # ── bus listener (cross-thread — regression instrument for S4) ──
    h2 = MockHass()
    rec2: list = []
    h2.bus.async_listen("e", lambda ev: rec2.append(ev.data))
    def _fire_from_thread() -> None:
        # WITHOUT add_job: should silently drop (mimics S4 bug).
        h2.bus.async_fire("e", {"src": "worker"})
    t = threading.Thread(target=_fire_from_thread)
    t.start(); t.join()
    _assert("cross-thread fire WITHOUT add_job drops to listeners (S4 bug shape)",
            len(rec2) == 0)
    _assert("cross-thread fire WITHOUT add_job still records to .fired",
            len(h2.bus.fired) == 1)

    # With add_job: should reach listeners.
    async def _crossthread_with_addjob_test():
        h3 = MockHass()
        rec3: list = []
        h3.bus.async_listen("e", lambda ev: rec3.append(ev.data))
        h3.attach_loop(asyncio.get_running_loop())
        def _fire() -> None:
            h3.add_job(h3.bus.async_fire, "e", {"src": "worker"})
        await asyncio.to_thread(_fire)
        # Let the event loop process the scheduled call.
        await asyncio.sleep(0.01)
        return rec3
    rec3 = asyncio.run(_crossthread_with_addjob_test())
    _assert("cross-thread fire WITH add_job reaches listener (S4 fix shape)",
            len(rec3) == 1 and rec3[0]["src"] == "worker")

    # ── services ──
    h4 = MockHass()
    async def _call():
        await h4.services.async_call(
            "light", "turn_on",
            {"entity_id": "light.kitchen", "brightness_pct": 80},
            blocking=False,
        )
    asyncio.run(_call())
    calls = h4.service_calls_for("light", "turn_on")
    _assert("service call recorded", len(calls) == 1)
    _assert("service call data preserved",
            calls[0].data["brightness_pct"] == 80)
    _assert("service call filter by data",
            len(h4.service_calls_for("light", "turn_on",
                entity_id="light.kitchen")) == 1)
    _assert("service call filter excludes mismatch",
            len(h4.service_calls_for("light", "turn_on",
                entity_id="light.nope")) == 0)

    # ── state_changed event ──
    h5 = MockHass()
    h5.set_state("light.x", "off")
    received_sc: list = []
    h5.bus.async_listen("state_changed", lambda ev: received_sc.append(ev.data))
    h5.fire_state_changed("light.x", {"state": "on", "attributes": {"brightness": 100}})
    _assert("fire_state_changed dispatches event",
            len(received_sc) == 1 and received_sc[0]["entity_id"] == "light.x")
    _assert("fire_state_changed updates state store",
            h5.states.get("light.x").state == "on")
    _assert("fire_state_changed preserves attrs",
            h5.states.get("light.x").attributes["brightness"] == 100)

    # ── executor + add_job synchronous ──
    h6 = MockHass()
    async def _exec_test():
        result = await h6.async_add_executor_job(lambda x: x * 2, 21)
        return result
    res = asyncio.run(_exec_test())
    _assert("async_add_executor_job runs sync", res == 42)

    # ── unload cycle ──
    h7 = MockHass()
    unload_order: list = []
    h7.register_unload(lambda: unload_order.append("a"))
    h7.register_unload(lambda: unload_order.append("b"))
    asyncio.run(h7.simulate_unload())
    _assert("simulate_unload runs all callbacks in order",
            unload_order == ["a", "b"])

    # ── reset ──
    h8 = MockHass()
    h8.set_state("light.r", "on")
    asyncio.run(h8.services.async_call("x", "y", {}))
    h8.bus.async_fire("z", {})
    h8.reset()
    _assert("reset clears fired", len(h8.bus.fired) == 0)
    _assert("reset clears service calls", len(h8.services.calls) == 0)
    # State NOT cleared by reset (intentional — usually we want to keep setup).
    _assert("reset preserves state store", h8.states.get("light.r") is not None)

    print(f"\n{pass_count} pass · {fail_count} fail")
    if fail_count:
        print("\nFailures:")
        for f in failures:
            print(f"  - {f}")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(_selftest())
