"""Pure event filtering and normalization for Home Agent Edge.

This module intentionally has no Home Assistant imports.  It is the privacy
boundary between the broad HA event bus and the narrow edge outbox, and can be
unit tested without a running HA instance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping

from .const import EVENT_CONVERSATION_FINISHED, SCHEMA_VERSION


_COMMON_STATE_ATTRIBUTES = frozenset(
    {
        "device_class",
        "source",
        "tracking_type",
        "in_zones",
        "restored",
    }
)
_LOCATION_ATTRIBUTES = frozenset(
    {
        "latitude",
        "longitude",
        "gps_accuracy",
        "altitude",
        "course",
        "speed",
        "vertical_accuracy",
    }
)
_ZONE_ATTRIBUTES = frozenset({"latitude", "longitude", "radius", "passive"})
_CONVERSATION_METADATA_FIELDS = frozenset(
    {"conversation_id", "agent_id", "route", "language", "device_id"}
)


def utc_now_iso() -> str:
    """Return a canonical UTC timestamp."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _as_iso(value: Any, default: str) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _safe_scalar(value: Any) -> str | int | float | bool | None:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _state_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return dict(value)
    as_dict = getattr(value, "as_dict", None)
    if callable(as_dict):
        result = as_dict()
        return dict(result) if isinstance(result, Mapping) else None
    return None


@dataclass(frozen=True, slots=True)
class EdgePolicy:
    """Reviewed allowlist plus fail-closed privacy directives."""

    entity_ids: frozenset[str]
    blocked_entity_ids: frozenset[str] = field(default_factory=frozenset)
    blocked_user_ids: frozenset[str] = field(default_factory=frozenset)
    require_authenticated_conversation: bool = True

    @classmethod
    def from_values(
        cls,
        entity_ids: list[str] | tuple[str, ...] | set[str],
        blocked_entity_ids: list[str] | tuple[str, ...] | set[str] = (),
        blocked_user_ids: list[str] | tuple[str, ...] | set[str] = (),
        *,
        include_conversation_text: bool = False,
        require_authenticated_conversation: bool = True,
    ) -> "EdgePolicy":
        # Kept as a compatibility-only keyword for old config entries.  Its
        # value is deliberately ignored; conversation content is hard-off.
        _ = include_conversation_text
        return cls(
            frozenset(_clean_values(entity_ids)),
            frozenset(_clean_values(blocked_entity_ids)),
            frozenset(_clean_values(blocked_user_ids)),
            require_authenticated_conversation=bool(require_authenticated_conversation),
        )

    def normalize_event(
        self,
        event_type: str,
        data: Mapping[str, Any] | None,
        context: Any = None,
        *,
        occurred_at: Any = None,
        received_at: str | None = None,
    ) -> dict[str, Any] | None:
        """Normalize one reviewed event, or return ``None`` to drop it."""
        received = received_at or utc_now_iso()
        context_data = _context_dict(context)
        user_id = context_data.get("user_id")
        if user_id and user_id in self.blocked_user_ids:
            return None

        if event_type == "state_changed":
            return self._normalize_state_changed(
                dict(data or {}), context_data, occurred_at, received
            )
        if event_type == EVENT_CONVERSATION_FINISHED:
            return self._normalize_conversation(
                dict(data or {}), context_data, occurred_at, received
            )
        return None

    def with_core_privacy_policy(self, document: Mapping[str, Any]) -> "EdgePolicy":
        if set(document) != {
            "version", "blocked_entity_ids", "blocked_user_ids", "policy_digest"
        }:
            raise ValueError("Core privacy policy schema is invalid")
        blocked = document["blocked_entity_ids"]
        blocked_users = document["blocked_user_ids"]
        if (
            document["version"] != 1
            or not isinstance(blocked, list)
            or not isinstance(blocked_users, list)
        ):
            raise ValueError("Core privacy policy version is invalid")
        cleaned = sorted(_clean_values(blocked))
        if len(cleaned) != len(blocked):
            raise ValueError("Core privacy policy entity list is invalid")
        cleaned_users = sorted(_clean_values(blocked_users))
        if len(cleaned_users) != len(blocked_users):
            raise ValueError("Core privacy policy user list is invalid")
        material = json.dumps(
            {
                "blocked_entity_ids": cleaned,
                "blocked_user_ids": cleaned_users,
                "version": 1,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        digest = hashlib.sha256(material).hexdigest()
        if document["policy_digest"] != digest:
            raise ValueError("Core privacy policy digest is invalid")
        return EdgePolicy(
            entity_ids=self.entity_ids,
            blocked_entity_ids=self.blocked_entity_ids | frozenset(cleaned),
            blocked_user_ids=self.blocked_user_ids | frozenset(cleaned_users),
            require_authenticated_conversation=self.require_authenticated_conversation,
        )

    def _normalize_state_changed(
        self,
        data: dict[str, Any],
        context: dict[str, str | None],
        occurred_at: Any,
        received_at: str,
    ) -> dict[str, Any] | None:
        entity_id = str(data.get("entity_id") or "").strip().lower()
        if (
            not entity_id
            or entity_id not in self.entity_ids
            or entity_id in self.blocked_entity_ids
        ):
            return None

        old_state = _state_dict(data.get("old_state"))
        new_state = _state_dict(data.get("new_state"))
        if old_state is None and new_state is None:
            return None

        return {
            "schema_version": SCHEMA_VERSION,
            "source_event_type": "state_changed",
            "observation_kind": "snapshot" if data.get("_snapshot") else "transition",
            "entity_id": entity_id,
            "occurred_at": _as_iso(occurred_at, received_at),
            "received_at": received_at,
            "context": context,
            "old_state": self._normalize_state(entity_id, old_state),
            "new_state": self._normalize_state(entity_id, new_state),
            "privacy": {
                "classification": (
                    "precise_location"
                    if entity_id.startswith(("person.", "device_tracker.", "zone."))
                    else "household_state"
                ),
                "retention_seconds": 24 * 60 * 60,
            },
        }

    def _normalize_state(
        self, entity_id: str, state: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        if state is None:
            return None
        attributes = state.get("attributes")
        if not isinstance(attributes, Mapping):
            attributes = {}

        allowed = set(_COMMON_STATE_ATTRIBUTES)
        if entity_id.startswith(("person.", "device_tracker.")):
            allowed.update(_LOCATION_ATTRIBUTES)
        elif entity_id.startswith("zone."):
            allowed.update(_ZONE_ATTRIBUTES)

        return {
            "state": _safe_scalar(state.get("state")),
            "last_changed": _as_iso(state.get("last_changed"), ""),
            "last_updated": _as_iso(state.get("last_updated"), ""),
            "attributes": {
                key: _safe_json(attributes[key])
                for key in sorted(allowed)
                if key in attributes
            },
        }

    def _normalize_conversation(
        self,
        data: dict[str, Any],
        context: dict[str, str | None],
        occurred_at: Any,
        received_at: str,
    ) -> dict[str, Any] | None:
        user_id = context.get("user_id")
        if self.require_authenticated_conversation and not user_id:
            return None

        payload: dict[str, Any] = {
            key: _safe_scalar(data.get(key))
            for key in sorted(_CONVERSATION_METADATA_FIELDS)
            if data.get(key) is not None
        }
        # Ordinary utterance content is not an Edge artifact.  A plain text
        # digest would still be a brute-forceable fingerprint for short home
        # commands, and lengths disclose unnecessary content metadata.  The
        # authenticated private teaching transaction is the only MVP path
        # that persists an exact quote, encrypted by Core.

        return {
            "schema_version": SCHEMA_VERSION,
            "source_event_type": EVENT_CONVERSATION_FINISHED,
            "occurred_at": _as_iso(occurred_at, received_at),
            "received_at": received_at,
            "context": context,
            "data": payload,
            "privacy": {
                "classification": "private_conversation",
                "retention_seconds": 24 * 60 * 60,
                "content_included": False,
                "content_discarded": True,
            },
        }


def _clean_values(values: Any) -> set[str]:
    if isinstance(values, str):
        values = values.split(",")
    return {str(value).strip().lower() for value in values if str(value).strip()}


def _context_dict(value: Any) -> dict[str, str | None]:
    if isinstance(value, Mapping):
        source = value
    else:
        source = {
            "id": getattr(value, "id", None),
            "user_id": getattr(value, "user_id", None),
            "parent_id": getattr(value, "parent_id", None),
        }
    return {
        "id": _optional_string(source.get("id")),
        "user_id": _optional_string(source.get("user_id")),
        "parent_id": _optional_string(source.get("parent_id")),
    }


def _optional_string(value: Any) -> str | None:
    return str(value) if value is not None else None


def _safe_json(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(k): _safe_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_safe_json(item) for item in value]
    return str(value)
