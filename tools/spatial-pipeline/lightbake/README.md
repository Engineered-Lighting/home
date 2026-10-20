# lightbake: L3 de-light and light-basis bake

An offline tool that runs on the workstation's RTX 4090, never on the servers. It turns the accepted Den pack into an **additive lighting layer**: de-lit albedo plus a precomputed per-emitter light basis for splats, points and the mesh. L4's runtime blend reads that layer. The owner approved the layer as Q8 on 2026-10-19.

The scan holds **radiance, not albedo**. Its colours record one late-afternoon daylight state, with auto exposure baked in. The tool removes that light before adding simulated light.

## Read-only guarantees

- Every pack file is opened `rb`. The SHA-256 of every file in the pack folder, and of its `.provenance.json` sibling, is taken before and after each step. A step fails if any byte differs.
- Output goes only to `<pack>.lighting/` beside the pack, for example `app/data/home-content/victoria/<revision>.lighting/`. The code refuses to write inside the pack folder. `app/data/` is gitignored.
- `lighting-manifest.json` binds the layer to the pack the same way the equipment registration does: `content_revision`, `registration_version`, and the SHA-256 of `content-pack.json` (canonical JSON), `frame.json` and `collision.glb`. It also binds each keyed asset's SHA-256 and each layer file's SHA-256. `binding.verify_layer` is the load rule: refuse on any mismatch.
- Provenance is `modeled`, `measured: false`, and photometry is `assumed`.
- The layer is private Den data. Never commit it, upload it, or paste imagery or coordinates from it.

## Run

```bash
cd tools/spatial-pipeline
venv/Scripts/python 90_delight_layer.py --equipment <equipment-registration.json>
venv/Scripts/python 91_bake_light_basis.py --equipment <equipment-registration.json>
```

`--equipment` is the equipment registration bound to the same pack. It supplies the four mount positions and the wall planes. Nothing is downloaded.

**Step 90, de-light:**
1. Derive mesh normals; the GLB ships none.
2. Build the ray-tracing scene. Rays are traced against the **uncut** scan surface: display cleanup can cut the rendered primitive, and trimesh keeps the uncut accessors in the same GLB with the same atlas. A **ceiling cap** is added because the Scaniverse mesh has no ceiling. The cap uses ceiling-splat radiance where observed. Elsewhere it is modeled as `L = rho_c/pi * E`, closed form because a flat ceiling doesn't see itself, with `rho_c` assumed (`--ceiling-albedo`, default 0.8).
3. Project normals onto splats and points. On-surface receivers start from the projected surface point.
4. Build window apertures from the exterior splats. Their radiance becomes Lambertian area emitters.
5. Fit the window gain (`--window-gain auto`). Phone auto exposure clips and compresses the exterior, so its splat radiance is a lower bound. One global gain of at least 1 is fitted with a white-patch prior: the 95th-percentile albedo of unclipped, non-emitter splats is 0.85. The gray-card capture (L7) replaces this.
6. Compute capture irradiance: a gather of the captured colours (all bounce orders, per spike 1) plus the window emitters plus any confirmed lamps.
7. Recover albedo as `rho = pi R / max(E, eps)`, clamped to 0.95 and flagged. One eps floor is shared by every receiver and the shader.
8. Run a sensitivity pass over the unanswered capture questions and the modeled ceiling.

**Step 91, bake:**
1. Path-trace 29 unit bases: 4 downlights and 24 radials in lx/lm (assumed Gaussian lobes from `home-den-fixtures.js`), plus 1 window column for capture daylight. The trace uses the scan BVH and the de-lit albedo, with direct light, shadows and 5 bounces.
2. Encode splats and points in the 40 B/receiver format, and the mesh per vertex.
3. Check self-consistency, check the size budget, and write the manifest.

**Why the mesh is per vertex, not lightmapped.** The Scaniverse atlas covers only 15 % of its area with UV triangles. The median face is about half a texel at 2048², so an atlas lightmap or a downsampled albedo atlas would be mostly bleed between islands. The rendered primitive's vertices are about 3 cm apart, so they carry the basis instead. The shader forms albedo at full atlas resolution, per fragment, as `clamp(pi * atlas / max(E_cap, floor), 0, rho_max)`. With no lightmap there is no OIDN pass: per-receiver noise is controlled by sample count.

The four robotic spots are live lights (L5) and are not baked.

## Capture-state parameters (Q1–Q4, unanswered)

| Question | Parameter | Default |
|---|---|---|
| Q1 room lamps on? | `--room-lamps lamps.json` | none (unknown, treated as off) |
| Q1 blinds | `--blinds` | `unknown`. Recorded only; apertures come from the exterior splats actually seen |
| Q3 sun or overcast | `--sky` | `unknown`; no direct-sun term |
| Q2 capture time | `--capture-time` | `unknown` (recorded) |
| Q4 glass-door wall | `--glass-door-wall` | `unknown` (recorded) |
| Exterior exposure | `--window-gain` | `auto`: white-patch fit, at least 1. The value is recorded |
| Unobserved ceiling | `--ceiling-albedo` | 0.8 (assumed) |

Step 90 reports how much the albedo moves under each alternative: ×0.5 and ×2 of the fitted window gain, ceiling albedo 0.6 and 0.9, a ceiling lamp that explains about a third of the light, and, given `--wall-bearings` and `--sun`, direct sun through the detected windows. The report is in `manifest.step90.sensitivity`.

## Layer files

There is one file per field for each receiver set: `splat` (`apartment.ply`), `splat-mobile` (`apartment.mobile.ply`) and `points` (`points.ply`). Each is keyed by vertex index.

| File | Per receiver |
|---|---|
| `*-albedo.bin` | rgb8, sRGB-encoded |
| `*-normal-oct.bin` | 2 × u8, octahedral |
| `*-basis-u8.bin` | 29 × u8, sqrt-encoded |
| `*-basis-scale.bin` | fp16 |
| `*-bounce-tint.bin` | rgb8 |
| `*-flags.bin` | u8 |

That is 40 B per receiver. Decode with `b_k = (u8/255)² · scale · column_scale_k`, `E = tint/lum(tint) · Σ w_k c_k b_k`, then `L = albedo/π · E`. The mesh is keyed by vertex index of `meshes[0].primitives[0]`, at 43 B per vertex:

| File | Per vertex |
|---|---|
| `mesh-capture-irradiance.bin` | fp16 × 3 |
| `mesh-normal-oct.bin` | 2 × u8 |
| `mesh-basis-u8.bin` | 29 × u8 |
| `mesh-basis-scale.bin` | fp16 |
| `mesh-bounce-tint.bin` | rgb8 |
| `mesh-flags.bin` | u8 |

The mesh layer must total 64 MB or less (the lightmap budget).

## Gates

- **Public (CI, synthetic, `python -m unittest discover -s lightbake/tests -t .`):**
  - The spike-1 room is reproduced through the tool's estimator and encoding, with exact-case logRMSE ≤ 0.2.
  - Tracer analytic checks pass.
  - A procedural pack goes through steps 90 and 91 with pack bytes unchanged, 40 B/receiver, mesh layer ≤ 64 MB, provenance `modeled`, capture self-consistency ≤ 0.25 stop, and the load rule refusing a mismatch.
- **Local, real pack:** pack hashes are identical, and re-rendering the capture state from the layer is within 0.25 stop median of the shipped colours (`manifest.metrics`). The clamp-hit fraction is reported. These numbers stay local.
