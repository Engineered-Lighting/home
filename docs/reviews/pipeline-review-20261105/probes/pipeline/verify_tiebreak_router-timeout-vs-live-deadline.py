"""verify_tiebreak_router-timeout-vs-live-deadline.py: tiebreak check of finding router-timeout-vs-live-deadline.

What it checks
  1. Router v0 (den-bundle, claude/rung-5-bundle) after one lost reply, with timeout_s at the
     proposed 20 s and at the finding's suggested live bound (8 s deadline + 3 s grace = 11 s):
       a. a silent sender (never answers: the finding's case);
       b. a sender that recovers the lost reply by identity lookup, as main's live transport
          does (qwen_transport.QwenTransport.__call__ polls lookup until deadline + grace) or as
          QwenLivePump._reconcile does on its next tick. Recovery at +3 s and at +14 s.
     Prints when the next request starts and whether the recovered answer was kept or dropped.
  2. A scored router run refuses while timeout_s is open (method.load_router(scored=True) and
     Router(scored=True)).
  3. main's QwenLivePump after the same lost reply (transport raises CompletionUnproven after
     its deadline + grace budget), driven like its own _run loop on a fake clock:
       a. the GPU-host row turns out completed: when the pump's next new request goes out;
       b. the GPU-host row is indeterminate: the recording pump halts and sends nothing more.
  So it shows whether "the live deadline ends it by 8 s" describes the live pump's own
  recovery from a lost reply, and whether the suggested 11 s would help or hurt.

How to run
  python3 -I verify_tiebreak_router-timeout-vs-live-deadline.py <claude/rung-5-bundle root> [<origin/main root>]
  (part 3 needs the origin/main root and Pillow, which main's own perception-lab tests use.)

Synthetic data only: fake clocks, fake senders, a temp sqlite store, in-memory JPEGs.
No network, no camera, no host.
"""
import io
import json
import os
import sys
import tempfile
from pathlib import Path


def router_part(bundle_root):
    sys.path.insert(0, os.path.join(bundle_root, 'tools', 'den-bundle'))
    import fakes
    import method
    import records
    import router
    import synth

    m = method.load_method()

    def rule_with(timeout_s):
        block = method.read_block(method.ROUTER_PATH, method.ROUTER_FENCE)
        if timeout_s is not None:
            block = dict(block)
            block['timeout_s'] = timeout_s
        return method.router_from(block, 'ROUTER-V0.md')

    obs, tracks = [], []
    for t in synth.steps(0.0, 130.0, 0.5):
        synth.seen(obs, tracks, 'A', t, 1.95, 0.95)
    store = records.Store(obs, tracks)

    class RecoveringSender(fakes.FakeSender):
        """Request 1's reply is lost; an identity lookup recovers 'ok' recover_s after the send.
        recover_s None = silent (never answers). Every other request answers ok after 1.25 s."""

        def __init__(self, clock, recover_s):
            fakes.FakeSender.__init__(self, clock, latency_s=1.25)
            self.recover_s = recover_s

        def send(self, request):
            self.sent.append((self.clock(), request))
            if len(self.sent) == 1:
                if self.recover_s is not None:
                    self.pending.append((self.clock() + self.recover_s, request['request_id'], 'ok'))
                return
            self.pending.append((self.clock() + self.latency_s, request['request_id'], 'ok'))

    def case(timeout_s, recover_s):
        rule = rule_with(timeout_s)
        clock = fakes.FakeClock(synth.T0 + 3.0)
        sender = RecoveringSender(clock, recover_s)
        r = router.Router(store, m, rule, clock, sender)
        recs = [x for x in router.run(r, clock, sender, synth.T0 + 100.0) if x['outcome'] != 'no_box']
        first, second = recs[0], recs[1]
        return first, second['sent_ts'] - first['sent_ts']

    results = {}
    for timeout_s, label in ((None, 'proposed 20'), (11, 'suggested 11')):
        for recover_s, rlabel in ((None, 'silent'), (3.0, 'recovered +3 s'), (14.0, 'recovered +14 s')):
            first, gap = case(timeout_s, recover_s)
            results[(label, rlabel)] = (first['outcome'], gap)
            print('RESULT router timeout_s=%-12s lost reply %-16s: request 1 outcome=%-7s next start %.1f s after it%s'
                  % (label, rlabel, first['outcome'], gap,
                     '' if recover_s is None else (' (recovered answer %s)' % ('KEPT' if first['outcome'] == 'ok' else 'DROPPED'))))

    # 2. scored runs refuse while timeout_s is open
    try:
        method.load_router(scored=True)
        print('FAIL scored load_router accepted an open timeout_s')
    except method.MethodError as e:
        print('PASS scored load_router refuses: %s' % e)
    try:
        router.Router(store, m, rule_with(None), fakes.FakeClock(synth.T0), fakes.FakeSender(fakes.FakeClock(synth.T0)),
                      scored=True)
        print('FAIL scored Router accepted open slots')
    except method.MethodError as e:
        print('PASS scored Router refuses: %s' % e)

    silent20 = results[('proposed 20', 'silent')][1]
    rec14_20 = results[('proposed 20', 'recovered +14 s')]
    rec14_11 = results[('suggested 11', 'recovered +14 s')]
    print('RESULT finding arithmetic (silent sender, 20 s): next start %.1f s after the lost request -> %s'
          % (silent20, 'REPRODUCED' if abs(silent20 - 50.0) < 0.5 else 'NOT reproduced'))
    print('RESULT with a sender that reconciles at +14 s: 20 s keeps the answer, next start %.1f s; '
          'the suggested 11 s drops it (%s), next start %.1f s' % (rec14_20[1], rec14_11[0], rec14_11[1]))


def live_part(main_root):
    lab = os.path.join(main_root, 'stack', 'services', 'perception-lab')
    sys.path.insert(0, lab)
    import qwen_live as live
    import qwen_scene as scene
    from config import Settings
    from store import Store
    from qwen_transport import CompletionUnproven

    class Clock:
        def __init__(self, now):
            self.now = now

        def __call__(self):
            return self.now

    def jpeg(colour):
        from PIL import Image
        b = io.BytesIO()
        Image.new('RGB', scene.IMAGE_SIZE, colour).save(b, format='JPEG')
        return b.getvalue()

    PROFILE = {'model_version': scene.MODEL, 'served_model': scene.SERVED_MODEL, 'artifact_sha256': 'a' * 64,
               'engine_epoch': 'boot-epoch', 'runtime_revision': 'fixture-runtime', 'adapter_revision': scene.PROMPT_VERSION}
    FACTS = {'people_visible': 'one', 'subject_posture': 'sitting', 'held_object': 'book', 'book_state': 'open',
             'gaze_target': 'book', 'typing_visible': 'no', 'screen_facing_subject': 'not_visible',
             'activity_hypothesis': 'reading', 'visible_basis': ['person_visible', 'book_in_hands'],
             'change_vs_prior': 'no_prior'}
    DEADLINE, GRACE = 7, 3
    TCONF = {'hostname': 'fixture.invalid', 'port': 9448, 'ca_file': '/fixture/ca.pem',
             'client_certificate': '/fixture/client.pem', 'client_key': '/fixture/client.key',
             'deadline_seconds': DEADLINE, 'response_grace_seconds': GRACE, 'poll_seconds': .5}

    def completed(rid, sha):
        return {'version': 1, 'request_id': rid, 'body_sha256': sha, 'terminal': True, 'state': 'completed',
                'engine_epoch': 'host-epoch', 'artifact_sha256': 'a' * 64, 'reason': None,
                'usage': {'prompt_tokens': 900, 'completion_tokens': 80},
                'response': {'id': 'x', 'model': 'qwen3-vl-30b', 'choices': [{'index': 0, 'finish_reason': 'stop',
                             'message': {'role': 'assistant', 'content': json.dumps(FACTS)}}]}}

    class Rig:
        def __init__(self, lost_row_state):
            self.dir = tempfile.TemporaryDirectory()
            self.clock, self.mono = Clock(1000.0), Clock(50.0)
            self.store = Store(Settings(storage_root=Path(self.dir.name), min_free_bytes=0), clock=self.clock)
            s, _ = self.store.create_session('den', 'detection', 'Fixture', 'request1')
            self.store.transition(s['id'], 'starting')
            self.store.transition(s['id'], 'recording')
            self.session = self.store.get_session(s['id'])
            self.seq, self.last_frame = 0, None
            self.frames_to(999.5, start=990.0)
            rig = self

            class Transport:
                config = dict(TCONF)

                def __init__(self):
                    self.sends, self.lost = [], {}

                def __call__(self, envelope):
                    self.sends.append(rig.clock.now)
                    if len(self.sends) == 1:
                        # Lost reply: the real transport polls lookup until deadline + grace, then gives up.
                        budget = envelope['deadline'] - rig.clock.now + GRACE
                        rig.clock.now += budget
                        rig.mono.now += budget
                        self.lost[envelope['request_id']] = envelope['body_sha256']
                        raise CompletionUnproven('analysis_completion_unproven')
                    return completed(envelope['request_id'], envelope['body_sha256'])

                def lookup(self, ref):
                    rid = ref['request_id']
                    if rid in self.lost:
                        if lost_row_state == 'completed':
                            return completed(rid, self.lost[rid])
                        return {'version': 1, 'request_id': rid, 'body_sha256': self.lost[rid], 'terminal': False,
                                'state': 'indeterminate', 'engine_epoch': 'host-epoch', 'artifact_sha256': 'a' * 64,
                                'reason': None}
                    return {'version': 1, 'request_id': rid, 'body_sha256': ref['body_sha256'], 'terminal': False,
                            'state': 'not_found', 'engine_epoch': 'host-epoch', 'artifact_sha256': 'a' * 64,
                            'reason': None}

                def close(self):
                    pass

            self.transport = Transport()
            self.pump = live.QwenLivePump(self.store, self.session, transport=self.transport, profile=dict(PROFILE),
                                          clock=self.clock, monotonic=self.mono, local_time=lambda e: (19, 40),
                                          enabled=lambda: True,
                                          occupied=lambda: {'state': 'occupied', 'reason': 'person_detected'})

        def frames_to(self, end, start=None):
            t = start if start is not None else self.last_frame + .5
            while t <= end + 1e-9:
                self.seq += 1
                meta = {'site_id': self.session['site_id'], 'camera_id': 'den', 'capture_epoch': 'cap-1',
                        'frame_id': 'cap-1-0-%d' % self.seq, 'sequence': self.seq, 'stream': 'rgb', 'width': 640,
                        'height': 360, 'format': 'jpeg', 'observed_at': round(t, 3), 'available_at': round(t, 3) + .3,
                        'valid_until': round(t, 3) + 2.5, 'uncertainty_seconds': .5, 'clock_mapping_status': 'verified',
                        'clock_mapping_id': 'clock-1', 'session_time_s': t - self.session['started_at']}
                self.store.add_media(self.session['id'], jpeg((self.seq % 250, 50, 60)), 'image/jpeg', meta,
                                     '["cap-1","rgb_preview",0,%d]' % self.seq)
                self.last_frame = t
                t += .5

        def run(self, horizon):
            """QwenLivePump._run on a fake clock: tick, then sleep max(0, interval - tick time)."""
            interval = self.pump.config['interval_seconds']
            log = []
            t_end = self.clock.now + horizon
            while self.clock.now < t_end:
                started = self.mono.now
                t0 = self.clock.now
                reason = self.pump.tick()['reason']
                log.append((round(t0 - 1000.0, 2), reason))
                delay = max(0.0, interval - (self.mono.now - started))
                self.clock.now += delay
                self.mono.now += delay
                self.frames_to(self.clock.now - .5)
            return log

        def close(self):
            self.store.close()
            self.dir.cleanup()

    print('RESULT main qwen_live: MAX_DEADLINE_SECONDS=%s; this rig: deadline %s s, grace %s s '
          '(qwen_transport allows grace 1-60 s)' % (live.MAX_DEADLINE_SECONDS, DEADLINE, GRACE))
    for state in ('completed', 'indeterminate'):
        rig = Rig(state)
        log = rig.run(120.0)
        sends = [round(s - 1000.0, 2) for s in rig.transport.sends]
        rig.close()
        print('RESULT live pump, lost reply, GPU-host row %-13s: sends at %s; ticks: %s'
              % (state, sends[:4], log[:6]))
        if state == 'completed':
            gap = sends[1] - sends[0] if len(sends) > 1 else float('inf')
            reconcile_tick = log[1] if len(log) > 1 else (None, None)
            print('RESULT live pump: next new request %.1f s after the lost one (not within the 8 s deadline); '
                  'the lost request reconciled at +%s s as %r (a late answer, not a usable fact)'
                  % (gap, reconcile_tick[0], reconcile_tick[1]))
        else:
            print('RESULT live pump: %d further send(s) in 120 s after the lost reply (recording pump halts on an '
                  'indeterminate row)' % (len(sends) - 1))


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    bundle_root = os.path.abspath(sys.argv[1])
    router_part(bundle_root)
    if len(sys.argv) > 2:
        try:
            live_part(os.path.abspath(sys.argv[2]))
        except ImportError as e:
            print('RESULT live part skipped: %s' % e)


if __name__ == '__main__':
    main()
