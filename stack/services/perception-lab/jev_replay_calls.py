"""Bounded paid JEV replay of a recorded session (plan §7.1 step 3).

Offline operator tool, run on the controller host; not part of the controller
release. It re-asks JEV at a recorded session's own request cutoffs with
today's v6 build and a chosen registered question set, through the
controller's own stack:

* the packet is built by JevProvider (the v6 wire, jev_wire.prepare), from the
  recorded request inputs (payload records, shared context, context snapshot);
* egress is authorized by LabJevPolicy, unchanged: the independent schema
  walk, the registered question set, per-block grants, sidecar provenance,
  digest binding, ledger re-derivation of every record and frame, the
  recompute, and durable admission with a reservation, the per-minute rate
  and the per-session call budget;
* the household policy file is read fresh for every check and must list the
  replay grant jev-replay-1 (and the grant of the replayed wire, den-facts-6
  or den-facts-7) in structured_sources; its kill switch (enabled) is read
  fresh too;
* the credential is the controller's private credential file.

Nothing is written to the live ledger. The ledger is opened read-only (use a
snapshot copy); admissions, reservations, the reasoning.request events and the
response cache live in a separate replay database. Responses are cached by
packet sha256, so a repeated packet never costs a second call. A hard call cap
counts every admission in the replay database, across runs.

The wire is the JEV config's own 'wire' unless --wire names another
registered one (e.g. den-facts-7 before the live config switches to it); its
desk_seat region is the config's, or --desk-seat FILE (a JSON object checked
exactly as the config key is), used by both the builder and the gate.

Usage:
  python jev_replay_calls.py --ledger SNAPSHOT.sqlite3 --replay-db REPLAY.sqlite3 --config JEV_CONFIG.json
      --session PREFIX --questions v6|v7|... [--wire den-facts-6|den-facts-7] [--desk-seat REGION.json]
      [--arms a,b] [--from-s S --to-s S] [--max-calls N] [--dry-run]
"""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import threading
import time

REPLAY_GRANT = 'jev-replay-1'
HARD_CALL_CAP = 600          # every admission in one replay database, all runs
REPLAY_POLICY_SECONDS = 30   # a replay grant is re-read and re-issued per check
MIN_CALL_SPACING_S = 1.0     # <= 60 calls per minute (plan §7.1)
MIN_FREE_BYTES = 2 * 1024**3


class ReplayStore:
    """The store surface LabJevPolicy needs: ledger reads from a read-only
    ledger view, every write (reservations, admissions, events) to the replay
    database only."""

    def __init__(self, view, path, clock=time.time):
        self.view, self.clock = view, clock
        self.path = Path(path)
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.transaction() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS lab_reasoning_requests(
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL, arm TEXT NOT NULL, generation INTEGER NOT NULL,
                state TEXT NOT NULL, payload TEXT NOT NULL, dispatched_at REAL, result TEXT)''')
            db.execute('''CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
                kind TEXT NOT NULL, created_at REAL NOT NULL, payload TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS replay_cache(packet_sha256 TEXT PRIMARY KEY, question_version TEXT NOT NULL,
                request_id TEXT NOT NULL, created_at REAL NOT NULL, result TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS replay_results(job TEXT NOT NULL, recorded_request_id TEXT NOT NULL,
                question_version TEXT NOT NULL, arm TEXT NOT NULL, cutoff REAL NOT NULL, packet_sha256 TEXT,
                source TEXT NOT NULL, status TEXT NOT NULL, result TEXT, PRIMARY KEY(job, recorded_request_id))''')

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield self.db
            except BaseException:
                self.db.execute('ROLLBACK')
                raise
            self.db.execute('COMMIT')

    def get_session(self, sid):
        if self.view.db.execute('SELECT 1 FROM session_erasures WHERE session_id=?', (sid,)).fetchone():
            raise ValueError('session erased or erasure pending')
        session = self.view.get_session(sid)
        session['fixture'] = bool(session.get('fixture'))
        return session

    def events_by_ids(self, session_id, ids, kinds):
        return self.view.events_by_ids(session_id, ids, kinds)

    def _event(self, db, sid, kind, payload):
        from reasoning import canonical
        return db.execute('INSERT INTO events(session_id,kind,created_at,payload) VALUES(?,?,?,?)',
                          (sid, kind, self.clock(), canonical(payload))).lastrowid

    def check_capacity(self, incoming=0):
        if shutil.disk_usage(str(self.path.parent)).free - incoming < MIN_FREE_BYTES:
            raise ValueError('Replay storage limit reached')

    def admissions(self):
        with self.lock:
            return self.db.execute('SELECT count(*) FROM lab_jev_egress').fetchone()[0]


def replay_policy(policy, *, now, job):
    """The household policy as a short replay grant, or ValueError. The
    standing session policy (schema 2) must be valid and enabled and must list
    jev-replay-1; the result is the schema-1 shape LabJevPolicy checks, bound
    to this replay job and valid for REPLAY_POLICY_SECONDS."""
    from managed_jev import session_policy
    from jev_policy import SOURCES_KEY, structured_sources
    checked = session_policy(policy, now=now)  # shape only (no session)
    if checked.get('schema_version') != 2 or checked.get('enabled') is not True:
        raise ValueError('Lab household policy unavailable')
    if REPLAY_GRANT not in structured_sources(checked):
        raise ValueError('Lab household policy unavailable')
    result = {k: v for k, v in checked.items() if k != 'session_seconds'}
    result.update(schema_version=1, expires_at=now + REPLAY_POLICY_SECONDS,
                  revision='%s:replay:%s' % (checked['revision'], job),
                  max_session_calls=min(checked['max_session_calls'], HARD_CALL_CAP))
    result[SOURCES_KEY] = list(checked[SOURCES_KEY])
    return result


def build(args, config, hooks):
    from jev_policy import LabJevPolicy
    from jev_provider import JevProvider, post_systemone
    from jev_replay import LedgerView
    from jev_wire import QUESTION_CHOICES
    private_json = hooks['private_json']
    wire_version, desk_seat = replay_wire(args, config, hooks)  # before anything is opened
    view = LedgerView(args.ledger)
    store = ReplayStore(view, args.replay_db)
    # A caller driving several sessions (lab_regression.py replay) names one job for all of them.
    job = getattr(args, 'job', None) or 'replay-%s-%s' % (args.questions, time.strftime('%Y%m%dT%H%M%S', time.gmtime()))

    def read_policy():
        return replay_policy(private_json(config['policy_file']), now=store.clock(), job=job)

    def toggle():
        policy = private_json(config['policy_file'])
        return {'state': 'on' if policy.get('enabled') is True else 'off', 'received_at': store.clock()}

    def credential():
        path = Path(config['credential_file'])
        if path.is_symlink() or path.stat().st_uid != os.geteuid() or path.stat().st_mode & 0o077:
            raise ValueError('Private Lab credential required')
        with path.open(encoding='utf-8') as stream:
            token = stream.read(4098).strip()
        if not 1 <= len(token) <= 4096:
            raise ValueError('Invalid Lab credential')
        return token

    credential = hooks.get('credential') or credential
    ha, zones = config.get('ha_bindings', {}), config.get('zone_bindings', {})
    gate = LabJevPolicy(store, read_policy, toggle, ha_bindings=ha, zone_bindings=zones,
                        site_timezone=config.get('site_timezone'), monitor_side=config.get('monitor_side'),
                        desk_seat=desk_seat)
    transport = hooks.get('transport') or post_systemone
    if args.dry_run:
        def transport(body, token, timeout):
            raise RuntimeError('dry run: no transport')
    provider = JevProvider(model_selector=private_json(config['policy_file'])['model_selector'], response_version=config['response_version'],
                           authorize=gate, credential=credential, transport=transport, allow_recent_history=True,
                           semantic=True, ha_bindings=ha, zone_bindings=zones, wire=wire_version,
                           monitor_side=config.get('monitor_side'), questions=QUESTION_CHOICES[args.questions],
                           desk_seat=desk_seat)
    return view, store, gate, provider, job


def replay_wire(args, config, hooks):
    """(wire id, desk_seat region or None): --wire else the config's wire;
    --desk-seat FILE else the config's desk_seat; checked as managed_jev
    checks the config (den-facts-7 needs a region; den-facts-8 uses one only
    as its no-3D-fact position fallback)."""
    from jev_semantics import desk_seat_config
    from jev_wire import WIRE_VERSION_7, WIRE_VERSIONS
    wire_version = getattr(args, 'wire', None) or config.get('wire')
    if wire_version not in WIRE_VERSIONS:
        raise SystemExit('a registered facts-only wire is required')
    path = getattr(args, 'desk_seat', None)
    region = hooks['private_json'](path) if path else config.get('desk_seat')
    region = None if region is None else desk_seat_config(region)
    if wire_version == WIRE_VERSION_7 and region is None:
        raise SystemExit('den-facts-7 needs a desk_seat region')
    return wire_version, region


def run(args, hooks=None):
    """hooks (tests only): private_json, transport, credential."""
    from jev_provider import ProviderFailure
    from jev_replay import _recorded
    from jev_evidence import granted_bindings, project, project_context
    from reasoning import canonical, digest
    import jev_wire as wire
    hooks = dict(hooks or {})
    if 'private_json' not in hooks:
        from live_detector_app import private_json
        hooks['private_json'] = private_json
    config = hooks['private_json'](args.config)
    if config.get('wire') not in wire.WIRE_VERSIONS:
        raise SystemExit('the controller config does not send a facts-only wire')
    view, store, gate, provider, job = build(args, config, hooks)
    try:
        gate.read()  # the replay grant and an enabled policy, before anything else
        return _run(args, view, store, gate, provider, job)
    finally:
        store.db.close()
        view.close()


def _run(args, view, store, gate, provider, job):
    from jev_provider import ProviderFailure
    from jev_replay import _recorded
    from jev_evidence import granted_bindings, project, project_context
    from reasoning import canonical, digest
    import jev_wire as wire
    question_version = provider.question_version
    sid = next(iter(view.session_ids([args.session])), None)
    if sid is None:
        raise SystemExit('session not found')
    session = store.get_session(sid)
    arms = set(args.arms.split(','))
    recorded = [r for r in _recorded(view, sid) if r['arm'] in arms]
    # Optional session-time window (seconds from the recording start): the
    # frame ring still sees every earlier request, only calls are limited.
    lo, hi = getattr(args, 'from_s', None), getattr(args, 'to_s', None)
    window = (lambda item: (lo is None or item['payload']['cutoff'] - session['started_at'] >= lo)
              and (hi is None or item['payload']['cutoff'] - session['started_at'] <= hi))
    rings = {}
    counts = {'requests': len(recorded), 'cache_hits': 0, 'calls': 0, 'failed': 0, 'missing_evidence': 0,
              'dry_run_built': 0, 'input_tokens': 0, 'usage_reported': 0}
    last_call = 0.0
    # Repeatability probe (plan §7.1): re-send `repeat` evenly spaced packets
    # that are already cached, once per run, never from or into the cache.
    probe = set()
    if getattr(args, 'repeat', 0):
        step = max(1, len(recorded) // args.repeat)
        probe = {item['request_id'] for item in recorded[::step][:args.repeat]}
    for item in recorded:
        payload = item['payload']
        arm, generation, cutoff = payload['arm'], payload['generation'], payload['cutoff']
        block = project_context(payload.get('context_snapshot'))
        dropped = [f for f, v in (block or {}).get('ha_facts', {}).items() if v != 'supplied']
        bound = dict(ha_bindings=granted_bindings(provider.ha_bindings, dropped), zone_bindings=provider.zone_bindings)
        ring = rings.setdefault((sid, arm, generation), wire.FrameRing())
        # The provider's own ring is this ring: the dry build and the provider
        # call see the same frames, so the packet digest is the one sent.
        provider.rings[(sid, arm, generation)] = ring
        shared = list(payload.get('shared_context') or [])
        if provider.wire in wire.ROOM_WIRES and not any(r.get('source_kind') == 'room.facts' for r in shared):
            # A session recorded under another wire: the room.facts record the
            # live scheduler would have offered at this cutoff, from the ledger.
            from room_facts import facts_context
            shared += facts_context(view, session, cutoff, generation=generation, arms=(arm,)).get(arm, [])
        try:
            local, packet, binding = wire.prepare(
                project(payload['records'], **bound), project(shared, **bound), block,
                site_id=payload['site_id'], session_id=sid, arm=arm, generation=generation, cutoff=cutoff,
                window_seconds=payload['window_seconds'], model=provider.model, storyline_seconds=provider.storyline_seconds,
                ring=ring, monitor_side=provider.monitor_side, question_version=question_version,
                wire_version=provider.wire, desk_seat=provider.desk_seat)
        except ValueError:
            record(store, job, item, question_version, None, 'local', 'request_invalid', None)
            counts['failed'] += 1
            continue
        if not window(item):
            continue  # built for the ring only: outside the requested window
        sha = digest(packet)
        with store.lock:
            cached = store.db.execute('SELECT result FROM replay_cache WHERE packet_sha256=?', (sha,)).fetchone()
        if probe and (cached is None or item['request_id'] not in probe):
            continue  # every item is still built: the frame ring sees all of them
        if cached is not None and not probe:
            counts['cache_hits'] += 1
            record(store, job, item, question_version, sha, 'cache', 'cached', json.loads(cached[0]))
            continue
        if args.dry_run:
            # Verify the egress checks without admission or transport.
            request = {'request_id': 'dry', 'site_id': payload['site_id'], 'session_id': sid, 'arm': arm,
                       'generation': generation, **{k: v for k, v in binding.items() if k not in ('packet_bytes', 'estimated_tokens')}}
            policy = gate.read()
            from jev_policy import structured_sources
            gate.verify_facts(request, packet, local, policy, structured_sources(policy))
            counts['dry_run_built'] += 1
            counts.setdefault('estimated_tokens', 0)
            counts['estimated_tokens'] += binding['estimated_tokens']
            continue
        if counts['calls'] >= args.max_calls or store.admissions() >= HARD_CALL_CAP:
            counts['stopped'] = 'call_cap'
            break
        wait = MIN_CALL_SPACING_S - (time.time() - last_call)
        if wait > 0:
            time.sleep(wait)
        request_id = 'rp%s-%s-%s' % (args.questions, sha[:16], item['request_id'][:24])
        if probe:
            request_id = '%s-r%d' % (request_id[:100], int(time.time()))
        now = store.clock()
        with store.transaction() as db:
            db.execute('INSERT INTO lab_reasoning_requests(id,session_id,arm,generation,state,payload,dispatched_at) '
                       'VALUES(?,?,?,?,?,?,?)', (request_id, sid, arm, generation, 'dispatching',
                                                 canonical({'recorded_request_id': item['request_id'], 'cutoff': cutoff,
                                                            'packet_sha256': sha, 'job': job}), now))
        last_call = time.time()
        counts['calls'] += 1
        state = 'completed'
        try:
            result = provider.evaluate(payload['records'], request_id=request_id, site_id=payload['site_id'], session_id=sid,
                                       arm=arm, generation=generation, cutoff=cutoff, deadline=store.clock() + 12,
                                       window_seconds=payload['window_seconds'], shared_context=payload.get('shared_context'),
                                       context_snapshot=payload.get('context_snapshot'))
        except ProviderFailure as failure:
            state = 'indeterminate' if failure.indeterminate else 'failed'
            result = {'status': state, 'reason': failure.reason, 'http_status': failure.http_status}
        sent = ((result.get('diagnostics') or {}).get('request'))
        if sent is not None and digest(sent) != sha:
            result = {**result, 'replay_warning': 'packet_differs_from_dry_build'}
        with store.transaction() as db:
            db.execute('UPDATE lab_reasoning_requests SET state=?, result=? WHERE id=?', (state, canonical(result), request_id))
        usage = (result.get('diagnostics') or {}).get('usage') or {}
        if 'input_tokens' in usage:
            counts['input_tokens'] += usage['input_tokens']
            counts['usage_reported'] += 1
        if probe:
            pass
        elif state == 'completed' and result.get('status') in ('ready', 'abstain'):
            with store.transaction() as db:
                db.execute('INSERT OR IGNORE INTO replay_cache VALUES(?,?,?,?,?)',
                           (sha, question_version, request_id, store.clock(), canonical(result)))
        elif result.get('status') == 'missing_evidence':
            counts['missing_evidence'] += 1
        else:
            counts['failed'] += 1
        record(store, job, item, question_version, sha, 'repeat' if probe else 'call', result.get('status') or state, result)
        if state == 'indeterminate':
            counts['stopped'] = 'indeterminate_execution'
            break
    return {'job': job, 'session': sid[:8], 'question_version': question_version, 'wire_version': provider.wire,
            'arms': sorted(arms),
            'admissions_in_replay_db': store.admissions(), **counts}


def record(store, job, item, question_version, sha, source, status, result):
    from reasoning import canonical
    with store.transaction() as db:
        db.execute('INSERT OR REPLACE INTO replay_results VALUES(?,?,?,?,?,?,?,?,?)',
                   (job, item['request_id'], question_version, item['arm'], item['payload']['cutoff'], sha, source, status,
                    canonical(result) if result is not None else None))


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--ledger', required=True)
    parser.add_argument('--replay-db', required=True)
    parser.add_argument('--config', required=True)
    parser.add_argument('--session', required=True)
    from jev_wire import QUESTION_CHOICES, WIRE_VERSIONS
    parser.add_argument('--questions', choices=sorted(QUESTION_CHOICES), required=True)
    parser.add_argument('--wire', choices=WIRE_VERSIONS, default=None, help="default: the JEV config's wire")
    parser.add_argument('--desk-seat', default=None, help="private JSON desk_seat region; default: the config's")
    parser.add_argument('--from-s', type=float, default=None, help='only call cutoffs at or after this session time')
    parser.add_argument('--to-s', type=float, default=None, help='only call cutoffs at or before this session time')
    parser.add_argument('--arms', default='a,b')
    parser.add_argument('--max-calls', type=int, default=120)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--repeat', type=int, default=0, help='re-send this many cached packets once (repeatability probe)')
    args = parser.parse_args(argv)
    if not 0 <= args.max_calls <= HARD_CALL_CAP:
        raise SystemExit('max-calls must be 0..%d' % HARD_CALL_CAP)
    if Path(args.replay_db).resolve() == Path(args.ledger).resolve():
        raise SystemExit('the replay database must be separate from the ledger')
    print(json.dumps(run(args), sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
