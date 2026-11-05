"""Joint association of tracks to one frame's observations.

Every joint hypothesis (each observation goes to at most one track, each track
takes at most one observation, only inside the gate) is weighed as

    product over taken pairs   of  link[t][o]   (P_D times the Gaussian)
    product over missed tracks of  miss[t]      (1 - P_D)
    product over free obs      of  free[o]      (birth or clutter density)

The best hypothesis is used; each track's marginal is the share of total
weight held by hypotheses that agree with the best one for that track. With
few people every hypothesis is enumerated, cluster by cluster; a cluster too
big to enumerate falls back to the best hypothesis with marginals of 0, which
reads as fully ambiguous.
"""


def clusters(n_tracks, n_obs, link):
    """Connected groups of tracks and observations joined by gated links."""
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
    """All hypotheses of one cluster as (weight, {track: obs or None})."""
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


def associate(link, miss, free, limit=20000, temperature=1.0):
    """Returns (choice, marginal, risk, alternatives).

    choice[t]   : the observation index track t takes, or None (missed);
    marginal[t] : the probability that choice[t] is right, given the model;
    risk[t]     : the probability that track t's person is not where the best
                  hypothesis puts it in a way that can move an identity: t
                  takes an observation the best gives another track, t's
                  observation goes to another track, or t's observation is
                  really a newcomer's (or a lost person's: `free` carries
                  that density). Two tracks that may have swapped both
                  carry the risk, the unseen one too;
    alternatives[t]: risk[t] split by who t may really be following:
                  {u: p} for another track u, {'new': p} for a newcomer.
                  The values add up to risk[t].

    `temperature` > 1 flattens the hypothesis weights (each to the power
    1 / temperature) before marginals and risks are taken, because the
    motion model is more sure of itself than people are predictable. The
    best hypothesis is the same at any temperature.
    """
    n_tracks, n_obs = len(link), len(free)
    choice, marginal, risk = [None] * n_tracks, [1.0] * n_tracks, [0.0] * n_tracks
    alternatives = [{} for _ in range(n_tracks)]
    for tracks, obs in clusters(n_tracks, n_obs, link):
        if not tracks:
            continue
        hyps = _enumerate(tracks, obs, link, miss, free, limit)
        if len(hyps) > limit:
            best = _greedy(tracks, obs, link)
            for t in tracks:
                choice[t], marginal[t], risk[t] = best.get(t), 0.0, 1.0
                alternatives[t] = {'new': 1.0}
            continue
        best_w, best = hyps[0]
        for w, h in hyps[1:]:
            if w > best_w:
                best_w, best = w, h
        if temperature != 1.0 and best_w > 0:
            hyps = [((w / best_w) ** (1.0 / temperature), h) for w, h in hyps]
        total = sum(w for w, _ in hyps)
        given = {o for o in best.values() if o is not None}
        owner = {o: u for u, o in best.items() if o is not None}
        for t in tracks:
            choice[t] = best[t]
            if total <= 0:
                marginal[t], risk[t] = 0.0, 1.0
                alternatives[t] = {'new': 1.0}
                continue
            agree = moved = 0.0
            alt = {}
            for w, h in hyps:
                mine = h[t]
                if mine == best[t]:
                    agree += w
                elif best[t] is None and mine not in given:
                    pass            # t may have been seen where the best saw a newcomer: no identity moves
                else:
                    moved += w
                    key = _alternative(t, mine, best, h, owner)
                    alt[key] = alt.get(key, 0.0) + w
            marginal[t] = agree / total
            risk[t] = moved / total
            alternatives[t] = {k: v / total for k, v in sorted(alt.items(), key=lambda kv: str(kv[0]))}
    return choice, marginal, risk, alternatives


def _alternative(t, mine, best, h, owner):
    """Who track t may really be following, in hypothesis h."""
    if mine is not None and mine in owner and owner[mine] != t:
        return owner[mine]                  # t takes the observation the best gives u
    if best[t] is not None:
        for u, o in h.items():
            if u != t and o == best[t]:
                return u                    # u takes the observation the best gives t
    return 'new'


def _greedy(tracks, obs, link):
    pairs = sorted(((-link[t][o], t, o) for t in tracks for o in obs if link[t][o] > 0))
    taken_t, taken_o, out = set(), set(), {}
    for _, t, o in pairs:
        if t not in taken_t and o not in taken_o:
            out[t] = o
            taken_t.add(t)
            taken_o.add(o)
    return out
