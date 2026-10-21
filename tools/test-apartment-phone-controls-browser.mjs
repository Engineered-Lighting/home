// Real mounted Home, authored room/transport only. Display selection must not
// write fixture state, save a room or reach household services.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
import {installSetupRooms,startSetupServer,openSetupContext,browserOptions,homeShell} from './fixtures/apartment-setup-fixtures.mjs';
const require=createRequire(import.meta.url),{chromium}=require('playwright');

test('phone keeps compact radial controls, selected fixture context and usable room navigation',{timeout:145000},async()=>{
  const run=path.resolve('.tmp/apartment-phone-controls-browser/run-'+Date.now());fs.mkdirSync(run,{recursive:true});
  const rooms=installSetupRooms(run),server=await startSetupServer(rooms);let browser;
  try{
    browser=await chromium.launch(browserOptions());
    const {page}=await openSetupContext(browser,server.origin,server.log,{site:'victoria'});
    await page.setViewportSize({width:390,height:844});
    const cdp=await page.context().newCDPSession(page);await cdp.send('Emulation.setTouchEmulationEnabled',{enabled:true});
    await homeShell(page,server.origin).boot('1','victoria');
    await page.waitForFunction(()=>window.__APT_ENGINE?.overlay?.virtualFixturesReadback().fixture_count===4);
    const fixtureState=()=>page.evaluate(()=>window.__APT_ENGINE.overlay.virtualFixturesReadback().fixtures);
    const initial=await fixtureState();
    await page.locator('[data-apt-fixture-label]').last().click();
    const inspector=page.getByLabel('Fixture lighting inspector',{exact:true});await inspector.waitFor();
    const controls=page.getByRole('tab',{name:'Controls',exact:true});
    assert.match(await controls.innerText(),/^S\d+$/,'selected source-derived short tag replaces the phone tab text');
    assert.match(await controls.getAttribute('aria-description'),/Authored socket/,'full fixture identity stays accessible');
    const picker=page.getByRole('combobox',{name:'Radial zone',exact:true});await picker.waitFor();
    assert.equal(await picker.locator('option').count(),7);
    const ring=inspector.getByRole('group',{name:'Radial zones',exact:true});
    assert.equal(Math.round((await ring.boundingBox()).width),118,'handoff ring remains compact');
    const hit=await picker.boundingBox();assert.ok(hit.height>=44&&hit.width>=44,'native equivalent has a full touch target');
    await picker.selectOption('2');
    assert.equal(await picker.inputValue(),'2');
    assert.equal(await ring.getByRole('button',{name:'Radial zone 2',exact:true}).getAttribute('aria-pressed'),'true');
    const toolbar=page.getByRole('toolbar',{name:'Room view',exact:true});
    const row=await toolbar.evaluate(node=>({box:node.getBoundingClientRect().toJSON(),buttons:[...node.querySelectorAll('button')]
      .filter(button=>button.getClientRects().length).map(button=>button.getBoundingClientRect().toJSON())}));
    assert.ok(row.box.left>=0&&row.box.right<=390);
    assert.ok(row.buttons.every(box=>box.height>=44&&box.top>=row.box.top&&box.bottom<=row.box.bottom+.1));
    await page.getByRole('button',{name:'Layers',exact:true}).click();
    const nav=page.getByRole('region',{name:'Room navigation',exact:true});
    const pose=()=>page.evaluate(()=>{const p=window.__APT_ENGINE.rig.snapshotPose();return {az:p.az,zoom:p.zoom};});
    const before=await pose();await nav.getByRole('button',{name:'Orbit right',exact:true}).click();
    await page.waitForFunction(az=>window.__APT_ENGINE.rig.snapshotPose().az!==az,before.az);
    const orbited=await pose();await nav.getByRole('button',{name:'Zoom in',exact:true}).click();
    await page.waitForFunction(zoom=>window.__APT_ENGINE.rig.snapshotPose().zoom!==zoom,orbited.zoom);
    const zoomed=await pose();await nav.getByRole('button',{name:'Fit room',exact:true}).click();
    await page.waitForFunction(zoom=>window.__APT_ENGINE.rig.snapshotPose().zoom!==zoom,zoomed.zoom);
    await page.getByRole('checkbox',{name:'Light pools',exact:true}).focus();
    await page.keyboard.press('Escape');
    await page.getByRole('dialog',{name:'Room layers',exact:true}).waitFor({state:'detached'});
    assert.equal(await inspector.isVisible(),true,'Escape closes only Layers from a focused checkbox');
    assert.deepEqual(await fixtureState(),initial,'zone and navigation leave every fixture unchanged');
    assert.deepEqual(server.log.forbidden,[]);
    assert.equal(server.log.requests.some(request=>!['GET','HEAD'].includes(request.method)),false);
    fs.writeFileSync(path.join(run,'receipt.json'),JSON.stringify({passed:true,authored:true,physical_commands:0,row,before,orbited,zoomed,final:await pose()},null,2));
  }finally{await browser?.close();await server.close();}
});
