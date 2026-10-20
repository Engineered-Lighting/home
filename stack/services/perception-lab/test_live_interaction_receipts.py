"""Real producer retries against an isolated public journal after lost replies.

The primary factory supplies actual capture/model/spatial computation from
fictional external I/O. Only a durable HTTP acknowledgement is withheld; no
completed semantic or geometry result substitutes for production computation.
"""
from copy import deepcopy
import base64
import json
from pathlib import Path
import threading
import unittest

import live_capture_fixtures as capture
from journal import Journal, PREFIX
from live_interaction import LiveInteractionProducer
from live_test_helpers import LiveHttp
import test_live_interaction as interaction_tests
import test_live_spatial as spatial_tests


class LiveInteractionReceiptTests(unittest.TestCase):
    setUp = interaction_tests.LiveInteractionTests.setUp
    prepare = interaction_tests.LiveInteractionTests.prepare
    control_clock = interaction_tests.LiveInteractionTests.control_clock
    construct = interaction_tests.LiveInteractionTests.construct
    snapshot = interaction_tests.LiveInteractionTests.snapshot

    def camera_pair(self, token, *, timeout):
        # This fictional sensor emits new captures every half second. Device
        # pair metadata and its per-frame causal owner receipt remain original.
        body = json.loads(spatial_tests.LiveSpatialTests.camera_pair(self, token, timeout=timeout))
        header = capture.decoded_header(body['header'])
        header['observed_at_unix_ns'] = 1800000000000000000 + (self.sequence - 7) * 500_000_000
        body['header'] = base64.urlsafe_b64encode(json.dumps(header).encode()).decode()
        return json.dumps(body).encode()

    def primary_contexts(self):
        self.prepare()
        # The public version-two graph owns real spatial computation. Its
        # existing local callback seam retains the input to a second producer.
        self.config.update(schema='home-production-acquisition-2', version=2)
        self.config.pop('interaction_method_ref')
        from journal import digest
        self.local_grant['configuration_digest'] = digest(self.config)
        contexts = []
        def retain(context, **_):
            contexts.append(deepcopy(context))
            return None
        self.graph = spatial_tests.LiveSpatialTests.construct(self, stages={'interaction': retain})
        self.assertEqual(self.graph.step()['status'], 'observed')
        self.assertEqual(len(contexts), 1)
        return contexts

    def secondary(self, context, *, lose_reply=3):
        holder = {}
        def resolve(store_id, receipt_id):
            for owner in (self.graph.capture, self.graph.spatial, holder.get('producer')):
                if owner is None:
                    continue
                try:
                    return owner.resolve_receipt(store_id, receipt_id)
                except KeyError:
                    continue
            raise KeyError((store_id, receipt_id))
        journal = Journal(Path(self.work.name) / 'synthetic-interaction-replies.sqlite',
            grants=[self.journal_grant], profile='home-live-shadow-1', live_config=self.live_config,
            clock=lambda: self.now, live_receipt_resolver=resolve)
        self.addCleanup(journal.close)
        server = LiveHttp(journal)
        self.addCleanup(server.close)
        receipts = []
        def publish(event):
            status, receipt = server.request('POST', PREFIX + '/events', event, token=capture.TOKEN)
            self.assertEqual(status, 200, receipt)
            receipts.append(deepcopy(receipt))
            if len(receipts) == lose_reply:
                raise TimeoutError('synthetic acknowledgement loss after durable acceptance')
            return receipt
        producer = LiveInteractionProducer(self.live_config, source_id=capture.SOURCE,
            revision=capture.REVISION, policy=self.interaction_policy, method_ref=self.interaction_method,
            capture_input=self.graph.capture.measurement_input, spatial_original=self.graph.spatial.original_receipt,
            clock=lambda: self.now, publish=publish, barrier=self.graph.capture.source_gap,
            max_captures=self.config['limits']['max_cycles'], monotonic=lambda: self.mono)
        holder['producer'] = producer
        self.addCleanup(producer.clear)
        first = self.copy_spatial(server, context)
        return producer, server, receipts, first

    def copy_spatial(self, server, context):
        context = deepcopy(context)
        status, ack = server.request('POST', PREFIX + '/events', context['spatial']['publication'], token=capture.TOKEN)
        self.assertEqual(status, 200, ack)
        context['spatial']['receipt'] = ack
        return context

    def page(self, server, endpoint='events?site_id=synthetic-site&room_id=synthetic-room&after=0'):
        status, result = server.request('GET', PREFIX + '/' + endpoint, token=capture.TOKEN)
        self.assertEqual(status, 200, result)
        return result['items']

    def test_lost_middle_reply_exact_retry_preserves_immutable_identity(self):
        contexts = self.primary_contexts()
        producer, server, replies, first = self.secondary(contexts[0])
        options = dict(authorized=lambda: True, deadline=self.mono + 10, cancel=threading.Event())
        lost = producer.offer(first, **options)
        self.assertEqual(lost['status'], 'refused', lost)
        history = self.page(server)
        self.assertEqual(len(history), 4)  # Real spatial parent and three facts.
        durable = deepcopy(history[-1])
        changed = deepcopy(first)
        changed['interaction'] = {'different_local_context': True}
        refused = producer.offer(changed, **options)
        self.assertEqual(refused['reason'], 'interaction_capture_identity_conflict', refused)
        self.assertEqual(self.page(server), history)

        self.sequence += 1
        self.now += .5
        self.mono += .5
        self.assertEqual(self.graph.step()['status'], 'observed')
        pending = producer.offer(contexts[1], **options)
        self.assertEqual(pending['reason'], 'prior_interaction_publication_unconfirmed', pending)
        self.assertEqual(self.page(server), history)

        retried = producer.offer(first, **options)
        self.assertEqual(retried['status'], 'supplied', retried)
        self.assertEqual(replies[2], replies[3])
        after_retry = self.page(server)
        self.assertEqual(len(after_retry), 6)
        self.assertEqual(after_retry[3], durable)
        self.assertEqual(retried['publications'][2]['payload'], durable['payload'])
        self.assertEqual(retried['publications'][2]['payload']['available_at'], durable['payload']['available_at'])
        self.assertEqual(retried['publications'][2]['payload']['observation_root_times'], durable['payload']['observation_root_times'])
        repeat = producer.offer(first, **options)
        self.assertEqual(repeat, retried)
        self.assertEqual(self.page(server), after_retry)

        # The second journal has a different transport epoch. Cross-journal
        # supersedes references are intentionally not rewritten to fake an
        # operational migration; the real graph test below proves next-head CAS.

    def test_expired_original_support_cannot_be_republished_or_refreshed(self):
        contexts = self.primary_contexts()
        producer, server, _, first = self.secondary(contexts[0], lose_reply=0)
        options = dict(authorized=lambda: True, deadline=self.mono + 10, cancel=threading.Event())
        result = producer.offer(first, **options)
        self.assertEqual(result['status'], 'supplied', result)
        history = self.page(server)
        self.now = min(event['valid_until'] for event in result['publications'])
        self.mono += 1
        expired = producer.offer(first, **options)
        self.assertEqual(expired['status'], 'refused', {key: expired.get(key) for key in ('status', 'reason')})
        self.assertEqual(self.page(server), history)
        current = self.page(server, 'snapshot?site_id=synthetic-site&room_id=synthetic-room')
        self.assertFalse(any(item['payload']['schema'] == 'home-live-fact-1' for item in current))

    def test_actual_graph_next_capture_uses_exact_previous_semantic_heads(self):
        self.prepare()
        graph = self.construct()
        self.assertEqual(graph.step()['interaction'], 'completed')
        first = {item['payload']['kind']: item for item in self.snapshot()['items']
                 if item['payload']['schema'] == 'home-live-fact-1'}
        self.sequence += 1
        self.now += .5
        self.mono += .5
        self.assertEqual(graph.step()['interaction'], 'completed')
        current = self.snapshot()['items']
        second = {item['payload']['kind']: item for item in current
                  if item['payload']['schema'] == 'home-live-fact-1'}
        self.assertEqual(set(first), set(second))
        for kind, newer in second.items():
            older = first[kind]
            self.assertEqual(older['payload']['person_ref'], newer['payload']['person_ref'])
            self.assertEqual(older['payload']['subject_ref'], newer['payload']['subject_ref'])
            expected = {key: older[key] for key in ('stream_epoch', 'cursor', 'producer_id', 'id', 'payload_digest')}
            expected['component_id'] = None
            self.assertEqual(newer['payload']['head']['supersedes'], expected)
            self.assertEqual(newer['payload']['head']['position']['capture_seq'], 8)
        self.assertTrue(all(item['payload']['head']['position']['capture_seq'] == 8 for item in current))

    def test_multiple_people_withdraw_support_without_semantic_failure_or_guess(self):
        self.prepare()
        graph = self.construct()
        self.assertEqual(graph.step()['interaction'], 'completed')
        self.boxes.append([430, 40, 620, 350])
        self.sequence += 1
        self.now += .5
        self.mono += .5
        cycle = graph.step()
        self.assertEqual(cycle['status'], 'observed', cycle)
        self.assertEqual(cycle['spatial'], 'completed', cycle)
        self.assertEqual(cycle['interaction'], 'unavailable', cycle)
        self.assertFalse(cycle['focused_output'])
        self.assertEqual(self.snapshot()['items'], [])


if __name__ == '__main__': unittest.main()
