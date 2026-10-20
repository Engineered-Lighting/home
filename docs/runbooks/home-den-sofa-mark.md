# Mark the den sofa for the lighting brain

The lighting brain's reading spotlight needs the sofa zone and seat. The owner
marks them once in Home, the same way as the desk target
([home-den-desk-target.md](home-den-desk-target.md)). Keep every file below
private. Values never go into the repository.

## Mark the sofa in Home

1. Open Apartment with Victoria selected, in a room view where the sofa seat is
   visible.
2. Under **Virtual spotlights**, choose **Mark sofa**. The detailed registered
   mesh loads first, as for the desk.
3. Click the four corners of the sofa seat in order around its edge. A drag adds
   no corner. Escape or **Clear selection** cancels.
4. Confirm that the markers bound the sofa seat, then choose **Save sofa mark**.
   Home prepares a private download named `victoria-den-sofa-target.json`. Keep
   it beside `victoria-den-desk-target.json`.

The record (`home-registered-sofa-target-1`) reuses the desk target's sealing.
It keeps the exact picked corners, their normals, and the scan, frame,
collision and equipment identity. It stores:

- **`zone`:** the seat polygon, with its centre and normal;
- **`seat`:** the point the brain treats as the seat. For now this is the
  polygon centre (`method: zone_centre`).

## Export it for the brain

On the workstation, write the brain's one private geometry file from the
owner's downloads and the saved equipment registration export:

```text
node tools/den-room/brain-targets.mjs --sofa ABSOLUTE_SAVED_SOFA_JSON --equipment ABSOLUTE_SAVED_EQUIPMENT_EXPORT --out FRESH_PRIVATE_JSON [--desk ABSOLUTE_SAVED_DESK_JSON] [--sofa-near ID,ID] [--desk-near ID,ID]
```

The tool:

- **checks the equipment export:** it must be a valid saved registration;
- **checks the marks:** it re-seals the sofa and desk records and refuses one
  that was changed. It also refuses a mark from a different scan or equipment
  revision than the export;
- **derives the four fixtures** through the same virtual fixture model that
  Home's `aimAtTarget` uses;
- **never overwrites** an existing output.

The console prints only digests, input hashes, counts and fixture IDs. It never
prints coordinates.

### Near fixtures

`near_fixtures` lists the fixtures whose ambient each zone lights. The defaults
follow the wall sides in the activity-lighting contract:

| Zone | Default near fixtures | Wall side |
|---|---|---|
| sofa | `victoria-den-proposed-fixture-02`, `victoria-den-mount-03-virtual-fixture` | wall B |
| desk | `victoria-den-proposed-fixture-01`, `victoria-den-mount-04-virtual-fixture` | wall A |

`--sofa-near` and `--desk-near` replace a default with a comma-separated list
of contract fixture IDs.

The summary's `near_check` compares each zone's list with the two fixtures
nearest its seat, measured horizontally. It shows IDs only. If it reports
`differs_from_two_nearest`, check the choice. Then re-run with the flag if
needed, to a fresh output.

## Export format: `lighting-brain-geometry-1`

The brain (AL-1, `stack/services/lighting-brain/README.md`, "Geometry export")
reads exactly this file. The keys after `zones` are extra identity and are
ignored by its parser.

```json
{
  "schema": "lighting-brain-geometry-1", "home": "victoria", "room": "den", "frame": "victoria-den",
  "camera_m": [0,0,0],
  "fixtures": [
    {"fixture_id": "victoria-den-proposed-fixture-01", "aim_origin_m": [0,0,0],
     "horizontal_world_reference": [0,1,0],
     "limits": {"pan_min_deg": -180, "pan_max_deg": 180, "tilt_min_deg": -90, "tilt_max_deg": 90}}
  ],
  "zones": {
    "sofa": {"seat_m": [0,0,0], "near_fixtures": ["…"], "seat_method": "zone_centre",
             "zone_vertices_m": [[0,0,0],[0,0,0],[0,0,0],[0,0,0]], "zone_centre_m": [0,0,0], "zone_normal_unit": [0,0,1]},
    "desk": {"seat_m": null, "near_fixtures": ["…"]}
  },
  "frame_convention": "z_up_metric_floor0", "unit": "m",
  "binding": {"content_revision": "…", "registration_version": 0, "manifest_digest": "…",
              "frame_digest": "…", "canonical_collision_digest": "…"},
  "equipment_ref": {"revision": 0, "digest": "…"},
  "generated_at": 0.0,
  "sources": {"sofa": {"schema": "home-registered-sofa-target-1", "digest": "…"}},
  "truth_role": "owner_marked_targets_and_virtual_fixture_model", "physical_commands": false,
  "digest": "sha256 of the canonical body without digest"
}
```

- **Coordinates:** the registered den room frame, in metres (Z up), as in the
  activity-lighting contract.
- **`fixtures`:** always the four contract fixture IDs, in contract order.
  - `aim_origin_m` and `horizontal_world_reference` are Home's descriptor
    values: `display_origin_m` and `horizontal_world_reference`. The brain's
    pan/tilt check therefore matches the renderer.
  - `limits` holds the virtual preview limits.
- **`camera_m`:** the registered OAK-D record's position, or `null` when the
  registration has none.
- **`zones.desk.seat_m`:**
  - with `--desk`, the desk centre, plus the desk polygon as extra keys;
  - without it, `null`.
- **`binding` and `equipment_ref`:** these let the brain or an operator refuse
  a file made against a different registration.

Placing the file on the LA box is a host operation, part of AL-6. It needs the
owner's approval in the planning session.
