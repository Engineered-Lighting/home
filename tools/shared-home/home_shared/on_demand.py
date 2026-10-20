"""Explicit camera requests using existing capture, gateway and journal contracts.

No occupancy producer, MQTT dependency, automatic work, or inference retry.
Construct and call on the application's event-loop thread with commissioned
camera/gateway implementations. This class does not grant model admission.
"""
import asyncio
import base64
import time
import uuid
from .protocol import camera_frame


class OnDemandRefresh:
    def __init__(self, *, camera, gateway, journal, caption_writer,
                 model_revision, configuration_revision, clock=time.time):
        if (gateway.gateway_identity != journal.gateway_identity
                or gateway.config.device_id != journal.device_id
                or gateway.config.configuration_revision != configuration_revision
                or dict(gateway.config.cameras) != {'den_oak': 'den'}):
            raise ValueError('refresh provisioning mismatch')
        self.camera, self.gateway, self.journal = camera, gateway, journal
        self.caption_writer, self.clock = caption_writer, clock
        self.model_revision, self.configuration_revision = model_revision, configuration_revision
        self.session, self.sequence = uuid.uuid4().hex, 0
        self.task = None
        self.closed = False
        self.snapshot = None
        self.snapshot_timer = None
        previous = journal.latest_refresh_time()
        self.last_accepted = previous if previous is not None else float('-inf')

    async def accept(self, namespace, request_id):
        now = self.clock()
        self.journal.expire_refreshes(now=now)
        prior = self.journal.refresh_status(namespace, request_id)
        if prior:
            return prior
        if not self.closed and self.journal.outstanding() and (self.task is None or self.task.done()):
            # Only query the original ID. A restart cannot authorize another POST.
            self.task = asyncio.create_task(self.reconcile())
        if (self.closed or (self.task and not self.task.done()) or self.journal.outstanding()
                or now - self.last_accepted < 30):
            raise ValueError('refresh busy or unresolved')
        self.journal.reserve_refresh(namespace, request_id, now=now)
        self._clear_snapshot()
        self.last_accepted = now
        self.sequence += 1
        self.task = asyncio.create_task(self._execute(request_id, now, self.sequence))
        return self.journal.refresh_status(namespace, request_id)

    async def status(self, namespace, request_id):
        self.journal.expire_refreshes(now=self.clock())
        result = self.journal.refresh_status(namespace, request_id)
        if (result and not self.closed and self.journal.outstanding()
                and (self.task is None or self.task.done())):
            self.task = asyncio.create_task(self.reconcile())
        return result

    async def reconcile(self):
        outstanding = self.journal.outstanding()
        if outstanding is None or self.closed:
            return
        try:
            response = await asyncio.to_thread(self.gateway.status, outstanding.request_id)
            self.journal.accept_status(response)
        except Exception:
            pass  # Unavailable lookup leaves the original reservation intact.
        # Recovery closes accounting only; never republish an old observation.

    def _clear_snapshot(self):
        self.snapshot = None
        if self.snapshot_timer:
            self.snapshot_timer.cancel()
            self.snapshot_timer = None

    async def image(self, namespace, request_id):
        receipt = self.journal.refresh_status(namespace, request_id)
        value = self.snapshot
        if value and self.clock() >= value['valid_until']:
            self._clear_snapshot()
            return None
        if (self.closed or not receipt or receipt.get('state') != 'terminal'
                or receipt.get('outcome') != 'completed' or not value
                or value['request_id'] != request_id):
            return None
        return dict(value)

    async def _execute(self, request_id, scheduled, sequence):
        prepared = False
        started = time.monotonic()
        connection = None
        def current():
            return not self.closed and self.clock() < scheduled + 15 and time.monotonic() - started < 15
        try:
            # TCP and TLS to the gateway open while the frame is read (speed
            # plan S1.5). That connection carries this one request and no
            # other; a capture that fails or goes stale closes it unused.
            preopen = getattr(self.gateway, 'preopen', None)
            if callable(preopen):
                connection = preopen(scheduled + 15 - self.clock())
            # ChronyPreviewClient performs source/clock/session/decode acceptance.
            jpeg, header = await asyncio.to_thread(self.camera)
            frame = camera_frame(jpeg, header)
            now = self.clock()
            if not current() or not 0 <= now - frame.observed_at <= 3 - frame.uncertainty_seconds:
                raise ValueError('fresh capture unavailable')
            payload = dict(version=1, request_id=request_id, site_id='victoria', device_id=self.journal.device_id,
                camera_id='den_oak', room_id='den', source_event_id='interactive:' + request_id,
                producer_session=self.session, producer_sequence=sequence, occupancy_generation=0,
                capture_session=frame.capture_session, frame_id=frame.frame_id, scheduled_at=scheduled,
                captured_at=frame.observed_at, received_at=now, clock_uncertainty_seconds=frame.uncertainty_seconds,
                timestamp_quality='validated_observation', request_deadline=scheduled + 15,
                task_class='interactive', configuration_revision=self.configuration_revision,
                max_output_tokens=128, image_base64=base64.b64encode(jpeg).decode('ascii'))
            if not self.journal.prepare(payload):
                return
            prepared = True
            if connection is None:
                response = await asyncio.to_thread(self.gateway.submit, payload)
            else:
                response = await asyncio.to_thread(self.gateway.submit, payload, preopened=connection)
            while True:
                if not self.journal.accept_status(response):
                    return
                if response['status'] == 'terminal':
                    break
                if not current():
                    return
                await asyncio.sleep(.5)
                response = await asyncio.to_thread(self.gateway.status, request_id)
            if response.get('outcome') != 'completed' or not current():
                return
            value = response.get('observation')
            bindings = ('site_id', 'device_id', 'camera_id', 'room_id', 'producer_session',
                        'producer_sequence', 'occupancy_generation', 'capture_session', 'frame_id')
            if (not isinstance(value, dict) or any(value.get(k) != payload[k] for k in bindings)
                    or value.get('observed_at') != frame.observed_at
                    or value.get('valid_until') != frame.observed_at + 180
                    or value.get('configuration_revision') != self.configuration_revision
                    or value.get('model_revision') != self.model_revision):
                return
            self.caption_writer({**value, 'request_id': request_id, 'request_deadline': scheduled + 15},
                                generated_at=self.clock())
            # One exact analyzed frame, RAM only, never a second capture or a
            # persisted media archive. Namespace authorization stays in journal.
            self.snapshot = dict(request_id=request_id, frame_id=frame.frame_id,
                observed_at=frame.observed_at, valid_until=value['valid_until'],
                image_base64=payload['image_base64'])
            self.snapshot_timer = asyncio.get_running_loop().call_later(
                max(0, value['valid_until'] - self.clock()), self._clear_snapshot)
        except Exception:
            # A failed POST or status read remains charged. No automatic retry.
            if not prepared:
                self.journal.refresh_not_queued(request_id)
        finally:
            if connection is not None:
                # Sends nothing; a no-op once submit took the connection.
                connection.close()

    async def close(self):
        self.closed = True
        self._clear_snapshot()
        if self.task:
            # The enclosing supervisor bounds process shutdown. Do not cancel a
            # network thread and then pretend its execution reservation ended.
            await asyncio.shield(self.task)
