import test from 'node:test';
import assert from 'node:assert/strict';
import {runLabeler} from './fixtures/home-video-labeler-harness.mjs';

test('full Home: label edits undo, restore drafts, refuse writes and resolve save conflicts against real service contracts',{timeout:145000},async()=>{
  await runLabeler('home-full-ui-labeler-editor-browser',async({page,openShell,menu,labeler,labelerWrites,checks,inViewport})=>{
    await openShell();await menu('Video labeler');
    const dialog=page.getByRole('dialog',{name:'Video labeler — timeline segment labeling',exact:true});
    const save=dialog.getByRole('button',{name:'save',exact:true}),end=dialog.getByRole('spinbutton',{name:'end s',exact:true});
    const select=async()=>{await save.focus();await page.keyboard.press('ArrowDown');await end.waitFor();};
    const edit=async value=>{await end.fill(String(value));await end.press('Enter');};
    const expectEnd=async value=>{await page.waitForFunction(v=>document.querySelector('[role="dialog"] input[type="number"]')&&[...document.querySelectorAll('[role="dialog"] label')].some(l=>l.textContent==='end s'&&Number(l.querySelector('input')?.value)===v),value);};
    await dialog.getByText('rev 1',{exact:true}).waitFor();await select();assert.equal(Number(await end.inputValue()),.2);
    await edit(.3);await expectEnd(.3);
    await save.focus();await page.keyboard.press('Control+z');await expectEnd(.2);
    await page.keyboard.press('Control+y');await expectEnd(.3);
    await page.waitForFunction(()=>window.HomeVideoLabelerData.loadDraft('authored-video-a')?.doc?.axes?.activity_primary?.[0]?.end_s===.3);
    await dialog.getByRole('button',{name:'Close',exact:true}).click();await dialog.waitFor({state:'hidden'});
    await menu('Video labeler');await dialog.getByRole('button',{name:'restore 1 segments',exact:true}).click();
    await select();await expectEnd(.3);
    labeler.save='refused';await inViewport(save);await save.click();await dialog.getByText(/save failed.*Authored labels read-only/).waitFor();
    await expectEnd(.3);assert.equal(labelerWrites.length,1);assert.equal(labelerWrites[0].body.revision,1);
    labeler.save='conflict';await save.click();await dialog.getByText(/save conflict.*revision 2/).waitFor();await expectEnd(.3);
    await dialog.getByRole('button',{name:'reload server copy',exact:true}).click();await dialog.getByText('rev 2',{exact:true}).waitFor();
    await select();await expectEnd(.2);await edit(.4);labeler.save='ok';await save.click();await dialog.getByText('rev 3',{exact:true}).waitFor();
    assert.equal(labeler.axes.activity_primary[0].end_s,.4);assert.equal(labelerWrites.at(-1).body.revision,2);
    await edit(.5);labeler.save='conflict';await save.click();await dialog.getByText(/save conflict.*revision 4/).waitFor();
    labeler.save='ok';await dialog.getByRole('button',{name:'overwrite',exact:true}).click();await dialog.getByText('rev 5',{exact:true}).waitFor();
    assert.equal(labelerWrites.at(-1).body.revision,4);assert.equal(labeler.axes.activity_primary[0].end_s,.5);
    assert.equal(await page.evaluate(()=>window.HomeVideoLabelerData.loadDraft('authored-video-a')),null,'a confirmed save clears the local draft');
    labeler.labels='missing';await dialog.getByRole('button',{name:/Authored room B.mp4/}).click();
    await dialog.getByText('labels API not deployed',{exact:true}).waitFor();assert.equal(await save.isDisabled(),true);
    labeler.labels='error';await dialog.getByRole('button',{name:/Authored room A.mp4/}).click();
    await dialog.getByText('Authored labels API unavailable',{exact:true}).waitFor();assert.equal(await save.isDisabled(),true);
    labeler.labels='ok';await dialog.getByRole('button',{name:/Authored room B.mp4/}).click();await dialog.getByText('rev 5',{exact:true}).waitFor();assert.equal(await save.isEnabled(),true);
    assert.deepEqual(labelerWrites.map(x=>({method:x.method,revision:x.body.revision,end:x.body.axes.activity_primary[0].end_s})),
      [{method:'PUT',revision:1,end:.3},{method:'PUT',revision:1,end:.3},{method:'PUT',revision:2,end:.4},{method:'PUT',revision:3,end:.5},{method:'PUT',revision:4,end:.5}],
      'only explicit authored saves write, carrying the retained edit and correct revision');
    checks.push({stage:'labeler_edit_undo_redo_draft_refusal_conflict_reload_overwrite_and_api_recovery',writes:labelerWrites.length,revision:labeler.revision});
    await dialog.getByRole('button',{name:'Close',exact:true}).focus();await page.keyboard.press('Escape');await dialog.waitFor({state:'hidden'});
  });
});
