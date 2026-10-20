// Fixed loopback TLS observation only. This route cannot start a worker,
// execute a model, select an origin, or issue a lighting command.
import https from 'node:https';
export const FAST_TRACKING_PREFIX='/api/perception-tracking/v1';
const privateHeaders={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff','Cross-Origin-Resource-Policy':'same-origin'};
export function createFastTrackingHandler({ca,credential,authenticate,request=https.request}={}) {
  let active=0;
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
    if(active>=2){reply(429,'tracking_viewer_limit');return true;}
    let token;
    try{token=credential();}catch{reply(503,'tracking_unconfigured');return true;}
    if(!ca||typeof token!=='string'||! /^[A-Za-z0-9._~+/=-]{32,512}$/.test(token)){
      reply(503,'tracking_unconfigured');return true;
    }
    active++;let upstream,incoming,done=false,started=false,total=0;
    const finish=()=>{if(done)return;done=true;active--;clearTimeout(timer);upstream?.destroy();incoming?.destroy();if(!res.writableEnded)res.end();};
    const timer=setTimeout(finish,suffix==='/clock'?2000:900000);timer.unref?.();
    req.once('aborted',finish);res.once('close',finish);
    try{
      upstream=request({hostname:'127.0.0.1',port:9451,servername:'192.168.0.100',method:'GET',path:'/v1/tracking'+suffix,
        ca,rejectUnauthorized:true,agent:false,headers:{Authorization:'Bearer '+token,Accept:suffix==='/clock'?'application/json':'text/event-stream','Accept-Encoding':'identity'}},response=>{
        incoming=response;
        const type=response.headers['content-type'];
        if(response.statusCode!==200||type!==(suffix==='/clock'?'application/json':'text/event-stream')
            ||response.headers['content-encoding']&&response.headers['content-encoding']!=='identity'){
          reply(503,'tracking_upstream_unavailable');finish();return;
        }
        if(authenticate(req)!==true){reply(401,'tracking_session_required');finish();return;}
        started=true;res.writeHead(200,{...privateHeaders,'Content-Type':type,'X-Accel-Buffering':'no'});res.flushHeaders?.();
        response.on('data',chunk=>{
          if(done)return;
          total+=chunk.length;
          if(authenticate(req)!==true||chunk.length>65536||suffix==='/clock'&&total>2048||total>32*1024*1024){finish();return;}
          // A slow browser loses the stream instead of retaining old poses.
          if(!res.write(chunk))finish();
        });
        response.once('error',finish);response.once('aborted',finish);response.once('end',finish);
      });
      upstream.once('error',()=>{if(!started&&!done)reply(503,'tracking_upstream_unavailable');finish();});upstream.end();
    }catch{if(!started&&!done)reply(503,'tracking_upstream_unavailable');finish();}
    return true;
  };
}
