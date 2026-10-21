// Mounted display controls: real fixture geometry and authored registration.
// Rendering choices must never change fixture state or dispatch commands.
import test from 'node:test';import assert from 'node:assert/strict';
import fs from 'node:fs';import path from 'node:path';import {createRequire} from 'node:module';
import {installSetupRooms,startSetupServer,openSetupContext,browserOptions,homeShell} from './fixtures/apartment-setup-fixtures.mjs';
const require=createRequire(import.meta.url),{chromium}=require('playwright');
test('Layers controls selected/all/off beams and independent pools without changing the lights',{timeout:145000},async()=>{
  const run=path.resolve('.tmp/apartment-light-display-browser/run-'+Date.now());fs.mkdirSync(run,{recursive:true});
  const rooms=installSetupRooms(run),server=await startSetupServer(rooms);let browser;
  try{
    browser=await chromium.launch(browserOptions());const owned=await openSetupContext(browser,server.origin,server.log,{site:'victoria'});
    const {page}=owned;await homeShell(page,server.origin).boot('1','victoria');
    await page.waitForFunction(()=>window.__APT_ENGINE?.overlay?.virtualFixturesReadback().fixture_count>0);
    const read=()=>page.evaluate(()=>{const overlay=window.__APT_ENGINE.overlay;
      const visible=node=>{for(let at=node;at;at=at.parent)if(!at.visible)return false;return !!node;};
      const meshes=[];overlay.virtualFixturesGroup.traverse(node=>{if(node.name==='modeled-aim-volume'||node.name==='modeled-aim-receiver-glow')meshes.push({name:node.name,id:node.userData.fixture_id,visible:visible(node)});});
      return{fixtures:overlay.virtualFixturesReadback().fixtures,display:overlay.virtualFixtureDisplayReadback(),meshes};});
    const initial=await read();assert.equal(initial.display.beams,'selected');assert.ok(initial.meshes.every(node=>node.name!=='modeled-aim-volume'||!node.visible));
    await page.locator('[data-apt-fixture-label]').first().click();
    await page.locator('[aria-label="Fixture lighting inspector"]').waitFor();
    const selected=await read();assert.ok(selected.meshes.filter(node=>node.name==='modeled-aim-volume').some(node=>node.visible));
    assert.equal(await page.getByText('Spot · modeled',{exact:true}).count(),0);
    await page.getByRole('button',{name:'Layers',exact:true}).click();
    await page.getByRole('button',{name:'All beams',exact:true}).click();
    assert.equal((await read()).display.beams,'all');
    await page.getByRole('button',{name:'No beams',exact:true}).click();
    assert.ok((await read()).meshes.filter(node=>node.name==='modeled-aim-volume').every(node=>!node.visible));
    await page.getByRole('checkbox',{name:'Light pools',exact:true}).uncheck();
    assert.equal((await read()).display.pools,false);
    assert.ok((await read()).meshes.filter(node=>node.name==='modeled-aim-receiver-glow').every(node=>!node.visible));
    await page.getByRole('checkbox',{name:'Light pools',exact:true}).check();
    await page.getByRole('button',{name:'Selected beam',exact:true}).click();
    assert.equal((await read()).display.pools,true);
    assert.deepEqual((await read()).fixtures,initial.fixtures,'display controls preserve every fixture channel, aim and binding');
    assert.deepEqual(server.log.forbidden,[]);
    assert.equal(server.log.requests.some(request=>!['GET','HEAD'].includes(request.method)),false,'no command or save');
    fs.writeFileSync(path.join(run,'receipt.json'),JSON.stringify({passed:true,authored:true,household:false,physical_commands:0,initial,selected,final:await read()},null,2));
  }finally{await browser?.close();await server.close();}
});
