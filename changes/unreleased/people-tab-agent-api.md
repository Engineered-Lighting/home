Render the People tab from the agent authority when the legacy store is fenced.

The E4 cutover froze the legacy identity store, so the Home Assistant
integration answers `ready: false` with an empty identity list and the People tab
renders nothing -- no people, no relationship map. The same household was
migrated into the agent's authority and is already served by
`GET /api/agent/v1/household` and `/relationships`, same-origin through the web
gateway.

The tab now falls back to those endpoints when the legacy store reports itself
unavailable. Nothing changes while that store is healthy.

Two details worth stating. The agent authority carries predicates rather than the
legacy relationship vocabulary, so predicates are mapped explicitly and an
unmapped one keeps its own name instead of being coerced into a nearby category.
And it carries no per-person `relationship_type`, so that is derived from the
edges actually recorded -- anyone with no recorded relationship stays "unknown"
rather than being assigned a category nobody asserted.
