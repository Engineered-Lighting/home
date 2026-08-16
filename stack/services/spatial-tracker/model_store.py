"""Apartment model store.

Pulls the apartment model from Home Assistant's
``/api/extended_openai_conversation/apartment_model`` endpoint every 30 s,
caches the last good copy to ``{DATA_DIR}/model/apartment_model.json`` and
falls back to that file (or an empty model) when HA is unreachable or the
model does not exist yet.

The pure, network-free part is :class:`ApartmentModel` — a parsed wrapper
around the raw model dict providing shapely zone polygons and camera lookup.
Tests use it directly; the async poller (:class:`ModelStore`) is only wired
in ``main.py``.

Coordinate frame: right-handed, Z-up, meters, floor at z=0. Named targets and
fixture calibration records are preserved even though tracking currently only
indexes zones, devices, and cameras.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

from shapely.geometry import Point, Polygon

log = logging.getLogger("spatial.model")

MODEL_FILENAME = "apartment_model.json"
FLOOR_FILENAME = "floor.json"
WALKABLE_BUFFER_M = 0.5


class ApartmentModel:
    """Parsed apartment model (pure; safe to construct in tests)."""

    def __init__(self, raw: Optional[dict]):
        self.raw: dict = raw or {}
        self.revision = self.raw.get("revision")
        self.schema_version = self.raw.get("schema_version")
        self.zones: list[dict] = list(self.raw.get("zones") or [])
        self.devices: list[dict] = list(self.raw.get("devices") or [])
        self.targets: list[dict] = list(self.raw.get("targets") or [])

        self.zone_polys: dict[str, Polygon] = {}
        self._camera_to_zone: dict[str, str] = {}
        for z in self.zones:
            zid = z.get("id")
            poly_pts = z.get("floor_polygon") or []
            if zid and len(poly_pts) >= 3:
                try:
                    self.zone_polys[zid] = Polygon([(float(x), float(y)) for x, y in poly_pts])
                except Exception:
                    log.warning("zone %s has invalid floor_polygon; skipped", zid)
            cam = z.get("frigate_camera")
            if zid and cam:
                self._camera_to_zone[cam] = zid

        self._camera_devices: dict[str, dict] = {}
        for dev in self.devices:
            cam = dev.get("camera") or {}
            name = cam.get("frigate_name")
            if name:
                self._camera_devices[name] = dev

    # ---- lookups -------------------------------------------------------
    def zone_for_camera(self, camera: str) -> Optional[str]:
        """Map a Frigate camera name to a room/zone id.

        Primary: zones[].frigate_camera.  Fallback: camera name == zone id.
        Returns None when the camera maps to nothing (e.g. outdoor cams) —
        callers should ignore such events.
        """
        zid = self._camera_to_zone.get(camera)
        if zid:
            return zid
        if camera in self.zone_polys or any(z.get("id") == camera for z in self.zones):
            return camera
        return None

    def camera_device(self, frigate_name: str) -> Optional[dict]:
        return self._camera_devices.get(frigate_name)

    def zone(self, zone_id: str) -> Optional[dict]:
        for z in self.zones:
            if z.get("id") == zone_id:
                return z
        return None

    def zones_containing(self, x: float, y: float) -> list[str]:
        """Zone ids whose floor polygon covers (x, y); boundary counts."""
        p = Point(float(x), float(y))
        return [zid for zid, poly in self.zone_polys.items() if poly.covers(p)]

    def devices_in_room(self, room_id: Optional[str], dev_type: Optional[str] = None) -> list[dict]:
        out = []
        for d in self.devices:
            if room_id is not None and d.get("room_id") != room_id:
                continue
            if dev_type is not None and d.get("type") != dev_type:
                continue
            out.append(d)
        return out

    @property
    def exists(self) -> bool:
        return bool(self.raw.get("exists", bool(self.zones or self.devices)))


EMPTY_MODEL = ApartmentModel({})


def load_floor(data_dir: str | Path) -> Optional[Polygon]:
    """Load {data_dir}/model/floor.json -> walkable shapely polygon (with holes), or None."""
    path = Path(data_dir) / "model" / FLOOR_FILENAME
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        shell = raw.get("walkable_polygon") or []
        holes = raw.get("holes") or []
        if len(shell) < 3:
            return None
        return Polygon(shell, [h for h in holes if len(h) >= 3])
    except Exception:
        log.exception("failed to parse %s", path)
        return None


class ModelStore:
    """Async store: HA poller + on-disk fallback.

    - ``get_model()`` is a cheap synchronous read of the current model.
    - ``poll_forever(session)`` runs the 30 s HA poll loop.
    - Every successful fetch is cached to ``{data_dir}/model/apartment_model.json``
      so a later HA outage (or restart) still has the last-good model.
    - Writes are guarded by an asyncio lock.
    """

    def __init__(
        self,
        ha_url: str,
        ha_token: str,
        data_dir: str | Path = "/data",
        poll_interval: float = 30.0,
    ):
        self.ha_url = (ha_url or "").rstrip("/")
        self.ha_token = ha_token or ""
        self.data_dir = Path(data_dir)
        self.poll_interval = poll_interval
        self._lock = asyncio.Lock()
        self._model: ApartmentModel = EMPTY_MODEL
        self._walkable: Optional[Polygon] = None
        self._walkable_hull: Optional[Polygon] = None
        self._last_fetch_ok: Optional[float] = None
        self.load_local()

    # ---- sync accessors -------------------------------------------------
    def get_model(self) -> ApartmentModel:
        return self._model

    def walkable(self) -> Optional[Polygon]:
        return self._walkable

    def walkable_hull(self) -> Optional[Polygon]:
        """Walkable polygon buffered +0.5 m (used to gate floor hits)."""
        return self._walkable_hull

    # ---- local fallback --------------------------------------------------
    def load_local(self) -> None:
        path = self.data_dir / "model" / MODEL_FILENAME
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                self._model = ApartmentModel(raw)
                log.info("loaded seed apartment model from %s (rev=%s, %d zones)",
                         path, self._model.revision, len(self._model.zones))
            except Exception:
                log.exception("failed to parse seed model %s; using empty model", path)
                self._model = EMPTY_MODEL
        else:
            log.info("no seed model at %s; starting with empty model", path)
            self._model = EMPTY_MODEL
        self._walkable = load_floor(self.data_dir)
        self._walkable_hull = (
            self._walkable.buffer(WALKABLE_BUFFER_M) if self._walkable is not None else None
        )
        if self._walkable is not None:
            log.info("walkable floor polygon loaded (area %.1f m^2)", self._walkable.area)

    def _cache_path(self) -> Path:
        return self.data_dir / "model" / MODEL_FILENAME

    def _write_cache(self, raw: dict) -> None:
        path = self._cache_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(raw, indent=2), encoding="utf-8")
            os.replace(tmp, path)
        except Exception:
            log.exception("failed to cache apartment model to %s", path)

    # ---- async poller ----------------------------------------------------
    async def fetch_once(self, session) -> bool:
        """One HA GET. Returns True on success (model updated + cached)."""
        url = f"{self.ha_url}/api/extended_openai_conversation/apartment_model"
        headers = {}
        if self.ha_token:
            headers["Authorization"] = f"Bearer {self.ha_token}"
        try:
            async with session.get(url, headers=headers, timeout=10) as resp:
                if resp.status != 200:
                    log.warning("apartment_model GET %s -> HTTP %d", url, resp.status)
                    return False
                raw = await resp.json()
        except Exception as e:
            log.warning("apartment_model GET failed (%s); using fallback model", e)
            return False

        if not isinstance(raw, dict):
            log.warning("apartment_model GET returned non-dict; ignoring")
            return False
        if raw.get("exists") is False:
            log.info("HA reports apartment_model does not exist yet; keeping fallback")
            return False

        async with self._lock:
            old_rev = self._model.revision
            self._model = ApartmentModel(raw)
            self._last_fetch_ok = time.time()
        if self._model.revision != old_rev:
            log.info("apartment model updated: rev %s -> %s (%d zones, %d devices)",
                     old_rev, self._model.revision, len(self._model.zones), len(self._model.devices))
        self._write_cache(raw)
        return True

    async def poll_forever(self, session) -> None:
        while True:
            try:
                await self.fetch_once(session)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("model poll iteration failed")
            await asyncio.sleep(self.poll_interval)
