# Den commissioning kit

Workstation-only operator tooling for [ticket #197](https://github.com/Engineered-Lighting/home/issues/197).
The [runbook](../../docs/runbooks/first-live-den-commissioning.md) is the starting point.
Node.js with its standard library is sufficient; no package installation is needed.

```powershell
node tools/den-commissioning/commission.mjs template --output C:\owner-local\den-record.json
node tools/den-commissioning/commission.mjs validate --record C:\owner-local\den-record.json
node --test tools/den-commissioning/test-commission.mjs
```

The example `C:\owner-local` directory must already exist and remain owner-controlled.
The CLI creates only explicitly named files and its explicitly named state directory.
It makes no network requests, invokes no subprocesses, and never opens a camera,
image, depth array, recording, host, or arbitrary referenced artifact. It reads JSON
records and verifies supplied digests' **syntax**, not the contents of the referenced
survey/model artifacts. Real applicability remains an independently assessed input.

Commands require all listed options:

| Command | Options | Effect |
| --- | --- | --- |
| `template` | `--output` | Creates a blank local record, refusing overwrite |
| `validate` | `--record` | Reports validity, grouped missing steps and aggregate counts |
| `freeze` | `--record --state-dir --output` | Seals a complete record and partition membership; refuses retrospective revision changes |
| `record-attempt` | `--freeze --manifest --state-dir` | Retains an immutable predeclared completed/failed/aborted/excluded attempt manifest |
| `retire` | `--freeze --reason --state-dir` | Permanently marks the held-out membership development in this ledger |
| `inspect` | `--freeze --state-dir` | Produces a shareable aggregate report including missing planned attempts |
| `aggregate` | `--freeze --summary` | Validates an owner-created error/containment/latency aggregate without reading individual observations |

Exit codes: `0` operation/record complete, `3` incomplete independent inputs, `2`
invalid/refused. None means live, physical, or accuracy acceptance. Reports always
say `runtime_qualification: not_assessed` where applicable and `physical_authority: false`.
Blank templates deliberately return incomplete and remain usable for an initial
survey; they need no future producer results to be created or reviewed.

Bounds: each regular JSON input/output is at most 1 MiB; arrays hold at most 512
IDs, positions 32, fixtures 16; the state directory is limited to 4,096 entries and
32 MiB. These are tool resource caps, **not measured operational or quality limits**.
All quality/freshness/rate/capacity requirements are owner-supplied with no defaults.
Writes use exclusive creation and fsync. A state lock rejects concurrent mutations.
An interrupted write/leftover lock is an explicit operator recovery condition; the
tool does not delete history or guess that a lock is stale. Preserve the directory
and investigate the interrupted operation before removing its stale lock manually.

Frozen output contains the complete local record and must stay local. Its
`freeze_digest` is SHA256 of canonical JSON: recursively sorted object keys,
preserved array order, UTF-8, no insignificant whitespace. `assessment_partition_refs`
hold the role, opaque partition ID, and SHA256 over sorted episode IDs. Downstream
run manifests use:

```json
{
  "commissioning_ref": {
    "schema": "den-commissioning-freeze-1",
    "record_id": "owner-chosen-opaque-id",
    "freeze_digest": "64-lowercase-hex-characters"
  },
  "assessment_partition_ref": {
    "partition_id": "owner-chosen-opaque-id",
    "role": "held_out",
    "membership_digest": "64-lowercase-hex-characters"
  }
}
```

The record itself has schema `den-commissioning-record-1` and a positive revision.
Same `(record_id, revision)` cannot change content. Exact record retries may export
another new frozen file. Exact attempt retries are acknowledged; changed content
under an attempt ID rejects. New revisions cannot reuse retired episodes as held-out,
even after renaming the partition; already assessed membership cannot be refrozen
with changed requirements or method pins. A prior development episode cannot become
held-out. These checks depend on retaining the **same state directory**: this is an
owner-operated provenance ledger, not tamper-proof storage or an authorization service.
Never delete/copy around it to reset a result. Keep a private backup with the records.

The kit defines required measurement/acceptance inputs; it does not implement the
producer, acquisition launcher, registration, final episode scorer, deployment,
physical control, or report truth verification. See the runbook for the independent
reference and metric definitions that later assessment must implement.

The optional `aggregate` command accepts schema
`den-commissioning-measurement-summary-1`: `freeze_digest`, `assessment_digest`
(digest of the retained local assessment manifest), an exact frozen
`assessment_partition_ref`, `units: {distance: "m", time: "s"}`, `duration_s`,
`source_validity`, `work_center_error_m`, `reference_uncertainty_m`, `containment`,
and `latency_s`. Source validity uses integer `{total_samples, valid_samples,
invalid_samples, unknown_samples}` with an exact total. Error, reference uncertainty
for each of the five parts, and each latency group
use `{samples, eligible_s, p50, p95, max}`; zero samples require null percentiles.
Latency groups are `observation_to_available` and `publication_to_decision`.
Containment has separate `task`, `body`, `head`, `hands`, `protection` groups using
`{eligible_s, contained_s, undercovered_s, unavailable_reference_s, missing_prediction_s}`.
Contained plus undercovered duration must equal eligible duration; missing predictions
must be included in undercoverage; eligible plus unavailable reference duration cannot
exceed total duration. No individual rows, paths, names or extra fields are accepted.
The owner creates these aggregates using the frozen measurement/counting protocol.
Any unavailable metric group is explicit `null`, listed in `missing_groups`, and
returns exit 3; it is never replaced by zero. All required group keys must exist.
Output explicitly says `owner_supplied_not_recomputed`: structural consistency does
not verify raw truth, metric computation, completeness or acceptance thresholds.
