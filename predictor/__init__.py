"""predictor — the anticipatory-lighting Markov next-zone predictor
(Addendum 37). Shared by the live add-on and the offline backtest harness so
backtest numbers stay faithful to live behaviour — one implementation, no
reimplementation drift (Addendum 37 §18).
"""

from .counts import CountStore
from .model import (ALPHA, EPSILON, EXIT, ZONES, Predictor, motion_factor)
from .seed import load_adjacency, parse_adjacency
from .stitch import (ENTRY, clean_transitions, stitch_transitions,
                     to_epoch, transitions_since)

__all__ = [
    "CountStore", "Predictor", "motion_factor", "ZONES", "EXIT", "ENTRY",
    "ALPHA", "EPSILON", "load_adjacency", "parse_adjacency",
    "clean_transitions", "transitions_since", "stitch_transitions", "to_epoch",
]
