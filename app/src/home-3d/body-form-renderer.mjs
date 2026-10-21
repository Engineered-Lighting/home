import * as THREE from 'three';
import {buildBodyForm} from './body-form-model.mjs';

// Shared unit geometry, bounded to two frame-scoped forms. Mesh slots are
// render storage only, never track identity or history. Every pose is replaced.
export function createBodyFormRenderer(root) {
  const sphere=new THREE.SphereGeometry(1,20,14),capsule=new THREE.CapsuleGeometry(1,1,6,12);
  // A neutral clay preview shows volume even in an unlit scan, without adding
  // lights that would change the room. This is not photometric relighting.
  const size=64,data=new Uint8Array(size*size*4),light=new THREE.Vector3(-.45,.65,.65).normalize();
  for(let y=0;y<size;y++)for(let x=0;x<size;x++){
    const nx=(x+.5)/size*2-1,ny=(y+.5)/size*2-1,nz=Math.sqrt(Math.max(0,1-nx*nx-ny*ny));
    const diffuse=Math.max(0,nx*light.x+ny*light.y+nz*light.z),shade=Math.min(1,.22+.65*diffuse+.07*Math.pow(diffuse,18));
    const value=Math.round(shade*255),at=(y*size+x)*4;data.set([value,value,value,255],at);
  }
  const matcap=new THREE.DataTexture(data,size,size);matcap.colorSpace=THREE.SRGBColorSpace;
  matcap.minFilter=THREE.LinearFilter;matcap.magFilter=THREE.LinearFilter;matcap.needsUpdate=true;
  const solid=new THREE.MeshMatcapMaterial({color:0xd0d2d5,matcap});
  const inferred=new THREE.MeshBasicMaterial({color:0xbac1c8,wireframe:true,transparent:true,opacity:.4,depthWrite:false});
  const slots=[],axis=new THREE.Vector3(0,1,0),matrix=new THREE.Matrix4();
  let used=0,disposed=false,summary={status:'unavailable',people:0,observedJoints:0,estimatedJoints:0,parts:0};
  function hide(){for(const mesh of slots){mesh.visible=false;mesh.onAfterRender=()=>{};}used=0;}
  function update(packet,onDraw){
    if(disposed)return summary;
    hide();const models=(packet.people||[]).slice(0,2).map(person=>buildBodyForm(person,{frameKey:packet.frame_key}));
    for(let i=0;i<models.length;i++)for(const part of models[i].parts){
      let mesh=slots[used];
      if(!mesh){mesh=new THREE.Mesh(sphere,solid);mesh.raycast=()=>{};root.add(mesh);slots.push(mesh);}
      mesh.name='body-form-'+part.key;mesh.visible=true;mesh.userData.bodyForm=true;
      mesh.userData.estimated=part.estimated;mesh.userData.frameScopedPerson=i;
      mesh.material=part.estimated?inferred:solid;
      mesh.castShadow=!part.estimated;mesh.receiveShadow=!part.estimated;
      // Keep normal scene depth, including walls. No draw-through-wall overlay.
      mesh.renderOrder=0;mesh.onAfterRender=onDraw;
      if(part.kind==='segment'){
        const start=new THREE.Vector3(...part.start),end=new THREE.Vector3(...part.end),span=end.clone().sub(start);
        mesh.position.copy(start).add(end).multiplyScalar(.5);
        // Extend the central cylinder while retaining spherical end caps.
        // Each bounded slot owns one reusable buffer, never a per-frame mesh.
        const geometry=mesh.userData.boneGeometry||=capsule.clone();mesh.geometry=geometry;
        const source=capsule.attributes.position,target=geometry.attributes.position;
        for(let n=0;n<source.count;n++){
          const y=source.getY(n);
          target.setXYZ(n,source.getX(n)*part.radius,
            Math.sign(y)*(span.length()/2+(Math.abs(y)-.5)*part.radius),source.getZ(n)*part.radius);
        }
        target.needsUpdate=true;geometry.computeBoundingSphere();mesh.scale.setScalar(1);
        mesh.quaternion.setFromUnitVectors(axis,span.normalize());
      }else{
        mesh.geometry=sphere;mesh.position.fromArray(part.center);mesh.scale.fromArray(part.radii);
        matrix.makeBasis(...part.basis.map(v=>new THREE.Vector3(...v)));mesh.quaternion.setFromRotationMatrix(matrix);
      }
      used++;
    }
    const supported=models.filter(m=>m.parts.length);
    summary={status:!supported.length?'unavailable':supported.some(m=>m.status==='partial')?'partial':
      supported.some(m=>m.status==='estimated')?'estimated':'complete',people:supported.length,
      observedJoints:models.reduce((n,m)=>n+m.observedJoints,0),estimatedJoints:models.reduce((n,m)=>n+m.estimatedJoints,0),parts:used,
      missingJoints:models.reduce((n,m)=>n+m.missingJoints,0),frameScoped:true,volume:'approximate',
      physicalOcclusion:false,measuredStature:false,reasons:models.filter(m=>m.reason).map(m=>m.reason)};
    return summary;
  }
  return {update,clear(){hide();summary={status:'unavailable',people:0,observedJoints:0,estimatedJoints:0,parts:0};},
    readback:()=>({...summary,reasons:[...(summary.reasons||[])]}),dispose(){if(disposed)return;disposed=true;for(const mesh of slots){root.remove(mesh);mesh.userData.boneGeometry?.dispose();}
      sphere.dispose();capsule.dispose();solid.dispose();inferred.dispose();matcap.dispose();slots.length=0;}};
}
