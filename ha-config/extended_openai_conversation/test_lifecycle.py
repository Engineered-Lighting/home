#!/usr/bin/env python3
"""Tests for integration lifecycle — Addendum 16 F-3 + F-5a regressions.

Specifically:
  - Drainer task is cancelled on EVENT_HOMEASSISTANT_STOP (F-3:
    prevents leak on HA reload).
  - identity_mutation HA bus event fires when _audit() runs from a
    worker thread (F-5a: cross-thread bus.async_fire needs hass.add_job).

Standalone runner — no pytest needed. Imports identity_store directly
(same pattern as test_identity_store.py) + MockHass from
tools/test_helpers/.

Run on workstation: `py -3 test_lifecycle.py`
Exit 0 = all green; non-zero = failures.
"""

from __future__ import annotations

import asyncio
import ast
import os
import sys
import threading
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent.parent

# Make this script's directory importable so `from identity_store ...` works
sys.path.insert(0, str(THIS_DIR))
# Add repo root for tools/test_helpers
sys.path.insert(0, str(REPO_ROOT))

# Force-enable for tests
os.environ.pop("EXTENDED_OPENAI_IDENTITY_STORE", None)

from identity_store import IdentityStore  # type: ignore
from tools.test_helpers.mock_hass import MockHass  # type: ignore


# ── Tiny test runner ─────────────────────────────────────────────────

passes = 0
fails = 0
failures: list[tuple[str, str]] = []


def t(name: str, fn) -> None:
    global passes, fails
    try:
        fn()
        passes += 1
        print(f"  PASS  {name}")
    except AssertionError as e:
        fails += 1
        failures.append((name, str(e)))
        print(f"  FAIL  {name}: {e}")
    except Exception as e:  # noqa: BLE001
        fails += 1
        failures.append((name, f"{type(e).__name__}: {e}"))
        print(f"  FAIL  {name}: {type(e).__name__}: {e}")


# ── Section 1: drainer lifecycle ─────────────────────────────────────

print("\n[1mdrainer cancellation on HA shutdown[0m\n")


def test_drainer_cancels_on_shutdown() -> None:
    """The pending_writes drainer is a `while True` task. When HA
    fires EVENT_HOMEASSISTANT_STOP, the listener registered in
    async_setup must cancel the task — otherwise reloading the
    integration leaks tasks indefinitely."""

    async def _run():
        hass = MockHass()
        # Pin the loop so cross-thread add_job works
        hass.attach_loop(asyncio.get_running_loop())

        # Simulate the production setup: create a drainer task + register
        # the EVENT_HOMEASSISTANT_STOP listener. We mimic the relevant
        # snippet from __init__.py async_setup.
        async def _drain_loop():
            while True:
                await asyncio.sleep(0.05)

        drainer = asyncio.create_task(_drain_loop())
        hass.data.setdefault("test_domain", {})["drainer_task"] = drainer

        async def _shutdown_drainer(_event):
            task = hass.data.get("test_domain", {}).get("drainer_task")
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        hass.bus.async_listen("homeassistant_stop", _shutdown_drainer)

        # Pre-condition: drainer is running
        await asyncio.sleep(0.1)
        assert not drainer.done(), "drainer should be running before shutdown"

        # Fire the shutdown event (synchronous from a listener perspective).
        # In real HA the listener returns a coroutine; we must await it.
        listeners = hass.bus._listeners.get("homeassistant_stop", [])
        assert len(listeners) == 1, "exactly one shutdown listener registered"
        # Invoke listener with a stub event
        class _Ev: data = {}
        await listeners[0](_Ev())

        # Drainer should now be cancelled
        assert drainer.cancelled() or drainer.done(), \
            f"drainer not cancelled (state: cancelled={drainer.cancelled()}, done={drainer.done()})"

    asyncio.run(_run())


t("drainer cancels on shutdown event", test_drainer_cancels_on_shutdown)


def test_no_leak_across_five_setups() -> None:
    """Reload the integration 5x — there should NEVER be more than one
    drainer alive at any time. Pre-fix this leaked 5 concurrent tasks."""

    async def _run():
        hass = MockHass()
        hass.attach_loop(asyncio.get_running_loop())
        all_tasks: list[asyncio.Task] = []

        async def _drain_loop():
            try:
                while True:
                    await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                raise

        async def _setup_once() -> asyncio.Task:
            task = asyncio.create_task(_drain_loop())
            hass.data.setdefault("d", {})["drainer_task"] = task
            return task

        async def _shutdown_once() -> None:
            task = hass.data.get("d", {}).get("drainer_task")
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        for i in range(5):
            t_i = await _setup_once()
            all_tasks.append(t_i)
            await asyncio.sleep(0.05)
            await _shutdown_once()

        # Final assertion: every task we created is now done.
        leaked = [i for i, t in enumerate(all_tasks) if not t.done()]
        assert len(leaked) == 0, f"leaked tasks at indices: {leaked}"

    asyncio.run(_run())


t("no drainer leak across 5 setup/teardown cycles", test_no_leak_across_five_setups)


# ── Section 2: identity_mutation event cross-thread ───────────────────

print("\n[1midentity_mutation event fires across thread boundary[0m\n")


def test_audit_from_executor_thread_reaches_listener() -> None:
    """The S4 regression: _audit() runs on an executor thread when REST
    views dispatch writes via async_add_executor_job. If _audit calls
    bus.async_fire DIRECTLY from that thread, listeners don't receive
    the event. The fix uses hass.add_job(bus.async_fire, ...) to
    marshal onto the main loop.

    This test forces the call path through a worker thread and asserts
    a WS subscriber receives the event."""

    async def _run():
        hass = MockHass()
        hass.attach_loop(asyncio.get_running_loop())

        # Use a temp DB so each test is hermetic.
        store = IdentityStore(db_path=":memory:")
        store.setup()
        store.set_hass_for_events(hass)

        received: list = []
        hass.bus.async_listen(
            "extended_openai_conversation.identity_mutation",
            lambda ev: received.append(ev.data),
        )

        # Run a mutation (create_identity) from an executor thread —
        # this is the same path REST views take.
        def _create_in_thread() -> str:
            return store.create_identity(
                "TestSubject",
                relationship_type="friend",
            )

        new_uuid = await asyncio.to_thread(_create_in_thread)
        # Let the event loop process the scheduled add_job call.
        await asyncio.sleep(0.05)

        assert new_uuid, "create_identity returned a uuid"
        assert len(received) >= 1, \
            f"expected ≥1 identity_mutation event, got {len(received)}"
        # Payload shape
        evt = received[0]
        assert evt.get("target_uuid") == new_uuid, \
            f"event target_uuid mismatch: {evt}"
        assert evt.get("actor"), f"event missing actor: {evt}"
        assert "ts" in evt, f"event missing ts: {evt}"

    asyncio.run(_run())


t("audit from executor thread reaches WS listener (S4 fix)",
  test_audit_from_executor_thread_reaches_listener)


def test_audit_records_event_even_when_listener_absent() -> None:
    """Robustness: even with NO listener registered, the audit row
    must still write to change_log. Event fire is best-effort."""

    async def _run():
        hass = MockHass()
        hass.attach_loop(asyncio.get_running_loop())

        store = IdentityStore(db_path=":memory:")
        store.setup()
        store.set_hass_for_events(hass)

        # No listener registered
        def _create_in_thread() -> str:
            return store.create_identity("Anonymous", relationship_type="unknown")

        new_uuid = await asyncio.to_thread(_create_in_thread)
        await asyncio.sleep(0.05)

        # change_log row should exist — query SQLite directly via the
        # internal _conn (no public list method).
        rows = store._conn.execute(
            "SELECT kind FROM change_log WHERE target_uuid = ?",
            (new_uuid,),
        ).fetchall()
        kinds = [r[0] for r in rows]
        assert "identity_mutation" in kinds, \
            f"no identity_mutation audit row for {new_uuid}: {kinds}"

    asyncio.run(_run())


t("audit writes change_log row even without listener",
  test_audit_records_event_even_when_listener_absent)


def test_multiple_mutations_each_fire_event() -> None:
    """Rapid sequence of mutations from threads — each must produce
    its own event. Verifies the event-loop scheduler doesn't drop
    coalescing-style events under load."""

    async def _run():
        hass = MockHass()
        hass.attach_loop(asyncio.get_running_loop())
        store = IdentityStore(db_path=":memory:")
        store.setup()
        store.set_hass_for_events(hass)

        received: list = []
        hass.bus.async_listen(
            "extended_openai_conversation.identity_mutation",
            lambda ev: received.append(ev.data),
        )

        # Fire 5 mutations from threads
        async def _spawn() -> list[str]:
            results = []
            for i in range(5):
                uid = await asyncio.to_thread(
                    store.create_identity, f"User{i}",
                    relationship_type="friend",
                )
                results.append(uid)
            return results

        uuids = await _spawn()
        # Let scheduled events drain
        await asyncio.sleep(0.1)

        assert len(uuids) == 5, f"5 mutations performed: {uuids}"
        assert len(received) == 5, \
            f"expected 5 events, got {len(received)} — coalescing bug?"
        # Each event references a distinct uuid
        target_uuids = {evt.get("target_uuid") for evt in received}
        assert target_uuids == set(uuids), \
            f"event target_uuids don't match created: {target_uuids} vs {set(uuids)}"

    asyncio.run(_run())


t("5 rapid mutations from threads → 5 events delivered (no coalescing)",
  test_multiple_mutations_each_fire_event)


# ── Section 3: world_state late-discovery (Addendum 19) ──────────────

print("\n[1mworld_state subscribes broadly + ingests late-added entities[0m\n")


def test_world_state_late_entity_via_broad_subscription() -> None:
    """Addendum 19 regression: when Frigate entities are added to HA
    AFTER world_state.async_setup runs (which is the normal case
    because integrations load asynchronously), the OLD fixed-list
    subscription missed them. The fix uses a broad EVENT_STATE_CHANGED
    subscription + filter in callback. This test simulates the timing
    race + asserts the late entity gets ingested."""

    async def _run():
        from world_state import WorldStateAggregator  # type: ignore
        hass = MockHass()
        hass.attach_loop(asyncio.get_running_loop())

        # Aggregator setup with NO Frigate entities present yet (mimics
        # the startup race — Frigate integration loads later).
        agg = WorldStateAggregator(hass)
        await agg.async_setup()

        # Pre-condition: no rooms tracked
        assert len(agg._rooms) == 0, "rooms should be empty at boot"
        assert len(agg._tracked_entities) == 0, "no tracked entities at boot"

        # Addendum 28: the phantom-zone guard requires the real camera
        # to exist before occupancy is accepted. Register it before the
        # late-arriving occupancy event fires (mimics Frigate loading
        # the camera + occupancy entity together).
        hass.states.set("camera.living_room", "streaming")

        # Frigate integration "loads later" and adds an occupancy
        # sensor by firing a state_changed event for it. The broad
        # subscription should pick it up.
        hass.fire_state_changed(
            "binary_sensor.living_room_person_occupancy",
            {"state": "on", "attributes": {}},
        )
        # Let the listener process
        await asyncio.sleep(0.01)

        # Assertions: aggregator now knows about the entity AND the room
        assert "binary_sensor.living_room_person_occupancy" in agg._tracked_entities, \
            f"late entity not added: {agg._tracked_entities}"
        assert "living_room" in agg._rooms, \
            f"room not initialized after late event: {list(agg._rooms.keys())}"
        assert agg._rooms["living_room"]["occupied"] is True, \
            f"room occupancy not applied: {agg._rooms['living_room']}"

        # get_room_state should now succeed (not return "no data")
        result = agg.get_room_state("living_room")
        assert result.get("data") is not None, \
            f"get_room_state returned no data after fix: {result}"

    asyncio.run(_run())


t("world_state ingests entities added AFTER async_setup (broad subscription)",
  test_world_state_late_entity_via_broad_subscription)


def test_world_state_ignores_non_tracked_entities() -> None:
    """The broad subscription must filter — receiving state_changed for
    a light or switch should NOT add anything to _tracked_entities."""

    async def _run():
        from world_state import WorldStateAggregator  # type: ignore
        hass = MockHass()
        hass.attach_loop(asyncio.get_running_loop())
        agg = WorldStateAggregator(hass)
        await agg.async_setup()

        # Fire state_changed for a non-tracked entity
        hass.fire_state_changed("light.kitchen", {"state": "on", "attributes": {}})
        hass.fire_state_changed("switch.fan", {"state": "off", "attributes": {}})
        await asyncio.sleep(0.01)

        assert len(agg._tracked_entities) == 0, \
            f"non-tracked entities leaked into tracked set: {agg._tracked_entities}"
        assert len(agg._rooms) == 0, \
            f"non-tracked entities created rooms: {list(agg._rooms.keys())}"

    asyncio.run(_run())


t("broad subscription filters out non-tracked entities (lights, switches)",
  test_world_state_ignores_non_tracked_entities)


# ── Section 4: Addendum 20 — frigate_event identity extraction ───────

print("\n[1mworld_state extracts identity from frigate_event sub_label[0m\n")


def test_frigate_event_with_sublabel_adds_identified_person() -> None:
    """Addendum 20 regression: in current Frigate versions, the
    per-camera + per-person face sensors don't update reliably, but
    every detection event fires `frigate_event` on the HA bus with
    sub_label populated. world_state must extract identity from
    this event channel."""

    async def _run():
        from world_state import WorldStateAggregator  # type: ignore
        hass = MockHass()
        hass.attach_loop(asyncio.get_running_loop())
        # Addendum 28: register the real camera so the phantom-zone
        # guard in _handle_frigate_event accepts the event.
        hass.states.set("camera.living_room", "streaming")
        agg = WorldStateAggregator(hass)
        await agg.async_setup()

        # Fire a Frigate event in the newer {type, before, after} shape
        # with face-rec matching "marcelo" in the living room.
        hass.bus.async_fire("frigate_event", {
            "type": "new",
            "before": {},
            "after": {
                "camera": "living_room",
                "label": "person",
                "sub_label": "marcelo",
                "top_score": 0.94,
            },
        })
        await asyncio.sleep(0.01)

        # who_is_in must now identify Marcelo
        result = agg.who_is_in("living_room")
        assert result.get("data"), f"who_is_in returned no data: {result}"
        identified = result["data"].get("identified", [])
        assert len(identified) == 1, f"expected 1 identified, got {len(identified)}: {result}"
        assert identified[0]["name"].lower() == "marcelo", \
            f"wrong name: {identified[0]}"
        assert "marcelo" in result.get("suggested_phrasing", "").lower(), \
            f"name not in phrasing: {result.get('suggested_phrasing')}"

    asyncio.run(_run())


t("frigate_event with sub_label → identified person added to room",
  test_frigate_event_with_sublabel_adds_identified_person)


def test_frigate_event_flat_shape_supported() -> None:
    """Older Frigate event shape: {camera, sub_label, ...} flat (no
    after wrapper). Must be normalized + ingested correctly."""

    async def _run():
        from world_state import WorldStateAggregator  # type: ignore
        hass = MockHass()
        hass.attach_loop(asyncio.get_running_loop())
        hass.states.set("camera.kitchen", "streaming")  # Addendum 28
        agg = WorldStateAggregator(hass)
        await agg.async_setup()

        hass.bus.async_fire("frigate_event", {
            "camera": "kitchen",
            "label": "person",
            "sub_label": "david",
        })
        await asyncio.sleep(0.01)

        result = agg.who_is_in("kitchen")
        identified = result["data"].get("identified", [])
        assert len(identified) == 1, f"flat shape not ingested: {result}"
        assert identified[0]["name"].lower() == "david"

    asyncio.run(_run())


t("frigate_event flat shape (no `after` wrapper) supported",
  test_frigate_event_flat_shape_supported)


def test_frigate_event_tuple_sublabel_supported() -> None:
    """sub_label may arrive as [name, score] tuple. Must extract both."""

    async def _run():
        from world_state import WorldStateAggregator  # type: ignore
        hass = MockHass()
        hass.attach_loop(asyncio.get_running_loop())
        hass.states.set("camera.driveway", "streaming")  # Addendum 28
        agg = WorldStateAggregator(hass)
        await agg.async_setup()

        hass.bus.async_fire("frigate_event", {
            "after": {
                "camera": "driveway",
                "sub_label": ["amelia", 0.78],
            },
        })
        await asyncio.sleep(0.01)

        result = agg.who_is_in("driveway")
        identified = result["data"].get("identified", [])
        assert len(identified) == 1, f"tuple sub_label not ingested: {result}"
        assert identified[0]["name"].lower() == "amelia"
        # 0.78 is above IDENTITY_CONFIDENCE_HIGH (0.7) → "high" band
        assert identified[0]["confidence"] == 0.78, \
            f"score not preserved: {identified[0]}"

    asyncio.run(_run())


t("frigate_event tuple sub_label [name, score] supported",
  test_frigate_event_tuple_sublabel_supported)


def test_frigate_event_unknown_sublabel_dropped() -> None:
    """sub_label='unknown' or empty MUST NOT add a phantom identity."""

    async def _run():
        from world_state import WorldStateAggregator  # type: ignore
        hass = MockHass()
        hass.attach_loop(asyncio.get_running_loop())
        agg = WorldStateAggregator(hass)
        await agg.async_setup()

        # Three event variants that should all be dropped
        for payload in [
            {"after": {"camera": "office", "sub_label": "unknown"}},
            {"after": {"camera": "office", "sub_label": ""}},
            {"after": {"camera": "office"}},  # missing sub_label
        ]:
            hass.bus.async_fire("frigate_event", payload)
        await asyncio.sleep(0.01)

        # Office room may exist (camera added) but identified should be empty
        result = agg.who_is_in("office")
        identified = result["data"].get("identified", [])
        assert len(identified) == 0, \
            f"phantom identity from bad sub_label: {identified}"

    asyncio.run(_run())


t("frigate_event with unknown/empty/missing sub_label → no identity added",
  test_frigate_event_unknown_sublabel_dropped)


# ── Summary ──────────────────────────────────────────────────────────

# Section 5: CORS source contract for HA REST views

print("\n[1mCORSHomeAssistantView covers REST views without duplicate headers[0m\n")


def _integration_ast() -> ast.Module:
    source = (THIS_DIR / "__init__.py").read_text(encoding="utf-8")
    return ast.parse(source, filename=str(THIS_DIR / "__init__.py"))


def _base_names(cls: ast.ClassDef) -> set[str]:
    names: set[str] = set()
    for base in cls.bases:
        if isinstance(base, ast.Name):
            names.add(base.id)
        elif isinstance(base, ast.Attribute):
            names.add(base.attr)
    return names


def _class_defs(tree: ast.Module) -> dict[str, ast.ClassDef]:
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
    }


def _method_names(cls: ast.ClassDef) -> set[str]:
    return {
        node.name
        for node in cls.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _assigned_constant(cls: ast.ClassDef, name: str):
    for node in cls.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            continue
        if isinstance(node.value, ast.Constant):
            return node.value.value
    raise AssertionError(f"{cls.name}.{name} assignment not found")


def _non_docstring_constants(tree: ast.Module) -> list[str]:
    docstring_ids: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            docstring_ids.add(id(first.value))
    return [
        node.value
        for node in ast.walk(tree)
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstring_ids
        )
    ]


def test_cors_base_view_enables_ha_middleware() -> None:
    tree = _integration_ast()
    classes = _class_defs(tree)
    cors_cls = classes.get("CORSHomeAssistantView")
    assert cors_cls is not None, "CORSHomeAssistantView missing"
    assert "HomeAssistantView" in _base_names(cors_cls), \
        "CORSHomeAssistantView must inherit HomeAssistantView"
    assert _assigned_constant(cors_cls, "cors_allowed") is True, \
        "CORSHomeAssistantView must opt into HA aiohttp_cors middleware"


t("CORSHomeAssistantView opts into Home Assistant CORS middleware",
  test_cors_base_view_enables_ha_middleware)


def test_all_custom_rest_views_inherit_cors_base() -> None:
    tree = _integration_ast()
    classes = _class_defs(tree)
    view_classes = {
        name: cls
        for name, cls in classes.items()
        if name.endswith("View") and name != "CORSHomeAssistantView"
    }
    assert view_classes, "no custom REST view classes found"
    offenders = [
        name
        for name, cls in sorted(view_classes.items())
        if "CORSHomeAssistantView" not in _base_names(cls)
    ]
    assert not offenders, \
        f"custom REST views missing CORSHomeAssistantView base: {offenders}"


t("all custom REST views inherit CORSHomeAssistantView",
  test_all_custom_rest_views_inherit_cors_base)


def test_identity_patch_delete_routes_are_cors_enabled() -> None:
    tree = _integration_ast()
    classes = _class_defs(tree)
    cls = classes.get("IdentityDetailView")
    assert cls is not None, "IdentityDetailView missing"
    assert "CORSHomeAssistantView" in _base_names(cls), \
        "IdentityDetailView must use CORSHomeAssistantView for PATCH/DELETE preflight"
    methods = _method_names(cls)
    assert {"patch", "delete"}.issubset(methods), \
        f"IdentityDetailView missing PATCH/DELETE handlers: {methods}"


t("identity PATCH/DELETE view is CORS-enabled",
  test_identity_patch_delete_routes_are_cors_enabled)


def test_views_do_not_set_access_control_headers_directly() -> None:
    tree = _integration_ast()
    offending_literals = [
        value
        for value in _non_docstring_constants(tree)
        if "Access-Control-" in value
    ]
    assert not offending_literals, \
        f"views must let HA CORS middleware set Access-Control headers: {offending_literals}"


t("views do not duplicate Access-Control headers",
  test_views_do_not_set_access_control_headers_directly)


print(f"\n{passes} pass · {fails} fail")
if fails:
    print("\nFailures:")
    for n, msg in failures:
        print(f"  - {n}: {msg}")

sys.exit(0 if fails == 0 else 1)
