import assert from 'node:assert/strict';
import {bodyPerson} from './body-form-fixtures.mjs';

// Test-only same-origin producer. All values are authored, never household data.
export function bodyFormTrackingService(){
  let scene,sequence=0,people=[bodyPerson({offset:[1.2,1.4,0]})],mode='available';
  const timers=new Set(),attempt='c'.repeat(32),now=()=>Number(process.hrtime.bigint())/1e9;
  return {bind(manifest){scene={siteId:'victoria',contentRevision:manifest.revision,registrationVersion:manifest.registrationVersion,
      frameDigest:manifest.assets['frame.json'].sha256,collisionDigest:manifest.assets['collision.glb'].sha256,cameraId:'den'};},
    setPeople(next){people=structuredClone(next);mode='available';},setMode(next){mode=next;},
    handle(req,res,pathname){
      if(!['/api/perception-tracking/v1/clock','/api/perception-tracking/v1/stream'].includes(pathname))return false;
      assert.ok(req.headers.cookie?.includes('authored_owner_session=1'));assert.equal(req.headers['x-home-lab-site'],'victoria');
      if(pathname.endsWith('/clock')){res.writeHead(200,{'Content-Type':'application/json','Cache-Control':'no-store'});
        res.end(JSON.stringify({attempt_id:attempt,monotonic_s:now()}));return true;}
      res.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-store','X-Home-Tracking-Stream':'e'.repeat(32)});
      const timer=setInterval(()=>{
        const at=now(),frame_key=(++sequence).toString(16).padStart(64,'0');
        const items=structuredClone(people);for(const person of items)if(person.body_pose)person.body_pose.frame_key=frame_key;
        res.write('data: '+JSON.stringify({schema:'home-fast-tracking-1',attempt_id:attempt,sequence,frame_key,
          status:mode==='unavailable'?'unavailable':'available',identity:{epoch:'authored-frame-scope'},
          scene:mode==='wrong_scene'?{...scene,contentRevision:'wrong-authored-pack'}:scene,
          lighting_authority:false,physical_permission:false,identity_authority:false,projection:'unqualified_nominal_eeprom',label:'Approximate room placement',
          source_la_monotonic_interval_s:[at-.02,at-.01],expires_monotonic_s:mode==='stale'?at-.01:at+.73,
          source_clock:'authored_test_clock',exposure_accuracy:'authored',people:items})+'\n\n');
      },100);timers.add(timer);req.on('close',()=>{clearInterval(timer);timers.delete(timer);});return true;
    },close(){for(const timer of timers)clearInterval(timer);timers.clear();}};
}
