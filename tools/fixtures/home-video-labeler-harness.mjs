// Mounted production labeler; all media and service contracts are authored.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {runFullUiShell} from './home-full-ui-harness.mjs';

export async function runLabeler(name, steps) {
  return runFullUiShell(name, async shell => {
    const {page,origin,checks}=shell;
    const camera=JSON.parse(fs.readFileSync(new URL('./apartment-connected-view.json',import.meta.url),'utf8')).camera;
    const media=Buffer.concat([Buffer.from(camera.init_base64,'base64'),...camera.fragments.map(x=>Buffer.from(x.data_base64,'base64'))]);
    const axes=()=>({activity_primary:[{id:'authored-reading',start_s:0,end_s:.2,value:'reading',review_state:'needs_review',source:'human'}],posture:[],quality:[],custom:[]});
    const state={list:'populated',labels:'ok',media:'ok',jobs:'populated',save:'ok',jobAction:'ok',revision:1,axes:axes()};
    const calls=[],writes=[],videos=[{id:'authored-video-a',filename:'Authored room A.mp4',duration_s:1,fps:30,width:320,height:180,
      import_status:'ready',has_proxy:true,has_sprite:false,is_holdout:false},
      {id:'authored-video-b',filename:'Authored room B.mp4',duration_s:1,fps:30,width:320,height:180,
        import_status:'ready',has_proxy:true,has_sprite:false,is_holdout:false}];
    let listHold=null;
    const holdList=()=>{let release;const promise=new Promise(resolve=>{release=resolve;});listHold=promise;return ()=>{listHold=null;release();};};
    await page.context().route('**/proxy/video-labeler/**',async route=>{
      const request=route.request(),url=new URL(request.url()),method=request.method(),p=url.pathname.slice('/proxy/video-labeler'.length);
      assert.equal(url.origin,origin,'labeler never contacts an upstream');calls.push({path:p,method});
      const json=(body,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
      if(!['GET','HEAD'].includes(method)){
        const body=request.postDataJSON();writes.push({path:p,method,body});
        if(method==='PUT'&&/^\/api\/video-labeler\/videos\/authored-video-[ab]\/labels$/.test(p)){
          if(state.save==='refused')return json({detail:'Authored labels read-only'},403);
          if(state.save==='conflict'){state.revision++;return json({detail:'revision conflict',revision:state.revision,axes:state.axes},409);}
          assert.equal(body.revision,state.revision,'save uses the current revision');
          state.axes=structuredClone(body.axes);state.revision++;return json({revision:state.revision,axes:state.axes,id_map:{}});
        }
        if(method==='POST'&&p==='/api/video-labeler/jobs/authored-job/retry'){
          if(state.jobAction==='refused')return json({detail:'Authored retry refused'},403);
          state.jobs='queued';return json({ok:true});
        }
        if(method==='POST'&&p==='/api/video-labeler/jobs/authored-job/cancel'){
          state.jobs='cancelled';return json({ok:true});
        }
        assert.fail('Unexpected authored write '+method+' '+p);
      }
      if(p==='/healthz')return json({ok:true,jobs_running:0,gpu_free_gb:8,disk_free_gb:50});
      if(p==='/api/video-labeler/videos'){
        if(listHold)await listHold;
        return state.list==='error'?json({detail:'Authored list unavailable'},503):json({videos:state.list==='empty'?[]:videos});
      }
      if(p==='/api/video-labeler/labels/ontology')return json({custom:[]});
      if(/^\/api\/video-labeler\/videos\/authored-video-[ab]\/labels$/.test(p))return state.labels==='ok'
        ?json({revision:state.revision,axes:state.axes}):json({detail:'Authored labels API unavailable'},state.labels==='missing'?404:503);
      if(p==='/api/video-labeler/jobs')return state.jobs==='error'?json({detail:'Authored jobs unavailable'},503):json({jobs:state.jobs==='empty'?[]:
        [{id:'authored-job',type:'proxy',state:state.jobs==='populated'?'failed':state.jobs,progress:.5,error:state.jobs==='populated'?'Authored transcode failure':null}]});
      if(p.endsWith('/stream')){
        if(state.media==='error')return route.fulfill({status:404,body:'Authored media unavailable'});
        const range=request.headers().range,match=range&&/^bytes=(\d+)-(\d*)$/.exec(range);
        const start=match?Number(match[1]):0,end=match&&match[2]?Math.min(Number(match[2]),media.length-1):media.length-1;
        assert.ok(start<=end&&end<media.length,'authored media range is valid');
        return route.fulfill({status:match?206:200,contentType:'video/mp4',headers:{'Accept-Ranges':'bytes',...(match?{'Content-Range':`bytes ${start}-${end}/${media.length}`}:{})},body:media.subarray(start,end+1)});
      }
      return json({detail:'Optional authored capability unavailable'},404);
    });
    try { await steps({...shell,labeler:state,labelerCalls:calls,labelerWrites:writes,videos,holdList}); }
    finally { checks.push({stage:'labeler_authored_contracts',calls,writes,private_media:false,physical_commands:0,
      media:await page.locator('video').evaluateAll(nodes=>nodes.map(v=>({src:v.currentSrc,readyState:v.readyState,width:v.videoWidth,height:v.videoHeight,duration:v.duration,error:v.error?.message}))).catch(()=>[])}); }
  });
}
