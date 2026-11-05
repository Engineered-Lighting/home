# Owner choices (decision walk of 2026-11-02, with later notes)

The walk stopped at 10 of 29 decisions. Every other decision in
`decisions.json` is still open.

1. **recording-retention:** den recordings may be kept on household computers
   until the owner deletes them, whoever appears. Anything published or shared
   blurs faces and identifies no one.
2. **labelling:** the owner labels alone, blind to system output, with a
   delayed self re-mark of a sample. Results report a single labeller.
3. **onset-protocol:** contact-starting. Onset is the first touch, timed to the
   system's first output that leaves `none` (`starting`). The 5 s dwell is
   reported separately. The 3 s median target is kept.
4. **need-test-order:** first: R, then N1, then N2, before the perception
   rungs. On 2026-11-04 the owner asked for every rung's code to be built now.
   Runs and qualification still follow the ladder's order.
5. **demand-gates:** fine at 0.6 or more, casual at 0.4 or more; false-fine at
   most 0.033 of non-fine seconds; at least 30 episodes per band from at least
   3 sessions over at least 2 days; detection grace 6 s for one person, about
   10 s for two; 10 ECE bins; recall and coverage floors set later on the
   development split, before held-out.
6. **promotion-margins:** two-sided 95% intervals from 10,000 session-bootstrap
   rounds. Each metric's δ is still open.
7. **reference-geometry:** strict, with `tau_R` 5 cm.
8. **delivered-statistic:** area share: 70% of the visible patch at the floor
   level; at least 50% of the patch visible to be scored.
9. **service-floors:** 300 lx fine, 200 lx casual, a 120 s quiet window.
10. **attribution-thresholds:** deferred.
11. **photometry-commissioning:** not answered. Rung 7's meter tolerance in lux
    stays an empty slot.
