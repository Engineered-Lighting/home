// Fixed loopback TLS observation only. This route cannot start a worker,
// execute a model, select an origin, or issue a lighting command.
import https from 'node:https';
import {randomUUID} from 'node:crypto';
export const FAST_TRACKING_PREFIX='/api/perception-tracking/v1';
const privateHeaders={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff','Cross-Origin-Resource-Policy':'same-origin'};
export function createFastTrackingHandler({ca,credential,authenticate,request=https.request,
  onTrace=process.env.DEN_FAST_TRACKING_TRACE==='1'?event=>console.log('[den-fast-tracking-trace-v1] '+JSON.stringify(event)):null}={}) {
  let activeStreams=0,activeReads=0;
  return (req,res)=>{
    if(!String(req.url).startsWith(FAST_TRACKING_PREFIX))return false;
    const reply=(status,error)=>{res.writeHead(status,{...privateHeaders,'Content-Type':'application/json'});res.end(JSON.stringify({error}));};
    if(authenticate?.(req)!==true){reply(401,'tracking_session_required');return true;}
    if(req.headers['x-home-lab-site']!=='victoria'){reply(403,'tracking_site_mismatch');return true;}
    const suffix=req.url.slice(FAST_TRACKING_PREFIX.length);
    if(req.method!=='GET'||!['/stream','/clock'].includes(suffix)||req.headers['transfer-encoding']
        ||req.headers['content-length']&&req.headers['content-length']!=='0'){
      req.resume?.();reply(404,'tracking_read_route_denied');return true;
    }
    const stream=suffix==='/stream';
    if((stream?activeStreams:activeReads)>=2){reply(429,'tracking_viewer_limit');return true;}
    let token;
    try{token=credential();}catch{reply(503,'tracking_unconfigured');return true;}
    if(!ca||typeof token!=='string'||! /^[A-Za-z0-9._~+/=-]{32,512}$/.test(token)){
      reply(503,'tracking_unconfigured');return true;
    }
    if(stream)activeStreams++;else activeReads++;
    let upstream,incoming,done=false,started=false,total=0,traceBuffer='';
    const streamId=randomUUID().replaceAll('-','');
    const trace=(stage,packet=null,extra={})=>onTrace?.({stage,at_s:Number(process.hrtime.bigint())/1e9,clock_domain:'la_monotonic',stream_id:streamId,
      attempt_id:packet?.attempt_id??null,sequence:packet?.sequence??null,frame_key:packet?.frame_key??null,...extra});
    function packetRefs(chunk){
      if(!onTrace||!stream)return [];
      traceBuffer+=chunk.toString('utf8');if(traceBuffer.length>65536)throw Error('tracking_trace_frame_bound');
      const refs=[];let split;
      while((split=traceBuffer.indexOf('\n\n'))>=0){
        const event=traceBuffer.slice(0,split);traceBuffer=traceBuffer.slice(split+2);
        if(!event.startsWith('data: '))throw Error('tracking_trace_event');
        const p=JSON.parse(event.slice(6));
        refs.push({attempt_id:/^[a-f0-9]{32}$/.test(p.attempt_id)?p.attempt_id:null,
          sequence:Number.isSafeInteger(p.sequence)?p.sequence:null,frame_key:/^[a-f0-9]{64}$/.test(p.frame_key)?p.frame_key:null});
      }return refs;
    }
    const finish=()=>{if(done)return;done=true;if(stream)activeStreams--;else activeReads--;clearTimeout(timer);upstream?.destroy();incoming?.destroy();if(!res.writableEnded)res.end();};
    const timer=setTimeout(finish,suffix==='/clock'?2000:900000);timer.unref?.();
    req.once('aborted',finish);res.once('close',finish);
    try{
      upstream=request({hostname:'127.0.0.1',port:9451,servername:'192.168.0.100',method:'GET',path:'/v1/tracking'+suffix,
        ca,rejectUnauthorized:true,agent:false,headers:{Authorization:'Bearer '+token,Accept:suffix==='/clock'?'application/json':'text/event-stream','Accept-Encoding':'identity','X-Home-Tracking-Stream':streamId}},response=>{
        incoming=response;
        const type=response.headers['content-type'];
        if(response.statusCode!==200||type!==(suffix==='/clock'?'application/json':'text/event-stream')
            ||response.headers['content-encoding']&&response.headers['content-encoding']!=='identity'){
          trace('gateway_upstream_rejected',null,{http_status:response.statusCode});reply(503,'tracking_upstream_unavailable');finish();return;
        }
        if(authenticate(req)!==true){reply(401,'tracking_session_required');finish();return;}
        started=true;res.writeHead(200,{...privateHeaders,'Content-Type':type,'X-Accel-Buffering':'no','X-Home-Tracking-Stream':streamId});res.flushHeaders?.();
        response.on('data',chunk=>{
          if(done)return;
          total+=chunk.length;
          if(authenticate(req)!==true||chunk.length>65536||suffix==='/clock'&&total>2048||total>32*1024*1024){finish();return;}
          let refs;try{refs=packetRefs(chunk);}catch{trace('gateway_stream_invalid');finish();return;}
          for(const ref of refs)trace('gateway_received',ref);
          // This is a successful write to the response, not browser receipt.
          const accepted=res.write(chunk);
          for(const ref of refs)trace(accepted?'gateway_written':'gateway_backpressure',ref);
          if(!accepted)finish();
        });
        response.once('error',finish);response.once('aborted',finish);response.once('end',finish);
      });
      upstream.once('error',()=>{if(!started&&!done)reply(503,'tracking_upstream_unavailable');finish();});upstream.end();
    }catch{if(!started&&!done)reply(503,'tracking_upstream_unavailable');finish();}
    return true;
  };
}
