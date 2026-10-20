'use strict';
// Fixed child entry. Its parent is the incumbent controller's trusted supervisor.
const readline=require('node:readline');
const Worker=require('../home-live-context-worker.cjs');
const recorderClient=require('./recorder-client.cjs');
let runtime=null,clock=null,pending=Promise.resolve(),failure=null,diagnostic=null;
async function command(input){
  if(input.op==='describe')return Worker.describe(input.config);
  if(input.op==='start'){
    if(runtime)throw Error('worker_already_started');
    const record=recorderClient(input.recorder);
    clock=Number.isFinite(input.controlled_time)?input.controlled_time:null;
    runtime=await Worker.start(input.config,{record,onStatus:value=>{
      const bounded=x=>typeof x==='string'&&/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$/.test(x);
      diagnostic={status:bounded(value.status)?value.status:'worker_status_unavailable',reason:bounded(value.reason)?value.reason:'worker_status_unavailable'};
      if(value.status==='failed_closed')failure=value.reason||'worker_failed_closed';
    },onDecision:()=>{diagnostic=null;},
      ...(clock===null?{}:{now:()=>clock,schedule:false})});
    return {status:'ready',worker_id:input.config.worker_id,description:runtime.worker.describe()};
  }
  if(input.op==='status'&&runtime)return {status:failure?'failed':'ready',reason:failure,diagnostic};
  if(input.op==='tick'&&runtime&&clock!==null){clock=input.t;await runtime.cycle();return {status:'cycled'};}
  if(input.op==='drain'&&runtime){
    if(typeof runtime.drain!=='function')throw Error('worker_drain_hook_unavailable');
    return await runtime.drain({deadline_ms:input.deadline_ms,through_cursor:input.through_cursor});
  }
  if(input.op==='stop'&&runtime){runtime.stop();return {status:'stopped'};}
  throw Error('worker_command_refused');
}
const lines=readline.createInterface({input:process.stdin,crlfDelay:Infinity});
lines.on('line',line=>{pending=pending.then(async()=>{
  try{if(Buffer.byteLength(line)>1048576)throw Error('worker_command_bound');
    process.stdout.write(JSON.stringify({ok:true,value:await command(JSON.parse(line))})+'\n');}
  catch(error){process.stdout.write(JSON.stringify({ok:false,error:error.message})+'\n');}
});});
lines.on('close',()=>pending.finally(()=>runtime?.stop()));
