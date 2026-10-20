// Simulation controls and Previous Home use the actual controller. Room data
// is authored; private repository camera stills are explicitly never served.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {runFullUiShell} from './fixtures/home-full-ui-harness.mjs';

test('full Home: Simulation stays disclosed, changes/resets state, and exits from Previous Home without losing the draft', {timeout:145000}, async()=>{
  await runFullUiShell('home-full-ui-simulation-browser', async ({page,origin,echo,openShell,menu,tab,checks,viewports,inViewport})=>{
    await page.context().route('**/simulation/cameras/**',route=>route.fulfill({status:404,body:'Private camera stills excluded from authored verification'}));
    const assets={...echo.files,'points.sim.ply':echo.files['points.ply']};
    assets['manifest.json']=Buffer.from(JSON.stringify({version:1,files:Object.fromEntries(Object.entries(assets).map(([name,bytes])=>
      [name,{bytes:bytes.length,sha8:createHash('sha256').update(bytes).digest('hex').slice(0,8)}]))}));
    await page.context().route('**/assets/apartment/**',route=>{
      const request=route.request(),url=new URL(request.url());assert.equal(url.origin,origin);assert.equal(request.method(),'GET');
      const bytes=assets[url.pathname.slice('/assets/apartment/'.length)];
      return route.fulfill({status:bytes?200:404,contentType:url.pathname.endsWith('.json')?'application/json':'application/octet-stream',body:bytes||'Only authored simulation assets are served'});
    });
    for(const viewport of viewports){
      await page.setViewportSize(viewport);
      await openShell('?apartmentRedesign=1&worldZoom=off&simulation=1&scenario=healthy');
      const notice=page.locator('.apt-home-topbar').getByRole('button',{name:'Simulation active — open controls',exact:true});
      await notice.waitFor();await inViewport(notice);
      assert.equal(await page.evaluate(()=>window.__SIM_ACTIVE),true);
      await notice.click();
      const dialog=page.getByRole('dialog',{name:'Simulation controls',exact:true});
      const scenario=dialog.getByRole('combobox',{name:'Simulation scenario',exact:true});
      assert.equal(await scenario.inputValue(),'healthy');
      if(viewport.width<700)for(const action of [scenario,dialog.getByRole('button',{name:'reset',exact:true}),dialog.getByRole('button',{name:'exit',exact:true}),dialog.getByRole('button',{name:'Close simulation controls',exact:true})]){
        const box=await action.boundingBox();
        checks.push({stage:'simulation_phone_target',name:await action.getAttribute('aria-label')||await action.innerText(),box});
        // getBoundingClientRect can lose a few fractional bits during the
        // panel's translateY entrance. Retain the44px bound to .001px precision.
        assert.ok(box.width+.001>=44&&box.height+.001>=44,'phone Simulation controls have44px targets: '+JSON.stringify(box));
      }
      await scenario.selectOption('empty');assert.equal(await scenario.inputValue(),'empty');
      await dialog.getByRole('button',{name:'Close simulation controls',exact:true}).focus();
      await page.keyboard.press('Escape');await dialog.waitFor({state:'hidden'});
      await tab('Chat');await page.getByText(/\[sim\] empty scenario — minimal history/).waitFor();
      await menu('Simulation');await dialog.getByRole('button',{name:'reset',exact:true}).click();
      await scenario.filter({has:page.locator('option:checked[value="healthy"]')}).waitFor();
      await page.keyboard.press('Escape');await dialog.waitFor({state:'hidden'});
      await page.getByText('good morning',{exact:true}).first().waitFor();
      const draft=page.getByRole('combobox',{name:'Command input',exact:true});await draft.fill('Authored simulation draft');
      await menu('Previous Home');await page.locator('.apt-home-shell[data-home-layout="unified"]').waitFor({state:'hidden'});
      assert.equal(await draft.inputValue(),'Authored simulation draft','Previous Home preserves the controller draft');
      await page.getByRole('button',{name:'Open simulation controls',exact:true}).click();
      await dialog.getByRole('button',{name:'exit',exact:true}).click();await dialog.waitFor({state:'hidden'});
      assert.equal(await page.evaluate(()=>window.__SIM_ACTIVE),false);
      assert.equal(new URL(page.url()).searchParams.has('simulation'),false,'exit cannot reactivate on reload');
      assert.equal(new URL(page.url()).searchParams.has('scenario'),false);
      assert.equal(await draft.inputValue(),'Authored simulation draft');
      checks.push({stage:'simulation_disclosure_empty_reset_previous_home_exit',viewport});
    }
  });
});
