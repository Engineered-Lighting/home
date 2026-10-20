// Real HDR-frame ownership with the shipped Three module; no GPU or room data.
import test from 'node:test';
import assert from 'node:assert/strict';
import { register } from 'node:module';
const threeUrl = new URL('../app/src/vendor/three-0.180.0/three.module.js',import.meta.url).href;
register('data:text/javascript,'+encodeURIComponent(`export async function resolve(s,c,n){
  if(s==='three')return {url:${JSON.stringify(threeUrl)},shortCircuit:true};return n(s,c);
}`));
const THREE = await import(threeUrl);
const {createLitFrame} = await import('../app/src/home-3d/lit-frame.mjs');

function rendererStub() {
  let target = {width:8,height:8}, color = new THREE.Color(0x123456), alpha = .7;
  return {autoClear:true, render(){return 'base-render';},
    getRenderTarget:()=>target, setRenderTarget:next=>{target=next;},
    getClearColor:out=>out.copy(color), getClearAlpha:()=>alpha,
    setClearColor:(next,a)=>{color.set(next);alpha=a;},
    getDrawingBufferSize:out=>out.set(8,8),
    getContext:()=>({getContextAttributes:()=>({antialias:false}),finish(){}}),
  };
}

test('a retained foreign render wrapper stays usable across Lighting off/on',()=>{
  const renderer=rendererStub(), scene=new THREE.Scene(), camera=new THREE.Camera();
  const original=renderer.render, frame=createLitFrame({renderer,scene});
  scene.overrideMaterial=new THREE.MeshBasicMaterial();
  try {
    frame.install(); const first=renderer.render;
    let probes=0;
    const foreign=function(...args){probes++;return first.apply(this,args);};
    renderer.render=foreign;
    frame.uninstall();
    assert.equal(renderer.render,foreign,'a later owner is retained');
    frame.install();
    assert.equal(renderer.render(scene,camera),'base-render');
    assert.equal(probes,1,'retired HDR wrapper delegates to its own original renderer once');
    frame.uninstall();
    assert.equal(renderer.render,foreign);
    assert.equal(renderer.render(scene,camera),'base-render');
    renderer.render=original;
  } finally {frame.dispose();scene.overrideMaterial.dispose();}
});

test('a failed authored HDR proof restores its caller render target',async()=>{
  const renderer=rendererStub(),scene=new THREE.Scene(),target=renderer.getRenderTarget();
  renderer.render=()=>{throw Error('authored proof draw failure');};
  const frame=createLitFrame({renderer,scene});
  try {
    await assert.rejects(frame.proofPasses(new Float32Array([1,1,1,1]),1,1),/authored proof draw failure/);
    assert.equal(renderer.getRenderTarget(),target);
  } finally {frame.dispose();}
});

test('the frame cost probe restores an existing capture render target',async()=>{
  const renderer=rendererStub(),scene=new THREE.Scene(),camera=new THREE.Camera();
  const frame=createLitFrame({renderer,scene});
  try {
    frame.install();renderer.render(scene,camera);
    const capture={width:4,height:4};renderer.setRenderTarget(capture);
    assert.ok(Number.isFinite(await frame.time(1)));
    assert.equal(renderer.getRenderTarget(),capture);
  } finally {frame.dispose();}
});

test('a failed lit scene draw restores capture target, camera layers and receiver visibility',()=>{
  const renderer=rendererStub(), scene=new THREE.Scene(), camera=new THREE.Camera();
  const receiver=new THREE.Group(); receiver.layers.set(5);scene.add(receiver);
  const target=renderer.getRenderTarget(), color=renderer.getClearColor(new THREE.Color()).clone();
  const original=function(targetScene){if(targetScene===scene)throw Error('authored scene draw failure');};
  renderer.render=original;camera.layers.set(4);
  const frame=createLitFrame({renderer,scene});
  try {
    frame.install();frame.setReceivers([receiver]);
    assert.throws(()=>renderer.render(scene,camera),/authored scene draw failure/);
    assert.equal(renderer.getRenderTarget(),target);
    assert.equal(renderer.autoClear,true);assert.equal(renderer.getClearAlpha(),.7);
    assert.ok(renderer.getClearColor(new THREE.Color()).equals(color));
    assert.equal(camera.layers.mask,1<<4);assert.equal(receiver.visible,true);
    frame.uninstall();assert.equal(receiver.layers.mask,1<<5);
    assert.equal(renderer.render,original);assert.equal(scene.getObjectByName('lit-frame'),undefined);
  } finally {frame.dispose();}
});
