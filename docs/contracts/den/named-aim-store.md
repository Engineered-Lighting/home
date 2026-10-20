# Named-aim store

**Status:** proposed contract (APT-A07), 2026-10-20. **There is no server store
today.**

The desk and sofa marks exist as sealed private downloads from Home's selector:

- `victoria-den-desk-target.json`: `home-registered-desk-target-1` or `-2`;
- `victoria-den-sofa-target.json`: `home-registered-sofa-target-1`.

`tools/den-room/brain-targets.mjs` turns them into the brain's one private
file, `lighting-brain-geometry-1`.

This document fixes the record shape and the revision rules, so that a future
store, and the UI's "saved places", can hold the same marks without changing
what the brain reads.

Examples:
- [fixtures/named-aim-sofa.json](fixtures/named-aim-sofa.json)
- [fixtures/sofa-target-synthetic.json](fixtures/sofa-target-synthetic.json)
- [fixtures/lighting-brain-geometry-synthetic.json](fixtures/lighting-brain-geometry-synthetic.json)

## Record: `home-named-aim-1`

```json
{
  "schema": "home-named-aim-1",
  "id": "victoria-den:sofa",
  "kind": "sofa",
  "label": "Sofa",
  "scope": {"site_id": "victoria", "map_id": "victoria-den", "room_id": "den"},
  "binding": {"content_revision": "…", "registration_version": 0, "manifest_digest": "…",
              "frame_digest": "…", "canonical_collision_digest": "…"},
  "equipment_ref": {"revision": 0, "digest": "…"},
  "revision": 3,
  "base_revision": 2,
  "updated_at": 0.0,
  "geometry": {"kind": "polygon", "vertices_m": [[0,0,0],[0,0,0],[0,0,0],[0,0,0]],
               "centre_m": [0,0,0], "normal_unit": [0,0,1]},
  "seat_m": [0,0,0],
  "seat_method": "zone_centre",
  "source": {"schema": "home-registered-sofa-target-1", "digest": "…"},
  "tombstone": false,
  "physical_commands": false
}
```

**Fields**
- **`kind`:** one of the following.
  - `desk` and `sofa`: at most one of each per room.
  - `place`: any number of later saved places. Each `place` has a stable
    `id` (`victoria-den:place:<slug>`) and an owner-edited `label`.
- **`geometry`:** either a `polygon` (four owner-picked scan corners, sealed
  by the selector) or a `point` (`point_m` plus `normal_unit`, for a single
  saved aim point).
- **`seat_m`:** present only on `sofa` today. It is the polygon centre.
- **`source`:** names the sealed selector record that the geometry came from.
  The record is only valid if re-sealing that source gives the same geometry.
  That is the same refusal the export tool applies (`sofa_target_changed`,
  `desk_target_changed`).
- **Coordinates:** the registered den room frame, in metres, Z up. They are
  **private**: never in the repository, logs or URLs.

## Revision and conflict rules

1. **Revisions:** every save writes `revision = base_revision + 1`. The first
   save has `base_revision: null` and `revision: 1`.
2. **Optimistic concurrency:** a save whose `base_revision` is not the stored
   revision is refused as a **conflict** (HTTP 409, reason
   `named_aim_revision_conflict`). The UI keeps the unsaved mark as a draft
   and shows both versions. It never overwrites, and never auto-merges
   geometry.
3. **Binding:** a record is valid only for its `binding` and `equipment_ref`.
   After a re-scan, a registration change or an equipment revision, a stored
   record is **retired**, not migrated. The UI shows it as "Needs marking
   again" (reason `named_aim_binding_changed`) and never aims at it.
4. **Delete:** deleting writes a tombstone revision (`tombstone: true`,
   geometry removed). This keeps the revision history and makes stale writers
   conflict.
5. **One mark per kind:** saving a `desk` or `sofa` replaces that kind's
   record (by revision); it never adds a second one.
6. **No silent edits:** any change to geometry needs the owner's explicit
   confirmation in the selector, as today ("These corners bound the sofa
   seat"). Labels can change without re-sealing, and still bump `revision`.

## The pinned read-only export

The brain and the activity loop read **files, never the store or the
browser**:

| File | Schema | Writer | Readers |
|---|---|---|---|
| `victoria-den-desk-target.json` | `home-registered-desk-target-1` / `-2` | Home selector (download) | the export tool, the desk aim calculator |
| `victoria-den-sofa-target.json` | `home-registered-sofa-target-1` | Home selector (download) | the export tool |
| `lighting-brain-geometry.json` | `lighting-brain-geometry-1` | `tools/den-room/brain-targets.mjs` | lighting brain (AL-1). The activity loop's zone hint (AL-3) still expects the old `lighting-brain-targets-1` file. See below. |

**How the export is pinned**
- **Identity:** the export carries `binding`, `equipment_ref`,
  `sources.{sofa,desk}.digest` and its own `digest`. A reader that holds a
  different registration must refuse the file.
- **Write-once:** the tool writes a fresh file (`wx`, mode 0600) and never
  overwrites. A changed mark means a new export, followed by an owner-approved
  host step that places it.
- **Read-only:** the store and the UI never write to the export. The UI may
  show only "exported" or "not exported" and an 8-character digest prefix,
  never coordinates.
- **Saved places are not exported yet.** `lighting-brain-geometry-1` has only
  `zones.sofa` and `zones.desk`. A `place` is exported only once the brain
  contract adds it.

**Known gap (AL-3):** `perception-lab/activity_pose.check_zones` reads
`lighting-brain-targets-1` (`targets.{sofa,desk}.zone_vertices_m`). The
planning session folded that file into `lighting-brain-geometry-1`, which keeps
the same polygon keys under `zones.{sofa,desk}`. Either AL-3 reads
`zones.*.zone_vertices_m` and `zone_normal_unit` from the geometry file, or the
tool writes a second file. This is a planning decision.
