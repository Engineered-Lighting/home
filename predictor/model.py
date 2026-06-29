"""model.py — the first-order, time-of-day-conditioned Markov next-zone
predictor (Addendum 37 §6).

predict() ranks the next zone a person will move to given (from_zone,
tod_bucket); update() folds one observed transition into the decayed counts.
A seed-adjacency Dirichlet prior warm-starts cold zones and a confidence
composite keeps the predictor quiet until it has real evidence.
"""

from __future__ import annotations

from .counts import CountStore

EXIT = "__exit__"

# The 15-node zone graph (Addendum 37 §3).
ZONES = [
    "front_door", "office", "sofa", "weights", "front_left",
    "whole_living_room", "dining_left", "dining_right", "whole_dining_room",
    "sink", "island_left", "island_right", "whole_kitchen",
    "workshop_zone", "e28",
]

# Dirichlet pseudocounts. Sized so the prior (total ~4) warm-starts a cold
# zone yet washes out within ~30 real observations (Addendum 37 §6).
ALPHA = 0.8       # per seed-adjacent edge (and __exit__)
EPSILON = 0.02    # per non-adjacent edge — a soft plausibility floor
EVIDENCE_FULL = 30.0   # real-observation count at which evidence saturates


def motion_factor(speed_mps):
    """Live speed -> a [0,1] gate. A settled person (<0.3 m/s) suppresses
    next-zone prediction; a walking person (>=1.0 m/s) does not. Unknown
    speed -> 1.0 (do not suppress)."""
    if speed_mps is None:
        return 1.0
    s = float(speed_mps)
    if s < 0.3:
        return 0.0
    if s >= 1.0:
        return 1.0
    return (s - 0.3) / 0.7


class Predictor:
    """Online Markov next-zone predictor. Imported by both the live add-on
    and the offline backtest harness — one implementation, no drift."""

    def __init__(self, adjacency=None, half_life_days=23.0):
        self.adjacency = adjacency or {}
        self.targets = list(ZONES) + [EXIT]
        self.counts = CountStore(half_life_days)

    def prior(self, from_zone):
        """Dirichlet pseudocounts for from_zone — seed-adjacent zones and
        __exit__ get ALPHA, every other destination EPSILON."""
        adj = self.adjacency.get(from_zone, set())
        return {t: (ALPHA if (t == EXIT or t in adj) else EPSILON)
                for t in self.targets}

    def update(self, from_zone, tod, to_zone, ts):
        """Fold one observed transition into the decayed counts."""
        self.counts.add(from_zone, tod, to_zone, ts)

    def predict(self, from_zone, tod, now_ts, speed_mps=None):
        """Ranked next-zone distribution + a confidence composite.

        confidence = evidence x peakiness — low until the node has real
        observations AND one destination clearly leads, so the predictor
        stays quiet through cold-start and genuine ambiguity. The motion
        gate folds live speed in as effective_confidence."""
        real = self.counts.get(from_zone, tod, now_ts)
        prior = self.prior(from_zone)
        combined = {t: real.get(t, 0.0) + prior[t] for t in self.targets}
        total = sum(combined.values()) or 1.0
        ranked = sorted(((t, combined[t] / total) for t in self.targets),
                        key=lambda kv: kv[1], reverse=True)

        real_total = sum(real.values())
        evidence = min(1.0, real_total / EVIDENCE_FULL)
        p1 = ranked[0][1]
        p2 = ranked[1][1] if len(ranked) > 1 else 0.0
        markov_conf = evidence * (p1 - p2)
        mf = motion_factor(speed_mps)
        return {
            "from_zone": from_zone,
            "tod": tod,
            "predictions": [{"zone": z, "p": round(p, 4)}
                            for z, p in ranked],
            "top_zone": ranked[0][0],
            "markov_confidence": round(markov_conf, 4),
            "motion_factor": round(mf, 3),
            "effective_confidence": round(markov_conf * mf, 4),
            "evidence": round(evidence, 3),
            "observations": round(real_total, 2),
        }
