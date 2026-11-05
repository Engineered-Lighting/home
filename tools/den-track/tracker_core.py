"""The geometry-only tracker, on plain inputs.

Each step takes one frame: its time and a list of detections, each with an id,
a floor position (x, y) in the room frame, a 2x2 covariance, the obs_ids behind
it and any body-facing evidence. It returns every track's state after that
frame. It never looks ahead (causal) and has no randomness: the same frames
give the same tracks, every run.

Per frame:
1. predict every track to the frame time (constant velocity; a track that is
   not being seen slows down instead of sailing on);
2. gate each pair on the Mahalanobis distance and weigh every joint hypothesis
   (associate.py) for the confirmed tracks; tentative tracks then get what is
   left, so a phantom never casts doubt on a person;
3. update linked tracks; tracks with no link coast and end after a while;
4. start a tentative track from each free detection; it is confirmed after
   enough links.

Confidence is the probability that the track is still on one person. It starts
at 1 and is multiplied, at every link, by one minus the link's identity risk,
and the lost share is kept as named rivals (swapped with whom, or a newcomer).

The record reports it through `association`. Status is `ambiguous` while
confidence is below `ambiguous_below` or another confirmed track is within reach
(`crossing_radius_m`, wider while either has been unseen), and stays so until the
track has been seen with no doubt for `reconfirm_s`. A new track starts that way,
since its first sightings can be two people and it has no history to say otherwise,
and the line that confirms it claims nothing. Nothing in geometry can show that a
mix-up did not happen, so the return to `confident` does not claim that it did not:
it restarts the claim, from `since_ts`. The track is one person from then on, and
whether that is the person from before the ambiguity is not stated.
"""
import math

import associate
import kalman


class Track:
    __slots__ = ('id', 'x', 'P', 'last_seen', 'confirmed', 'confidence', 'rivals',
                 'ambiguous', 'since_ts', 'last_doubt', 'near', 'heading', 'heading_as_of', 'history', 'obs_ids')

    def __init__(self, tid, x, P, t, obs_ids):
        self.id, self.x, self.P = tid, x, P
        self.last_seen = t
        self.confirmed = False
        self.confidence = 1.0
        self.rivals = {}            # other track's id, or None for a newcomer -> probability
        self.ambiguous = True       # a new track has no history to be sure of
        self.since_ts = t
        self.last_doubt = t         # the last frame with any doubt about this track
        self.near = []              # ids of confirmed tracks within crossing_radius_m this frame
        self.heading = None         # (deg, sd_deg)
        self.heading_as_of = None
        self.history = [True]
        self.obs_ids = list(obs_ids)

    def state(self, t, coasting_after_s):
        if not self.confirmed:
            return 'tentative'
        return 'coasting' if t - self.last_seen >= coasting_after_s else 'confirmed'


class TrackerCore:
    def __init__(self, params, exits=(), floor=None):
        self.p = params
        self.exits = [tuple(e) for e in exits]
        self.floor = floor  # (x0, y0, x1, y1) or None
        self.tracks = []
        self.next_id = 1
        self.t = None

    # -- density of a new person or a false detection at a floor point --------
    def free_density(self, z):
        p = self.p
        d = p['clutter_density'] + p['birth_density']
        s = p['exit_radius_m']
        for ex in self.exits:
            d2 = (z[0] - ex[0]) ** 2 + (z[1] - ex[1]) ** 2
            d += p['exit_birth_weight'] * math.exp(-0.5 * d2 / (s * s)) / (2 * math.pi * s * s)
        return d

    def near_exit(self, xy):
        return any(math.dist(xy, ex) <= self.p['exit_radius_m'] for ex in self.exits)

    def _within_reach(self, a, b, t):
        """Close enough to be a crossing: within the radius, wider the longer either has been unseen."""
        p = self.p
        unseen = max(t - a.last_seen, t - b.last_seen)
        reach = p['crossing_radius_m']
        if unseen > p['unseen_after_s']:
            reach += p['hidden_reach_mps'] * unseen
        return math.dist(a.x[:2], b.x[:2]) <= reach

    def _stay_on_floor(self, tr):
        """A person leaves by a door, not through a wall: an unseen track stops at the floor's edge."""
        if self.floor is None:
            return
        m = self.p['floor_margin_m']
        x0, y0, x1, y1 = self.floor
        for axis, lo, hi in ((0, x0 - m, x1 + m), (1, y0 - m, y1 + m)):
            if tr.x[axis] < lo or tr.x[axis] > hi:
                tr.x[axis] = min(max(tr.x[axis], lo), hi)
                tr.x[axis + 2] = 0.0

    # -- association ------------------------------------------------------------
    def _associate(self, group, dets, free_idx, t):
        """(chosen detection index or None, rivals by track id) for each track in group."""
        p = self.p
        # A frame with nobody in it writes no Observation, so "unseen" is a time, not a count
        # of frames. A person out of sight for a while is less likely to be seen right now.
        coasting = [t - tr.last_seen > p['unseen_after_s'] for tr in group]
        detect = [p['p_detect_unseen'] if unseen else p['p_detect'] for unseen in coasting]
        link = []
        for tr, p_d in zip(group, detect):
            row = []
            for j in free_idx:
                nu, _, S_inv, det = kalman.innovation(tr.x, tr.P, dets[j]['xy'], dets[j]['R'])
                d2 = kalman.mahalanobis2(nu, S_inv)
                row.append(p_d * kalman.gaussian2(d2, det) if d2 <= p['gate_chi2'] else 0.0)
            link.append(row)
        miss = [1.0 - p_d for p_d in detect]
        free = [self.free_density(dets[j]['xy']) for j in free_idx]
        choice, rivals = associate.associate(link, miss, free, coasting, p['max_hypotheses'])
        out = []
        for i, tr in enumerate(group):
            named = {(None if who is None else group[who].id): share for who, share in rivals[i].items()}
            out.append((None if choice[i] is None else free_idx[choice[i]], named))
        return out

    def _take(self, tr, det, t):
        p = self.p
        R = det['R']
        nu, _, S_inv, _ = kalman.innovation(tr.x, tr.P, det['xy'], R)
        d2 = kalman.mahalanobis2(nu, S_inv)
        if d2 > p['robust_d2']:
            # Probably a depth outlier: let it pull the track less.
            R = [[v * d2 / p['robust_d2'] for v in row] for row in R]
        tr.x, tr.P = kalman.update(tr.x, tr.P, det['xy'], R)
        tr.last_seen = t
        tr.obs_ids = list(det['obs_ids'])
        tr.history.append(True)

    def _doubt(self, tr, named, t):
        """Move the identity risk of this frame from `confidence` to named rivals."""
        risk = sum(named.values())
        if risk < self.p['risk_floor']:
            return
        lost = tr.confidence * risk
        tr.confidence -= lost
        for who, share in named.items():
            tr.rivals[who] = tr.rivals.get(who, 0.0) + lost * share / risk
        tr.last_doubt = t

    def _status(self, tr, t, seen):
        p = self.p
        quiet = tr.last_doubt is None or t - tr.last_doubt >= p['reconfirm_s']
        if seen and quiet and tr.confirmed and not tr.near and (tr.ambiguous or tr.confidence < 1.0):
            tr.confidence, tr.rivals, tr.last_doubt = 1.0, {}, None
            if tr.ambiguous:
                tr.ambiguous, tr.since_ts = False, t
        elif (tr.confidence < p['ambiguous_below'] or tr.near) and not tr.ambiguous:
            tr.ambiguous, tr.since_ts = True, t

    def _accept_heading(self, tr, det, t):
        """heading_as_of moves only here: on shoulder and hip evidence this track is sure of."""
        evidence = det.get('heading')
        if evidence is not None and not tr.ambiguous and evidence[1] <= self.p['heading_max_sd_deg']:
            tr.heading, tr.heading_as_of = evidence, t

    # -- one frame -------------------------------------------------------------
    def step(self, t, detections):
        """detections: [{'id', 'xy', 'R', 'obs_ids', 'heading'}].

        Returns [(track, state)] for this frame, by track number: every live
        track, and each track that ended here with state 'ended'.
        """
        p = self.p
        if self.t is not None and t < self.t:
            raise ValueError('frames must arrive in time order (%.3f after %.3f)' % (t, self.t))
        dt = 0.0 if self.t is None else t - self.t
        self.t = t
        dets = sorted(detections, key=lambda d: d['id'])
        for tr in self.tracks:
            unseen = t - tr.last_seen
            keep = math.exp(-dt / p['coast_velocity_tau_s']) if unseen > dt + 1e-9 else 1.0
            tr.x, tr.P = kalman.predict(tr.x, tr.P, dt, p['accel_sigma'], keep)
            tr.obs_ids = []
            if unseen > p['unseen_after_s']:
                self._stay_on_floor(tr)

        # A track whose time ran out while no frame came ends before it can take anyone.
        ended = [tr for tr in self.tracks if self._timed_out(tr, t)]
        self.tracks = [tr for tr in self.tracks if tr not in ended]

        confirmed = [tr for tr in self.tracks if tr.confirmed]
        tentative = [tr for tr in self.tracks if not tr.confirmed]
        free_idx = list(range(len(dets)))
        took = {}
        for tr, (j, named) in zip(confirmed, self._associate(confirmed, dets, free_idx, t)):
            self._doubt(tr, named, t)
            took[tr.id] = j
        free_idx = [j for j in free_idx if j not in took.values()]
        for tr, (j, _) in zip(tentative, self._associate(tentative, dets, free_idx, t)):
            took[tr.id] = j
        for tr in self.tracks:
            j = took[tr.id]
            if j is None:
                tr.history.append(False)
            else:
                self._take(tr, dets[j], t)

        for tr in self.tracks:      # a tentative track doubts itself beside a person; it never casts doubt on one
            tr.near = [o.id for o in confirmed if o is not tr and self._within_reach(tr, o, t)]
            if tr.near:
                tr.last_doubt = t
        for tr in self.tracks:
            j = took[tr.id]
            self._status(tr, t, j is not None)
            if j is not None:
                self._accept_heading(tr, dets[j], t)

        window = p['confirm_window']
        keep_tracks = []
        for tr in self.tracks:
            del tr.history[:-window]
            if not tr.confirmed and sum(tr.history) >= p['confirm_hits']:
                tr.confirmed = True
            (ended if self._gone(tr) else keep_tracks).append(tr)
        self.tracks = keep_tracks
        taken = {j for j in took.values() if j is not None}
        for j, det in enumerate(dets):
            if j not in taken:
                x, P = kalman.initial(det['xy'], det['R'], p['new_track_speed_sigma'])
                born = Track(self.next_id, x, P, t, det['obs_ids'])
                born.near = [o.id for o in confirmed if self._within_reach(born, o, t)]
                self._accept_heading(born, det, t)
                self.tracks.append(born)
                self.next_id += 1
        lines = [(tr, tr.state(t, p['coasting_after_s'])) for tr in self.tracks] + [(tr, 'ended') for tr in ended]
        lines.sort(key=lambda line: line[0].id)
        return lines

    def _timed_out(self, tr, t):
        p = self.p
        unseen = t - tr.last_seen
        if not tr.confirmed:
            return unseen > p['tentative_max_gap_s']
        if unseen >= p['exit_coast_s'] and self.near_exit(tr.x[:2]):
            return True
        return unseen > p['max_coast_s']

    def _gone(self, tr):
        """After this frame's links: a tentative track that fell short."""
        p = self.p
        return (not tr.confirmed and len(tr.history) >= p['confirm_window']
                and sum(tr.history) < p['confirm_hits'])
