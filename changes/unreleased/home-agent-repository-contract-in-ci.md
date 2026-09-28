---
title: Run the Home Agent repository contract suite in CI
target: internal
type: fixed
---

The hosted Home Agent web boundary gate now runs
`tests/home_agent/test_repository_contract.py`, which previously ran in no
workflow and let stale assertions merge unnoticed. The gate's path triggers now
cover every file the suite reads, including `stack/**`, the Home Agent edge
integration, and the Home Agent docs it checks.
