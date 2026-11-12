---
title: The den lighting coordinator's core, offline
target: internal
type: added
---

Adds the den lighting coordinator's pure core to `tools/den-core`. It's one program that decides every den
light: the bulbs through den_lighting plans, and each spotlight's job. `den_coordinator.py` takes events
(people with their 3D skeleton, PersonState, Home Assistant status, fixtures, room labels, owner settings,
Stop) and writes Decisions, fixture intents (aim and move, keep-lit, Stop, Resume), bulb plans and learning
updates.

The parts:
- `share.py` gives each spotlight one job: the reader's task, then pointing at a detected person dark,
  then labeled art, else resting.
- `eye.py` keeps the light lighting you from ever being in front of you, ranks fixtures over the shoulder
  with the lighting brain's `aim.py`, and adds a margin so a near side-on fixture can't flip.
- `body.py` holds the seated test, facing, the task point and the page-turn filter.
- `joints.py` holds the fixture joint frame. `scurve.py` holds move timing. `spot_light.py` holds Home's
  beam formula, and the lowest level that's enough.
- `need_v1.py` counts any close-up task and makes the phone patience a setting. need v0 is untouched.
- `learn.py` learns halfway from the owner's own changes. `bulbs.py` builds den_lighting plans.
- `report.py` prints what the coordinator would do on the owner's real sofa, from the den simulation's
  newest fit.

A fixture beside the reader counts. When two fixtures are about equally beside or behind the reader, the
brighter one wins. On the owner's real sofa that picks F3.

Nothing is sent to a host or a light. Existing den-core modules and replays are unchanged: all 505 existing
tests pass, along with 66 new ones.
