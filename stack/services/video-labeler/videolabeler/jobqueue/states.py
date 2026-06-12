"""Job state machine constants."""
from __future__ import annotations

QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"

ACTIVE = frozenset({QUEUED, RUNNING})
TERMINAL = frozenset({SUCCEEDED, FAILED, CANCELLED})
ALL_STATES = frozenset({QUEUED, RUNNING, SUCCEEDED, FAILED, CANCELLED})

LANE_CPU = "cpu"
LANE_GPU = "gpu"
LANES = (LANE_CPU, LANE_GPU)
