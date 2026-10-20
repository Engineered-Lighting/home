"""Public journal regression for a durable publication with a lost reply.

The primary graph supplies real qualified capture/model/spatial context from
fictional external I/O. A second real spatial producer writes its own isolated
journal. Only the publication response is lost; no geometry or journal result
is substituted for the production implementation.
"""
from copy import deepcopy
from pathlib import Path
import threading
import unittest

import live_capture_fixtures as capture
from journal import Journal, PREFIX
from live_spatial import LiveSpatialProducer
from live_test_helpers import LiveHttp
import test_live_spatial as spatial_tests


class LiveSpatialReceiptTests(unittest.TestCase):
    setUp = spatial_tests.LiveSpatialTests.setUp
    prepare = spatial_tests.LiveSpatialTests.prepare
    camera_pair = spatial_tests.LiveSpatialTests.camera_pair
    construct = spatial_tests.LiveSpatialTests.construct
    control_clock = spatial_tests.LiveSpatialTests.control_clock
    advance = spatial_tests.LiveSpatialTests.advance

    def test_lost_ack_retry_is_causal_and_restores_continuity_for_next_capture(self):
        self.prepare()
        self.local_grant['operations'].append('interaction')
        contexts = []

        def retain_context(context, **_):
            retained = deepcopy(context)
            retained['spatial'] = None
            contexts.append(retained)
            return None

        graph = self.construct(stages={'interaction': retain_context})
        self.assertEqual(graph.step()['status'], 'observed')
        self.assertEqual(len(contexts), 1)
        producer_holder = {}

        def original_receipt(store_id, receipt_id):
            try:
                return graph.capture.resolve_receipt(store_id, receipt_id)
            except KeyError:
                return producer_holder['producer'].resolve_receipt(store_id, receipt_id)

        journal = Journal(Path(self.work.name) / 'synthetic-spatial-lost-ack.sqlite',
                          grants=[self.journal_grant], profile='home-live-shadow-1',
                          live_config=self.live_config, clock=lambda: self.now,
                          live_receipt_resolver=original_receipt)
        self.addCleanup(journal.close)
        server = LiveHttp(journal)
        self.addCleanup(server.close)
        receipts = []

        def publish_then_lose_first_reply(event):
            status, receipt = server.request('POST', PREFIX + '/events', event, token=capture.TOKEN)
            self.assertEqual(status, 200, receipt)
            receipts.append(deepcopy(receipt))
            if len(receipts) == 1:
                raise TimeoutError('synthetic response loss after durable acceptance')
            return receipt

        producer = LiveSpatialProducer(self.live_config, source_id=capture.SOURCE,
            revision=capture.REVISION, policy=self.spatial_policy, method_ref=self.spatial_method,
            capture_input=graph.capture.measurement_input, clock=lambda: self.now,
            publish=publish_then_lose_first_reply, barrier=graph.capture.source_gap,
            max_captures=self.config['limits']['max_cycles'], monotonic=lambda: self.mono)
        producer_holder['producer'] = producer
        self.addCleanup(producer.clear)
        options = dict(authorized=lambda: True, deadline=self.mono + 10, cancel=threading.Event())

        def page():
            status, result = server.request('GET', PREFIX +
                '/events?site_id=synthetic-site&room_id=synthetic-room&after=0', token=capture.TOKEN)
            self.assertEqual(status, 200, result)
            return result['items']

        first_context = deepcopy(contexts[0])
        future_model = deepcopy(first_context)
        future_model['pose_available_at'] = self.now + 1
        refused = producer.offer(future_model, **options)
        self.assertEqual(refused['reason'], 'spatial_model_clock_invalid')
        wrong_clock = deepcopy(first_context)
        wrong_clock['availability_clock'] = 'device_ticks'
        refused = producer.offer(wrong_clock, **options)
        self.assertEqual(refused['reason'], 'spatial_model_clock_invalid')
        self.assertEqual(page(), [])
        lost = producer.offer(first_context, **options)
        self.assertEqual(lost['status'], 'refused', lost)
        first_page = page()
        self.assertEqual(len(first_page), 1)
        first_publication = deepcopy(first_page[0])
        first_payload = first_publication['payload']

        changed = deepcopy(first_context)
        changed['configuration_digest'] = 'f' * 64
        conflict = producer.offer(changed, **options)
        self.assertEqual(conflict['reason'], 'spatial_capture_identity_conflict', conflict)
        self.assertEqual(page(), first_page)
        self.assertEqual(len(receipts), 1)

        # A newer capture cannot pass the unacknowledged publication. The
        # original capture remains available, so an exact retry can settle it.
        self.advance()
        self.assertEqual(graph.step()['status'], 'observed')
        self.assertEqual(len(contexts), 2)
        pending = producer.offer(contexts[1], **options)
        self.assertEqual(pending['reason'], 'prior_spatial_publication_unconfirmed', pending)
        self.assertEqual(page(), first_page)
        self.assertEqual(len(receipts), 1)

        retry = producer.offer(first_context, **options)
        self.assertEqual(retry['status'], 'supplied', retry)
        self.assertEqual(retry['receipt']['cursor'], receipts[0]['cursor'])
        self.assertEqual(retry['publication']['payload'], first_payload)
        self.assertEqual(retry['publication']['payload']['available_at'], first_payload['available_at'])
        self.assertEqual(retry['publication']['payload']['observation_root_times'],
                         first_payload['observation_root_times'])
        self.assertEqual(page(), first_page)
        self.assertEqual(len(receipts), 2)

        progressed = producer.offer(contexts[1], **options)
        self.assertEqual(progressed['status'], 'supplied', progressed)
        self.assertEqual(progressed['publication']['payload']['person_ref'], first_payload['person_ref'])
        self.assertEqual(progressed['publication']['payload']['subject_ref'], first_payload['subject_ref'])
        self.assertEqual(progressed['publication']['payload']['head']['position']['capture_seq'], 8)
        self.assertGreater(progressed['receipt']['cursor'], retry['receipt']['cursor'])
        history = page()
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0], first_publication)

        status, snapshot = server.request('GET', PREFIX +
            '/snapshot?site_id=synthetic-site&room_id=synthetic-room', token=capture.TOKEN)
        self.assertEqual(status, 200, snapshot)
        self.assertEqual(len(snapshot['items']), 1)
        self.assertEqual(snapshot['items'][0]['payload'], progressed['publication']['payload'])


if __name__ == '__main__':
    unittest.main()
