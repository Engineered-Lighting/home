"""probe_router_backoff_on_429.py: router v0's back-off and timeout against the live pump it must meet.

What it checks
  A. main's QwenLivePump (stack/services/perception-lab/qwen_live.py), driven with a
     scripted transport exactly as its own tests drive it: after a 429 refusal
     (queue_busy, perceiver_busy, interactive_queued) it skips one tick and sends again
     4 s later; only 503 admission_closed / thermal_shed start the 30 s back-off.
  B. den-bundle's Router (ROUTER-V0): after one 'refused' answer (its README names the
     GPU host's 429, note R19) it sends nothing for 30 s, for every track.
  C. Throughput under the same i.i.d. 429 sequence (p = 0.1, 0.3; R19: the pilot
     answers 429 whenever vLLM has a waiting request or >= 3 running, voice included):
     answered requests per minute, live pump vs router v0, and router v0's per-person
     gap with two tracks.
  D. A lost reply: router v0 holds its one slot for timeout_s (proposed 20 s) and then
     backs off 30 s, while every live request's deadline is at most 8 s
     (qwen_live.MAX_DEADLINE_SECONDS; the pilot clamps to its deadline_seconds).
  Prints RESULT lines and PASS/FAIL (FAIL = router v0 disagrees with the live rule).

How to run
  python3 -I probe_router_backoff_on_429.py <claude/rung-5-bundle checkout root> <origin/main checkout root>

Synthetic data only: temp sqlite store and in-memory JPEGs; no network, no camera.
"""
import io
import json
import os
import random
import sys
import tempfile
from pathlib import Path


def main():
    bundle_root, main_root = map(os.path.abspath, sys.argv[1:3])
    lab = os.path.join(main_root, 'stack', 'services', 'perception-lab')
    sys.path.insert(0, lab)
    sys.path.insert(0, os.path.join(bundle_root, 'tools', 'den-bundle'))
    import qwen_live as live
    import qwen_scene as scene
    from config import Settings
    from store import Store
    from qwen_transport import QwenNotExecuted
    import fakes
    import method
    import records
    import router
    import synth

    # ---------------- A: the live pump, as test_qwen_live.PumpTests builds it -------------
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
    TCONF = {'hostname': 'fixture.invalid', 'port': 9448, 'ca_file': '/fixture/ca.pem',
             'client_certificate': '/fixture/client.pem', 'client_key': '/fixture/client.key',
             'deadline_seconds': 7, 'response_grace_seconds': 3, 'poll_seconds': .5}

    class FakeTransport:
        def __init__(self, outcomes):
            self.config = dict(TCONF)
            self.outcomes = list(outcomes)
            self.sent = 0

        def __call__(self, envelope):
            self.sent += 1
            o = self.outcomes.pop(0)
            if isinstance(o, Exception):
                raise o
            return {'version': 1, 'request_id': envelope['request_id'], 'body_sha256': envelope['body_sha256'],
                    'terminal': True, 'state': 'completed', 'engine_epoch': 'host-epoch', 'artifact_sha256': 'a' * 64,
                    'reason': None, 'usage': {'prompt_tokens': 900, 'completion_tokens': 80},
                    'response': {'id': 'x', 'model': 'qwen3-vl-30b', 'choices': [{'index': 0, 'finish_reason': 'stop',
                                 'message': {'role': 'assistant', 'content': json.dumps(FACTS)}}]}}

        def lookup(self, reference):
            return {'version': 1, 'request_id': reference['request_id'], 'body_sha256': reference['body_sha256'],
                    'terminal': False, 'state': 'not_found', 'engine_epoch': 'host-epoch', 'artifact_sha256': 'a' * 64,
                    'reason': None}

        def close(self):
            pass

    class PumpRig:
        def __init__(self, outcomes):
            self.dir = tempfile.TemporaryDirectory()
            self.clock, self.mono = Clock(1000.0), Clock(50.0)
            self.store = Store(Settings(storage_root=Path(self.dir.name), min_free_bytes=0), clock=self.clock)
            s, _ = self.store.create_session('den', 'detection', 'Fixture', 'request1')
            self.store.transition(s['id'], 'starting')
            self.store.transition(s['id'], 'recording')
            self.session = self.store.get_session(s['id'])
            self.seq = 0
            self.add_frames(990.0, 999.5)
            self.transport = FakeTransport(outcomes)
            self.pump = live.QwenLivePump(self.store, self.session, transport=self.transport, profile=dict(PROFILE),
                                          clock=self.clock, monotonic=self.mono, local_time=lambda e: (19, 40),
                                          enabled=lambda: True,
                                          occupied=lambda: {'state': 'occupied', 'reason': 'person_detected'})

        def add_frames(self, start, end):
            t = start
            while t <= end + 1e-9:
                self.seq += 1
                meta = {'site_id': self.session['site_id'], 'camera_id': 'den', 'capture_epoch': 'cap-1', 'frame_id': 'cap-1-0-%d' % self.seq,
                        'sequence': self.seq, 'stream': 'rgb', 'width': 640, 'height': 360, 'format': 'jpeg',
                        'observed_at': round(t, 3), 'available_at': round(t, 3) + .3, 'valid_until': round(t, 3) + 2.5,
                        'uncertainty_seconds': .5, 'clock_mapping_status': 'verified', 'clock_mapping_id': 'clock-1',
                        'session_time_s': t - self.session['started_at']}
                self.store.add_media(self.session['id'], jpeg((self.seq % 250, 50, 60)), 'image/jpeg', meta,
                                     '["cap-1","rgb_preview",0,%d]' % self.seq)
                t += .5

        def advance(self, dt):
            self.clock.now += dt
            self.mono.now += dt
            self.add_frames(self.clock.now - dt + .5, self.clock.now - .5)

        def close(self):
            self.store.close()
            self.dir.cleanup()

    fails = 0
    print('RESULT main qwen_live: BACKOFF_REFUSALS=%s BACKOFF_SECONDS=%s interval=%s max_per_minute=%s MAX_DEADLINE_SECONDS=%s'
          % (sorted(live.BACKOFF_REFUSALS), live.BACKOFF_SECONDS, live.DEFAULTS['interval_seconds'],
             live.DEFAULTS['max_per_minute'], live.MAX_DEADLINE_SECONDS))
    for reason in ('queue_busy', 'perceiver_busy', 'interactive_queued', 'admission_closed'):
        rig = PumpRig([QwenNotExecuted(reason), None, None])
        first = rig.pump.tick()['reason']
        rig.advance(4)
        second = rig.pump.tick()['reason']
        print('RESULT A live pump: refusal %-18s -> tick+4s: %-24s backoff_until set: %s, sends so far %d'
              % (reason, second, rig.pump.backoff_until is not None, rig.transport.sent))
        rig.close()

    # ---------------- B: router v0 after one refusal --------------------------------------
    m, rule = method.load_method(), method.load_router()

    def store_for(n_tracks, horizon):
        obs, tracks = [], []
        for t in synth.steps(0.0, horizon, 0.5):
            synth.seen(obs, tracks, 'A', t, 1.95, 0.95)
            if n_tracks == 2:
                synth.seen(obs, tracks, 'B', t, 3.2, 2.6, standing=False)
        return records.Store(obs, tracks)

    def route(store, script, until, S=1.25):
        clock = fakes.FakeClock(synth.T0 + 3.0)
        sender = fakes.FakeSender(clock, latency_s=S, script=script)
        r = router.Router(store, m, rule, clock, sender)
        return router.run(r, clock, sender, synth.T0 + until)

    st = store_for(1, 120.0)
    recs = [r for r in route(st, {1: 'refused'}, 100.0) if r['outcome'] != 'no_box']
    gap = recs[1]['sent_ts'] - recs[0]['done_ts']
    print('RESULT B router v0: request 1 refused at +%.2f s; request 2 sent %.2f s after that answer' % (
        recs[0]['done_ts'] - recs[0]['sent_ts'], gap))
    if gap >= 29.9:
        fails += 1
        print('FAIL B router v0 waits %.1f s after a 429 where the live pump waits 4 s' % gap)

    # ---------------- C: throughput under the same i.i.d. 429 sequence ----------------------
    horizon = 1200.0
    for p in (0.1, 0.3):
        rng = random.Random(7)
        seq = ['refused' if rng.random() < p else 'ok' for _ in range(2000)]
        script = {i + 1: o for i, o in enumerate(seq)}
        for n in (1, 2):
            st = store_for(n, horizon + 10)
            recs = [r for r in route(st, script, horizon) if r['outcome'] != 'no_box']
            oks = [r for r in recs if r['outcome'] == 'ok']
            gaps = []
            for tid in ('A', 'B')[:n]:
                ts_ = [r['done_ts'] for r in oks if r['track_id'] == tid]
                gaps += [b - a for a, b in zip(ts_, ts_[1:])]
            mean_gap = sum(gaps) / len(gaps) if gaps else float('inf')
            print('RESULT C router v0 p=%.1f tracks=%d: %.2f answered/min, mean per-person refresh %.1f s, max %.1f s'
                  % (p, n, len(oks) / (horizon - 3) * 60, mean_gap, max(gaps) if gaps else float('inf')))
        # live pump: one tick every 4 s, same sequence of outcomes per attempt
        ticks = int((horizon - 3) // 4)
        outcomes = [QwenNotExecuted('queue_busy') if o == 'refused' else None for o in seq[:ticks + 5]]
        rig = PumpRig(outcomes)
        ok = 0
        for _ in range(ticks):
            if rig.pump.tick()['reason'] == 'recorded_valid':
                ok += 1
            rig.advance(4)
        rig.close()
        print('RESULT C live pump  p=%.1f: %.2f answered/min (no back-off on 429; one request per tick)'
              % (p, ok / (ticks * 4) * 60))

    # ---------------- D: a reply that never comes ------------------------------------------
    st = store_for(1, 120.0)
    recs = [r for r in route(st, {1: 'silent'}, 100.0) if r['outcome'] != 'no_box']
    print('RESULT D router v0: request 1 %s at +%.1f s; next send %.1f s after request 1 (live deadline <= %d s)'
          % (recs[0]['outcome'], recs[0]['done_ts'] - recs[0]['sent_ts'], recs[1]['sent_ts'] - recs[0]['sent_ts'],
             live.MAX_DEADLINE_SECONDS))
    if recs[1]['sent_ts'] - recs[0]['sent_ts'] > live.MAX_DEADLINE_SECONDS + 30 + 1:
        fails += 1
        print('FAIL D router v0 idles %.0f s after a lost reply; the live deadline ends it by %d s'
              % (recs[1]['sent_ts'] - recs[0]['sent_ts'], live.MAX_DEADLINE_SECONDS))
    print('RESULT %d disagreement(s) between router v0 and the live pump' % fails)


if __name__ == '__main__':
    main()
