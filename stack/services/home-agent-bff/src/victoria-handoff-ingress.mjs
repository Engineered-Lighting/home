import crypto from "node:crypto";
import https from "node:https";
import { jsonHasDuplicateObjectKeys } from "./bff.mjs";
import { VictoriaSessionHandoff } from "./victoria-session-handoff.mjs";
import { VictoriaLinkAuthentication } from "./victoria-link-authentication.mjs";

export const VICTORIA_HANDOFF_PATH="/internal/shared-identity/v1/victoria-handoff";

// Dedicated TLS listener factory; never mounted on legacy BFF or started from
// environment defaults. Browser creation uses a separate authenticated boundary.
export function createVictoriaHandoffIngress({handoff,credential,tls,authentication}={}) {
  if(!(handoff instanceof VictoriaSessionHandoff) || typeof credential!=="string" ||
    !/^[a-f0-9]{64}$/.test(credential) || !tls?.key || !tls?.cert) throw new Error("invalid_handoff_ingress_configuration");
  if(authentication!==undefined && (!(authentication instanceof VictoriaLinkAuthentication) || !authentication.usesHandoff(handoff))) throw new Error("invalid_handoff_ingress_configuration");
  const expected=Buffer.from(`Bearer ${credential}`);
  let active=0;
  const server=https.createServer({...tls,minVersion:"TLSv1.2"},async(req,res)=>{
    const reply=(status,error,result)=>{
      if(res.destroyed || res.writableEnded)return;
      res.writeHead(status,{"content-type":"application/json","cache-control":"no-store"});
      res.end(JSON.stringify(result || {error}));
    };
    const authOperation=authentication ? ({"/internal/shared-identity/v1/victoria-auth-admission":"admit",
      "/internal/shared-identity/v1/victoria-auth-outcome":"outcome"})[req.url] : null;
    if(req.method!=="POST" || req.url!==VICTORIA_HANDOFF_PATH && !authOperation)return reply(404,"not_found");
    if(req.socket.encrypted!==true)return reply(403,"secure_transport_required");
    const headers=req.headersDistinct;
    if(headers.origin || headers.cookie)return reply(403,"service_transport_required");
    if(Object.keys(headers).some(k=>k.startsWith("x-authenticated-")))return reply(400,"identity_headers_forbidden");
    const auth=headers.authorization;
    if(auth?.length!==1)return reply(401,"unauthorized");
    const actual=Buffer.from(auth[0]);
    if(actual.length!==expected.length || !crypto.timingSafeEqual(actual,expected))return reply(401,"unauthorized");
    if(headers["content-encoding"])return reply(400,"invalid_request");
    if(headers["content-type"]?.length!==1 || headers["content-type"][0]!=="application/json")return reply(415,"json_required");
    const length=headers["content-length"];
    if(length && (length.length!==1 || !/^[0-9]{1,4}$/.test(length[0]) || Number(length[0])>1024))return reply(413,"body_too_large");
    if(active>=2)return reply(429,"handoff_busy");
    active++;
    let accepted=false,timer;
    const operation=(async()=>{
      const chunks=[];let count=0;
      for await(const chunk of req){count+=chunk.length;if(count>1024)throw new Error();chunks.push(chunk);}
      const raw=new TextDecoder("utf-8",{fatal:true}).decode(Buffer.concat(chunks));
      if(jsonHasDuplicateObjectKeys(raw))throw new Error();
      const value=JSON.parse(raw);
      if(authOperation) {
        if(!value || Array.isArray(value) || value.version!==1 || Object.keys(value).sort().join(",")!==
          (authOperation==="admit"?"admission,version":"pairing_id,version")) throw new Error();
        accepted=true;
        if(authOperation==="admit") return {version:1,admission:await authentication.admit(value.admission)};
        return {version:1,proof:await authentication.outcome(value.pairing_id)};
      }
      if(!value || Array.isArray(value) || Object.keys(value).sort().join(",")!=="pairing_id,token,version" || value.version!==1)throw new Error();
      accepted=true;
      const result=await handoff.redeem({token:value.token,pairingId:value.pairing_id});
      return {version:1,handoff:result};
    })().finally(()=>{active--;clearTimeout(timer);});
    const timeout=new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error()),10_000);});
    try{reply(200,null,await Promise.race([operation,timeout]));}
    catch{reply(accepted?503:422,accepted?"handoff_outcome_unknown":"invalid_request");}
    finally{
      // Incomplete uploads cannot occupy a slot forever after the response.
      // Redeeming work remains charged until its real operation settles.
      if(!req.complete)req.destroy();
    }
  });
  server.maxHeadersCount=32;server.headersTimeout=10_000;server.requestTimeout=10_000;
  server.maxRequestsPerSocket=100;
  return server;
}
