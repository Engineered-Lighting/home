"""Joint association of tracks to one frame's detections.

Every joint hypothesis (each detection goes to at most one track, each track
takes at most one detection, only inside the gate) is weighed as

    product over taken pairs   of  link[t][o]   (P_D times the Gaussian)
    product over missed tracks of  miss[t]      (1 - P_D)
    product over free dets     of  free[o]      (birth or clutter density)

The best hypothesis is used. A track's rivals are the share of total weight
held by hypotheses that would move an identity, named by who it would move to.
With few people every hypothesis is enumerated, cluster by cluster; a cluster
too big to enumerate falls back to a greedy pick that reads as fully ambiguous.
"""


def clusters(n_tracks, n_obs, link):
    """Connected groups of tracks and detections joined by gated links."""
    parent = list(range(n_tracks + n_obs))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for t in range(n_tracks):
        for o in range(n_obs):
            if link[t][o] > 0:
                ra, rb = find(t), find(n_tracks + o)
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)
    groups = {}
    for node in range(n_tracks + n_obs):
        groups.setdefault(find(node), []).append(node)
    out = []
    for root in sorted(groups):
        nodes = groups[root]
        out.append(([n for n in nodes if n < n_tracks], [n - n_tracks for n in nodes if n >= n_tracks]))
    return out


def _enumerate(tracks, obs, link, miss, free, limit):
    """All hypotheses of one cluster as (weight, {track: det or None})."""
    out = []

    def walk(k, used, weight, chosen):
        if len(out) > limit:
            return
        if k == len(tracks):
            w = weight
            for o in obs:
                if o not in used:
                    w *= free[o]
            out.append((w, dict(chosen)))
            return
        t = tracks[k]
        chosen[t] = None
        walk(k + 1, used, weight * miss[t], chosen)
        for o in obs:
            if o not in used and link[t][o] > 0:
                chosen[t] = o
                used.add(o)
                walk(k + 1, used, weight * link[t][o], chosen)
                used.discard(o)
        del chosen[t]

    walk(0, set(), 1.0, {})
    return out


def _rival(t, mine, best, h):
    """Who the identity would move to under hypothesis h: a track, or None for a newcomer."""
    if mine is not None:
        for s, o in best.items():
            if s != t and o == mine:
                return s
    if best[t] is not None:
        for s, o in h.items():
            if s != t and o == best[t]:
                return s
    return None


def associate(link, miss, free, coasting=None, limit=20000):
    """Returns (choice, rivals).

    choice[t] : the detection index track t takes, or None (missed);
    rivals[t] : {other track or None: probability}, the ways track t's person
                may not be where the best hypothesis puts it in a way that can
                move an identity: t takes a detection the best gives another
                track or a newcomer, t's detection goes to another track, or
                (only when t is `coasting`, unseen since before this frame)
                t's detection is really a newcomer's. Two tracks that may have
                swapped both carry the risk, the unseen one too.
    """
    n_tracks, n_obs = len(link), len(free)
    coasting = coasting or [False] * n_tracks
    choice, rivals = [None] * n_tracks, [{} for _ in range(n_tracks)]
    for tracks, obs in clusters(n_tracks, n_obs, link):
        if not tracks:
            continue
        hyps = _enumerate(tracks, obs, link, miss, free, limit)
        if len(hyps) > limit:
            best = _greedy(tracks, obs, link)
            for t in tracks:
                choice[t], rivals[t] = best.get(t), {None: 1.0}
            continue
        total = sum(w for w, _ in hyps)
        best_w, best = hyps[0]
        for w, h in hyps[1:]:
            if w > best_w:
                best_w, best = w, h
        given = {o for o in best.values() if o is not None}
        for t in tracks:
            choice[t] = best[t]
            if total <= 0:
                rivals[t] = {None: 1.0}
                continue
            moved = {}
            for w, h in hyps:
                mine = h[t]
                if mine == best[t]:
                    continue
                if best[t] is None and mine not in given:
                    continue    # t may have been seen where the best saw a newcomer: no identity moves
                if mine is None and not coasting[t] and best[t] not in h.values():
                    continue    # a newcomer exactly where a track in view is seen: treated as noise
                who = _rival(t, mine, best, h)
                moved[who] = moved.get(who, 0.0) + w / total
            rivals[t] = moved
    return choice, rivals


def _greedy(tracks, obs, link):
    pairs = sorted(((-link[t][o], t, o) for t in tracks for o in obs if link[t][o] > 0))
    taken_t, taken_o, out = set(), set(), {}
    for _, t, o in pairs:
        if t not in taken_t and o not in taken_o:
            out[t] = o
            taken_t.add(t)
            taken_o.add(o)
    return out
