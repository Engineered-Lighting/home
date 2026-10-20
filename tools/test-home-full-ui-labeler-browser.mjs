import test from 'node:test';
import assert from 'node:assert/strict';
import {runLabeler} from './fixtures/home-video-labeler-harness.mjs';

test('full Home: Video labeler loads, recovers, plays media, handles job refusal and preserves desktop-only availability',{timeout:145000},async()=>{
  await runLabeler('home-full-ui-labeler-browser',async({page,openShell,menu,labeler,holdList,labelerWrites,viewports,inViewport,checks})=>{
    await openShell();
    const dialog=page.getByRole('dialog',{name:'Video labeler — timeline segment labeling',exact:true});
    const refresh=()=>dialog.getByRole('button',{name:'refresh',exact:true}).click();
    const release=holdList();
    try{await menu('Video labeler');await dialog.getByText('loading videos…',{exact:true}).waitFor();}
    finally{release();}
    await dialog.getByRole('button',{name:/Authored room A.mp4/}).waitFor();
    labeler.list='error';await refresh();await dialog.getByText(/service unreachable.*Authored list unavailable/).waitFor();
    labeler.list='empty';await refresh();await dialog.getByText(/no videos yet/).waitFor();
    labeler.list='populated';await refresh();await dialog.getByRole('button',{name:/Authored room A.mp4/}).waitFor();
    await dialog.getByRole('button',{name:'Close',exact:true}).click();await dialog.waitFor({state:'hidden'});
    for(const viewport of viewports){
      await page.setViewportSize(viewport);
      if(viewport.width<700){
        await page.getByRole('button',{name:'Home menu',exact:true}).click();
        await page.getByRole('menu').waitFor();
        assert.equal(await page.getByRole('menuitem',{name:'Video labeler',exact:true}).count(),0,'phone menu follows the existing desktop-only labeler contract');
        await page.keyboard.press('Escape');assert.equal(await dialog.count(),0);
        checks.push({stage:'labeler_phone_desktop_only_menu'});continue;
      }
      await menu('Video labeler');await dialog.waitFor();
      const video=dialog.locator('video');await page.waitForFunction(()=>{const v=document.querySelector('[role="dialog"] video');return v?.readyState>=2&&v.videoWidth===320;});
      await dialog.getByRole('button',{name:/Authored room B.mp4/}).click();
      await video.waitFor();
      await page.waitForFunction(()=>document.querySelector('[role="dialog"] video')?.currentSrc.includes('authored-video-b'));
      const play=dialog.getByRole('button',{name:'play',exact:true});await inViewport(play);
      await play.click();await page.waitForFunction(()=>document.querySelector('[role="dialog"] video')?.currentTime>0);
      assert.equal(await video.evaluate(v=>v.videoWidth),320,'the selected authored media decodes');
      if(await dialog.getByRole('button',{name:'pause',exact:true}).isVisible())await dialog.getByRole('button',{name:'pause',exact:true}).click();
      const close=dialog.getByRole('button',{name:'Close',exact:true});await inViewport(close);
      await dialog.getByRole('button',{name:'jobs',exact:true}).click();await dialog.getByText('Authored transcode failure',{exact:true}).waitFor();
      labeler.jobAction='refused';await dialog.getByRole('button',{name:'retry',exact:true}).click();await dialog.getByText(/retry failed.*Authored retry refused/).waitFor();
      labeler.jobAction='ok';await dialog.getByRole('button',{name:'retry',exact:true}).click();await dialog.getByRole('cell',{name:'queued',exact:true}).waitFor();
      const cancel=dialog.getByRole('button',{name:'cancel',exact:true});await inViewport(cancel);await cancel.click();
      await dialog.getByRole('cell',{name:'cancelled',exact:true}).waitFor();
      await dialog.getByRole('button',{name:'label',exact:true}).click();
      labeler.jobs='error';await dialog.getByRole('button',{name:'jobs',exact:true}).click();await dialog.getByRole('alert').filter({hasText:'Authored jobs unavailable'}).waitFor();
      await dialog.getByRole('button',{name:'label',exact:true}).click();labeler.jobs='empty';await dialog.getByRole('button',{name:'jobs',exact:true}).click();await dialog.getByText(/no jobs yet/).waitFor();
      await dialog.getByRole('button',{name:'label',exact:true}).click();
      labeler.media='error';
      await dialog.getByRole('button',{name:/Authored room A.mp4/}).click();
      await dialog.getByText('stream unavailable',{exact:true}).waitFor();
      labeler.media='ok';await dialog.getByRole('button',{name:'retry',exact:true}).click();
      await page.waitForFunction(()=>document.querySelector('[role="dialog"] video')?.videoWidth===320);
      checks.push({stage:'labeler_selection_media_jobs_refusal_recovery',viewport});
      await dialog.getByRole('button',{name:'Close',exact:true}).focus();await page.keyboard.press('Escape');await dialog.waitFor({state:'hidden'});
    }
    assert.equal(labelerWrites.filter(x=>x.path.endsWith('/retry')).length,2);assert.equal(labelerWrites.filter(x=>x.path.endsWith('/cancel')).length,1);
  });
});
