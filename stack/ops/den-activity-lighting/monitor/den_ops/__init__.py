"""Always-on operations for den activity lighting (AL-5, Engineered-Lighting/home#325).

- ``den_ops check``: one monitor pass (run by a systemd timer every minute).
- ``den_ops soak``: the supervised soak sampler and its pass criteria.

Standard library only; runs on Python 3.10 (Jetson) and 3.12 (LA).
"""

STATUS_SCHEMA = "den-ops-status-1"
CONFIG_SCHEMA = "den-ops-monitor-1"
STATE_SCHEMA = "den-ops-monitor-state-1"
BASELINE_SCHEMA = "den-ops-fault-baseline-1"
UNITS_BASELINE_SCHEMA = "den-ops-failed-units-baseline-1"
SOAK_CONFIG_SCHEMA = "den-ops-soak-1"
SOAK_RECEIPT_SCHEMA = "den-ops-soak-receipt-1"

STEPS = ("normal", "cadence_reduced", "deep_stack_paused", "loop_stopped")
