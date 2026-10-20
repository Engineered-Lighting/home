# Den assessment tools

Owner-local reference editor and independent scoring for the actual v4
acquisition factory. Start with
[`home-live-producer-assessment.md`](../../docs/runbooks/home-live-producer-assessment.md).

`assess.py reference-editor --output ABSOLUTE.html` creates a standalone offline,
prediction-hidden reference form. `reference-template --measurements ABSOLUTE.json
--output ABSOLUTE.json` creates an intentionally incomplete import template.
`score --measurements ABSOLUTE.json --references ABSOLUTE.json --output ABSOLUTE.json`
computes an aggregate; none of these commands opens a camera, host, model or network.
No dependencies need installation. Files are bounded and outputs refuse overwrite.

Runtime start/stop/status/read-results belong to the fixed managed supervisor,
not to a second camera process or owner-written Python composition. Candidate
records are not qualified geometry. Real measurement and installed host acceptance
have not occurred. Keep all individual measurements/references owner-local.
