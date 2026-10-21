// Authored transport acceptance of the actual mounted Home Chat controller.
import assert from 'node:assert/strict';
import test from 'node:test';
import { runFullUiShell } from './fixtures/home-full-ui-harness.mjs';
test('mounted Chat streams, cancels and recovers from pipeline errors without duplicate messages', {timeout:145000}, async()=>{
await runFullUiShell('home-full-ui-chat-browser', async ({page,origin,openShell,waitSite,tab,checks}) => {
  let authorized=true; const runs=[],cancels=[],unexpected=[];
  await page.context().route('**/auth/status', route => route.fulfill({json:{authenticated:authorized,authorityTag:authorized?'a'.repeat(64):null}}));
  await page.context().routeWebSocket('**/*', socket => {
    const url=new URL(socket.url());
    if(url.origin.replace(/^ws/,'http')!==origin || url.pathname!=='/proxy/ha/api/websocket')return socket.close();
    const send=value=>socket.send(JSON.stringify(value));
    socket.onMessage(raw=>{
      const msg=JSON.parse(String(raw));
      if(msg.type==='auth')return send({type:'auth_ok',ha_version:'authored'});
      if(msg.type==='ping')return send({type:'pong',id:msg.id});
      if(msg.type==='assist_pipeline/run'){
        runs.push({msg,event:(type,data={})=>send({type:'event',id:msg.id,event:{type,data}}),
          reject:message=>send({type:'result',id:msg.id,success:false,error:{message}})});
        send({type:'result',id:msg.id,success:true,result:{}});return;
      }
      if(msg.type==='assist_pipeline/cancel'){cancels.push(msg);return send({type:'result',id:msg.id,success:true,result:{}});}
      const allowed=['get_states','get_config','subscribe_events','unsubscribe_events','config/entity_registry/list','config/device_registry/list','config/area_registry/list','conversation/agent/list'];
      if(!allowed.includes(msg.type))unexpected.push(msg.type);
      send({type:'result',id:msg.id,success:allowed.includes(msg.type),result:msg.type==='get_config'?{unit_system:{length:'m'},components:[]}:[]});
    });
    send({type:'auth_required',ha_version:'authored'});
  });
  await openShell();await waitSite('echo');await tab('Chat');
  await page.waitForFunction(()=>window.__hav_haClient?.haVersion==='authored');
  const command=page.getByRole('combobox',{name:'Command input',exact:true});
  const waitRun=async count=>{const until=Date.now()+15000;while(runs.length<count){assert.ok(Date.now()<until,'mounted Chat dispatches authored pipeline');await page.waitForTimeout(50);}return runs.at(-1);};
  const send=async text=>{const count=runs.length;await command.fill(text);await command.press('Enter');return waitRun(count+1);};
  const finish=(run,text)=>{run.event('intent-end',{intent_output:{conversation_id:'authored-chat-local',response:{speech:{plain:{speech:text}}}}});run.event('run-end');};
  const run=await send('Explain what a lumen measures.');
  assert.equal(run.msg.input.text,'Explain what a lumen measures.');
  run.event('intent-progress',{chat_log_delta:{content:'Authored answer '}});
  await page.getByText('Authored answer',{exact:false}).first().waitFor();
  await page.getByRole('button',{name:'Stop generation',exact:true}).waitFor();
  run.event('intent-progress',{chat_log_delta:{content:'streamed.'}});finish(run,'Authored answer streamed.');
  await page.getByRole('button',{name:'Stop generation',exact:true}).waitFor({state:'hidden'});
  assert.equal(await page.getByText('Authored answer streamed.',{exact:true}).count(),1,'final answer replaces streaming without duplication');
  checks.push({stage:'mounted_chat_informational_stream_and_final_deduplication'});
  const cancelled=await send('Explain this cancellation example.');
  await page.getByRole('button',{name:'Stop generation',exact:true}).click();
  cancelled.event('intent-progress',{chat_log_delta:{content:'Forbidden late answer'}});finish(cancelled,'Forbidden late answer');
  await page.waitForTimeout(250);assert.equal(await page.getByText('Forbidden late answer',{exact:true}).count(),0);
  assert.equal(await page.getByRole('button',{name:'Stop generation',exact:true}).count(),0);
  checks.push({stage:'cancel_before_first_token_ignores_late_result',cancel_messages:cancels.length});
  await page.setViewportSize({width:390,height:844});
  const failed=await send('Explain this failure example.');failed.event('error',{message:'Authored pipeline unavailable'});
  await page.getByText('Authored pipeline unavailable',{exact:false}).first().waitFor();
  await page.getByRole('button',{name:'Stop generation',exact:true}).waitFor({state:'hidden'});
  assert.equal(await page.getByText('Authored pipeline unavailable',{exact:false}).count(),1,'one failed typed turn produces one error message');
  const recovered=await send('Explain this recovery example.');finish(recovered,'Authored recovery answer.');
  await page.getByText('Authored recovery answer.',{exact:true}).waitFor();
  checks.push({stage:'phone_chat_pipeline_error_then_successful_new_turn'});
  const rejected=await send('Explain this rejected request example.');rejected.reject('Authored request refused');
  await page.getByText('Authored request refused',{exact:false}).first().waitFor();
  await page.getByRole('button',{name:'Stop generation',exact:true}).waitFor({state:'hidden'});
  assert.equal(await page.getByText('Authored request refused',{exact:false}).count(),1,'rejection without an error event still reports one failure');
  const afterRejected=await send('Explain recovery after rejection.');finish(afterRejected,'Authored rejection recovery answer.');
  await page.getByText('Authored rejection recovery answer.',{exact:true}).waitFor();
  checks.push({stage:'rejection_without_error_event_and_recovery'});
  authorized=false; const count=runs.length;
  await command.fill('Explain the revoked session example.');await command.press('Enter');
  await page.getByText(/I cannot verify this Home session right now/).first().waitFor();
  assert.equal(runs.length,count,'revoked authority must not dispatch');
  assert.deepEqual(unexpected,[]);checks.push({stage:'revoked_owner_refused_without_pipeline_dispatch'});
});

});
