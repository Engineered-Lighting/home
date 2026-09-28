import path from "node:path";
import { SessionStore, createBff } from "./bff.mjs";
import { SharedSessionRevocationClient } from "./shared-session-revocation-client.mjs";
import { SharedLinkPairingJournal } from "./shared-link-pairing-journal.mjs";
import { SharedLinkIssuanceClient } from "./shared-link-issuance-client.mjs";
import { VictoriaHandoffClient } from "./victoria-handoff-client.mjs";
import { EchoLinkStart } from "./echo-link-start.mjs";
import { EchoLinkCeremony } from "./echo-link-ceremony.mjs";
import { EchoLinkReview } from "./echo-link-review.mjs";
import { HaLoginFlow } from "./ha-login-flow.mjs";
import { QualifiedHaAuth } from "./qualified-ha-auth.mjs";
import { HaRevocationOutbox } from "./ha-revocation-outbox.mjs";
import { FreshHaCeremony } from "./fresh-ha-ceremony.mjs";
import { GovernedFreshHaCeremony } from "./governed-fresh-ha-ceremony.mjs";
import { SharedAuthProofClient } from "./shared-auth-proof-client.mjs";
import { SharedAuthProofJournal } from "./shared-auth-proof-journal.mjs";
import { PersonalMemoryClient } from "./personal-memory-client.mjs";

// Trusted provisioning only. This factory neither reads browser configuration
// nor enables itself in the legacy server. Keys must survive process restarts.
export function createEchoLinkRuntime(baseConfig, provision) {
  const issuerId = "home-assistant:echo";
  const homeOrigins=provision?.personalMemoryHomeOrigins ?? [];
  if (!Array.isArray(homeOrigins) || homeOrigins.length>4 || new Set(homeOrigins).size!==homeOrigins.length ||
      homeOrigins.some(origin=>{
        try {const url=new URL(origin);return url.protocol!=="https:" || url.origin!==origin ||
          [...baseConfig.allowedOrigins].some(agent=>new URL(agent).hostname===url.hostname);}
        catch {return true;}
      })) throw new Error("preference_home_origin_rejected");
  if (!baseConfig.ready || !baseConfig.secureCookie || baseConfig.allowInMemorySessions ||
      !Buffer.isBuffer(provision?.commitmentKey) || provision.commitmentKey.length !== 32 ||
      !Buffer.isBuffer(provision?.journalKey) || provision.journalKey.length !== 32) {
    throw new Error("link_runtime_configuration_rejected");
  }
  const paths = [baseConfig.sessionDbPath, provision.pairingDbPath,
    provision.proofDbPath, provision.revocationDbPath];
  if (paths.some(value => typeof value !== "string" || !path.isAbsolute(value)) ||
      new Set(paths.map(value => path.resolve(value).toLowerCase())).size !== paths.length) {
    throw new Error("link_runtime_database_paths_rejected");
  }
  const ha = { issuerId, siteId: "echo", origin: baseConfig.haUrl,
    clientId: baseConfig.clientId, redirectUri: baseConfig.redirectUri };
  // Validate network bindings before opening any persistent resources.
  const auth = new QualifiedHaAuth(ha), flow = new HaLoginFlow(ha);
  const revocations = new SharedSessionRevocationClient({ ...provision.sessionRevocation, issuerId });
  const proofsClient = new SharedAuthProofClient({ ...provision.proofIngress, issuerId });
  const handoff = new VictoriaHandoffClient(provision.victoriaHandoff);
  const issuance = new SharedLinkIssuanceClient(provision.linkIssuance);
  const config = { ...baseConfig, personalMemoryHomeOrigins:Object.freeze([...homeOrigins]), sharedSessionRevocation: {
    client: revocations, commitmentKey: provision.commitmentKey } };
  const resources = [];
  const own = resource => { resources.push(resource); return resource; };
  // On shutdown a busy revocation outbox keeps its durable work and must be
  // drained before retrying close; never swallow that failure or erase its DB.
  const close = () => {
    while (resources.length) {
      resources.at(-1).close();
      resources.pop();
    }
  };
  try {
    const store = own(new SessionStore(config));
    const journal = own(new SharedLinkPairingJournal({ databasePath: provision.pairingDbPath,
      encryptionKey: provision.journalKey }));
    const proofs = own(new SharedAuthProofJournal({ databasePath: provision.proofDbPath,
      encryptionKey: provision.journalKey, issuerId }));
    const outbox = own(new HaRevocationOutbox({ databasePath: provision.revocationDbPath,
      encryptionKey: provision.journalKey, auth }));
    const ceremony = new EchoLinkCeremony({ store, commitmentKey: provision.commitmentKey,
      ceremony: new GovernedFreshHaCeremony({ client: proofsClient, journal: proofs,
        ceremony: new FreshHaCeremony({ flow, auth, outbox }) }) });
    const review = new EchoLinkReview({ ...provision.linkReview, store,
      commitmentKey: provision.commitmentKey });
    const linkStart = new EchoLinkStart({ store, config, journal, handoff, issuance,
      ceremony, review, commitmentKey: provision.commitmentKey,
      victoriaBrowserOrigin: provision.victoriaBrowserOrigin });
    if (!linkStart.browserSetup) throw new Error("link_runtime_browser_setup_required");
    const personalMemory = provision.personalMemory ? new PersonalMemoryClient({
      ...provision.personalMemory,store,config,commitmentKey:provision.commitmentKey }) : undefined;
    const server = createBff(config, { store, linkStart, linkReview: review, personalMemory });
    outbox.startCleanup();
    // The caller stops/drains the HTTP server before closing owned journals.
    return Object.freeze({ server, close });
  } catch (error) {
    close();
    throw error;
  }
}
