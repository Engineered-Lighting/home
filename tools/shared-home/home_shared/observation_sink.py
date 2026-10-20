"""Trusted local result-to-storage binding; no network or action authority.

The caller supplies authenticated gateway results for the active adapter work.
Only protocol.finish_observation may admit a result. Persistence completes before
a non-retained MQTT publication is returned to the caller. Storage failures are
raised, never converted into publications or new inference requests.

This is a serialized adapter callback, not a thread-safe daemon or durable
publication outbox. Once terminal work has been consumed, duplicate deliveries
are ignored. A crash or failure before publishing can lose a live caption; saved
history must never be replayed into current context on restart.
"""
from __future__ import annotations

from .adapter import VictoriaAdapter, Work
from .observations import ObservationInput, ObservationProvenance, VictoriaObservationStore
from .protocol import FrigateWire, finish_observation


# Do not forward arbitrary model-supplied keys into the public context contract.
_PUBLIC_FIELDS = frozenset({
    "request_id", "site_id", "camera_id", "room_id", "adapter_session",
    "sequence", "occupancy_generation", "capture_session", "frame_id",
    "observed_at", "valid_until", "uncertainty_seconds", "description",
    "observations", "status", "authority", "device_id", "source_event_id",
    "request_deadline", "configuration_revision", "model_revision",
})


class ObservationSink:
    """Persist accepted pilot observations using the provisioned source binding.

    Invoke finish on the same serialized execution path as adapter callbacks.
    The publisher must send the returned tuple once without retention. Gateway
    reservations remain the gateway's responsibility, including late results.
    """

    def __init__(self, adapter: VictoriaAdapter, store: VictoriaObservationStore):
        if (store.binding.site_id != adapter.site_id
                or store.binding.cameras.get(adapter.camera_id) != adapter.room_id):
            raise ValueError("observation store must bind the registered pilot site/camera/room")
        self.adapter = adapter
        self.store = store

    def finish(self, work: Work, response: dict, *, now_mono: float,
               now_unix: float) -> tuple[str, str, bool] | None:
        # Completion clears these handles. Snapshot provenance before admitting
        # the result, and never trust a model-supplied clock mapping revision.
        active_work, active_frame = self.adapter.active, self.adapter.active_frame
        observation = finish_observation(
            self.adapter, work, response, now_mono=now_mono, now_unix=now_unix)
        if observation is None:
            return None
        if active_work is None or active_frame is None:
            raise ValueError("accepted observation lost its trusted source provenance")

        structured = observation.get("observations", ())
        if not isinstance(structured, (list, tuple)):
            raise ValueError("structured observations must be an array")
        value = ObservationInput(
            site_id=observation["site_id"],
            source_instance=self.store.binding.source_instance,
            camera_id=observation["camera_id"],
            room_id=observation["room_id"],
            producer_session=observation["adapter_session"],
            producer_sequence=observation["sequence"],
            occupancy_generation=observation["occupancy_generation"],
            capture_session=observation["capture_session"],
            frame_id=observation["frame_id"],
            request_id=observation["request_id"],
            observed_at=observation["observed_at"],
            received_at=now_unix,
            description=observation["description"],
            observations=tuple(structured),
            provenance=ObservationProvenance(
                model_revision=observation.get("model_revision"),
                configuration_revision=observation.get("configuration_revision"),
                clock_mapping_revision=active_frame.clock_mapping_revision,
                uncertainty_seconds=active_frame.uncertainty_seconds,
                scheduled_at=active_work.scheduled_at,
                request_deadline=active_work.request_deadline,
                valid_until=observation["valid_until"],
            ),
        )
        self.store.ingest(value)
        public = {key: value for key, value in observation.items() if key in _PUBLIC_FIELDS}
        public["request_deadline"] = active_work.request_deadline
        public["source_instance"] = self.store.binding.source_instance
        return FrigateWire.publication(public)
