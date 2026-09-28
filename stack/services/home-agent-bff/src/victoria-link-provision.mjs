import fs from "node:fs";
import path from "node:path";
import { BlockList, isIP } from "node:net";
import { jsonHasDuplicateObjectKeys } from "./bff.mjs";

const privateAddresses=new BlockList();
for(const [address,prefix,family] of [["127.0.0.0",8,"ipv4"],["10.0.0.0",8,"ipv4"],
  ["172.16.0.0",12,"ipv4"],["192.168.0.0",16,"ipv4"],["100.64.0.0",10,"ipv4"],
  ["::1",128,"ipv6"],["fc00::",7,"ipv6"]]) privateAddresses.addSubnet(address,prefix,family);
function read(file,maximum) {
  if(typeof file!=="string" || !path.isAbsolute(file)) throw new Error("absolute_provision_file_required");
  const fd=fs.openSync(file,"r");
  try {
    const stat=fs.fstatSync(fd);
    if(!stat.isFile() || stat.size>maximum) throw new Error("provision_file_rejected");
    const bytes=Buffer.alloc(maximum+1),count=fs.readSync(fd,bytes,0,bytes.length,0);
    if(count>maximum) throw new Error("provision_file_rejected");
    return bytes.subarray(0,count);
  } finally {fs.closeSync(fd);}
}
function exact(value,fields) {
  if(!value || typeof value!=="object" || Array.isArray(value) ||
    Object.keys(value).sort().join()!==[...fields].sort().join()) throw new Error("provision_shape_rejected");
}
function listener(value) {
  exact(value,["address","port"]);
  const family=isIP(value.address);
  if(!family || !privateAddresses.check(value.address,family===4?"ipv4":"ipv6") ||
    !Number.isSafeInteger(value.port) || value.port<1024 || value.port>65535)
    throw new Error("private_listener_required");
  return Object.freeze({...value});
}

// Local operator configuration only. Reject inline secrets, arbitrary transport
// callbacks and default/wildcard listeners before the runtime opens its stores.
export function loadVictoriaLinkProvision(file) {
  const raw=new TextDecoder("utf-8",{fatal:true}).decode(read(file,8192));
  if(jsonHasDuplicateObjectKeys(raw)) throw new Error("duplicate_provision_field");
  const value=JSON.parse(raw);
  const fields=["version","browserOrigin","echoOrigins","haOrigin","clientId","redirectUri",
    "idleTtlMs","absoluteTtlMs","sessionDbPath","proofDbPath","revocationDbPath",
    "sessionEncryptionKeyFile","journalKeyFile","commitmentKeyFile","sessionRevocation","proofIngress",
    "handoffCredentialFile","browserTls","ingressTls","browserListener","ingressListener"];
  exact(value,fields);
  if(value.version!==1 || !Array.isArray(value.echoOrigins) || !value.echoOrigins.length ||
    value.echoOrigins.length>4 || new Set(value.echoOrigins).size!==value.echoOrigins.length)
    throw new Error("provision_version_or_origins_rejected");
  const key=name=>{
    const encoded=read(value[name],128).toString("utf8").trim();
    if(!/^[A-Za-z0-9_-]{43}$/.test(encoded)) throw new Error("provision_key_rejected");
    const bytes=Buffer.from(encoded,"base64url");
    if(bytes.length!==32 || bytes.toString("base64url")!==encoded) throw new Error("provision_key_rejected");
    return bytes;
  };
  const credential=file=>{
    const result=read(file,128).toString("utf8").trim();
    if(!/^[a-f0-9]{64}$/.test(result)) throw new Error("provision_credential_rejected");
    return result;
  };
  const transport=name=>{
    exact(value[name],["endpoint","credentialFile"]);
    return {endpoint:value[name].endpoint,credential:credential(value[name].credentialFile)};
  };
  const tls=name=>{
    exact(value[name],["certificateFile","privateKeyFile"]);
    return {cert:read(value[name].certificateFile,131072),key:read(value[name].privateKeyFile,131072)};
  };
  const browserListener=listener(value.browserListener),ingressListener=listener(value.ingressListener);
  if(browserListener.port===ingressListener.port) throw new Error("separate_listener_ports_required");
  return {browserOrigin:value.browserOrigin,echoOrigins:new Set(value.echoOrigins),haOrigin:value.haOrigin,
    clientId:value.clientId,redirectUri:value.redirectUri,idleTtlMs:value.idleTtlMs,absoluteTtlMs:value.absoluteTtlMs,
    sessionDbPath:value.sessionDbPath,proofDbPath:value.proofDbPath,revocationDbPath:value.revocationDbPath,
    sessionEncryptionKey:key("sessionEncryptionKeyFile"),journalKey:key("journalKeyFile"),commitmentKey:key("commitmentKeyFile"),
    sessionRevocation:transport("sessionRevocation"),proofIngress:transport("proofIngress"),
    handoffCredential:credential(value.handoffCredentialFile),browserTls:tls("browserTls"),ingressTls:tls("ingressTls"),
    browserListener,ingressListener};
}
