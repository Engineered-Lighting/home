---
area: perception-lab
target: controller
---
JEV: v5 requests get a pre-send token budget (trim oldest raw records, refuse over the hard cap), HTTP
validation refusals (400/401/403/404/413/422) are recorded as failed with their status instead of freezing
egress, and a `wire: den-facts-6` JEV config key sends the facts-only v6 packet live (shadow off).
