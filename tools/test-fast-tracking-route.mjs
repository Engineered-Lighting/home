import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {createFastTrackingHandler} from '../web-gateway/fast-tracking-route.mjs';
function fixture(options={}){
  let calls=0,upstream;
  const request=(config,callback)=>{
    calls++;assert.equal(config.hostname,'127.0.0.1');assert.equal(config.port,9451);assert.equal(config.rejectUnauthorized,true);
    const req=new EventEmitter();req.destroy=()=>{};req.end=()=>{};upstream={config,callback,req};return req;
  };
  const handler=createFastTrackingHandler({ca:'CA',credential:()=>'a'.repeat(40),authenticate:()=>true,request,...options});
  const req=new EventEmitter();Object.assign(req,{url:'/api/perception-tracking/v1/stream',method:'GET',headers:{'x-home-lab-site':'victoria'},resume(){}});
  const res=new EventEmitter();res.body=[];res.writeHead=(status,headers)=>{res.status=status;res.headers=headers;};res.write=bytes=>{res.body.push(bytes);return true;};res.end=()=>{res.writableEnded=true;};
  return {handler,req,res,calls:()=>calls,upstream:()=>upstream};
}
test('read-only fixed endpoint and existing Home authentication before upstream access',()=>{
  for(const patch of [{method:'POST'},{url:'/api/perception-tracking/v1/execute'},{url:'/api/perception-tracking/v1/stream?origin=bad'},
    {headers:{'x-home-lab-site':'echo'}}]){
    const f=fixture();Object.assign(f.req,patch);assert.equal(f.handler(f.req,f.res),true);assert.equal(f.calls(),0);
  }
  const denied=fixture({authenticate:()=>false});denied.handler(denied.req,denied.res);assert.equal(denied.res.status,401);assert.equal(denied.calls(),0);
});
test('stream delivers immediately, checks auth again, and drops a slow consumer',()=>{
  let authorized=true;const f=fixture({authenticate:()=>authorized});f.handler(f.req,f.res);
  const response=new EventEmitter();Object.assign(response,{statusCode:200,headers:{'content-type':'text/event-stream'},destroy(){this.destroyed=true;}});
  f.upstream().callback(response);response.emit('data',Buffer.from('data: {}\n\n'));
  assert.equal(f.res.body.length,1);authorized=false;response.emit('data',Buffer.from('data: {}\n\n'));
  assert.equal(f.res.body.length,1);assert.equal(response.destroyed,true);
  const slow=fixture();slow.handler(slow.req,slow.res);slow.res.write=()=>false;
  const body=new EventEmitter();Object.assign(body,{statusCode:200,headers:{'content-type':'text/event-stream'},destroy(){this.destroyed=true;}});
  slow.upstream().callback(body);body.emit('data',Buffer.from('data: {}\n\n'));assert.equal(body.destroyed,true);
});

test('two admitted streams leave a separate bounded clock slot',()=>{
  const f=fixture();f.handler(f.req,f.res);
  const req2=new EventEmitter();Object.assign(req2,{url:f.req.url,method:'GET',headers:f.req.headers});
  const res2=new EventEmitter();res2.writeHead=()=>{};res2.end=()=>{res2.writableEnded=true;};
  f.handler(req2,res2);
  const clockReq=new EventEmitter();Object.assign(clockReq,{url:'/api/perception-tracking/v1/clock',method:'GET',headers:f.req.headers});
  const clockRes=new EventEmitter();clockRes.writeHead=status=>{clockRes.status=status;};clockRes.end=()=>{clockRes.writableEnded=true;};
  f.handler(clockReq,clockRes);assert.equal(f.calls(),3,'viewers must not starve their clock refresh');
  f.res.emit('close');res2.emit('close');clockRes.emit('close');
});

test('diagnostics correlate split SSE writes using only frame references',()=>{
  const events=[],f=fixture({onTrace:event=>events.push(event)});f.handler(f.req,f.res);
  const response=new EventEmitter();Object.assign(response,{statusCode:200,headers:{'content-type':'text/event-stream'},destroy(){}});
  f.upstream().callback(response);
  const packet={attempt_id:'c'.repeat(32),sequence:7,frame_key:'d'.repeat(64),people:[{joints:[{position_m:[1,2,3]}]}],secret:'MUST_NOT_LOG'};
  const bytes=Buffer.from('data: '+JSON.stringify(packet)+'\n\n');response.emit('data',bytes.subarray(0,13));response.emit('data',bytes.subarray(13));
  assert.deepEqual(events.map(e=>e.stage),['gateway_received','gateway_written']);
  assert.equal(events[0].sequence,7);assert.equal(events[0].stream_id,f.upstream().config.headers['X-Home-Tracking-Stream']);
  assert.equal(f.res.headers['X-Home-Tracking-Stream'],events[0].stream_id);
  assert.doesNotMatch(JSON.stringify(events),/position_m|MUST_NOT_LOG|Authorization/);
  f.res.emit('close');
});
