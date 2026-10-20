// Actual Lights drawer + HA client, with an authored in-memory HA transport.
// All writes terminate here; the enclosing shell refuses external networking.
import test from 'node:test';
import assert from 'node:assert/strict';
import {runFullUiShell} from './fixtures/home-full-ui-harness.mjs';

const HELPER='input_boolean.living_lights_travel_mode';
test('full Home: Travel Mode distinguishes requested and confirmed state and recovers from refused writes', {timeout:145000}, async()=>{
  await runFullUiShell('home-full-ui-lights-browser', async ({page,origin,openShell,menu,checks,receipt,viewports,inViewport})=>{
    const fixture={state:'off',mode:'normal',writes:[]};
    const until=async predicate=>{const end=Date.now()+8000;while(!predicate()&&Date.now()<end)await new Promise(resolve=>setTimeout(resolve,25));assert.ok(predicate(),'authored HA write arrived');};
    const rows=()=>[{entity_id:HELPER,state:fixture.state,attributes:{friendly_name:'Authored Travel Mode'},last_changed:'2026-10-20T00:00:00Z'}];
    await page.context().route('**/proxy/ha/api/states',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(rows())}));
    await page.context().routeWebSocket('**/proxy/ha/api/websocket',socket=>{
      assert.equal(new URL(socket.url()).origin.replace(/^ws/,'http'),origin);
      const subscriptions=new Set();
      const send=value=>socket.send(JSON.stringify(value));
      socket.onMessage(bytes=>{
        const msg=JSON.parse(String(bytes)),reply=(result,success=true,error)=>send({id:msg.id,type:'result',success,result,...(error?{error}: {})});
        if(msg.type==='auth')return send({type:'auth_ok',ha_version:'authored'});
        if(msg.type==='ping')return send({type:'pong',id:msg.id});
        if(msg.type==='get_states')return reply(rows());
        if(msg.type==='get_config')return reply({unit_system:{length:'m'},components:[]});
        if(msg.type==='subscribe_events'){subscriptions.add(msg.id);return reply(null);}
        if(msg.type==='unsubscribe_events'){subscriptions.delete(msg.subscription);return reply(null);}
        if(msg.type==='call_service'){
          assert.ok((msg.domain==='input_boolean'&&msg.target?.entity_id===HELPER&&['turn_on','turn_off'].includes(msg.service))
            ||(['light','switch'].includes(msg.domain)&&msg.service==='turn_off')
            ||(msg.domain==='input_number'&&msg.service==='set_value'&&msg.target?.entity_id==='input_number.living_lights_brightness_bias_pp')
            ||(msg.domain==='input_datetime'&&msg.service==='set_datetime'),'only authored drawer calls: '+JSON.stringify(msg));
          fixture.writes.push({domain:msg.domain,service:msg.service,data:{...msg.service_data,...msg.target},mode:fixture.mode});
          if(fixture.mode==='refused')return reply(null,false,{code:'authored_refused',message:'Authored service refusal'});
          if(msg.domain==='input_boolean'&&fixture.mode==='normal')fixture.state=msg.service==='turn_on'?'on':'off';
          reply({context:{id:'authored-travel-write'}});
          if(msg.domain==='input_boolean'&&fixture.mode==='normal')for(const id of subscriptions)send({id,type:'event',event:{
            event_type:'state_changed',data:{entity_id:HELPER,new_state:rows()[0]}}});
          return;
        }
        assert.ok(['config/entity_registry/list','config/device_registry/list','config/area_registry/list','conversation/agent/list'].includes(msg.type),'only authored HA reads: '+msg.type);
        reply([]);
      });
      send({type:'auth_required',ha_version:'authored'});
    });
    receipt.travel_fixture={writes:fixture.writes,upstream:'authored in-memory transport',physical_commands:0};
    await openShell();
    const drawer=page.getByRole('dialog',{name:'Living Lights tuning',exact:true});
    for(const viewport of viewports){
      await page.setViewportSize(viewport);fixture.state='off';fixture.mode='normal';
      const beforeOpen=fixture.writes.length;
      await menu('Lights / Travel Mode');
      await drawer.getByText('travel mode off',{exact:true}).waitFor();
      assert.equal(fixture.writes.length,beforeOpen,'opening the drawer must not clear manual overrides or send other writes');
      const on=drawer.getByRole('button',{name:'turn on',exact:true});await inViewport(on);
      if(viewport.width<700){const box=await on.boundingBox();assert.ok(box.height>=44&&box.width>=44,'phone primary Travel Mode target is at least44px');}
      // A successful service response with unchanged readback is not confirmation.
      fixture.mode='stale';await on.click();
      await drawer.getByText(/Home Assistant has not confirmed Travel Mode/).waitFor({timeout:5000});
      assert.equal(fixture.state,'off');
      assert.equal(await drawer.getByText('Known lighting outputs are locked out and forced off.',{exact:true}).count(),0);
      fixture.mode='normal';await drawer.getByRole('button',{name:'turn on',exact:true}).waitFor();
      const before=fixture.writes.length;
      await drawer.getByRole('button',{name:'turn on',exact:true}).press('Space');
      await drawer.getByText('Known lighting outputs are locked out and forced off.',{exact:true}).waitFor();
      assert.equal(fixture.state,'on');
      assert.deepEqual(fixture.writes.slice(before).map(row=>row.domain+'.'+row.service),['input_boolean.turn_on','light.turn_off','switch.turn_off']);
      await drawer.getByRole('button',{name:'turn off',exact:true}).click();
      await drawer.getByText('travel mode off',{exact:true}).waitFor();assert.equal(fixture.state,'off');
      fixture.mode='refused';await drawer.getByRole('button',{name:'turn on',exact:true}).click();
      await drawer.getByText(/Authored service refusal/).waitFor();
      assert.equal(fixture.state,'off');
      await drawer.getByRole('button',{name:'dismiss',exact:true}).click();
      fixture.mode='normal';
      if(viewport.width>=700){
        await drawer.getByText('travel mode off',{exact:true}).waitFor();
        const cooldowns=()=>fixture.writes.filter(row=>row.domain==='input_datetime').length;
        const start=cooldowns(),editStart=fixture.writes.length;
        await drawer.getByRole('slider',{name:'Brightness bias',exact:true}).press('ArrowRight');
        await until(()=>fixture.writes.slice(editStart).some(row=>row.domain==='input_number'&&row.data.value===5)&&cooldowns()>start);
        await until(()=>cooldowns()>=start+2);
        checks.push({stage:'explicit_tuning_maintains_cooldowns',writes:cooldowns()-start});
      }
      await page.keyboard.press('Escape');
      if(viewport.width>=700){
        assert.equal(await drawer.isVisible(),true,'the focused native range owns Escape before the overlay');
        await drawer.getByRole('button',{name:/^close/}).click();
      }
      await drawer.waitFor({state:'hidden'});
      const closedWrites=fixture.writes.length;
      if(viewport.width>=700){await page.waitForTimeout(4200);assert.equal(fixture.writes.length,closedWrites,'closing stops tuning maintenance');}
      checks.push({stage:'travel_stale_confirmation_success_refusal_recovery',viewport});
    }
  });
});
