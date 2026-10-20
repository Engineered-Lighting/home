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
