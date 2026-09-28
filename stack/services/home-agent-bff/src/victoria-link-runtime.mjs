import path from "node:path";
import { createSecureContext } from "node:tls";
import { createVictoriaSessionStore } from "./bff.mjs";
import { QualifiedHaAuth } from "./qualified-ha-auth.mjs";
import { HaLoginFlow } from "./ha-login-flow.mjs";
import { HaRevocationOutbox } from "./ha-revocation-outbox.mjs";
import { FreshHaCeremony } from "./fresh-ha-ceremony.mjs";
import { GovernedFreshHaCeremony } from "./governed-fresh-ha-ceremony.mjs";
import { SharedAuthProofClient } from "./shared-auth-proof-client.mjs";
import { SharedAuthProofJournal } from "./shared-auth-proof-journal.mjs";
import { SharedSessionRevocationClient } from "./shared-session-revocation-client.mjs";
import { VictoriaSessionHandoff } from "./victoria-session-handoff.mjs";
import { VictoriaLinkCeremony } from "./victoria-link-ceremony.mjs";
import { VictoriaLinkAuthentication } from "./victoria-link-authentication.mjs";
import { createVictoriaHandoffBrowser } from "./victoria-handoff-browser.mjs";
import { createVictoriaHandoffIngress } from "./victoria-handoff-ingress.mjs";

// Trusted provisioning only. Neither listener starts here. The operator must
// bind them separately and stop/drain both before closing durable resources.
export function createVictoriaLinkRuntime(provision) {
  const issuerId="home-assistant:victoria";
  for (const name of ["commitmentKey","journalKey","sessionEncryptionKey"]) {
    if (!Buffer.isBuffer(provision?.[name]) || provision[name].length!==32)
      throw new Error("victoria_runtime_key_rejected");
  }
  const keys=[provision.commitmentKey,provision.journalKey,provision.sessionEncryptionKey];
  if (new Set(keys.map(key=>key.toString("hex"))).size!==keys.length)
    throw new Error("victoria_runtime_separate_keys_required");
  const paths=[provision.sessionDbPath,provision.proofDbPath,provision.revocationDbPath];
  if (paths.some(value=>typeof value!=="string" || !path.isAbsolute(value)) ||
      new Set(paths.map(value=>path.resolve(value).toLowerCase())).size!==paths.length)
    throw new Error("victoria_runtime_database_paths_rejected");
  if (typeof provision.handoffCredential!=="string" || !/^[a-f0-9]{64}$/.test(provision.handoffCredential))
    throw new Error("victoria_runtime_handoff_credential_rejected");
  for (const tls of [provision.browserTls,provision.ingressTls]) {
    if (!tls?.key || !tls?.cert) throw new Error("victoria_runtime_tls_required");
    createSecureContext({...tls,minVersion:"TLSv1.2"});
  }
  const ha={issuerId,siteId:"victoria",origin:provision.haOrigin,
    clientId:provision.clientId,redirectUri:provision.redirectUri};
  const auth=new QualifiedHaAuth(ha), flow=new HaLoginFlow(ha);
  const revocations=new SharedSessionRevocationClient({...provision.sessionRevocation,issuerId});
  const proofClient=new SharedAuthProofClient({...provision.proofIngress,issuerId});
  const resources=[], servers=[];
  const own=resource=>{resources.push(resource);return resource;};
  const close=()=>{
    if (servers.some(server=>server.listening)) throw new Error("victoria_runtime_drain_listeners_first");
    while(resources.length) {resources.at(-1).close();resources.pop();}
  };
  try {
    const {store,config}=createVictoriaSessionStore({auth,
      browserOrigin:provision.browserOrigin,echoOrigins:provision.echoOrigins,
      sessionDbPath:provision.sessionDbPath,sessionEncryptionKey:provision.sessionEncryptionKey,
      idleTtlMs:provision.idleTtlMs,absoluteTtlMs:provision.absoluteTtlMs,
      sharedSessionRevocation:{client:revocations,commitmentKey:provision.commitmentKey}});
    own(store);
    const proofs=own(new SharedAuthProofJournal({databasePath:provision.proofDbPath,
      encryptionKey:provision.journalKey,issuerId}));
    const outbox=own(new HaRevocationOutbox({databasePath:provision.revocationDbPath,
      encryptionKey:provision.journalKey,auth}));
    const handoff=own(new VictoriaSessionHandoff({store,config,commitmentKey:provision.commitmentKey}));
    const ceremony=new VictoriaLinkCeremony({store,config,commitmentKey:provision.commitmentKey,
      ceremony:new GovernedFreshHaCeremony({client:proofClient,journal:proofs,
        ceremony:new FreshHaCeremony({flow,auth,outbox})})});
    const authentication=new VictoriaLinkAuthentication({store,config,handoff,ceremony});
    const browserServer=createVictoriaHandoffBrowser({store,config,handoff,authentication,tls:provision.browserTls});
    servers.push(browserServer);
    const ingressServer=createVictoriaHandoffIngress({handoff,authentication,
      credential:provision.handoffCredential,tls:provision.ingressTls});
    servers.push(ingressServer);
    outbox.startCleanup();
    return Object.freeze({browserServer,ingressServer,close});
  } catch(error) {
    for(const server of servers) server.close();
    close();
    throw error;
  }
}
