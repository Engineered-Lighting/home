// Actual Remote profile dialog and HomeServices probes, all same-origin and authored.
import test from 'node:test';
import assert from 'node:assert/strict';
import {runFullUiShell} from './fixtures/home-full-ui-harness.mjs';

test('full Home: connection probes show failure, recover, and preserve phone actions and keyboard focus', {timeout:145000}, async()=>{
  await runFullUiShell('home-full-ui-connection-browser', async ({page,origin,openShell,menu,checks,viewports,inViewport})=>{
    let failed=true,probes=0;
    await page.context().route('**/proxy/metrics/healthz',route=>{
      assert.equal(new URL(route.request().url()).origin,origin);assert.equal(route.request().method(),'GET');probes++;
      return route.fulfill({status:failed?503:200,contentType:'application/json',body:JSON.stringify({ok:!failed,service:'authored-metrics'})});
    });
    await openShell();
    const dialog=page.getByRole('dialog',{name:'Remote profile',exact:true});
    for(const viewport of viewports){
      await page.setViewportSize(viewport);await menu('Preferences / connection');
      await dialog.waitFor();assert.match(await dialog.innerText(),/Web gateway.*travel/);
      const metrics=dialog.getByText('Metrics',{exact:true});
      const status=metrics.locator('xpath=following-sibling::*[2]');
      failed=true;const before=probes;
      await dialog.getByRole('button',{name:'test all',exact:true}).click();
      await status.filter({hasText:'HTTP 503'}).waitFor();assert.ok(probes>before,'real resolver issued the metrics probe');
      await dialog.getByRole('button',{name:'test all',exact:true}).waitFor();
      failed=false;
      await dialog.getByRole('button',{name:'test all',exact:true}).press('Space');
      await status.filter({hasText:/^\d+ms$/}).waitFor();
      const action=dialog.getByRole('button',{name:'test all',exact:true});await inViewport(action);
      if(viewport.width<700){
        for(const target of [action,dialog.getByRole('button',{name:'x',exact:true})]){
          const box=await target.boundingBox();assert.ok(box.height>=44&&box.width>=44,'phone connection primary and close targets are at least44px');
        }
      }
      const focusable=dialog.locator('button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href]');
      const first=focusable.first(),last=focusable.last();
      await last.focus();await page.keyboard.press('Tab');assert.equal(await first.evaluate(node=>node===document.activeElement),true,'Tab wraps to the first dialog action');
      await first.focus();await page.keyboard.press('Shift+Tab');assert.equal(await last.evaluate(node=>node===document.activeElement),true,'Shift+Tab wraps to the last dialog action');
      await action.focus();await page.keyboard.press('Escape');await dialog.waitFor({state:'hidden'});
      assert.equal(await page.evaluate(()=>{const a=document.activeElement;return !!a&&a!==document.body&&a.isConnected&&a.getClientRects().length>0;}),true,'closing restores a visible focus target');
      checks.push({stage:'connection_failure_retry_recovery_keyboard_phone',viewport,probes});
    }
  });
});
