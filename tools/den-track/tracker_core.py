"""The geometry-only tracker, on plain inputs.

Each step takes one frame: its time and a list of observations, each with an
id, a floor position (x, y) in the room frame and a 2x2 covariance. It returns
the tracks after that frame. It never looks ahead (causal) and has no
randomness: the same frames give the same tracks, every run.

Per frame:
1. predict every track to the frame time (constant velocity; a track that is
   not being seen slows down instead of sailing on);
2. gate each track-observation pair on the Mahalanobis distance;
3. weigh every joint hypothesis (associate.py), take the best one, and get
   each track's marginal: how much of the evidence agrees with its link;
4. update linked tracks; tracks with no link coast and end after a while;
5. start a tentative track from each free observation; it is confirmed after
   enough links.

Confidence is the probability that the track is still on the person it
started on. It starts at 1 and is multiplied, at every link, by one minus the
link's identity risk: the weight of hypotheses where the observation belongs
to another track, or (after the track has gone unseen) to a newcomer. Nothing
in geometry can show that a mix-up did not happen, so confidence never rises
again. A track that two people may have swapped carries that doubt for the
rest of its life, and a swap made under it is not silent.
"""
import math

import associate
import kalman


class Track:
    __slots__ = ('id', 'x', 'P', 'born', 'last_seen', 'hits', 'misses', 'confirmed', 'confidence',
                 'last_obs_ids', 'heading_deg', 'heading_as_of', 'coast_frames', 'history')

    def __init__(self, tid, x, P, t, obs_id):
        self.id, self.x, self.P = tid, x, P
        self.born = self.last_seen = t
        self.hits, self.misses, self.coast_frames = 1, 0, 0
        self.confirmed = False
        self.confidence = 1.0
        self.last_obs_ids = [obs_id]
        self.heading_deg = None
        self.heading_as_of = None
        self.history = [True]


class TrackerCore:
    def __init__(self, params, exits=(), floor=None):
        self.p = params
        self.exits = [tuple(e) for e in exits]
        self.floor = floor  # (x0, y0, x1, y1) or None
        self.tracks = []
        self.ended = []
        self.next_id = 1
        self.t = None

    # -- density of a new person or a false observation at a floor point ----
    def free_density(self, z):
        p = self.p
        d = p['clutter_density'] + p['birth_density']
        s = p['exit_radius_m']
        for ex in self.exits:
            d2 = (z[0] - ex[0]) ** 2 + (z[1] - ex[1]) ** 2
            d += p['exit_birth_weight'] * math.exp(-0.5 * d2 / (s * s)) / (2 * math.pi * s * s)
        return d

    def near_exit(self, xy):
        r = self.p['exit_radius_m']
        return any(math.dist(xy, ex) <= 2 * r for ex in self.exits)

    def off_floor(self, xy):
        if self.floor is None:
            return False
        x0, y0, x1, y1 = self.floor
        m = self.p['floor_margin_m']
        return not (x0 - m <= xy[0] <= x1 + m and y0 - m <= xy[1] <= y1 + m)

    # -- one frame -------------------------------------------------------------
    def step(self, t, observations):
        """observations: [{'id', 'xy', 'R'}]. Returns (live tracks, ended)."""
        p = self.p
        if self.t is not None and t < self.t:
            raise ValueError('frames must arrive in time order (%.3f after %.3f)' % (t, self.t))
        dt = 0.0 if self.t is None else t - self.t
        self.t = t
        obs = sorted(observations, key=lambda o: o['id'])
        for tr in self.tracks:
            unseen = t - tr.last_seen
            keep = math.exp(-dt / p['coast_velocity_tau_s']) if unseen > dt + 1e-9 else 1.0
            tr.x, tr.P = kalman.predict(tr.x, tr.P, dt, p['accel_sigma'], keep)
        link, gated = [], []
        for tr in self.tracks:
            row = []
            for o in obs:
                nu, S, S_inv, det = kalman.innovation(tr.x, tr.P, o['xy'], o['R'])
                d2 = kalman.mahalanobis2(nu, S_inv)
                row.append(p['p_detect'] * kalman.gaussian2(d2, det) if d2 <= p['gate_chi2'] else 0.0)
            link.append(row)
        miss = [1.0 - p['p_detect'] for _ in self.tracks]
        free = [self.free_density(o['xy']) for o in obs]
        coasting = [tr.coast_frames > 0 for tr in self.tracks]
        choice, marginal, risk = associate.associate(link, miss, free, coasting, p['max_hypotheses'])
        for i, tr in enumerate(self.tracks):
            if risk[i] >= p['risk_floor']:
                tr.confidence *= (1.0 - risk[i])
        taken = set()
        for i, tr in enumerate(self.tracks):
            j = choice[i]
            if j is None:
                tr.misses += 1
                tr.coast_frames += 1
                tr.history.append(False)
                continue
            o = obs[j]
            taken.add(j)
            R = o['R']
            nu, S, S_inv, _ = kalman.innovation(tr.x, tr.P, o['xy'], R)
            d2 = kalman.mahalanobis2(nu, S_inv)
            if d2 > p['robust_d2']:
                # Probably a depth outlier: let it pull the track less.
                R = [[v * d2 / p['robust_d2'] for v in row] for row in R]
            tr.x, tr.P = kalman.update(tr.x, tr.P, o['xy'], R)
            tr.last_seen = t
            tr.hits += 1
            tr.coast_frames = 0
            tr.last_obs_ids = [o['id']]
            tr.history.append(True)
            speed = math.hypot(tr.x[2], tr.x[3])
            if speed >= p['heading_min_speed']:
                tr.heading_deg = math.degrees(math.atan2(tr.x[3], tr.x[2])) % 360.0
                tr.heading_as_of = t
        window = p['confirm_window']
        for tr in self.tracks:
            del tr.history[:-window]
            if not tr.confirmed and sum(tr.history) >= p['confirm_hits']:
                tr.confirmed = True
        keep_tracks, ended = [], []
        for tr in self.tracks:
            unseen = t - tr.last_seen
            if not tr.confirmed:
                gone = len(tr.history) >= window and sum(tr.history) < p['confirm_hits']
            elif self.off_floor(tr.x[:2]) or (self.near_exit(tr.x[:2]) and unseen >= p['exit_coast_s']):
                gone = unseen > 0
            else:
                gone = unseen > p['max_coast_s']
            (ended if gone else keep_tracks).append(tr)
        self.tracks = keep_tracks
        for j, o in enumerate(obs):
            if j in taken:
                continue
            x, P = kalman.initial(o['xy'], o['R'], p['new_track_speed_sigma'])
            tr = Track(self.next_id, x, P, t, o['id'])
            self.next_id += 1
            self.tracks.append(tr)
        self.ended.extend(tr for tr in ended if tr.confirmed)
        return [tr for tr in self.tracks if tr.confirmed], [tr for tr in ended if tr.confirmed]
