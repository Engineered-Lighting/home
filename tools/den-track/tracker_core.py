"""The geometry-only tracker, on plain inputs.

Each step takes one frame: its capture time and a list of measurements, each
with the ids of the observations it was made from, a floor position (x, y) in
the room frame, its 2x2 covariance and, when the shoulders or hips gave one,
a body heading. It returns every track after that frame. It never looks
ahead (causal) and has no randomness: the same frames give the same tracks,
every run.

Per frame:
1. predict every track to the frame time, twice: walking on at constant
   velocity, and stopped (a track that is not being seen slows down instead
   of sailing on; with a known floor, no prediction leaves it);
2. gate each track-measurement pair on the Mahalanobis distance;
3. weigh every joint hypothesis (associate.py), take the best one, and get
   each track's identity risk: the weight of hypotheses in which its
   measurement belongs to another track, a newcomer or a lost person, plus
   the chance that an observation merged into its measurement is another
   track's;
4. update linked tracks; tracks with no link coast and end after a while;
5. start a tentative track from each free measurement; it is confirmed after
   enough links.

Ambiguity comes in intervals. `p_keep` is the probability that the track is
still on the person it was on when the current interval opened; it starts at
1 and every frame's risk (at or above `risk_floor`) moves weight from it to
the named alternatives (another track, or a newcomer). Below `confident_at`
the track reports `ambiguous` with those hypotheses. Geometry cannot undo a
mix-up, so p_keep never rises inside an interval: the interval closes only
when the track has been seen with no risk for `settle_s`, and then a new
interval starts at 1. A swap made inside an interval is therefore reported,
not silent, and each crossing is judged on its own.
"""
import math

import associate
import kalman

TENTATIVE, CONFIRMED, COASTING, ENDED = 'tentative', 'confirmed', 'coasting', 'ended'


class Track:
    __slots__ = ('id', 'x', 'P', 'born', 'last_seen', 'hits', 'confirmed', 'state', 'history',
                 'last_obs_ids', 'heading', 'heading_as_of',
                 'p_keep', 'alternatives', 'ambiguous', 'since_ts', 'calm_since', 'risk', 'prior')

    def __init__(self, tid, x, P, t, obs_ids):
        self.id, self.x, self.P = tid, x, P
        self.born = self.last_seen = t
        self.hits = 1
        self.confirmed = False
        self.state = TENTATIVE
        self.history = [True]
        self.last_obs_ids = list(obs_ids)
        self.heading = None            # (deg, sd_deg), bearing from +y, clockwise
        self.heading_as_of = None
        self.p_keep = 1.0
        self.alternatives = {}         # track id or 'new' -> probability
        self.ambiguous = False
        self.since_ts = t
        self.calm_since = t
        self.risk = 0.0
        self.prior = [(1.0, x, P)]


class TrackerCore:
    def __init__(self, params, exits=(), floor=None, id_prefix='T'):
        self.p = params
        self.exits = [tuple(e) for e in exits]
        self.floor = floor  # (x0, y0, x1, y1) or None
        self.id_prefix = id_prefix
        self.tracks = []
        self.next_id = 1
        self.t = None
        self.lost = []      # [(last seen time, floor point)] of people who vanished in the room

    # -- density of a new person or a false measurement at a floor point -----
    def free_density(self, z):
        """Density of a newcomer or a false measurement at floor point z: a
        flat floor of clutter and births, more at an exit, and more where a
        lost person (a track that ended away from an exit) may reappear."""
        p = self.p
        d = p['clutter_density'] + p['birth_density']
        s = p['exit_radius_m']
        for ex in self.exits:
            d2 = (z[0] - ex[0]) ** 2 + (z[1] - ex[1]) ** 2
            d += p['exit_birth_weight'] * math.exp(-0.5 * d2 / (s * s)) / (2 * math.pi * s * s)
        for t_end, xy in self.lost:
            age = self.t - t_end
            s = p['lost_sd_m'] + p['walk_speed_mps'] * age
            d2 = (z[0] - xy[0]) ** 2 + (z[1] - xy[1]) ** 2
            d += p['lost_weight'] * math.exp(-0.5 * d2 / (s * s)) / (2 * math.pi * s * s)
        return d

    def near_exit(self, xy):
        r = self.p['exit_radius_m']
        return any(math.dist(xy, ex) <= 2 * r for ex in self.exits)

    def _likelihood(self, tr, xy, R):
        """(density, best component) of a floor point under the track's
        walking and stopped predictions, each gated on its own."""
        total, best, best_w = 0.0, 0, -1.0
        for c, (w, x, P) in enumerate(tr.prior):
            nu, S, S_inv, det = kalman.innovation(x, P, xy, R)
            d2 = kalman.mahalanobis2(nu, S_inv)
            if d2 <= self.p['gate_chi2']:
                g = w * kalman.gaussian2(d2, det)
                total += g
                if g > best_w:
                    best, best_w = c, g
        return total, best

    def _keep_on_floor(self, x):
        """Nobody walks through a wall: a prediction that leaves the floor
        (plus a margin) stops at its edge, with the outward speed dropped. A
        track never ends for being off the floor; people leave by an exit."""
        if self.floor is None:
            return
        x0, y0, x1, y1 = self.floor
        m = self.p['floor_margin_m']
        for k, lo, hi in ((0, x0 - m, x1 + m), (1, y0 - m, y1 + m)):
            if x[k] < lo:
                x[k] = lo
                x[k + 2] = max(x[k + 2], 0.0)
            elif x[k] > hi:
                x[k] = hi
                x[k + 2] = min(x[k + 2], 0.0)

    # -- one frame -------------------------------------------------------------
    def step(self, t, measurements):
        """measurements: [{'ids': [obs_id, ...], 'xy', 'R', 'heading', 'members'}]
        as records.read_observations makes them; `members` may be absent.

        Returns (live, ended): the live tracks after this frame, in id order,
        and the tracks that ended at this frame."""
        p = self.p
        if self.t is not None and t < self.t:
            raise ValueError('frames must arrive in time order (%.3f after %.3f)' % (t, self.t))
        dt = 0.0 if self.t is None else t - self.t
        self.t = t
        self.lost = [(te, xy) for te, xy in self.lost if t - te <= p['lost_memory_s']]
        meas = sorted(measurements, key=lambda m: m['ids'][0])
        for tr in self.tracks:
            unseen = t - tr.last_seen
            keep = math.exp(-dt / p['coast_velocity_tau_s']) if unseen > dt + 1e-9 else 1.0
            # Two predictions: walking on at constant velocity, or stopped
            # where last estimated. People stop and turn often; a walking-
            # only model would think two people who stop face to face had
            # walked through each other.
            v = p['stop_speed_sigma'] ** 2
            stopped = ([tr.x[0], tr.x[1], 0.0, 0.0],
                       [[tr.P[0][0], tr.P[0][1], 0, 0], [tr.P[1][0], tr.P[1][1], 0, 0], [0, 0, v, 0], [0, 0, 0, v]])
            walk = kalman.predict(tr.x, tr.P, dt, p['accel_sigma'], keep)
            stop = kalman.predict(stopped[0], stopped[1], dt, p['accel_sigma'], 1.0)
            tr.prior = [(1.0 - p['p_stop'], walk[0], walk[1]), (p['p_stop'], stop[0], stop[1])]
            for _, x, _ in tr.prior:
                self._keep_on_floor(x)
            tr.x, tr.P = walk[0], walk[1]
        # A person who was not seen in the last frame is probably still
        # hidden: such a track expects to be detected less often.
        # And two people close together often hide each other, so a track
        # near another expects to be detected less often too.
        coasting = [t - tr.last_seen > dt + 1e-9 for tr in self.tracks]
        p_det = [p['p_detect_unseen'] if c else p['p_detect'] for c in coasting]
        for i, tr in enumerate(self.tracks):
            if any(u != i and other.confirmed and math.dist(tr.x[:2], other.x[:2]) <= p['near_m']
                   for u, other in enumerate(self.tracks)):
                p_det[i] = min(p_det[i], p['p_detect_near'])
        link = [[p_det[i] * self._likelihood(tr, m['xy'], m['R'])[0] for m in meas]
                for i, tr in enumerate(self.tracks)]
        miss = [1.0 - pd for pd in p_det]
        free = [self.free_density(m['xy']) for m in meas]
        choice, _, risk, alts = associate.associate(link, miss, free, p['max_hypotheses'], p['risk_temperature'])
        for i, tr in enumerate(self.tracks):
            named = {}
            for k, v in alts[i].items():
                key = 'new' if k == 'new' else self.tracks[k].id
                named[key] = named.get(key, 0.0) + v
            r = risk[i]
            if choice[i] is not None:
                r, named = self._member_risk(i, meas[choice[i]], p_det, r, named)
            self._doubt(tr, t, r, named, seen=choice[i] is not None)
        taken = set()
        for i, tr in enumerate(self.tracks):
            j = choice[i]
            if j is None:
                tr.last_obs_ids = []
                tr.history.append(False)
                continue
            m = meas[j]
            taken.add(j)
            _, c = self._likelihood(tr, m['xy'], m['R'])
            tr.x, tr.P = tr.prior[c][1], tr.prior[c][2]
            R = m['R']
            nu, S, S_inv, _ = kalman.innovation(tr.x, tr.P, m['xy'], R)
            d2 = kalman.mahalanobis2(nu, S_inv)
            if d2 > p['robust_d2']:
                # Probably a depth outlier: let it pull the track less.
                R = [[v * d2 / p['robust_d2'] for v in row] for row in R]
            tr.x, tr.P = kalman.update(tr.x, tr.P, m['xy'], R)
            tr.last_seen = t
            tr.hits += 1
            tr.last_obs_ids = list(m['ids'])
            tr.history.append(True)
            if m.get('heading') is not None and not tr.ambiguous:
                # Shoulder and hip evidence that may be someone else's is not accepted.
                tr.heading, tr.heading_as_of = m['heading'], t
        window = p['confirm_window']
        live, ended = [], []
        for tr in self.tracks:
            del tr.history[:-window]
            if not tr.confirmed and sum(tr.history) >= p['confirm_hits']:
                tr.confirmed = True
            unseen = t - tr.last_seen
            if not tr.confirmed:
                gone = len(tr.history) >= window and sum(tr.history) < p['confirm_hits']
            elif self.near_exit(tr.x[:2]) and unseen >= p['exit_coast_s']:
                gone = True
            else:
                gone = unseen > p['max_coast_s']
            if gone:
                tr.state = ENDED
                tr.last_obs_ids = []
                ended.append(tr)
                if tr.hits >= p['lost_min_hits'] and not self.near_exit(tr.x[:2]):
                    # Probably still in the room, hidden: remember where.
                    self.lost.append((tr.last_seen, (tr.x[0], tr.x[1])))
            else:
                if not tr.confirmed:
                    tr.state = TENTATIVE
                elif unseen >= p['coast_after_s']:
                    tr.state = COASTING
                else:
                    tr.state = CONFIRMED
                live.append(tr)
        for j, m in enumerate(meas):
            if j in taken:
                continue
            x, P = kalman.initial(m['xy'], m['R'], p['new_track_speed_sigma'])
            tr = Track('%s%d' % (self.id_prefix, self.next_id), x, P, t, m['ids'])
            if m.get('heading') is not None:
                tr.heading, tr.heading_as_of = m['heading'], t
            self.next_id += 1
            live.append(tr)
        self.tracks = live
        return list(live), ended

    def _member_risk(self, i, m, p_det, risk, named):
        """A merged measurement may mix two people where they overlap in the
        image: weigh each merged observation on its own against every track,
        and add the chance that it is another track's (or a newcomer's)."""
        worst, worst_named = 0.0, {}
        for mem in m.get('members') or []:
            w = [p_det[u] * self._likelihood(tr, mem['xy'], mem['R'])[0] for u, tr in enumerate(self.tracks)]
            free = self.free_density(mem['xy'])
            top = max(w + [free])
            if top <= 0:
                continue
            w = [(wu / top) ** (1.0 / self.p['risk_temperature']) if wu > 0 else 0.0 for wu in w]
            free = (free / top) ** (1.0 / self.p['risk_temperature'])
            total = sum(w) + free
            others = {self.tracks[u].id: wu / total for u, wu in enumerate(w) if u != i and wu > 0}
            r = sum(others.values())
            if r > worst:
                worst, worst_named = r, others
        if worst <= 0:
            return risk, named
        combined = 1.0 - (1.0 - risk) * (1.0 - worst)
        out = {k: v * (1.0 - worst) for k, v in named.items()}
        for k, v in worst_named.items():
            out[k] = out.get(k, 0.0) + v
        scale = combined / sum(out.values()) if sum(out.values()) > 0 else 0.0
        return combined, {k: v * scale for k, v in out.items()}

    def _doubt(self, tr, t, risk, named, seen):
        """Fold this frame's identity risk into the track's interval."""
        p = self.p
        tr.risk = risk
        if risk >= p['risk_floor']:
            for k, v in named.items():
                tr.alternatives[k] = tr.alternatives.get(k, 0.0) + tr.p_keep * v
            tr.p_keep *= (1.0 - risk)
            tr.calm_since = None
        elif seen and tr.calm_since is None:
            tr.calm_since = t
        if not tr.ambiguous and tr.p_keep < p['confident_at']:
            tr.ambiguous, tr.since_ts = True, t
        elif tr.ambiguous and seen and tr.calm_since is not None and t - tr.calm_since >= p['settle_s']:
            tr.ambiguous, tr.since_ts = False, t
            tr.p_keep, tr.alternatives = 1.0, {}
        elif not tr.ambiguous and tr.calm_since is not None and tr.p_keep < 1.0 \
                and t - tr.calm_since >= p['settle_s']:
            tr.p_keep, tr.alternatives = 1.0, {}     # small doubt that never reached ambiguity
        if not seen and tr.calm_since is not None and tr.ambiguous:
            tr.calm_since = None                     # settling needs the track in view

    def hypotheses(self, tr):
        """Named hypotheses while ambiguous; [] while confident."""
        if not tr.ambiguous:
            return []
        out = [{'hypothesis': 'same', 'track_id': tr.id, 'p': tr.p_keep}]
        for k in sorted(tr.alternatives, key=str):
            if k == 'new':
                out.append({'hypothesis': 'newcomer', 'track_id': None, 'p': tr.alternatives[k]})
            else:
                out.append({'hypothesis': 'swapped', 'track_id': k, 'p': tr.alternatives[k]})
        return out
