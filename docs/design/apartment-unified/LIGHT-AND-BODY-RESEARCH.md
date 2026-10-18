# Light and body simulation: research notes

October 2026. The prototype's **Lit** view is Tier 0 below. Everything past Tier 0 is a recommendation.

## What makes a simulated room read as real
1. **Photometric sources.** Each channel's beam shape and output in real units, and its color temperature.
2. **Visibility.** Soft shadows from walls, furniture and the person, updated live.
3. **Bounce.** The radial zones exist to light walls so the wall lights the room. Without indirect light, "Toward walls" looks like almost nothing happened.

Materials cap all three. The scan gives albedo; roughness is assumed.

## Techniques available in the browser
- **Shadow-mapped spot lights** (WebGL or WebGPU). Direct light and soft shadows from any mesh, including a body proxy. Real time; each shadow-casting light renders the scene once more. This is what Lit uses.
- **IES profiles.** three.js `IESSpotLight` takes intensity in candela and an IES lookup texture, and works only with `WebGPURenderer`. Until recently it sampled only one axis of the profile; a September 2026 pull request adds 2D and symmetric profiles. Sources: [IESSpotLight docs](https://threejs.org/docs/pages/IESSpotLight.html), [issue #34560](https://github.com/mrdoob/three.js/issues/34560), [PR #34626](https://github.com/mrdoob/three.js/pull/34626).
- **Screen-space GI** (`SSGINode`, WebGPU/TSL). Indirect diffuse and AO from screen-space data. Cost per pixel is slice count × step count × 2. It misses surfaces that are off screen, which matters when the lit wall is behind the camera. Source: [SSGINode docs](https://threejs.org/docs/pages/SSGINode.html).
- **Probe volumes or SDF cone tracing.** World-space bounce that includes off-screen walls. Custom work today; see the [three.js GI discussion](https://discourse.threejs.org/t/global-illumination/91243).
- **Volumetric haze.** The three.js WebGPU example ray-marches as a post-process and respects native lights and shadows, which makes the spot's cone visible. A froxel grid is the cheaper approach at scale. Sources: [example](https://threejs.org/examples/webgpu_volume_lighting.html), [forum](https://discourse.threejs.org/t/volumetric-lighting-in-webgpu/87959).
- **Progressive path tracing.** Ground truth for bounce and soft shadows, but noisy until it converges, so it suits a still rather than live interaction ([three-gpu-pathtracer](https://github.com/gkjohnson/three-gpu-pathtracer), [erichlof renderer](https://erichlof.github.io/THREE.js-PathTracing-Renderer/)).
- **Relightable Gaussian splats.** Still research. R3DG bakes visibility with BVH ray tracing; GS³ needs hundreds to thousands of one-light-at-a-time captures; GHPT reconstructs a mesh and path-traces on it. Sources: [R3DG](https://nju-3dv.github.io/projects/Relightable3DGaussian/), [GS³](https://dl.acm.org/doi/10.1145/3680528.3687576), [GHPT](https://nwu-vislab.github.io/GHPT/). Takeaway: relight a mesh proxy and keep the cloud or splat as the unlit "how it looks" layer.

## Recommended pipeline
**Tier 0, prototype (now).** WebGL, physically based spot lights, ACES tone mapping. Spot and Down cast 1024² PCF soft shadows; each radial zone is its own unshadowed lobe. Materials are assumed colors; the body is a capsule proxy that casts shadows. Not photometric.

**Tier 1, production real time (WebGPU).**
- One IES file per channel, measured on the fixture: spot at each zoom step, down, and one radial zone. Intensity in candela from lumen output × the driver's brightness curve.
- Shadows: spot 2048² with softness from emitter size, down 1024², radial one 512² cube shadow per fixture shared by its six zones.
- Bounce: SSGI first, plus a baked probe volume per room for off-screen walls.
- Materials: albedo from the scan's photo atlas (already sampled for the cloud) and roughness by surface class, calibrated once against a gray card seen by the Den camera.
- Readout: lux at each named aim, which photometric units make cheap, shown in the Aim tab.
- Budget: 60 fps on desktop with up to 8 shadow-casting lights; fall back to Tier 0 without WebGPU.

**Tier 2, Render.** A progressive path trace of the current or proposed scene with the same IES files, for a still that can be compared with the camera frame.

**Validation.** Render from the calibrated Den camera pose and compare with real frames at known light states. Tune exposure and albedo until they match.

## What Lit does now
- **Units.** Each channel has an assumed lumen rating (spot 450 lm, down 350, radial zone 120, ceiling 800, plug 400), converted to candela from its beam angle with I = Φ / (2π(1 − cos θ)). A fixed night exposure replaces hand-tuned brightness, so a narrow spot reads brighter than a wide wash of the same output, as it does in the room.
- **Bounce.** One bounce by instant radiosity: the eight strongest lit patches (the spot's target, the Down pool, each lit zone's wall) become hemispherical lights carrying the reflected flux (incoming flux × assumed albedo). Lighting a wall now lights the room around it.
- **Shadow softness.** Spot shadows are crisp (small aperture); Down shadows are soft (diffuser).
- **Limits.** Bounce lights cast no shadows, so bounce can leak up to 4.5 m through a wall. Albedos and lumen ratings are assumptions, and there are no IES beam shapes yet.

## Making it accurate
In order of payoff:
1. **Measure the fixtures.** One IES file per channel (spot per zoom step, down, one radial zone), plus lumens against brightness. Everything downstream inherits these numbers.
2. **Calibrate against the camera.** With the Den camera calibrated to the mesh, capture each channel alone at known levels. Fit wall, floor and furniture albedo and each channel's output until the render matches the frames. Target: wall luminance within about 15%, shadow edges within a few centimetres.
3. **WebGPU renderer.** IES spot lights with 2D profiles, emitter-size soft shadows, and shadowed bounce (reflective shadow maps, or a per-room probe volume with visibility) so bounce stops leaking through walls. Screen-space GI adds near-field bounce and contact shadows.
4. **Body.** Replace the capsules with a fitted body mesh as the shadow caster, and use the same mesh for the aim solver's occlusion test.
5. **Ground truth.** A progressive path-traced still with the same IES files and materials, to check the real-time view against, and to preview a scene before it's saved.

## Body volume
**Prototype.** Eleven capsules and a head sphere around the pose joints, with radii for an average adult. They cast shadows in Lit. The aim solver also tests each beam against them, names the part in the way ("Your head shadows it"), marks the hit and offers a light that clears it.

**Production.**
- Fit a parametric body mesh per person from the camera: shape once and cached per person, pose per frame.
- Licensing: SMPL and SMPL-X model files are licensed for non-commercial research; commercial use needs a license via Meshcapade ([SMPL-X license](https://smpl-x.is.tue.mpg.de/modellicense.html)). Apache-2.0 alternatives include Meta's MHR and Naver's Anny ([write-up](https://clad.you/blog/posts/body-pipeline/)).
- Speed: Multi-HMR's small model runs at 30 fps on a V100 ([paper](https://www.ecva.net/papers/eccv_2024/papers_ECCV/papers/03364.pdf)); SAT-HMR reports about 24 fps ([paper](https://arxiv.org/html/2411.19824v4)); Fast SAM 3D Body reports about 65 ms per frame on an RTX 5090 ([paper](https://arxiv.org/pdf/2603.15603v1)). The Den's perception hardware isn't known to this design. If it can't keep up, fit the mesh a few times a second and drive it between fits with the skeleton.
- Sensors: mmWave gives presence, position and motion, not shape. Use it to hold the last body when the camera loses the person, and to know when nobody is there.
- Solver rules the body enables: keep the beam at least 10 cm clear of the head and shoulders; never aim into the eyes (reject beams that pass the face within about 30° of the gaze direction); for reading, prefer a fixture behind and above the reading shoulder so the pool lands on the page and the head doesn't shadow it.
- Privacy: only joint and shape parameters leave the perception service. No imagery is stored.
