# den-bundle ROUTER-V0: the router rule

Rung 5's router v0 sets each person's request rate within today's caps, from
the ladder: at most one request every 4 s and 15 a minute, one in flight
(A6), round-robin across tracks, and a 30 s back-off after an admission refusal
or an error. Every request record carries this rule's `version` and the sha256
of the block below (canonical JSON, so CRLF or LF reads the same). Change a
number, bump `version`.

```json den-bundle-router
{
  "rule": "router",
  "version": "v0",
  "min_interval_s": 4,
  "max_per_minute": 15,
  "minute_s": 60,
  "max_in_flight": 1,
  "backoff_s": 30,
  "order": "round-robin",
  "eligible_states": ["confirmed"],
  "timeout_s": {"open": true, "proposed": 20, "why": "Today's caps name no timeout. Proposed 20 s: the look-test tool's timeout that N2's ask step reuses (N2 PLAN section 6). A request with no answer by then counts as an error and starts the back-off."}
}
```

## The rule

- **Schedule.** The router may start a request at the earliest time that is
  at least `min_interval_s` after the last start, outside any back-off, with
  nothing in flight, and with fewer than `max_per_minute` starts in the
  `minute_s` before it. It then sends at once with whatever evidence is there:
  a source that is late is flagged in the bundle's `missing`, never waited for.
- **Who is eligible.** A track whose latest line (as of now) is in
  `eligible_states` and is dated within the last `window_s` (METHOD-V1).
  Tentative, coasting and ended tracks get no requests.
- **Round-robin.** Tracks keep the order they were first seen. Each start goes
  to the first eligible track after the one served last, so two tracks
  alternate, and each person's period is `min_interval_s` times the number of
  eligible tracks (8 s for two, 4 s for one). The request record names it as
  `per_person_period_s`.
- **No box.** A bundle with no boxed frame has nothing to crop. It is logged as
  `no_box` and not sent; it uses no cap, and the turn passes to the next track.
- **Refusals and errors.** An admission refusal (LA's 429, note R19), an error
  or a timeout (`timeout_s`) stops all sending for `backoff_s`, counted from
  that answer. Every start, refused or not, counts toward both caps. A late
  answer to a request already timed out is ignored.
- **Records.** One `router_request/v0` line per start or `no_box`, each with the
  rule's and the method's version, sha256 and open slots.
