"""Retained publishing: on change, and every 60 seconds regardless.

Home Assistant restarts and re-reads retained topics, so every belief topic is
published retained. To keep the broker quiet the publisher sends a topic only
when its payload changed or when the last send is ``STATE_REPEAT_S`` old --
which doubles as the proof that the publisher is alive and writing, not merely
connected.

Three special operations:

- ``clear`` sends a zero-length retained payload, which deletes the broker's
  retained message. Called for every state topic at startup so a crashed
  process's last belief cannot outlive it;
- ``announce`` publishes availability ``online`` retained (the last will,
  registered before connect, publishes ``offline``);
- ``shutdown`` publishes availability ``offline`` on a clean stop, so the
  entities go unavailable immediately instead of waiting for the broker to
  notice a dropped socket.

The client is anything with paho's ``publish(topic, payload, qos, retain)``.
"""
from __future__ import annotations

import datetime as dt

from .discovery import AVAILABILITY_OFFLINE, AVAILABILITY_ONLINE

STATE_REPEAT_S = 60
QOS = 0


class StatePublisher:
    """Publishes retained payloads, remembering what each topic last carried."""

    def __init__(self, client: object, repeat_s: float = STATE_REPEAT_S) -> None:
        self.client = client
        self.repeat_s = float(repeat_s)
        self.published = 0
        self.suppressed = 0
        self._last: dict[str, tuple[str, dt.datetime]] = {}

    # --- the general case -----------------------------------------------------

    def publish(self, topic: str, payload: str, now: dt.datetime, retain: bool = True,
                force: bool = False) -> bool:
        """Publish when the payload changed, the repeat is due, or ``force``."""
        previous = self._last.get(topic)
        if not force and previous is not None:
            last_payload, last_at = previous
            if last_payload == payload and (now - last_at).total_seconds() < self.repeat_s:
                self.suppressed += 1
                return False
        self.client.publish(topic, payload, qos=QOS, retain=retain)
        self._last[topic] = (payload, now)
        self.published += 1
        return True

    def changed_only(self, topic: str, payload: str, now: dt.datetime) -> bool:
        """Publish only on a change; no 60 s repeat (for attribute topics)."""
        previous = self._last.get(topic)
        if previous is not None and previous[0] == payload:
            self.suppressed += 1
            return False
        self.client.publish(topic, payload, qos=QOS, retain=True)
        self._last[topic] = (payload, now)
        self.published += 1
        return True

    # --- lifecycle ------------------------------------------------------------

    def clear(self, topics: tuple[str, ...] | list[str]) -> int:
        """Delete the retained message on each topic (zero-length payload)."""
        count = 0
        for topic in topics:
            self.client.publish(topic, "", qos=QOS, retain=True)
            self._last.pop(topic, None)
            count += 1
        self.published += count
        return count

    def announce(self, availability_topic: str) -> None:
        """Say the publisher is online (retained)."""
        self.client.publish(availability_topic, AVAILABILITY_ONLINE, qos=QOS, retain=True)
        self.published += 1

    def shutdown(self, availability_topic: str) -> None:
        """Say the publisher is offline on a clean stop (retained)."""
        self.client.publish(availability_topic, AVAILABILITY_OFFLINE, qos=QOS, retain=True)
        self.published += 1

    def forget(self) -> None:
        """Drop the change-detection cache, so the next tick republishes all.

        Called on every (re)connect: the broker may have lost the session and
        a retained topic may be stale, so nothing may be suppressed as
        "already published" across a reconnect.
        """
        self._last.clear()

    def as_dict(self) -> dict:
        return {"published": self.published, "suppressed": self.suppressed,
                "topics": len(self._last)}
