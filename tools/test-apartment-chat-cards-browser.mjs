import test from 'node:test';import assert from 'node:assert/strict';import fs from 'node:fs';
import {runFullUiShell} from './fixtures/home-full-ui-harness.mjs';
const seeded=[
{id:'qa-card-user',kind:'user',time:'12:00',text:'Show the authored room results.'},
{id:'qa-card-home',kind:'home',time:'12:00',text:'These results use authored local data.'},
{id:'qa-card-action',kind:'action',time:'12:00',title:'Study light',service:'light.turn_off',target:'light.authored_echo_light',attrs:{brightness_pct:30},status:'success',latency:'84ms'},
{id:'qa-card-perception',kind:'perception',time:'12:01',text:'No analyzed image is available in this authored local review.',imageUnavailable:true},
{id:'qa-card-tool',kind:'tool',time:'12:02',name:'read_room_state',args:{room:'Authored study'},status:'success',latency:'4ms'},
{id:'qa-card-review',kind:'personal-memory-review',time:'12:03',text:'Preference review is unavailable in this authored fixture. Nothing is stored.'}
];
test('populated Chat keeps unavailable media captions readable in its narrow pane and on phones',{timeout:145000},async()=>{
 await runFullUiShell('apartment-chat-cards-browser',async({page,origin,openShell,waitSite,tab,checks,receipt})=>{
  receipt.bootstrap_initial_events=true;receipt.private_review_content=false;
  const unexpected=[];
  await page.context().route('**/home-mount.jsx*',route=>route.fulfill({contentType:'text/javascript',body:fs.readFileSync('app/src/home-mount.jsx','utf8').replace('<HomeApp />','<HomeApp autoplay={false} initialEvents={'+JSON.stringify(seeded)+'} />')}));
  await page.context().routeWebSocket('**/*',socket=>{
    const url=new URL(socket.url());if(url.origin.replace(/^ws/,'http')!==origin||url.pathname!=='/proxy/ha/api/websocket')return socket.close();
    const send=value=>socket.send(JSON.stringify(value));socket.onMessage(raw=>{const msg=JSON.parse(String(raw));
      if(msg.type==='auth')return send({type:'auth_ok',ha_version:'authored-cards'});if(msg.type==='ping')return send({type:'pong',id:msg.id});
      const allowed=['get_states','get_config','subscribe_events','unsubscribe_events','config/entity_registry/list','config/device_registry/list','config/area_registry/list','conversation/agent/list'];
      if(!allowed.includes(msg.type))unexpected.push(msg.type);
      send({type:'result',id:msg.id,success:allowed.includes(msg.type),result:msg.type==='get_config'?{unit_system:{length:'m'},components:[]}:[]});
    });send({type:'auth_required',ha_version:'authored-cards'});
  });
  await openShell();await waitSite('echo');await tab('Chat');
  await page.waitForFunction(()=>window.__hav_haClient?.haVersion==='authored-cards');
  for(const viewport of [{width:1366,height:768},{width:390,height:844},{width:390,height:460}]){
    await page.setViewportSize(viewport);
    if(viewport.width<900&&await page.locator('.apt-home-shell').getAttribute('data-sheet')!=='full'){
      await page.getByRole('button',{name:'Resize Home sheet',exact:true}).press('ArrowUp');
      await page.waitForFunction(()=>document.querySelector('.apt-home-shell')?.dataset.sheet==='full');
    }
    const perception=page.locator('.apt-ui-chat-event[data-kind="perception"]'),caption=perception.getByText(seeded[3].text,{exact:true});
    await caption.waitFor();
    const row=await perception.boundingBox(),body=await caption.boundingBox(),status=await perception.getByRole('status').boundingBox();
    assert.ok(body.width>=row.width*.85,'the caption uses the Chat pane width: '+JSON.stringify({viewport,row,body}));
    assert.ok(status.y>=body.y+body.height-1,'the unavailable status follows the caption instead of squeezing it');
    const action=page.getByRole('button',{name:'Study light details',exact:true});await action.click();
    await page.getByText('light.turn_off',{exact:true}).waitFor();await action.click();
    const cmd=page.getByRole('combobox',{name:'Command input',exact:true});await cmd.fill('/');
    assert.equal(await cmd.getAttribute('aria-expanded'),'true','the existing slash picker opens');await cmd.press('Escape');
    assert.equal(await cmd.getAttribute('aria-expanded'),'false');
    assert.equal(await cmd.inputValue(),'/','Escape keeps the command draft');
    await cmd.fill('/w');assert.equal(await cmd.getAttribute('aria-expanded'),'true','typing reopens matching suggestions');await cmd.press('Escape');
    assert.equal(await cmd.evaluate(node=>node===document.activeElement),true,'Escape retains input focus');
    if(process.env.HOME_UI_PRIVATE_STILLS==='1'){
      await page.locator('.apt-ui-chat-feed').hover();await page.mouse.wheel(0,-3000);
      await page.screenshot({path:receipt.run+'/cards-'+viewport.width+'x'+viewport.height+'.png'});
    }
    checks.push({stage:'caption_card_details_and_slash_recovery',viewport});
  }
  assert.deepEqual(unexpected,[]);
 });
});
