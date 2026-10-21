/* Ordinary, qualitative equipment preview. The view-ray treatment follows
 * docs/design/virtual-spotlight-rendering.md, without changing the independently
 * admitted live-execution renderer. No light transport or physical authority. */
import * as THREE from 'three';

const MAX_DEPTH_DIMENSION = 1024, MAX_OPAQUE_MESHES = 512, MAX_ROOM_PLANES = 33;

export function displaySpotlightWhite(kelvin) {
    const stops = [[1500, '#ff9451'], [2700, '#ffd0a0'], [4000, '#ffe6cf'], [6500, '#f5f7ff']];
    const value = Math.min(6500, Math.max(1500, kelvin));
    const index = stops.findIndex(stop => stop[0] >= value);
    if (index === 0) return new THREE.Color(stops[0][1]);
    const a = stops[index - 1], b = stops[index];
    return new THREE.Color(a[1]).lerp(new THREE.Color(b[1]), (value - a[0]) / (b[0] - a[0]));
}

/** One bounded opaque-depth capture for every equipment beam in a frame.
 * Wrappers borrow the current resident materials, including their cutaway and
 * alpha-test shaders. Neither materials nor resident visibility are modified. */
export function createEquipmentSpotlightDepth(excludedRoot) {
    const scene = new THREE.Scene(), wrappers = new Map(), size = new THREE.Vector2();
    const viewport = new THREE.Vector4(), scissor = new THREE.Vector4(), clearColor = new THREE.Color();
    let target = null, disposed = false, rendererOwner = null, lastCamera = null, lastScene = null, lastFrame = -1;
    let status = 'not_rendered', passes = 0, meshCount = 0;
    function reset() {
        target?.dispose(); target = null; scene.clear(); wrappers.clear();
        rendererOwner = null; lastCamera = null; lastScene = null; lastFrame = -1; meshCount = 0;
        status = disposed ? 'disposed' : 'not_rendered';
    }
    function capture(renderer, residentScene, camera) {
        if (disposed || !renderer.getDrawingBufferSize || !renderer.getRenderTarget || !renderer.setRenderTarget) return null;
        const frame = renderer.info?.render?.frame;
        if (target && frame === lastFrame && renderer === rendererOwner && camera === lastCamera && residentScene === lastScene) return status === 'bounded_visible_opaque_depth' ? target.depthTexture : null;
        if (rendererOwner && rendererOwner !== renderer) reset();
        rendererOwner = renderer; lastCamera = camera; lastScene = residentScene;
        renderer.getDrawingBufferSize(size);
        const width = Math.max(1, Math.min(MAX_DEPTH_DIMENSION, size.x));
        const height = Math.max(1, Math.min(MAX_DEPTH_DIMENSION, size.y));
        if (!target) {
            target = new THREE.WebGLRenderTarget(width, height, { minFilter: THREE.NearestFilter, magFilter: THREE.NearestFilter });
            target.depthTexture = new THREE.DepthTexture(width, height, THREE.UnsignedIntType);
        } else if (target.width !== width || target.height !== height) target.setSize(width, height);
        scene.clear(); meshCount = 0;
        const current = new Set();
        residentScene.traverseVisible(node => {
            for (let ancestor = node; ancestor; ancestor = ancestor.parent) if (ancestor === excludedRoot) return;
            const materials = Array.isArray(node.material) ? node.material : [node.material];
            if (!node.isMesh || !materials.length || materials.some(material => !material || !material.visible || material.transparent || !material.depthWrite)) return;
            if (++meshCount > MAX_OPAQUE_MESHES) return;
            let proxy = wrappers.get(node);
            if (!proxy || proxy.geometry !== node.geometry) {
                proxy = node.clone(false); proxy.matrixAutoUpdate = false; proxy.frustumCulled = false;
                proxy.onBeforeRender = () => {}; proxy.onAfterRender = () => {};
                wrappers.set(node, proxy);
            }
            proxy.material = node.material; proxy.matrix.copy(node.matrixWorld); proxy.layers.mask = node.layers.mask;
            scene.add(proxy); current.add(node);
        });
        for (const node of wrappers.keys()) if (!current.has(node)) wrappers.delete(node);
        if (meshCount > MAX_OPAQUE_MESHES) { status = 'unavailable_mesh_budget'; lastFrame = renderer.info?.render?.frame; return null; }
        const previousTarget = renderer.getRenderTarget(), previousCubeFace = renderer.getActiveCubeFace?.() ?? 0;
        const previousMip = renderer.getActiveMipmapLevel?.() ?? 0;
        const previousAutoClear = renderer.autoClear, previousScissorTest = renderer.getScissorTest();
        const previousClearAlpha = renderer.getClearAlpha(); renderer.getClearColor(clearColor);
        renderer.getViewport(viewport); renderer.getScissor(scissor);
        try {
            renderer.setRenderTarget(target); renderer.setViewport(0, 0, width, height); renderer.setScissorTest(false);
            renderer.setClearColor(0, 0); renderer.autoClear = true; renderer.render(scene, camera); passes++;
        } finally {
            renderer.setRenderTarget(previousTarget, previousCubeFace, previousMip);
            renderer.setViewport(viewport); renderer.setScissor(scissor); renderer.setScissorTest(previousScissorTest);
            renderer.setClearColor(clearColor, previousClearAlpha); renderer.autoClear = previousAutoClear;
            // Nested render advances Three's frame counter. Store its final
            // value so the remaining equipment beams reuse this exact capture.
            lastFrame = renderer.info?.render?.frame;
        }
        status = 'bounded_visible_opaque_depth';
        return target.depthTexture;
    }
    return { capture, reset, size,
        readback: () => ({ status, disposed, target_count: target ? 1 : 0, passes, opaque_mesh_count: meshCount,
            max_dimension: MAX_DEPTH_DIMENSION, max_opaque_meshes: MAX_OPAQUE_MESHES }),
        dispose() { if (disposed) return; disposed = true; reset(); },
    };
}

/** A registered reference plane is not necessarily a visible scanned surface.
 * Keep the illustrative wash only where the current opaque scan agrees with
 * that plane. In particular, a cutaway wall cannot leave a floating light card.
 * The 2–8 cm blend is a display intersection tolerance, not registration proof. */
export function attachEquipmentReceiverDepth(receiver, depth) {
    const uniforms = { uReceiverSceneDepth: { value: null }, uReceiverViewport: { value: new THREE.Vector2() },
        uReceiverProjectionInverse: { value: new THREE.Matrix4() }, uReceiverDepthValid: { value: 0 } };
    receiver.material.onBeforeCompile = shader => {
        Object.assign(shader.uniforms, uniforms);
        shader.vertexShader = shader.vertexShader.replace('#include <common>', '#include <common>\nvarying float vEquipmentReceiverViewZ;')
            .replace('#include <project_vertex>', '#include <project_vertex>\nvEquipmentReceiverViewZ = -mvPosition.z;');
        shader.fragmentShader = shader.fragmentShader.replace('#include <common>', `#include <common>
            varying float vEquipmentReceiverViewZ;
            uniform sampler2D uReceiverSceneDepth;
            uniform vec2 uReceiverViewport;
            uniform mat4 uReceiverProjectionInverse;
            uniform float uReceiverDepthValid;`)
            .replace('#include <dithering_fragment>', `#include <dithering_fragment>
                if (uReceiverDepthValid < .5) discard;
                vec2 receiverScreen = gl_FragCoord.xy / uReceiverViewport;
                float receiverDepth = texture2D(uReceiverSceneDepth, receiverScreen).x;
                if (receiverDepth >= 1.0) discard;
                vec4 receiverView = uReceiverProjectionInverse * vec4(receiverScreen*2.0-1.0, receiverDepth*2.0-1.0, 1.0);
                float receiverSeparation = abs(-receiverView.z/receiverView.w-vEquipmentReceiverViewZ);
                gl_FragColor.a *= 1.0-smoothstep(.02,.08,receiverSeparation);
                if (gl_FragColor.a < .0005) discard;`);
    };
    receiver.material.customProgramCacheKey = () => 'equipment-visible-receiver-1';
    receiver.onBeforeRender = (renderer, scene, camera) => {
        uniforms.uReceiverDepthValid.value = 0;
        const texture = depth.capture(renderer, scene, camera);
        if (!texture) return;
        uniforms.uReceiverSceneDepth.value = texture; uniforms.uReceiverViewport.value.copy(depth.size);
        uniforms.uReceiverProjectionInverse.value.copy(camera.projectionMatrixInverse);
        uniforms.uReceiverDepthValid.value = 1;
    };
    receiver.userData.receiving_surface = 'visible_opaque_depth_required';
    return receiver;
}

const vertexShader = /* glsl */`
    varying vec3 vBeamPosition;
    uniform float uLength;
    uniform float uRadius;
    void main() {
        vBeamPosition = position * vec3(uRadius, uLength, uRadius);
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
    }
`;

const fragmentShader = /* glsl */`
    precision highp float;
    varying vec3 vBeamPosition;
    uniform vec3 uCameraLocal;
    uniform vec3 uLightColor;
    uniform float uLevel;
    uniform float uLength;
    uniform float uRadius;
    uniform float uTanAngle;
    uniform sampler2D uSceneDepth;
    uniform vec2 uViewportSize;
    uniform mat4 uProjectionInverse;
    uniform mat4 uViewToLocal;
    uniform float uSceneDepthValid;
    uniform mat4 uBeamToRoom;
    uniform vec4 uRoomPlanes[33];
    uniform int uRoomPlaneCount;
    float safeAxis(float value) { return value < 0.0 ? -max(abs(value), 0.00001) : max(abs(value), 0.00001); }
    void main() {
        if (uLevel <= 0.0 || uSceneDepthValid < 0.5) discard;
        vec3 ray = normalize(vBeamPosition - uCameraLocal);
        vec3 inverseRay = 1.0 / vec3(safeAxis(ray.x), safeAxis(ray.y), safeAxis(ray.z));
        vec3 lo = (vec3(-uRadius, -0.5*uLength, -uRadius)-uCameraLocal)*inverseRay;
        vec3 hi = (vec3(uRadius, 0.5*uLength, uRadius)-uCameraLocal)*inverseRay;
        vec3 nearBounds = min(lo,hi), farBounds = max(lo,hi);
        float entry = max(0.0,max(nearBounds.x,max(nearBounds.y,nearBounds.z)));
        float exit = min(length(vBeamPosition-uCameraLocal),min(farBounds.x,min(farBounds.y,farBounds.z)));
        vec2 screen = gl_FragCoord.xy/uViewportSize;
        float depth = texture2D(uSceneDepth,screen).x;
        if(depth < 1.0) {
            vec4 view = uProjectionInverse*vec4(screen*2.0-1.0,depth*2.0-1.0,1.0);
            vec3 surface = (uViewToLocal*vec4(view.xyz/view.w,1.0)).xyz;
            exit = min(exit,dot(surface-uCameraLocal,ray));
        }
        if(exit <= entry) discard;
        const int STEPS = 32;
        float stepLength = (exit-entry)/float(STEPS), opticalDepth = 0.0;
        for(int i=0;i<STEPS;i++) {
            float distanceToEye = entry+(float(i)+0.5)*stepLength;
            vec3 point = uCameraLocal+ray*distanceToEye;
            vec3 roomPoint = (uBeamToRoom*vec4(point,1.0)).xyz;
            float roomKeep = 1.0;
            for(int j=0;j<33;j++) {
                if(j>=uRoomPlaneCount) break;
                roomKeep *= step(uRoomPlanes[j].w-0.001,dot(uRoomPlanes[j].xyz,roomPoint));
            }
            float axial = 0.5*uLength-point.y;
            float radius = max(0.012,axial*uTanAngle);
            float radial = length(point.xz)/radius;
            float penumbra = 1.0-smoothstep(0.48,1.0,radial);
            float endFade = 1.0-smoothstep(0.50*uLength,uLength,axial);
            float sourceFade = smoothstep(0.015,0.10,axial);
            float inside = step(0.0,axial)*step(axial,uLength)*roomKeep;
            float falloff = 1.0/(1.0+0.42*axial*axial);
            float nearFade = smoothstep(0.02,0.20,distanceToEye);
            opticalDepth += penumbra*penumbra*endFade*sourceFade*inside*falloff*nearFade*stepLength;
        }
        float forward = pow(max(0.0,dot(-ray,vec3(0.0,-1.0,0.0))),4.0);
        float alpha = min((1.0-exp(-opticalDepth*1.65))*1.35*(0.65+0.35*forward),0.32)*uLevel;
        if(alpha<0.0005) discard;
        gl_FragColor = vec4(uLightColor,alpha);
        #include <tonemapping_fragment>
        #include <colorspace_fragment>
    }
`;

export function createEquipmentSpotlightVisual(parent, depth, item) {
    const uniforms = { uCameraLocal: { value: new THREE.Vector3() }, uLightColor: { value: new THREE.Color() },
        uLevel: { value: 0 }, uLength: { value: 1 }, uRadius: { value: 1 }, uTanAngle: { value: .2 },
        uSceneDepth: { value: null }, uViewportSize: { value: new THREE.Vector2() }, uProjectionInverse: { value: new THREE.Matrix4() },
        uViewToLocal: { value: new THREE.Matrix4() }, uSceneDepthValid: { value: 0 }, uBeamToRoom: { value: new THREE.Matrix4() },
        uRoomPlanes: { value: Array.from({ length: MAX_ROOM_PLANES }, () => new THREE.Vector4()) }, uRoomPlaneCount: { value: 0 } };
    const planes = [{ normal: item.floor.normal, offset: -item.floor.constant }, ...item.walls];
    planes.forEach((plane, index) => uniforms.uRoomPlanes.value[index].set(...plane.normal.toArray(), plane.offset));
    uniforms.uRoomPlaneCount.value = planes.length;
    const material = new THREE.ShaderMaterial({ uniforms, vertexShader, fragmentShader,
        transparent: true, depthWrite: false, depthTest: false, side: THREE.BackSide,
        blending: THREE.AdditiveBlending, toneMapped: false });
    const volume = new THREE.Mesh(new THREE.ConeGeometry(1, 1, 64, 1, false), material);
    volume.name = 'modeled-aim-volume'; volume.renderOrder = 21; parent.add(volume);
    volume.userData = { presentation: 'qualitative_scattering', scattering_steps: 32, receiving_surface: false };
    const inverse = new THREE.Matrix4(), physicalScale = new THREE.Matrix4();
    let disposed = false;
    material.addEventListener('dispose', () => { disposed = true; });
    volume.onBeforeRender = (renderer, scene, camera) => {
        if (disposed) return;
        uniforms.uSceneDepthValid.value = 0;
        const texture = depth.capture(renderer, scene, camera);
        if (!texture) { volume.userData.depth_status = depth.readback().status; return; }
        inverse.copy(volume.matrixWorld).invert();
        physicalScale.makeScale(uniforms.uRadius.value, uniforms.uLength.value, uniforms.uRadius.value);
        inverse.premultiply(physicalScale);
        camera.getWorldPosition(uniforms.uCameraLocal.value).applyMatrix4(inverse);
        uniforms.uSceneDepth.value = texture; uniforms.uViewportSize.value.copy(depth.size);
        uniforms.uProjectionInverse.value.copy(camera.projectionMatrixInverse);
        uniforms.uViewToLocal.value.multiplyMatrices(inverse, camera.matrixWorld);
        uniforms.uSceneDepthValid.value = 1; volume.userData.depth_status = depth.readback().status;
    };
    const glowMaterial = new THREE.ShaderMaterial({
        uniforms: { uColor: { value: new THREE.Color() }, uLevel: { value: 0 } },
        vertexShader: `varying vec2 vUv;void main(){vUv=uv;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0);}`,
        fragmentShader: `varying vec2 vUv;uniform vec3 uColor;uniform float uLevel;
            void main(){float r=length(vUv-.5)*2.;float a=exp(-r*r*6.)*(1.-smoothstep(.6,1.,r))*uLevel;
            gl_FragColor=vec4(mix(uColor,vec3(1.),.25),a*.6);
            #include <tonemapping_fragment>
            #include <colorspace_fragment>
            }`, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, toneMapped: false,
    });
    const glow = new THREE.Mesh(new THREE.PlaneGeometry(.08, .08), glowMaterial);
    glow.name = 'modeled-aim-source-glow'; glow.renderOrder = 22; parent.add(glow);
    const parentRotation = new THREE.Quaternion(), cameraRotation = new THREE.Quaternion();
    glow.onBeforeRender = (_renderer, _scene, camera) => {
        glow.parent.getWorldQuaternion(parentRotation).invert(); camera.getWorldQuaternion(cameraRotation);
        glow.quaternion.copy(parentRotation).multiply(cameraRotation); glow.updateWorldMatrix(false, false);
    };
    let displayGain = 1, channelLevel = 0;
    const paintLevel = () => { uniforms.uLevel.value = channelLevel * displayGain; glowMaterial.uniforms.uLevel.value = uniforms.uLevel.value; };
    return { volume, glow,
        setDisplayGain(value) { displayGain = Math.max(0, Math.min(1, value)); paintLevel(); },
        update(origin, direction, reach, fullAngle, channel) {
            const length = Math.max(.001, reach), tanAngle = Math.tan(fullAngle*Math.PI/360), radius = length*tanAngle;
            volume.position.copy(origin).addScaledVector(direction, length/2);
            volume.quaternion.setFromUnitVectors(new THREE.Vector3(0,-1,0), direction); volume.scale.set(radius,length,radius);
            uniforms.uLength.value = length; uniforms.uRadius.value = radius; uniforms.uTanAngle.value = tanAngle;
            channelLevel = channel.on ? channel.brightness : 0; paintLevel();
            uniforms.uLightColor.value.copy(displaySpotlightWhite(channel.color_temp_kelvin));
            uniforms.uBeamToRoom.value.compose(volume.position.clone().add(item.anchor), volume.quaternion, new THREE.Vector3(1,1,1));
            glow.position.copy(origin); glowMaterial.uniforms.uColor.value.copy(uniforms.uLightColor.value);
            glowMaterial.uniforms.uLevel.value = uniforms.uLevel.value;
            volume.visible = glow.visible = channel.on && channel.brightness > 0 && reach > .001;
        },
    };
}
