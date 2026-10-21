// Existing Echo aim controller inside the shared Light/Aim/Setup navigation.
// Authored transport only; no movement, saves, or household connections.
import test from 'node:test';import assert from 'node:assert/strict';
import fs from 'node:fs';import path from 'node:path';import {createRequire} from 'node:module';
import {installSetupRooms,authoredEchoModel,startSetupServer,openSetupContext,mockHomeAssistant,browserOptions,homeShell} from './fixtures/apartment-setup-fixtures.mjs';
const require=createRequire(import.meta.url),{chromium}=require('playwright');
test('Echo Aim opens directly and retains profiles, return to Light and shared Setup',{timeout:145000},async()=>{
  const run=path.resolve('.tmp/apartment-echo-aim-tabs-browser/run-'+Date.now());fs.mkdirSync(run,{recursive:true});
  const rooms=installSetupRooms(run,{echoModel:seed=>{const model=authoredEchoModel(seed);
    Object.assign(model.devices[0],{fixture_kind:'engineered_gimbal_v1',aiming_origin:'fixture_bottom',
      gimbal:{limits:{pan_min_deg:-180,pan_max_deg:180,tilt_min_deg:-90,tilt_max_deg:90,tilt_positive:'down'},product_profile_sha256:'a'.repeat(64)}});
    return model;}}),server=await startSetupServer(rooms);let browser;
  try{
    browser=await chromium.launch(browserOptions());const {page,context}=await openSetupContext(browser,server.origin,server.log,{site:'echo'});
    const ha=await mockHomeAssistant(context,server.origin,{lights:[{entity_id:'light.authored_ceiling_a',name:'Authored ceiling A'}]});
    await page.addInitScript(origin=>localStorage.setItem('apartment3d.trackerBase',origin+'/proxy/tracker'),server.origin);
    const shell=homeShell(page,server.origin);await shell.boot('1','echo');
    await page.locator('.apt-home-controls .apt-ui-list-button').filter({hasText:'Authored ceiling A'}).click();
    const light=page.getByLabel('Echo light inspector',{exact:true});await light.waitFor();
    await light.getByRole('tab',{name:'Aim',exact:true}).click();
    const aim=page.getByRole('dialog',{name:'Engineered fixture aiming inspector',exact:true});await aim.waitFor();
    assert.equal(await page.locator('[data-apt-aim-inspector]').count(),1,'one existing aiming controller');
    assert.equal(await page.getByRole('button',{name:'Open engineered aim',exact:true}).count(),0,'no intermediate handoff pane');
    assert.equal(await aim.getByRole('heading',{name:'Authored ceiling A',exact:true}).isVisible(),true);
    assert.match(await aim.innerText(),/Authored ceiling A/);
    await aim.getByText('Profiles and shared mount',{exact:true}).click();
    const profiles=aim.getByRole('group',{name:'Lighting profile view',exact:true});
    for(const mode of ['Auto','Current','Engineered','Both']){
      const button=profiles.getByRole('button',{name:mode,exact:true});assert.equal(await button.isEnabled(),true);
      await button.click();assert.equal(await button.getAttribute('aria-pressed'),'true');
    }
    await aim.getByRole('tablist',{name:'Light sections',exact:true}).getByRole('tab',{name:'Light',exact:true}).click();
    await light.waitFor();assert.equal(await light.getByRole('heading',{name:'Authored ceiling A',exact:true}).isVisible(),true);
    await light.getByRole('tab',{name:'Aim',exact:true}).click();await aim.waitFor();
    await aim.getByRole('tablist',{name:'Light sections',exact:true}).getByRole('tab',{name:'Setup',exact:true}).click();
    await shell.inspector().waitFor();assert.equal(await page.getByRole('toolbar',{name:'Room view',exact:true}).isVisible(),true);
    assert.equal(await page.getByRole('group',{name:'Apartment edit orbit controls',exact:true}).count(),0);
    assert.equal(await page.getByRole('button',{name:'Cloud view',exact:true}).isDisabled(),true,'Setup preserves its scene view');
    await page.getByRole('button',{name:'done',exact:true}).click();await shell.waitSetupState('off');
    assert.deepEqual(ha.calls,[]);assert.deepEqual(server.log.forbidden,[]);
    assert.equal(server.log.requests.some(request=>!['GET','HEAD'].includes(request.method)),false);
  }finally{await browser?.close();await server.close();}
});
