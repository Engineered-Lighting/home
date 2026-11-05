"""Observations to tracks to an E2 score, for one take or a session of takes.

A session is a folder of take folders. Each take folder holds:

    observations.jsonl   rung 2's `observation` records for the take
    truth.jsonl          the owner's den-track-truth/v1 annotation
    tracks.jsonl         written here: `track` records
    tracks.run.json      written here: which method and input made them

Nothing here opens a network connection, a camera or a light.
"""
import json
import os

import records
import scorer_core
import synth
import tracker_core
import truth as truth_mod

OBSERVATIONS, TRUTH, TRACKS, RUN = 'observations.jsonl', 'truth.jsonl', 'tracks.jsonl', 'tracks.run.json'


class RunError(Exception):
    pass


def make_tracker(values):
    room = values['room']
    floor = room.get('floor')
    return tracker_core.TrackerCore(values['tracker'], exits=room.get('exits') or [],
                                    floor=tuple(floor) if floor else None,
                                    id_prefix=values['record']['track_id_prefix'])


def with_ticks(frames, window=20):
    """Insert empty frames into gaps, so time passes for the tracker when
    nobody is seen.

    An observation file has no empty frames: a frame where the camera saw
    nobody leaves no line. Without ticks a hidden person's track would not
    coast and a false one would not expire until someone was seen again. The
    tick spacing is the median of the last `window` gaps between frames, a
    causal estimate of the frame interval; a gap longer than 1.5 of it gets
    empty frames at that spacing. Nothing after a tick is used to make it."""
    out, gaps, prev = [], [], None
    for t, sensor, ms in frames:
        if prev is not None:
            gap = t - prev
            if len(gaps) >= 3:
                step = sorted(gaps)[len(gaps) // 2]
                if step > 0 and gap > 1.5 * step:
                    k = 1
                    while prev + (k + 0.5) * step < t:
                        out.append((round(prev + k * step, 4), sensor, []))
                        k += 1
            if 0 < gap:
                gaps.append(gap)
                del gaps[:-window]
        out.append((t, sensor, ms))
        prev = t
    return out


def track_frames(frames, values):
    """[(capture_ts, sensor_id, [measurement])] -> [track record]."""
    tracker = make_tracker(values)
    time_field = values['record']['time_field']
    out = []
    for t, _sensor, ms in with_ticks(frames):
        live, ended = tracker.step(t, ms)
        for tr in sorted(ended + live, key=lambda tr: int(tr.id[len(tracker.id_prefix):])):
            out.append(records.track_record(tracker, tr, t, time_field))
    return out


def track_take(take_dir, method):
    """Track one take folder; writes tracks.jsonl and tracks.run.json."""
    obs_path = os.path.join(take_dir, OBSERVATIONS)
    if not os.path.isfile(obs_path):
        raise RunError('%s has no %s' % (take_dir, OBSERVATIONS))
    frames, counts = records.read_observations(obs_path, method.values['input'])
    recs = track_frames(frames, method.values)
    records.write_jsonl(os.path.join(take_dir, TRACKS), recs)
    run = {
        'tool': 'den-track',
        'method_version': method.version,
        'method_fingerprint': method.fingerprint,
        'open_slots': [name for name, _ in method.open],
        'observations_sha256': records.file_sha256(obs_path),
        'observation_counts': counts,
        'frames': len(frames),
        'track_records': len(recs),
        'tracks': len({r['track_id'] for r in recs}),
    }
    with open(os.path.join(take_dir, RUN), 'w', encoding='utf-8', newline='\n') as f:
        json.dump(run, f, indent=2, sort_keys=True)
        f.write('\n')
    return run


def take_dirs(session_dir):
    if not os.path.isdir(session_dir):
        raise RunError('%s is not a folder' % session_dir)
    out = sorted(os.path.join(session_dir, d) for d in os.listdir(session_dir)
                 if os.path.isfile(os.path.join(session_dir, d, OBSERVATIONS)))
    if not out:
        raise RunError('%s holds no take folders (each with %s)' % (session_dir, OBSERVATIONS))
    return out


def links_for_take(track_recs, truth, scored_states):
    """E2 links: every labelled observation with the track that took it."""
    took = {}
    for rec in track_recs:
        if rec['state'] not in scored_states:
            continue
        confident = rec['association']['status'] == 'confident'
        for obs_id in rec['last_obs_ids']:
            if obs_id in took:
                raise RunError('observation %s is on two track records (%s, %s)'
                               % (obs_id, took[obs_id][0], rec['track_id']))
            took[obs_id] = (rec['track_id'], confident)
    links = []
    for obs_id, (ts, person) in sorted(truth['labels'].items(), key=lambda kv: (kv[1][0], kv[0])):
        track, confident = took.get(obs_id, (None, None))
        links.append((ts, obs_id, person, track, confident))
    unlabelled = sum(1 for o in took if o not in truth['labels'])
    return links, unlabelled


def score_takes(takes, values):
    """takes: [(take_id, track records, truth)] -> the pooled E2 report."""
    sc = values['scoring']
    per_take, notes = [], []
    for take_id, recs, truth in takes:
        links, unlabelled = links_for_take(recs, truth, sc['scored_states'])
        res = scorer_core.score_take(links, truth['windows'])
        per_take.append((take_id, res))
        notes.append({'take': take_id, 'labelled': len(links), 'unsure': truth['unsure'],
                      'linked_but_unlabelled': unlabelled, 'windows': len(truth['windows']),
                      'ambiguous_share': res['ambiguous_share'],
                      'switches_outside_windows': res['switches_outside_windows']})
    report = scorer_core.pool(per_take, sc['idf1_gate'], sc['min_crossings'], sc['alpha'])
    report['takes'] = notes
    return report


def score_session(session_dir, method, practice):
    """Score every take folder; refuses a scored run unless the method is closed
    and every take was tracked under this exact method and its observations."""
    if not practice:
        method.require_closed()
    takes, problems = [], []
    for d in take_dirs(session_dir):
        take_id = os.path.basename(d)
        for name in (TRUTH, TRACKS, RUN):
            if not os.path.isfile(os.path.join(d, name)):
                raise RunError('%s has no %s' % (d, name))
        with open(os.path.join(d, RUN), encoding='utf-8-sig') as f:
            run = json.load(f)
        if run.get('method_fingerprint') != method.fingerprint:
            problems.append('%s was tracked under method %s, not the current %s'
                            % (take_id, run.get('method_fingerprint'), method.fingerprint))
        if run.get('open_slots'):
            problems.append('%s was tracked while slots were open: %s' % (take_id, ', '.join(run['open_slots'])))
        obs_hash = records.file_sha256(os.path.join(d, OBSERVATIONS))
        if run.get('observations_sha256') != obs_hash:
            problems.append('%s: observations.jsonl changed after it was tracked' % take_id)
        truth = truth_mod.read(os.path.join(d, TRUTH))
        want = truth['take'].get('observations_sha256')
        if want and want != obs_hash:
            problems.append('%s: truth.jsonl labels a different observations file' % take_id)
        if not practice and not want:
            problems.append('%s: truth.jsonl does not name its observations_sha256' % take_id)
        takes.append((take_id, records.read_tracks(os.path.join(d, TRACKS)), truth))
    if problems and not practice:
        raise RunError('refusing a scored run:\n  ' + '\n  '.join(problems))
    report = score_takes(takes, method.values)
    report.update({'practice': practice, 'method_version': method.version,
                   'method_fingerprint': method.fingerprint,
                   'open_slots': [name for name, _ in method.open], 'problems': problems})
    if practice:
        report['note'] = 'PRACTICE: not a score. Open slots use their proposed values.'
    return report


# -- synthetic ------------------------------------------------------------------

def write_synthetic_take(take_dir, scene):
    """Write a synthetic take folder (observations and truth) for one scene."""
    os.makedirs(take_dir, exist_ok=True)
    obs, lines = synth.records(scene)
    obs_path = os.path.join(take_dir, OBSERVATIONS)
    records.write_jsonl(obs_path, obs)
    lines[0]['observations_sha256'] = records.file_sha256(obs_path)
    records.write_jsonl(os.path.join(take_dir, TRUTH), lines)


def synthetic_take(scene, values):
    """(take_id, track records, truth) for one scene, in memory."""
    obs, lines = synth.records(scene)
    frames = observation_frames(obs, values['input'])
    recs = track_frames(frames, values)
    return lines[0]['take_id'], recs, truth_from_lines(lines)


def observation_frames(obs, cfg):
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, OBSERVATIONS)
        records.write_jsonl(p, obs)
        frames, _ = records.read_observations(p, cfg)
    return frames


def truth_from_lines(lines):
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, TRUTH)
        records.write_jsonl(p, lines)
        return truth_mod.read(p)


def practice(values, scenes=None, seeds=5, noise='typical', first_seed=0, fps=4.0):
    """Synthetic E2 on every scene and seed: the tuning harness. `values`
    should carry the synthetic den's room (synth.ROOM_MODEL)."""
    scenes = scenes or sorted(synth.SCENES)
    for name in scenes:
        if name not in synth.SCENES:
            raise RunError('no synthetic scene %r (have: %s)' % (name, ', '.join(sorted(synth.SCENES))))
    takes = []
    for name in scenes:
        for seed in range(first_seed, first_seed + seeds):
            takes.append(synthetic_take(synth.scenario(name, seed, noise, fps=fps), values))
    report = score_takes(takes, values)
    by_scene = {}
    for r in report['windows']:
        k = by_scene.setdefault(r['take'].rsplit('-', 2)[0], {'windows': 0, 'failed': 0, 'silent': 0, 'reported': 0,
                                                              'idtp': 0, 'idfp': 0, 'idfn': 0, 'amb': 0.0})
        k['windows'] += 1
        k['failed'] += 1 if r['silent_swaps'] else 0
        k['silent'] += r['silent_swaps']
        k['reported'] += r['reported_switches']
        for c in ('idtp', 'idfp', 'idfn'):
            k[c] += r[c]
        k['amb'] += (r['ambiguous_share'] or 0.0) * (r['idtp'] + r['idfp'])
    for k in by_scene.values():
        d = 2 * k['idtp'] + k['idfp'] + k['idfn']
        k['idf1'] = (2 * k['idtp'] / d) if d else None
        n = k['idtp'] + k['idfp']
        k['ambiguous_share'] = k.pop('amb') / n if n else None
    report['by_scene'] = by_scene
    report['practice'] = True
    report['problems'] = []
    report['note'] = 'PRACTICE on synthetic scenes: not a score.'
    return report
