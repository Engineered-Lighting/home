import crypto from "node:crypto";
import https from "node:https";
import { COOKIE_NAME, SessionStore, parseCookies, jsonHasDuplicateObjectKeys } from "./bff.mjs";
import { VictoriaSessionHandoff } from "./victoria-session-handoff.mjs";
import { createVictoriaBrowserLogin } from "./victoria-browser-login.mjs";
import { VictoriaLinkAuthentication } from "./victoria-link-authentication.mjs";
import { readFileSync } from "node:fs";

export const VICTORIA_HANDOFF_BROWSER_PATH="/api/agent/shared-identity/handoff";

// Separate Victoria browser boundary; no legacy Core proxy, arbitrary redirect,
// login-code ingestion or coordinator redemption is exposed by this factory.
export function createVictoriaHandoffBrowser({store,config,handoff,tls,authentication}={}) {
  if(!(store instanceof SessionStore) || !(handoff instanceof VictoriaSessionHandoff) || !handoff.usesBoundary(store,config) ||
    !(config.allowedOrigins instanceof Set) || config.allowedOrigins.size!==1 || !tls?.key || !tls?.cert) throw new Error("invalid_handoff_browser_configuration");
  if(authentication!==undefined && (!(authentication instanceof VictoriaLinkAuthentication) || !authentication.usesBoundary(store,config) || !authentication.usesHandoff(handoff))) throw new Error("invalid_handoff_browser_configuration");
  const origin=[...config.allowedOrigins][0],host=new URL(origin).host;
  const login=createVictoriaBrowserLogin({store,config});
  const assets=authentication ? new Map([
    ["/",["text/html; charset=utf-8",readFileSync(new URL("./victoria-link-page.html",import.meta.url))]],
    ["/link-setup.js",["application/javascript; charset=utf-8",readFileSync(new URL("./victoria-link-page.js",import.meta.url))]],
    ["/link-setup.css",["text/css; charset=utf-8",readFileSync(new URL("./victoria-link-page.css",import.meta.url))]],
  ]) : new Map();
  let active=0;
  const server=https.createServer({...tls,minVersion:"TLSv1.2"},async(req,res)=>{
    const reply=(status,error,result)=>{
      if(res.destroyed || res.writableEnded)return;
      res.writeHead(status,{"content-type":"application/json","cache-control":"no-store","referrer-policy":"no-referrer","x-content-type-options":"nosniff"});
      res.end(JSON.stringify(result || {error}));
    };
    if(authentication && req.method==="GET" && (assets.has(req.url) || req.url==="/link-setup-config")) {
      if(req.socket.encrypted!==true || req.headersDistinct.host?.length!==1 || req.headersDistinct.host[0]!==host) return reply(403,"origin_denied");
      if(req.url==="/link-setup-config") return reply(200,null,{ha_origin:JSON.parse(config.haAuthBinding)[2]});
      const [type,body]=assets.get(req.url);
      res.writeHead(200,{"content-type":type,"cache-control":"no-store","referrer-policy":"no-referrer",
        "x-content-type-options":"nosniff","content-security-policy":"default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"});
      res.end(body);return;
    }
    try {if(await login.handle(req,res))return;}
    catch {return reply(503,"session_unavailable");}
    const authOperation=authentication ? ({"/api/agent/shared-identity/auth-begin":"begin",
      "/api/agent/shared-identity/auth-submit":"submit","/api/agent/shared-identity/auth-outcome":"recover"})[req.url] : null;
    if(req.method!=="POST" || req.url!==VICTORIA_HANDOFF_BROWSER_PATH && !authOperation)return reply(404,"not_found");
    const h=req.headersDistinct;
    if(req.socket.encrypted!==true || h.host?.length!==1 || h.host[0]!==host || h.origin?.length!==1 || h.origin[0]!==origin)
      return reply(403,"origin_denied");
    if(h.authorization || Object.keys(h).some(k=>k.startsWith("x-authenticated-")))return reply(400,"claimed_authority_forbidden");
    if(h.cookie?.length!==1 || h.cookie[0].split(";").filter(v=>v.trim().startsWith(`${COOKIE_NAME}=`)).length!==1)
      return reply(401,"authentication_required");
    const id=parseCookies(h.cookie[0])[COOKIE_NAME];
    let session;
    try{session=store.get(id);}catch{return reply(503,"session_unavailable");}
    if(!session)return reply(401,"authentication_required");
    const csrf=h["x-csrf-token"];
    if(csrf?.length!==1 || typeof session.csrf!=="string")return reply(403,"csrf_denied");
    const actual=Buffer.from(csrf[0]),expected=Buffer.from(session.csrf);
    if(actual.length!==expected.length || !crypto.timingSafeEqual(actual,expected))return reply(403,"csrf_denied");
    if(h["content-encoding"])return reply(400,"invalid_request");
    if(h["content-type"]?.length!==1 || h["content-type"][0]!=="application/json")return reply(415,"json_required");
    const length=h["content-length"];
    const maxBody=authOperation ? 1024 : 512;
    if(length && (length.length!==1 || !/^[0-9]{1,4}$/.test(length[0]) || Number(length[0])>maxBody))return reply(413,"body_too_large");
    if(active>=2)return reply(429,"handoff_busy");
    active++;let timer,accepted=false;
    const operation=(async()=>{
      const chunks=[];let count=0;
      for await(const chunk of req){count+=chunk.length;if(count>maxBody)throw new Error();chunks.push(chunk);}
      const raw=new TextDecoder("utf-8",{fatal:true}).decode(Buffer.concat(chunks));
      if(jsonHasDuplicateObjectKeys(raw))throw new Error();
      const value=JSON.parse(raw);
      if(authOperation) {
        accepted=true;
        return authentication.authenticate(id,authOperation,value);
      }
      if(!value || Array.isArray(value) || Object.keys(value).sort().join(",")!=="consent,pairing_id,version" ||
        value.version!==1 || value.consent!==true)throw new Error();
      accepted=true;
      const offer=await handoff.create({sessionId:id,pairingId:value.pairing_id});
      return {version:1,offer};
    })().finally(()=>{active--;clearTimeout(timer);});
    const timeout=new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error()),10_000);});
    try{reply(200,null,await Promise.race([operation,timeout]));}
    catch{reply(accepted?503:422,accepted?"handoff_unavailable":"invalid_request");}
    finally{if(!req.complete)req.destroy();}
  });
  server.maxHeadersCount=32;server.headersTimeout=10_000;server.requestTimeout=10_000;server.maxRequestsPerSocket=100;
  server.on("listening",()=>store.startCleanup(config,undefined));
  server.on("close",()=>{login.close();store.stopCleanup();});
  return server;
}
