import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import * as THREE from 'three';
import { createDeskTargetCapture, sealRegisteredDeskTarget, validateRegisteredDeskTarget } from './registered-desk-target.mjs';
import { createPicking } from './picking.js';

const copy = value => JSON.parse(JSON.stringify(value));
function canonical(value) {
    if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
    if (value && typeof value === 'object') return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`;
    return JSON.stringify(value);
}
const digest = value => createHash('sha256').update(canonical(value)).digest('hex');
const binding = { content_revision: 'authored-registered-room', registration_version: 1,
    manifest_digest: 'a'.repeat(64), frame_digest: 'b'.repeat(64), canonical_collision_digest: 'c'.repeat(64) };
const equipment_ref = { revision: 2, digest: 'd'.repeat(64) };
const normal = [-.2/Math.hypot(.2,1),0,1/Math.hypot(.2,1)];
const points = [[0,0,1],[2,0,1.4],[2,1,1.4],[0,1,1]];
const corners = () => points.map(point_m => ({ point_m: [...point_m], normal_unit: [...normal] }));
const seal = values => sealRegisteredDeskTarget({ binding, equipment_ref, corners: values, confirmed: true }, digest);

function displayMeshFixture(asset_name = 'mesh.glb') {
    const manifest = { siteId: 'victoria', revision: binding.content_revision, registrationVersion: binding.registration_version,
        assets: { 'frame.json': { sha256: binding.frame_digest }, 'collision.glb': { sha256: binding.canonical_collision_digest },
            'mesh.glb': { sha256: 'e'.repeat(64) }, 'mesh.mobile.glb': { sha256: 'f'.repeat(64) } } };
    const current = { ...binding, manifest_digest: digest(manifest) };
    return { manifest, binding: current, surface_ref: { asset_name, asset_sha256: manifest.assets[asset_name].sha256,
        manifest_digest: current.manifest_digest } };
}

test('version 2 retains exact display-mesh corners and validates against the independently supplied manifest', () => {
    for (const name of ['mesh.glb', 'mesh.mobile.glb']) {
        const f = displayMeshFixture(name), raw = corners(); raw[2].point_m[2] += .03;
        const record = sealRegisteredDeskTarget({ binding: f.binding, equipment_ref, corners: raw,
            confirmed: true, surface_ref: f.surface_ref }, digest);
        assert.equal(record.schema, 'home-registered-desk-target-2'); assert.equal(record.version, 2);
        assert.deepEqual(record.selection, { method: 'owner_selected_display_mesh', surface_ref: f.surface_ref, raw_corners: raw });
        assert.deepEqual(record.target.vertices_m, raw.map(value => value.point_m));
        assert.ok(record.target.planarity.max_distance_m > 0); assert.equal(record.target.planarity.qualified, false);
        assert.equal(record.qualification, null); assert.equal(record.physical_commands, false);
        assert.deepEqual(validateRegisteredDeskTarget(record, { binding: f.binding, equipment_ref, manifest: f.manifest }, digest), record);
        assert.throws(() => validateRegisteredDeskTarget(record, { binding: f.binding, equipment_ref }, digest), /manifest/);
        f.surface_ref.asset_sha256 = '0'.repeat(64); raw[0].point_m[0] = 99;
        assert.equal(record.selection.surface_ref.asset_sha256, f.manifest.assets[name].sha256);
        assert.equal(record.selection.raw_corners[0].point_m[0], 0);
    }
});

test('version 2 refuses substituted mesh sources and rehashed manifests with foreign registered identities', () => {
    const f = displayMeshFixture();
    const issue = (surface_ref = f.surface_ref, current = f.binding) => sealRegisteredDeskTarget({ binding: current,
        equipment_ref, corners: corners(), confirmed: true, surface_ref }, digest);
    for (const source of [null, { ...f.surface_ref, asset_name: 'collision.glb' },
        { ...f.surface_ref, asset_name: '../mesh.glb' }, { ...f.surface_ref, asset_sha256: 'wrong' },
        { ...f.surface_ref, manifest_digest: '0'.repeat(64) }, { ...f.surface_ref, extra: true }]) {
        assert.throws(() => issue(source), /surface/);
    }
    const record = issue();
    for (const edit of [m => { m.assets['mesh.glb'].sha256 = '0'.repeat(64); }, m => { m.extra = true; }]) {
        const changed = copy(f.manifest); edit(changed);
        assert.throws(() => validateRegisteredDeskTarget(record, { binding: f.binding, equipment_ref, manifest: changed }, digest), /manifest/);
    }
    for (const edit of [m => { m.siteId = 'la'; }, m => { m.revision += '-other'; },
        m => { m.registrationVersion += 1; }, m => { m.assets['frame.json'].sha256 = '0'.repeat(64); },
        m => { m.assets['collision.glb'].sha256 = '0'.repeat(64); }, m => { delete m.assets['mesh.glb']; },
        m => { m.assets['mesh.glb'].sha256 = '0'.repeat(64); }]) {
        const manifest = copy(f.manifest); edit(manifest);
        const current = { ...f.binding, manifest_digest: digest(manifest) };
        const changedRecord = issue({ ...f.surface_ref, manifest_digest: current.manifest_digest }, current);
        assert.throws(() => validateRegisteredDeskTarget(changedRecord, { binding: current, equipment_ref, manifest }, digest), /manifest|surface/);
    }
    const changed = copy(record); changed.selection.raw_corners[0].point_m[0] += .1;
    assert.throws(() => validateRegisteredDeskTarget(changed, { binding: f.binding, equipment_ref, manifest: f.manifest }, digest), /target_changed/);
});

test('omitting the display surface reference retains the exact closed version 1 contract', () => {
    const record = seal(corners());
    assert.equal(record.schema, 'home-registered-desk-target-1'); assert.equal(record.version, 1);
    assert.deepEqual(Object.keys(record.selection).sort(), ['asset_sha256', 'method', 'raw_corners']);
    assert.equal(record.selection.method, 'owner_selected_collision_surface');
    assert.equal(record.selection.asset_sha256, binding.canonical_collision_digest);
    assert.deepEqual(validateRegisteredDeskTarget(record, { binding, equipment_ref }, digest), record);
});

test('seals the actual sloped polygon with exact identities and no independent accuracy claim', () => {
    const original = corners(), record = seal(original);
    assert.deepEqual(record.target.vertices_m,points);
    assert.deepEqual(record.target.centre_m,[1,.5,1.2]);
    record.target.normal_unit.forEach((value,i) => assert.ok(Math.abs(value-normal[i]) < 1e-15));
    assert.ok(record.target.planarity.max_distance_m < 1e-14);
    assert.equal(record.target.planarity.qualified,false);
    assert.equal(record.qualification,null); assert.equal(record.physical_commands,false);
    assert.equal(record.truth_role,'owner_selected_scan_target');
    assert.equal(record.selection.asset_sha256,binding.canonical_collision_digest);
    assert.deepEqual(record.selection.raw_corners,original);
    assert.deepEqual(validateRegisteredDeskTarget(record,{ binding,equipment_ref },digest),record);
    original[0].point_m[0]=99;
    assert.deepEqual(record.target.vertices_m,points);
    const reversed=seal(corners().reverse());
    assert.deepEqual(reversed.target.vertices_m,[...points].reverse());
});

test('retains nonplanarity diagnostically without projecting or manufacturing thickness', () => {
    const raw=corners(); raw[2].point_m[2]+=.03;
    const record=seal(raw);
    assert.deepEqual(record.target.vertices_m,raw.map(value=>value.point_m));
    assert.ok(record.target.planarity.max_distance_m > .005);
    assert.equal(record.target.planarity.qualified,false);
    assert.equal(Object.hasOwn(record.target,'thickness_m'),false);
    assert.deepEqual(validateRegisteredDeskTarget(record,{ binding,equipment_ref },digest),record);
});

test('rejects bad ordering, duplicates, collinearity, downward or opposing normals and spatial bounds', () => {
    const raw=corners();
    const cases=[
        [raw[0],raw[2],raw[1],raw[3]],
        [raw[0],raw[1],raw[1],raw[3]],
        [[0,0,1],[1,0,1],[2,0,1],[0,1,1]].map(point_m=>({point_m,normal_unit:[0,0,1]})),
        [[0,0,1],[2,0,1],[.5,.2,1],[0,1,1]].map(point_m=>({point_m,normal_unit:[0,0,1]})),
        raw.map(value=>({...value,normal_unit:[0,0,-1]})),
        raw.map((value,i)=>({...value,normal_unit:i===0?[1/Math.hypot(1,.01),0,.01/Math.hypot(1,.01)]:[-1/Math.hypot(1,.01),0,.01/Math.hypot(1,.01)]})),
        [[0,0,1],[21,0,1],[21,1,1],[0,1,1]].map(point_m=>({point_m,normal_unit:[0,0,1]})),
        raw.map((value,i)=>i===0?{...value,point_m:[101,0,1]}:value),
        raw.map((value,i)=>i===0?{...value,point_m:[NaN,0,1]}:value),
        raw.map((value,i)=>i===0?{...value,normal_unit:[0,0,2]}:value),
        raw.map((value,i)=>i===0?{...value,extra:true}:value),
    ];
    for (const value of cases) assert.throws(()=>seal(value),TypeError);
    assert.throws(()=>sealRegisteredDeskTarget({binding,equipment_ref,corners:raw,confirmed:false},digest),/explicit_whole_desktop_confirmation/);
    assert.throws(()=>sealRegisteredDeskTarget({binding,equipment_ref,corners:raw,confirmed:true},()=>Promise.resolve('a'.repeat(64))),/digest_required/);
});

test('validation refuses edited geometry, raw originals, qualification, extra fields and stale pins', () => {
    const original=seal(corners());
    const alterations=[
        value=>{value.target.centre_m[0]+=.1;},
        value=>{value.target.vertices_m[0][2]+=.1;},
        value=>{value.selection.raw_corners[0].point_m[0]+=.1;},
        value=>{value.selection.asset_sha256='e'.repeat(64);},
        value=>{value.qualification={qualified:true};},
        value=>{value.physical_commands=true;},
        value=>{value.extra='ignored';},
        value=>{delete value.target.planarity;},
    ];
    for(const change of alterations){const changed=copy(original);change(changed);assert.throws(()=>validateRegisteredDeskTarget(changed,{binding,equipment_ref},digest),TypeError);}
    assert.throws(()=>validateRegisteredDeskTarget(original,{binding:{...binding,frame_digest:'e'.repeat(64)},equipment_ref},digest),/binding_changed/);
    assert.throws(()=>validateRegisteredDeskTarget(original,{binding,equipment_ref:{...equipment_ref,revision:3}},digest),/binding_changed/);
    assert.throws(()=>validateRegisteredDeskTarget(original,{binding,equipment_ref},()=> '0'.repeat(64)),/target_changed/);
});

class Surface {
    constructor(){this.listeners=new Map();this.captured=new Set();this.released=[];}
    addEventListener(name,handler){if(!this.listeners.has(name))this.listeners.set(name,new Set());this.listeners.get(name).add(handler);}
    removeEventListener(name,handler){this.listeners.get(name)?.delete(handler);}
    setPointerCapture(id){this.captured.add(id);}
    releasePointerCapture(id){this.captured.delete(id);this.released.push(id);}
    dispatch(name,values={}){
        const event={button:0,isPrimary:true,pointerId:1,clientX:10,clientY:20,target:{closest:()=>false},
            prevented:false,stopped:false,preventDefault(){this.prevented=true;},stopImmediatePropagation(){this.stopped=true;},...values};
        for(const handler of this.listeners.get(name)||[])handler(event);
        return event;
    }
    get listenerCount(){return [...this.listeners.values()].reduce((sum,set)=>sum+set.size,0);}
}
function fixture(){
    const element=new Surface(),windowTarget=new Surface(),state={identity:['authored-scene',2],enabled:true,index:0};
    const changes=[],completed=[];
    const controller=createDeskTargetCapture({element,windowTarget,getIdentity:()=>state.identity,isEnabled:()=>state.enabled,
        surfaceHit:()=>{const value=corners()[state.index++];return value?{point:value.point_m,normal:value.normal_unit}:null;},
        onChange:value=>changes.push(value),onComplete:value=>completed.push(value)});
    const click=()=>{element.dispatch('pointerdown');return element.dispatch('pointerup');};
    return {element,windowTarget,state,changes,completed,controller,click};
}

test('mixed winding collision faces accept every actual desktop click from above', () => {
    const camera = new THREE.OrthographicCamera(-5, 5, 5, -5, .01, 20);
    camera.position.set(0, 0, 5); camera.lookAt(0, 0, 0); camera.updateMatrixWorld(true);
    const picking = createPicking(camera, { getBoundingClientRect: () => ({ left: 0, top: 0, width: 1000, height: 1000 }) });
    const root = new THREE.Group(), material = new THREE.MeshBasicMaterial({ side: THREE.DoubleSide, visible: false });
    for (let index = 0; index < 10; index++) {
        const geometry = new THREE.PlaneGeometry(.8, .8);
        if (index < 9) {
            const indices = geometry.index.array;
            for (let at = 0; at < indices.length; at += 3) [indices[at+1], indices[at+2]] = [indices[at+2], indices[at+1]];
            geometry.computeVertexNormals();
        }
        const surface = new THREE.Mesh(geometry, material);
        surface.position.set(index - 4.5, 0, 1); root.add(surface);
    }
    root.updateMatrixWorld(true);
    const counts = [], reasons = [];
    try {
        for (let index = 0; index < 10; index++) {
            const element = new Surface(), windowTarget = new Surface();
            const controller = createDeskTargetCapture({ element, windowTarget,
                getIdentity: () => ['same-registered-scan'], isEnabled: () => true,
                surfaceHit: (x, y) => picking.surfaceHit(root, [root], x, y) });
            try {
                const x = index * 100 + 50;
                assert.ok(picking.pick([root], x, 500).length > 0, 'the exact collision face is hit');
                element.dispatch('pointerdown', { clientX: x, clientY: 500 });
                element.dispatch('pointerup', { clientX: x, clientY: 500 });
                const result = controller.readback(); counts.push(result.corners.length); reasons.push(result.reason);
                if (result.corners.length) {
                    assert.ok(Math.abs(result.corners[0].point_m[0] - (index - 4.5)) < 1e-12);
                    assert.equal(result.corners[0].point_m[2], 1, 'retain the exact hit, with no projection');
                    assert.deepEqual(result.corners[0].normal_unit, [0, 0, 1]);
                }
            } finally { controller.dispose(); }
        }
        assert.deepEqual(counts, Array(10).fill(1), `surface clicks were refused: ${JSON.stringify(reasons)}`);
        camera.position.set(0, 0, -2); camera.lookAt(0, 0, 1); camera.updateMatrixWorld(true);
        const element = new Surface(), windowTarget = new Surface();
        const underside = createDeskTargetCapture({ element, windowTarget,
            getIdentity: () => ['same-registered-scan'], isEnabled: () => true,
            surfaceHit: (x, y) => picking.surfaceHit(root, [root], x, y) });
        try {
            element.dispatch('pointerdown', { clientX: 50, clientY: 500 });
            element.dispatch('pointerup', { clientX: 50, clientY: 500 });
            assert.equal(underside.readback().corners.length, 0, 'the underside is not a desktop top');
            assert.equal(underside.readback().reason, 'registered_desk_surface_corner_required');
        } finally { underside.dispose(); }
    } finally {
        root.children.forEach(surface => surface.geometry.dispose()); material.dispose();
    }
});

test('actual surface picks reject vertical walls and preserve rotated apartment coordinates', () => {
    const material = new THREE.MeshBasicMaterial({ side: THREE.DoubleSide, visible: false });
    const wallGeometry = new THREE.BufferGeometry();
    wallGeometry.setAttribute('position', new THREE.Float32BufferAttribute([
        -1,0,-1, 1,0,-1, 1,0,1, -1,0,-1, 1,0,1, -1,0,1,
    ], 3));
    wallGeometry.computeVertexNormals();
    const wall = new THREE.Mesh(wallGeometry, material), root = new THREE.Group(); root.add(wall);
    const camera = new THREE.OrthographicCamera(-2, 2, 2, -2, .01, 20);
    camera.up.set(0, 0, 1); camera.position.set(0, -3, 0); camera.lookAt(0, 0, 0); camera.updateMatrixWorld(true);
    const picking = createPicking(camera, { getBoundingClientRect: () => ({ left: 0, top: 0, width: 800, height: 800 }) });
    const element = new Surface(), windowTarget = new Surface();
    const controller = createDeskTargetCapture({ element, windowTarget, getIdentity: () => ['same-scan'],
        isEnabled: () => true, surfaceHit: (x, y) => picking.surfaceHit(root, [root], x, y) });
    let desktop;
    try {
        root.updateMatrixWorld(true);
        assert.ok(picking.pick([root], 400, 400).length, 'vertical wall is actually hit');
        element.dispatch('pointerdown', { clientX: 400, clientY: 400 });
        element.dispatch('pointerup', { clientX: 400, clientY: 400 });
        assert.equal(controller.readback().corners.length, 0, 'vertical wall cannot become a desktop');

        root.remove(wall); desktop = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), material);
        desktop.position.z = 1; root.add(desktop);
        root.rotation.x = -Math.PI / 2; root.position.set(2, 3, -1); root.updateMatrixWorld(true);
        camera.up.set(0, 0, -1); camera.position.set(2, 9, -1); camera.lookAt(2, 4, -1); camera.updateMatrixWorld(true);
        const selected = [[-.8,-.8,1],[.8,-.8,1],[.8,.8,1],[-.8,.8,1]], originalHits = [];
        for (const local of selected) {
            const screen = new THREE.Vector3(...local).applyMatrix4(root.matrixWorld).project(camera);
            const clientX = (screen.x + 1) * 400, clientY = (1 - screen.y) * 400;
            originalHits.push(picking.surfaceHit(root, [root], clientX, clientY).point);
            element.dispatch('pointerdown', { clientX, clientY });
            element.dispatch('pointerup', { clientX, clientY });
        }
        const result = controller.readback(); assert.equal(result.status, 'complete');
        result.corners.forEach((value, index) => {
            assert.deepEqual(value.point_m, originalHits[index], 'retain the actual unprojected hit');
            value.point_m.forEach((number, axis) => assert.ok(Math.abs(number - selected[index][axis]) < 1e-12));
            assert.ok(Math.abs(value.normal_unit[2] - 1) < 1e-12);
        });
    } finally { controller.dispose(); wallGeometry.dispose(); desktop?.geometry.dispose(); material.dispose(); }
});

test('four public click gestures emit raw corners once; drags add none and all captures release', () => {
    const f=fixture();
    f.element.dispatch('pointerdown');
    f.element.dispatch('pointermove',{clientX:16});
    f.element.dispatch('pointermove',{clientX:10});
    f.element.dispatch('pointerup');
    assert.equal(f.controller.readback().corners.length,0);assert.equal(f.state.index,0);
    assert.equal(f.element.captured.size,0);
    for(let i=0;i<4;i++)assert.equal(f.click().prevented,true);
    assert.equal(f.completed.length,1);assert.deepEqual(f.completed[0],corners());
    assert.equal(f.controller.readback().status,'complete');
    assert.equal(Object.hasOwn(f.completed[0],'digest'),false);
    f.click();assert.equal(f.completed.length,1);assert.equal(f.element.captured.size,0);
    f.completed[0][0].point_m[0]=99;assert.equal(f.controller.readback().corners[0].point_m[0],0);
    f.controller.dispose();f.controller.dispose();
    assert.equal(f.element.listenerCount,0);assert.equal(f.windowTarget.listenerCount,0);
});

test('identity change, Escape, blur, pointer cancel and mode loss retire without completion or orbit ownership', () => {
    for(const stop of ['identity','escape','blur','pointercancel','mode','cancel']){
        const f=fixture();f.click();f.element.dispatch('pointerdown');
        if(stop==='identity'){f.state.identity=['different-scene',2];f.element.dispatch('pointerup');}
        if(stop==='escape')f.windowTarget.dispatch('keydown',{key:'Escape'});
        if(stop==='blur')f.windowTarget.dispatch('blur',{target:f.windowTarget});
        if(stop==='pointercancel')f.element.dispatch('pointercancel');
        if(stop==='mode'){f.state.enabled=false;f.element.dispatch('pointerup');}
        if(stop==='cancel')f.controller.cancel();
        assert.equal(f.controller.readback().status,'retired',stop);
        assert.equal(f.controller.readback().corners.length,0,stop);
        assert.equal(f.element.captured.size,0,stop);assert.equal(f.completed.length,0,stop);
        assert.equal(f.element.dispatch('pointerdown').prevented,false,stop);
        f.controller.dispose();
    }
});

test('stale or invalid final hit cannot complete, and surface failures release the pointer', () => {
    const f=fixture();for(let i=0;i<3;i++)f.click();
    f.element.dispatch('pointerdown');f.state.identity=['new-equipment-revision',3];f.element.dispatch('pointerup');
    assert.equal(f.completed.length,0);assert.equal(f.element.captured.size,0);f.controller.dispose();
    for(const hit of [()=>null,()=>{throw Error('surface_unavailable');},()=>({point:[0,0,1],normal:[0,0,-1]})]){
        const element=new Surface(),windowTarget=new Surface();
        const controller=createDeskTargetCapture({element,windowTarget,getIdentity:()=>['same'],isEnabled:()=>true,
            surfaceHit:hit,onComplete:()=>assert.fail('must not complete')});
        element.dispatch('pointerdown');element.dispatch('pointerup');
        assert.equal(controller.readback().corners.length,0);assert.equal(element.captured.size,0);
        controller.dispose();
    }
});

test('moving focus from the start button to confirmation never discards selected corners', () => {
    const f=fixture();f.click();
    f.windowTarget.dispatch('blur',{target:{tagName:'BUTTON'}});
    assert.equal(f.controller.readback().status,'selecting');
    assert.equal(f.controller.readback().corners.length,1);
    for(let i=1;i<4;i++)f.click();
    f.windowTarget.dispatch('blur',{target:{tagName:'BUTTON'}});
    assert.equal(f.controller.readback().status,'complete');
    assert.deepEqual(f.controller.readback().corners,corners());
    assert.equal(f.completed.length,1);
    f.controller.dispose();
});

test('completed corners survive real window blur while explicit and scope retirement remain effective', () => {
    for(const terminal of ['cancel','escape','identity','mode','dispose']){
        const f=fixture();for(let i=0;i<4;i++)f.click();
        f.windowTarget.dispatch('blur',{target:f.windowTarget});
        assert.equal(f.controller.readback().status,'complete',terminal);
        assert.deepEqual(f.controller.readback().corners,corners());
        if(terminal==='cancel')f.controller.cancel();
        if(terminal==='escape')f.windowTarget.dispatch('keydown',{key:'Escape'});
        if(terminal==='identity')f.state.identity=['different-scene',3];
        if(terminal==='mode')f.state.enabled=false;
        if(terminal==='dispose')f.controller.dispose();
        assert.equal(f.controller.readback().status,'retired',terminal);
        assert.equal(f.controller.readback().corners.length,0,terminal);
        f.controller.dispose();
    }
});
