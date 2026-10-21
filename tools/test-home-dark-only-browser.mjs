// Owner-deferred Light appearance: exercise saved preference migration and
// both mounted Home layouts using isolated authored services, never a home.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
import {installSetupRooms,startSetupServer,openSetupContext,browserOptions,homeShell} from './fixtures/apartment-setup-fixtures.mjs';
const require=createRequire(import.meta.url),{chromium}=require('playwright');
test('saved Light choices open Dark; Apartment and Previous Home expose no theme switch',{timeout:145000},async()=>{
  const run=path.resolve('.tmp/home-dark-only-browser/run-'+Date.now());fs.mkdirSync(run,{recursive:true});
  const rooms=installSetupRooms(run),server=await startSetupServer(rooms);let browser;
  try{
    browser=await chromium.launch(browserOptions());
    const owned=await openSetupContext(browser,server.origin,server.log,{site:'echo',theme:'light'});
    const {page}=owned,shell=homeShell(page,server.origin);await shell.boot('1','echo');
    const appearance=()=>page.evaluate(()=>({html:document.documentElement.dataset.theme,
      root:document.querySelector('[data-home-layout]')?.dataset.theme,
      saved:JSON.parse(localStorage.getItem('hg-prefs')||'{}').theme,
      chrome:document.querySelector('meta[name="theme-color"]')?.content}));
    await page.waitForFunction(()=>JSON.parse(localStorage.getItem('hg-prefs')||'{}').theme==='dark');
    assert.deepEqual(await appearance(),{html:'dark',root:'dark',saved:'dark',chrome:'#050606'});
    for(const viewport of [{width:1366,height:768},{width:390,height:844}]){
      await page.setViewportSize(viewport);
      await page.getByRole('button',{name:'Home menu',exact:true}).click();
      assert.equal(await page.getByRole('menuitem',{name:/^(?:Light|Dark) theme$/}).count(),0);
      assert.equal(await page.getByRole('menuitem',{name:'Previous Home',exact:true}).isVisible(),true);
      await page.keyboard.press('Escape');
    }
    await page.getByRole('button',{name:'Home menu',exact:true}).click();
    await page.getByRole('menuitem',{name:'Previous Home',exact:true}).click();
    await page.locator('[data-home-layout="classic"]').waitFor();
    assert.deepEqual(await appearance(),{html:'dark',root:'dark',saved:'dark',chrome:'#050606'});
    assert.equal(await page.getByRole('button',{name:/Switch to (?:light|dark) mode/i}).count(),0);
    await page.getByRole('button',{name:'Open redesigned Home',exact:true}).click();
    await page.locator('.apt-home-shell').waitFor();
    assert.equal((await appearance()).root,'dark');
    assert.deepEqual(server.log.forbidden,[],'fixture browser cannot reach a household upstream');
    await owned.context.close();
  }finally{await browser?.close();await server.close();}
});
