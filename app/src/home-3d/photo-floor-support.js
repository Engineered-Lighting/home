/* Display-only support from the registered, textured scan mesh. Gaussian
 * basis centers can lie below the floor and cannot safely define a room crop. */
export function createPhotoFloorUniforms() {
    return { uPhotoFloorOnly: { value: 0 } };
}

/** Call after injectMeshCutaway: vDollhouseLocal is the registered Z-up frame. */
export function injectPhotoFloorSupport(shader, uniforms) {
    Object.assign(shader.uniforms, uniforms);
    shader.fragmentShader = shader.fragmentShader
        .replace('#include <common>', '#include <common>\nuniform float uPhotoFloorOnly;')
        .replace('#include <dithering_fragment>', `#include <dithering_fragment>
if (uPhotoFloorOnly > 0.5) {
    vec3 floorFace = cross(dFdx(vDollhouseLocal), dFdy(vDollhouseLocal));
    float faceLength = length(floorFace);
    // Match the registered scan's existing lower floor allowance; some real
    // floor triangles dip below -10 cm. Keep the upper band clear of furniture.
    if (vDollhouseLocal.z < -0.25 || vDollhouseLocal.z > 0.10 || faceLength < 1e-10
        || abs(floorFace.z) < 0.75 * faceLength) discard;
}`);
}
