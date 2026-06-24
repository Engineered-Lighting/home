"""counts.py — the time-decayed transition-count store (Addendum 37 §6).

A first-order Markov count table keyed (from_zone, tod_bucket); each key holds
a count per destination zone. Counts decay exponentially (~23-day half-life)
so a changing routine is tracked and stale edges fade. Decay is lazy — applied
on read/update, never as a background sweep — so the whole "training loop" is
a single increment.
"""

from __future__ import annotations

SECONDS_PER_DAY = 86400.0


class CountStore:
    """Decayed counts: cell (from_zone, tod) -> {to_zone: [count, last_ts]}."""

    def __init__(self, half_life_days=23.0):
        self.half_life_days = max(0.1, float(half_life_days))
        self._cells = {}

    def _decay(self, last_ts, now_ts):
        dt_days = max(0.0, (now_ts - last_ts) / SECONDS_PER_DAY)
        return 0.5 ** (dt_days / self.half_life_days)

    def add(self, from_zone, tod, to_zone, ts):
        """Record one observed transition at wall-clock `ts`."""
        cell = self._cells.setdefault((from_zone, tod), {})
        entry = cell.get(to_zone)
        if entry is None:
            cell[to_zone] = [1.0, ts]
        else:
            entry[0] = entry[0] * self._decay(entry[1], ts) + 1.0
            entry[1] = ts

    def get(self, from_zone, tod, now_ts):
        """Decayed count per destination for (from_zone, tod) as of now_ts."""
        cell = self._cells.get((from_zone, tod), {})
        return {to: cnt * self._decay(ts, now_ts)
                for to, (cnt, ts) in cell.items()}

    def total(self, from_zone, tod, now_ts):
        return sum(self.get(from_zone, tod, now_ts).values())

    def to_dict(self):
        """Serialise for persistence (counts.json)."""
        return {f"{fz}\t{tod}": {to: [c, t] for to, (c, t) in cell.items()}
                for (fz, tod), cell in self._cells.items()}

    def load_dict(self, data):
        self._cells = {}
        for key, cell in (data or {}).items():
            fz, _, tod = key.partition("\t")
            self._cells[(fz, tod)] = {
                to: [float(v[0]), float(v[1])] for to, v in cell.items()}
        return self
