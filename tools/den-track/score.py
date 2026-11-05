"""Binds `observation` lines, `track` lines and ground truth to the E2 scorer."""
import records
import scorer_core

FACE_JOINTS = (0, 1, 2)     # coco17: nose and both eyes
RUNG5_CROSSING_TYPES = ('crossing', 'crossing_occluded', 'near_pass')   # two people in contact


class ScoreError(Exception):
    pass


def _listed(track_lines, obs_ids):
    """obs_id -> (track_id, confident), from each track line's last_obs_ids."""
    out = {}
    for line in track_lines:
        tid = line.get('track_id')
        confident = (line.get('association') or {}).get('status') == 'confident'
        for oid in line.get('last_obs_ids') or []:
            if oid not in obs_ids:
                raise ScoreError('track %s lists observation %s, which is not in the observations' % (tid, oid))
            if oid in out and out[oid][0] != tid:
                raise ScoreError('observation %s is on both %s and %s' % (oid, out[oid][0], tid))
            out[oid] = (tid, confident)
    return out


def face_visible(obs, score_min):
    """A stand-in for "a face is visible": nose and both eyes above score_min."""
    points = obs.get('keypoints')
    if not isinstance(points, list) or len(points) <= max(FACE_JOINTS):
        return False
    for j in FACE_JOINTS:
        score = points[j][2] if isinstance(points[j], list) and len(points[j]) > 2 else None
        if not records.is_number(score) or score < score_min:
            return False
    return True


def take_links(observations, track_lines, truth):
    """Links for one take, plus what was left out and why.

    A rejected Observation cannot be tracked, and one with no position cannot
    unless it joined its partner, so neither is scored. An unlabelled one is left
    out and counted; a scored run refuses any.
    """
    by_id = {o['obs_id']: o for o in observations}
    listed = _listed(track_lines, by_id)
    for oid, ts in truth.frame_ts.items():
        if oid not in by_id:
            raise ScoreError('truth labels observation %s, which is not in the observations' % oid)
        if by_id[oid]['capture_ts'] != ts:
            raise ScoreError('truth puts observation %s in the frame at %s, it was captured at %s'
                             % (oid, ts, by_id[oid]['capture_ts']))
    links, left_out = [], {'rejected': 0, 'no_position': 0, 'unlabelled': 0}
    for obs in observations:
        if obs.get('kind') not in records.PERSON_KINDS:
            continue
        oid = obs['obs_id']
        if not records.is_valid(obs):
            left_out['rejected'] += 1
        elif oid not in listed and records.floor_measurement(obs) is None:
            left_out['no_position'] += 1
        elif oid not in truth.labels:
            left_out['unlabelled'] += 1
        else:
            track, confident = listed.get(oid, (None, False))
            links.append((obs['capture_ts'], oid, truth.labels[oid], track, confident))
    return links, left_out


def windows_of(truth, pad_s):
    return [{'id': c['crossing_id'], 'kind': c['type'],
             'start': c['interval'][0] - pad_s, 'end': c['interval'][1] + pad_s} for c in truth.crossings]


def face_share(observations, truth, windows, score_min):
    """Per window: of the labelled keypoints Observations of a person, the share with a face."""
    shares = {}
    for w in windows:
        seen = shown = 0
        for obs in observations:
            if (obs.get('kind') == 'keypoints' and truth.labels.get(obs['obs_id']) is not None
                    and w['start'] <= obs['capture_ts'] <= w['end']):
                seen += 1
                shown += face_visible(obs, score_min)
        shares[w['id']] = (shown / seen) if seen else None
    return shares


def contact_ambiguous_share(links, interval):
    """Of the tracked links inside the owner's mark of the contact, the share reported ambiguous."""
    tracked = [l for l in links if interval[0] <= l[0] <= interval[1] and l[3] is not None]
    return (sum(1 for l in tracked if not l[4]) / len(tracked)) if tracked else None


def score_session(takes, scoring):
    """takes: [{'name', 'observations', 'tracks', 'truth'}]. Returns the full report body."""
    results, left_out = [], {'rejected': 0, 'no_position': 0, 'unlabelled': 0}
    crossing_lines = []
    for take in takes:
        links, skipped = take_links(take['observations'], take['tracks'], take['truth'])
        for k in left_out:
            left_out[k] += skipped[k]
        windows = windows_of(take['truth'], scoring['window_pad_s'])
        result = scorer_core.score_take(links, windows)
        faces = face_share(take['observations'], take['truth'], windows, scoring['face_visible_score_min'])
        for row, crossing in zip(result['windows'], take['truth'].crossings):
            row['take'] = take['name']
            row['interval'] = crossing['interval']
            row['contact_ambiguous_share'] = contact_ambiguous_share(links, crossing['interval'])
            row['face_visible_share'] = faces[row['id']]
            if crossing['type'] in RUNG5_CROSSING_TYPES:
                crossing_lines.append({'crossing_id': '%s/%s' % (take['name'], row['id']),
                                       'track_ids': sorted(set(row['mapping'].values())),
                                       'interval': crossing['interval']})
        results.append(result)
    summary = scorer_core.summarize(results, scoring['idf1_gate'], scoring['alpha'])
    rows = [row for result in results for row in result['windows']]
    faces = [r['face_visible_share'] for r in rows if r['face_visible_share'] is not None]
    summary['face_visible_share_mean'] = (sum(faces) / len(faces)) if faces else None
    summary['left_out'] = left_out
    summary['windows'] = rows
    summary['crossing_lines'] = crossing_lines
    return summary
