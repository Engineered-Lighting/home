'use strict';
// Independent original-control history checks; the worker is not an authority oracle.
const L=require('../../app/src/home-lighting-target-contract.js');
const same=(a,b)=>L.canonicalJson(a)===L.canonicalJson(b);
const exact=(o,keys)=>o&&typeof o==='object'&&!Array.isArray(o)&&Object.keys(o).length===keys.length&&keys.every(k=>Object.hasOwn(o,k));
const id=x=>typeof x==='string'&&/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}(?![\s\S])/.test(x);
function check(ok,reason='archive_control_history_mismatch'){if(!ok)throw Error(reason);}
function createControlWitness(config){
  let state={control_generation:0,control_active:false},stream=null,lastTime=null,deliveredState=null;
  const pending=new Map(),ids=new Set();
  function validateState(value){check(exact(value,['control_generation','control_active'])&&Number.isSafeInteger(value.control_generation)&&value.control_generation>=0&&typeof value.control_active==='boolean');}
  function validate(original,current,currentStream,currentTime){
    check(config.manual_policy&&exact(original,['operation','receipt']),'archive_uncommissioned_control_operations');
    const {operation:o,receipt:r}=original,policy=config.manual_policy;
    check(exact(o,['schema','version','id','operation','scope','manifest_ref','policy_ref','reason'])
      &&o.schema==='home-live-control-operation-1'&&o.version===1&&id(o.id)
      &&['activate','retire'].includes(o.operation)&&same(o.scope,config.identity)
      &&same(o.manifest_ref,config.manifest_ref)&&same(o.policy_ref,policy.policy_ref)
      &&(o.operation==='activate'?o.reason===null:id(o.reason)));
    check(exact(r,['schema','version','id','request_digest','stream_epoch','applied_at','before','after','operating_not_after'])
      &&r.schema==='home-live-control-receipt-1'&&r.version===1&&r.id===o.id&&r.request_digest===L.digest(o)
      &&id(r.stream_epoch)&&(!currentStream||r.stream_epoch===currentStream)
      &&Number.isFinite(r.applied_at)&&r.applied_at>=0&&(currentTime===null||r.applied_at>=currentTime)
      &&r.operating_not_after===policy.operating_not_after);
    validateState(r.before);validateState(r.after);check(same(r.before,current));
    check(same(r.after,{control_generation:current.control_generation+1,control_active:o.operation==='activate'}));
    if(o.operation==='activate')check(current.control_generation===0&&!current.control_active
      &&r.applied_at<r.operating_not_after&&r.operating_not_after-r.applied_at<=120);
    else check(current.control_active||current.control_generation===0);
    return {state:r.after,stream:r.stream_epoch,time:r.applied_at};
  }
  return {
    prepare(txId,originals){
      check(typeof txId==='string'&&!pending.has(txId)&&Array.isArray(originals)&&originals.length>0&&originals.length<=2);
      let next=state,nextStream=stream,nextTime=lastTime;const localIds=new Set();
      for(const original of originals){check(!ids.has(original.operation?.id)&&!localIds.has(original.operation?.id));
        const checked=validate(original,next,nextStream,nextTime);next=checked.state;nextStream=checked.stream;nextTime=checked.time;localIds.add(original.operation.id);}
      pending.set(txId,{originals,next,nextStream,nextTime});
    },
    settle(txId,status){
      const tx=pending.get(txId);check(tx&&['committed','aborted'].includes(status));
      if(status==='committed'){
        // A prepare cannot overwrite an intervening committed transition.
        check(same(tx.originals[0].receipt.before,state));
        state=tx.next;stream=tx.nextStream;lastTime=tx.nextTime;
        for(const original of tx.originals)ids.add(original.operation.id);
      }
      pending.delete(txId);
    },
    validateCut(cut){
      if(!config.manual_policy){check(!Object.hasOwn(cut,'control_generation')&&!Object.hasOwn(cut,'control_active'));return;}
      check(stream!==null&&cut.stream_epoch===stream&&cut.control_generation===state.control_generation
        &&cut.control_active===state.control_active,'archive_control_cut_without_original_authority');
      deliveredState={...state};
    },
    validateCheckpoint(checkpoint){
      if(!config.manual_policy)return;
      const originalCut=deliveredState||state;
      check(checkpoint&&checkpoint.control_generation===originalCut.control_generation&&checkpoint.control_active===originalCut.control_active,
        'archive_control_checkpoint_without_original_authority');
    },
    validateTick(){
      if(config.manual_policy)check(deliveredState&&same(deliveredState,state),'archive_control_tick_requires_fresh_cut');
    },
    validateTerminalCheckpoint(checkpoint){
      if(config.manual_policy)check(checkpoint&&checkpoint.control_generation===state.control_generation
        &&checkpoint.control_active===state.control_active,'archive_control_terminal_checkpoint_mismatch');
    },
    validateAcceptedCheckpoint(checkpoint){
      if(config.manual_policy)check(checkpoint&&checkpoint.control_generation===state.control_generation
        &&checkpoint.control_active===state.control_active,'archive_control_commit_authority_mismatch');
    },
    finalize(){
      check(pending.size===0,'archive_control_transaction_unconfirmed');
      if(config.manual_policy)check(stream!==null&&!state.control_active&&state.control_generation>0,'archive_control_retirement_missing');
    }
  };
}
module.exports={createControlWitness};
