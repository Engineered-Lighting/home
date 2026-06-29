"""Registry-lookup tools.

Four tools the LLM agent can call to discover entities WITHOUT the
DEFAULT_PROMPT having to inline the full exposed_entities CSV every
turn (Addendum 27 / Section 6 / M3):

  - areas_in_home()                    → list of areas (with floor name)
  - entities_in_area(area, domains?)   → entities in that area, optionally
                                         filtered to one or more domains
  - entities_with_label(label)         → entities tagged with a label
  - find_entity(query, area?)          → fuzzy search across name/aliases

All four read from HA's entity_registry + area_registry + (when present)
label_registry + floor_registry. Compatibility:
  - HA 2024.4+ for floor_registry (live install is 2026.5.1 — fine)
  - HA 2024.8+ for label_registry (same)

Lazy registry creation: HA only persists `core.label_registry` /
`core.floor_registry` after the user creates the first label/floor via
UI. Until then the registries return empty lists; tools degrade
gracefully with a hint ("no labels defined yet — add some in HA UI").

Return shape is INTENTIONALLY compact — the agent will call these many
times per session as a fallback for the inlined CSV, so each response
must stay under ~500 tokens per call to come out ahead on context.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    llm,
)

from ..exceptions import NativeNotFound
from .base import Function

_LOGGER = logging.getLogger(__name__)


# Label / floor registries arrived in newer HA releases. Import via
# try/except so the integration loads even on older HA installs (it'll
# just report "registry unavailable" from the tools).
try:
    from homeassistant.helpers import label_registry as lr
except ImportError:  # pragma: no cover — HA < 2024.8
    lr = None  # type: ignore[assignment]

try:
    from homeassistant.helpers import floor_registry as fr
except ImportError:  # pragma: no cover — HA < 2024.4
    fr = None  # type: ignore[assignment]


class RegistryFunction(Function):
    """Dispatcher for the 4 registry tools. Same shape as WorldStateFunction:
    one Function class, switches on function_config['name']."""

    def __init__(self) -> None:
        super().__init__(vol.Schema({vol.Required("name"): str}))

    async def execute(
        self,
        hass: HomeAssistant,
        function_config: dict[str, Any],
        arguments: dict[str, Any],
        llm_context: llm.LLMContext | None,
        exposed_entities: list[dict[str, Any]],
    ) -> Any:
        name = function_config["name"]
        if name == "areas_in_home":
            return self._areas_in_home(hass)
        if name == "entities_in_area":
            return self._entities_in_area(hass, arguments, exposed_entities)
        if name == "entities_with_label":
            return self._entities_with_label(hass, arguments, exposed_entities)
        if name == "find_entity":
            return self._find_entity(hass, arguments, exposed_entities)
        if name == "entities_by_domain":
            return self._entities_by_domain(hass, arguments, exposed_entities)
        raise NativeNotFound(name)

    # ─── helpers ────────────────────────────────────────────────────
    def _exposed_set(self, exposed_entities: list[dict[str, Any]]) -> set[str]:
        """Set of entity_ids the agent is permitted to see/touch. Used to
        filter registry-returned entities so we don't leak entities that
        weren't exposed via the integration's expose-to-agent config."""
        return {e.get("entity_id") for e in (exposed_entities or []) if e.get("entity_id")}

    def _short_entity(
        self,
        hass: HomeAssistant,
        ent: er.RegistryEntry,
        a_reg: Optional[ar.AreaRegistry] = None,
    ) -> dict[str, Any]:
        """Compact entity dict — name + state + area, no attributes.
        Keeping this small is the whole point of M3."""
        state_obj = hass.states.get(ent.entity_id)
        # Friendly name precedence: registry name > entity friendly_name
        # > entity_id slug. Aliases captured separately so find_entity
        # can match against them too.
        name = (
            ent.name
            or (state_obj.attributes.get("friendly_name") if state_obj else None)
            or ent.entity_id.split(".", 1)[-1].replace("_", " ")
        )
        area_name: Optional[str] = None
        if ent.area_id and a_reg is not None:
            area = a_reg.async_get_area(ent.area_id)
            if area:
                area_name = area.name
        return {
            "entity_id": ent.entity_id,
            "name": name,
            "state": state_obj.state if state_obj else "unknown",
            "area": area_name,
        }

    # ─── tools ──────────────────────────────────────────────────────
    def _areas_in_home(self, hass: HomeAssistant) -> dict[str, Any]:
        """List every area with its (optional) floor + a few example
        entities so the agent has enough context to pick the right
        area. Limit examples per area to keep the payload bounded."""
        try:
            a_reg = ar.async_get(hass)
            e_reg = er.async_get(hass)
        except Exception as e:  # noqa: BLE001
            return {"error": f"registry unavailable: {type(e).__name__}"}

        # Floor lookup is optional — populate when registry available.
        floor_by_id: dict[str, str] = {}
        if fr is not None:
            try:
                f_reg = fr.async_get(hass)
                for floor in f_reg.async_list_floors():
                    floor_by_id[floor.floor_id] = floor.name
            except Exception:
                pass

        areas_out = []
        try:
            for area in a_reg.async_list_areas():
                # Count entities per area; cheap because er.async_entries_for_area
                # iterates the in-memory registry index.
                entities = list(er.async_entries_for_area(e_reg, area.id))
                # Sample 3 entity NAMES to give the agent texture without dumping
                # the whole list (which is what M3 is fighting against).
                sample = [
                    (ent.name or ent.entity_id) for ent in entities[:3]
                ]
                areas_out.append({
                    "area_id": area.id,
                    "name": area.name,
                    "floor_name": (
                        floor_by_id.get(area.floor_id) if area.floor_id else None
                    ),
                    "entity_count": len(entities),
                    "sample_entities": sample,
                })
        except Exception as e:  # noqa: BLE001
            return {"error": f"area listing failed: {type(e).__name__}"}

        return {
            "data": {"areas": areas_out, "count": len(areas_out)},
            "suggested_phrasing": (
                f"{len(areas_out)} areas configured: "
                + ", ".join(a["name"] for a in areas_out[:6])
                + ("." if len(areas_out) <= 6 else f", and {len(areas_out) - 6} more.")
            ) if areas_out else "No areas are configured in HA yet.",
        }

    def _entities_in_area(
        self,
        hass: HomeAssistant,
        arguments: dict[str, Any],
        exposed_entities: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Entities in a single area, optionally filtered to a domain
        (e.g., domains=['light'] when the user said 'lights in the
        kitchen'). Returns compact rows + current state."""
        area_arg = (arguments.get("area") or "").strip()
        if not area_arg:
            return {"error": "area argument required"}
        domains = arguments.get("domains") or []
        if isinstance(domains, str):
            domains = [domains]
        domains_set = {d.strip().lower() for d in domains if d}

        try:
            a_reg = ar.async_get(hass)
            e_reg = er.async_get(hass)
        except Exception as e:  # noqa: BLE001
            return {"error": f"registry unavailable: {type(e).__name__}"}

        # Resolve area name or id → area_id. Name-tolerant: accept
        # "Kitchen" / "kitchen" / "kitchen_id" all equally.
        target = area_arg.lower().replace(" ", "_")
        area_id: Optional[str] = None
        for area in a_reg.async_list_areas():
            if area.id == area_arg or area.id == target:
                area_id = area.id
                break
            slug = area.name.lower().replace(" ", "_")
            if slug == target or area.name.lower() == area_arg.lower():
                area_id = area.id
                break
        if area_id is None:
            return {
                "data": None,
                "suggested_phrasing": (
                    f"I don't see an area called '{area_arg}'. "
                    f"Call areas_in_home() to see available areas."
                ),
            }

        exposed = self._exposed_set(exposed_entities)
        rows = []
        for ent in er.async_entries_for_area(e_reg, area_id):
            if exposed and ent.entity_id not in exposed:
                continue
            if domains_set and ent.entity_id.split(".", 1)[0] not in domains_set:
                continue
            rows.append(self._short_entity(hass, ent, a_reg))
        rows.sort(key=lambda r: (r["entity_id"].split(".", 1)[0], r["entity_id"]))

        return {
            "data": {
                "area_id": area_id,
                "domains_filter": sorted(domains_set) if domains_set else None,
                "entities": rows,
                "count": len(rows),
            },
            "suggested_phrasing": (
                f"{len(rows)} entities in the {area_arg}"
                + (f" matching {sorted(domains_set)}." if domains_set else ".")
            ),
        }

    def _entities_with_label(
        self,
        hass: HomeAssistant,
        arguments: dict[str, Any],
        exposed_entities: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Entities tagged with an HA label. Empty when label_registry
        isn't populated yet (user hasn't created any labels in UI)."""
        label_arg = (arguments.get("label") or "").strip()
        if not label_arg:
            return {"error": "label argument required"}

        if lr is None:
            return {
                "data": None,
                "suggested_phrasing": (
                    "Labels aren't available on this HA version."
                ),
            }
        try:
            l_reg = lr.async_get(hass)
            a_reg = ar.async_get(hass)
            e_reg = er.async_get(hass)
        except Exception as e:  # noqa: BLE001
            return {"error": f"registry unavailable: {type(e).__name__}"}

        # Resolve label by id, name, or slug.
        target = label_arg.lower().replace(" ", "_")
        label_id: Optional[str] = None
        try:
            labels = list(l_reg.async_list_labels())
        except Exception as e:  # noqa: BLE001
            return {"error": f"label listing failed: {type(e).__name__}"}
        if not labels:
            return {
                "data": None,
                "suggested_phrasing": (
                    f"No labels are defined in HA yet. "
                    f"Add labels via Settings → Devices & Services → Labels "
                    f"to make this lookup useful."
                ),
            }
        for label in labels:
            if label.label_id == label_arg or label.label_id == target:
                label_id = label.label_id
                break
            slug = label.name.lower().replace(" ", "_")
            if slug == target or label.name.lower() == label_arg.lower():
                label_id = label.label_id
                break
        if label_id is None:
            return {
                "data": None,
                "suggested_phrasing": (
                    f"No label called '{label_arg}'. Available labels: "
                    + ", ".join(l.name for l in labels[:8])
                ),
            }

        exposed = self._exposed_set(exposed_entities)
        rows = []
        try:
            for ent in er.async_entries_for_label(e_reg, label_id):
                if exposed and ent.entity_id not in exposed:
                    continue
                rows.append(self._short_entity(hass, ent, a_reg))
        except AttributeError:
            # async_entries_for_label was added to entity_registry in a
            # later HA version. Fall back to scanning entries.
            for ent in e_reg.entities.values():
                if label_id not in (ent.labels or set()):
                    continue
                if exposed and ent.entity_id not in exposed:
                    continue
                rows.append(self._short_entity(hass, ent, a_reg))
        rows.sort(key=lambda r: r["entity_id"])
        return {
            "data": {"label_id": label_id, "entities": rows, "count": len(rows)},
            "suggested_phrasing": f"{len(rows)} entities tagged '{label_arg}'.",
        }

    def _find_entity(
        self,
        hass: HomeAssistant,
        arguments: dict[str, Any],
        exposed_entities: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Fuzzy entity lookup by name / friendly_name / alias. Optional
        `area` arg narrows the search. Returns up to 8 candidates so the
        agent can pick or ask for clarification — NOT exhaustive."""
        query = (arguments.get("query") or "").strip()
        if not query:
            return {"error": "query argument required"}
        area_arg = (arguments.get("area") or "").strip().lower() or None
        domain_filter = (arguments.get("domain") or "").strip().lower() or None
        max_results = int(arguments.get("max_results") or 8)

        try:
            a_reg = ar.async_get(hass)
            e_reg = er.async_get(hass)
        except Exception as e:  # noqa: BLE001
            return {"error": f"registry unavailable: {type(e).__name__}"}

        # Resolve optional area filter to area_id.
        area_id: Optional[str] = None
        if area_arg:
            for area in a_reg.async_list_areas():
                if area.name.lower() == area_arg or area.id == area_arg:
                    area_id = area.id
                    break

        exposed = self._exposed_set(exposed_entities)
        q = query.lower()
        q_tokens = set(q.split())

        # Score every candidate. Simple substring + token-overlap scoring;
        # the LLM is smart enough to pick the right one from a small
        # candidate list, so we don't need clever fuzzy logic here.
        scored: list[tuple[float, dict[str, Any]]] = []
        for ent in e_reg.entities.values():
            if exposed and ent.entity_id not in exposed:
                continue
            if area_id and ent.area_id != area_id:
                continue
            if domain_filter and ent.entity_id.split(".", 1)[0] != domain_filter:
                continue
            state_obj = hass.states.get(ent.entity_id)
            friendly = (state_obj.attributes.get("friendly_name") if state_obj else None) or ""
            aliases = ent.aliases or set()
            haystack_parts = [
                ent.entity_id,
                ent.name or "",
                friendly,
                *aliases,
            ]
            haystack = " ".join(haystack_parts).lower()
            if q not in haystack and not (q_tokens & set(haystack.split())):
                continue
            # Score: 3 if exact name match, 2 if name contains query,
            # 1 if any token matches.
            name_l = (ent.name or friendly).lower()
            if name_l == q:
                score = 3.0
            elif q in name_l:
                score = 2.0
            elif q in haystack:
                score = 1.0
            else:
                score = 0.5
            row = self._short_entity(hass, ent, a_reg)
            row["aliases"] = sorted(aliases) if aliases else []
            scored.append((score, row))
        scored.sort(key=lambda t: (-t[0], t[1]["entity_id"]))
        rows = [r for _, r in scored[:max_results]]
        return {
            "data": {
                "query": query,
                "area_filter": area_arg,
                "domain_filter": domain_filter,
                "candidates": rows,
                "count": len(rows),
                "truncated": len(scored) > max_results,
            },
            "suggested_phrasing": (
                f"Found {len(rows)} match{'es' if len(rows) != 1 else ''} for '{query}'"
                + (f" in the {area_arg}" if area_arg else "")
                + ("." if len(rows) > 0 else ". Try a broader query or check areas_in_home().")
            ),
        }

    def _entities_by_domain(
        self,
        hass: HomeAssistant,
        arguments: dict[str, Any],
        exposed_entities: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Every entity in one domain across the whole home, optionally
        narrowed to an area. Use for 'all lights off' / 'lock every door'
        style commands where the agent needs to enumerate without a
        specific area binding. Returns entity_id only (no per-entity
        state) to keep the payload tight — for state, follow up with
        get_attributes() on a specific entity_id.
        """
        domain = (arguments.get("domain") or "").strip().lower()
        if not domain:
            return {"error": "domain argument required"}
        area_arg = (arguments.get("area") or "").strip().lower() or None

        try:
            a_reg = ar.async_get(hass)
            e_reg = er.async_get(hass)
        except Exception as e:  # noqa: BLE001
            return {"error": f"registry unavailable: {type(e).__name__}"}

        area_id: Optional[str] = None
        if area_arg:
            for area in a_reg.async_list_areas():
                if area.name.lower() == area_arg or area.id == area_arg:
                    area_id = area.id
                    break

        exposed = self._exposed_set(exposed_entities)
        ids: list[str] = []
        prefix = f"{domain}."
        for ent in e_reg.entities.values():
            if not ent.entity_id.startswith(prefix):
                continue
            if exposed and ent.entity_id not in exposed:
                continue
            if area_id and ent.area_id != area_id:
                continue
            ids.append(ent.entity_id)
        ids.sort()

        return {
            "data": {
                "domain": domain,
                "area_filter": area_arg,
                "entity_ids": ids,
                "count": len(ids),
            },
            "suggested_phrasing": (
                f"{len(ids)} {domain} entities"
                + (f" in the {area_arg}." if area_arg else " across the home.")
            ),
        }
