# Continuous body surface replacement

## Owner scope change: shelved

The owner rejected this candidate as unnatural and chose skeletal tracking only
for now. Stop body fitting, mesh integration and body shadow work. Keep #410's
skeletal presentation and its independent delivery/timing fixes. Draft #412 and
the isolated human preview are not eligible for integration or deployment.
Research and private evidence remain retained. The following is historical
candidate documentation, not an active implementation plan or acceptance.

## Decision and current status

The owner rejected the capsule/ellipsoid screenshot. The requested appearance is
a continuous, anatomically plausible monochrome human form, approximately sized
to its subject, without requiring personal likeness. The diagnostic capsule
runtime in draft #412 is not accepted as completing this outcome.

A private, isolated browser candidate now uses an actual skinned anatomical
surface. It has not replaced the Home runtime. No production pack, registration,
service, neural model or persisted field was changed. The held capsule package
remains withdrawn from visual acceptance and deployment.

## Source and reproducible conversion

Use the CC0 MakeHuman/MPFB2 assets bundled in NAVER Anny at
[`d6fc027ced5c17b6b0775dee944096ade7a9ef80`](https://github.com/naver/anny/tree/d6fc027ced5c17b6b0775dee944096ade7a9ef80/src/anny/data/mpfb2).
The [asset licence](https://github.com/naver/anny/blob/d6fc027ced5c17b6b0775dee944096ade7a9ef80/src/anny/data/mpfb2/LICENSE.md)
is distinct from Anny's Apache code licence and its separately restricted
SMPL-X topology downloads. This candidate uses none of those restricted assets.

The bounded preparation fetched only `base.obj`, `rig.default.json`,
`weights.default.json` and the asset licence, verifying exact sizes and Git blob
IDs against the pinned tree and recording SHA-256. No package install, PyTorch,
Blender, full model bundle or live host work was needed.

The converter resolves source VERTEX/MEAN/CUBE anchors before removing helpers,
uses only the exact `body` face group, and applies the source X+90-degree,
0.1-scale conversion to Z-up metres. See the
[first-party loader](https://github.com/naver/anny/blob/d6fc027ced5c17b6b0775dee944096ade7a9ef80/src/anny/models/full_model.py).
The raw template is not equivalent to Anny's phenotype-evaluated adult model.

The visual candidate deliberately aggregates the source weights to a 17-bone
presentation palette. One offline Catmull-Clark step interpolates positions and
weights with the same stencil. Four-influence normalization discards at most
0.8763% of the aggregate weight mass. The resulting surface has 53,514 vertices
and 107,024 triangles. This is a distinct nominal MakeHuman-derived variant,
not exact reproduction of Anny's full rig, learned fit or pose correctives.

Three's [SkinnedMesh](https://threejs.org/docs/pages/SkinnedMesh.html) performs
surface deformation using reusable geometry and inverse binds. A monochrome
[physically based material](https://threejs.org/docs/pages/MeshStandardMaterial.html)
receives the isolated preview's actual modeled lights; its spotlight shadow is
rendered on an authored plane, independently toggleable from light intensity.

## Evidence and limits

Private source, hashes, renderer, unit tests and screenshots are retained under
the owned body worktree's `.tmp/human-candidate/`. Browser inspection covers
standing, seated, crouched and raised arms, including side views, moving arms,
spotlight intensity and shadow toggles. Two focused source tests pass: finite
normalized weights/triangles/provenance, and finite standing/seated deformation,
single coordinate translation and repeated geometry reuse.

This is a visual candidate driven by authored complete poses. It is not a live
body fit, room registration, measured size, sustained performance receipt,
Apartment spotlight integration or dynamic shadow on a real scan/splat.
Retargeting, shoulder/hip proportions and uncertainty presentation still need
review. Sparse camera surface joints do not supply anatomical joint rotations,
hidden limbs, foot contact or temporal identity. These must not be invented from
per-frame detector IDs to make the presentation look complete.

Next integrate the accepted surface through the existing fenced Home consumer,
with explicit partial/inferred behavior and bounded per-person buffers; qualify
the existing room's actual lighting/shadow path and compare GPU costs. Keep
source expiry, timing admission and physical authority restrictions intact.
Use the acceptance correction in `docs/contracts/den/body-form-v1.md`; do not
promote this isolated preview to completed Home or actual-room acceptance.
