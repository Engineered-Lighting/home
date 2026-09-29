// Which home is the owner in? Used only to pick the default home for a Home
// chat command that names none; it never grants authority (either home can be
// named explicitly). Two signals, network first:
//   network  the requesting device's direct Tailscale path is on a home LAN
//   phone    the HA companion app puts the owner's person entity in a home zone
// Responses carry a site and a short label only: never coordinates, IPs or
// entity ids. Every failure is "unknown"; the endpoint itself never fails.
import { execFile } from "node:child_process";
import net from "node:net";

export const SITES = Object.freeze(["echo", "victoria"]);
const HOME = Object.freeze({ echo: "Los Angeles", victoria: "Victoria" });
export const CLIENT_ADDRESS = Symbol("homeWebClientAddress");
const PROXY_HEADER_MAX = 107; // PROXY protocol v1 maximum line length
const PROXY_HEADER_TIMEOUT_MS = 2_000;

// "echo=192.168.0.0/24,victoria=192.168.1.0/24" -> [[site, value], ...]
export function parseSiteList(text) {
  const entries = [];
  for (const part of String(text || "").split(",").map((item) => item.trim()).filter(Boolean)) {
    const match = /^(echo|victoria)=(.+)$/.exec(part);
    if (!match) throw new Error(`invalid site entry: ${part}`);
    entries.push([match[1], match[2].trim()]);
  }
  return entries;
}

function lanMatcher(entries) {
  const lists = entries.map(([site, cidr]) => {
    const [address, bits] = cidr.split("/");
    const family = net.isIP(address);
    if (!family || !/^\d{1,3}$/.test(bits || "")) throw new Error(`invalid LAN: ${cidr}`);
    const list = new net.BlockList();
    list.addSubnet(address, Number(bits), family === 6 ? "ipv6" : "ipv4");
    return [site, list, family === 6 ? "ipv6" : "ipv4"];
  });
  return (address) => {
    const family = net.isIP(address);
    if (!family) return null;
    const type = family === 6 ? "ipv6" : "ipv4";
    const found = new Set(lists.filter(([, list, t]) => t === type && list.check(address, type)).map(([site]) => site));
    return found.size === 1 ? [...found][0] : null;
  };
}

function within(promise, ms) {
  let timer;
  return Promise.race([promise, new Promise((resolve) => { timer = setTimeout(resolve, ms, null); })])
    .finally(() => clearTimeout(timer));
}

function hostOf(endpoint) {
  const text = String(endpoint || "");
  const bracketed = /^\[([^\]]+)\]:\d+$/.exec(text);
  if (bracketed) return bracketed[1];
  const index = text.lastIndexOf(":");
  return index > 0 ? text.slice(0, index) : "";
}

export function tailscaleStatusRunner({ bin = "tailscale", timeoutMs = 2_000 } = {}) {
  return () => new Promise((resolve, reject) => {
    execFile(bin, ["status", "--json"], { timeout: timeoutMs, maxBuffer: 8 * 1024 * 1024, windowsHide: true },
      (error, stdout) => {
        if (error) { reject(error); return; }
        try { resolve(JSON.parse(stdout)); } catch (parseError) { reject(parseError); }
      });
  });
}

export function createHomeLocator({
  lans = [], zones = [], person = "", haTarget = "", haToken = "",
  status = null, fetchImpl = globalThis.fetch, now = Date.now,
  statusTtlMs = 20_000, phoneTtlMs = 10_000, timeoutMs = 2_000,
} = {}) {
  const lanSite = lans.length ? lanMatcher(lans) : () => null;
  const zoneSite = new Map(zones.map(([site, zone]) => [zone.toLowerCase(), site]));
  const phoneEnabled = !!(zoneSite.size && /^person\.[a-z0-9_]+$/.test(person) && haTarget && haToken && fetchImpl);
  let statusCache = null;
  let phoneCache = null;

  async function peers() {
    if (statusCache && now() - statusCache.at < statusTtlMs) return statusCache.value;
    const value = status().then((data) => {
      const byIp = new Map();
      for (const peer of Object.values(data?.Peer || {})) {
        for (const ip of peer?.TailscaleIPs || []) byIp.set(ip, peer);
      }
      return byIp;
    });
    statusCache = { at: now(), value };
    value.catch(() => { if (statusCache?.value === value) statusCache = null; });
    return value;
  }

  async function network(clientIp) {
    if (!lans.length || !status || !net.isIP(clientIp || "")) return null;
    try {
      const peer = (await peers()).get(clientIp);
      // CurAddr is the peer's current direct path; empty when relayed.
      return peer ? lanSite(hostOf(peer.CurAddr)) : null;
    } catch {
      return null;
    }
  }

  async function phone() {
    if (!phoneEnabled) return null;
    if (phoneCache && now() - phoneCache.at < phoneTtlMs) return phoneCache.site;
    let site = null;
    try {
      const response = await fetchImpl(`${haTarget}/api/states/${person}`, {
        headers: { authorization: `Bearer ${haToken}`, accept: "application/json" },
        signal: AbortSignal.timeout(timeoutMs),
      });
      if (response.ok) {
        const body = await response.json();
        site = typeof body?.state === "string" ? zoneSite.get(body.state.toLowerCase()) || null : null;
      }
    } catch {
      site = null;
    }
    phoneCache = { at: now(), site };
    return site;
  }

  return {
    enabled: { network: lans.length > 0 && !!status, phone: phoneEnabled },
    async locate(clientIp) {
      const [byNetwork, byPhone] = await Promise.all([within(network(clientIp), timeoutMs), phone()]);
      if (byNetwork) return { version: 1, site: byNetwork, basis: "network", label: `on the ${HOME[byNetwork]} network` };
      if (byPhone) return { version: 1, site: byPhone, basis: "phone", label: `your phone is in ${HOME[byPhone]}` };
      return { version: 1, site: null, basis: null, label: "location unknown" };
    },
  };
}

// Tailscale Serve's TLS-terminated TCP forward can prepend a PROXY protocol v1
// line naming the tailnet client. Accept it only from loopback (Serve), and
// accept connections without it, so turning the Serve option on or off never
// breaks Home.
export function parseProxyLine(line) {
  const parts = line.split(" ");
  if (parts[0] !== "PROXY") return null;
  if (parts[1] === "UNKNOWN") return { address: "" };
  if (parts.length !== 6 || !["TCP4", "TCP6"].includes(parts[1])) return null;
  const family = net.isIP(parts[2]);
  if ((parts[1] === "TCP4" && family !== 4) || (parts[1] === "TCP6" && family !== 6)) return null;
  if (!net.isIP(parts[3]) || !/^\d{1,5}$/.test(parts[4]) || !/^\d{1,5}$/.test(parts[5])) return null;
  return { address: parts[2] };
}

function isLoopback(address) {
  return address === "127.0.0.1" || address === "::1" || address === "::ffff:127.0.0.1";
}

export function proxyProtocolListener(httpServer) {
  const SIGNATURE = Buffer.from("PROXY ");
  return net.createServer({ pauseOnConnect: false }, (socket) => {
    if (!isLoopback(socket.remoteAddress)) { httpServer.emit("connection", socket); return; }
    let buffered = Buffer.alloc(0);
    const timer = setTimeout(() => socket.destroy(), PROXY_HEADER_TIMEOUT_MS);
    const finish = (rest) => {
      clearTimeout(timer);
      socket.removeListener("data", onData);
      socket.removeListener("error", onError);
      socket.pause();
      if (rest.length) socket.unshift(rest);
      httpServer.emit("connection", socket);
      socket.resume();
    };
    const onError = () => { clearTimeout(timer); socket.destroy(); };
    const onData = (chunk) => {
      buffered = Buffer.concat([buffered, chunk]);
      const prefix = buffered.subarray(0, SIGNATURE.length);
      if (!SIGNATURE.subarray(0, prefix.length).equals(prefix)) { finish(buffered); return; }
      if (buffered.length < SIGNATURE.length) return;
      const end = buffered.indexOf("\r\n");
      if (end < 0) {
        if (buffered.length > PROXY_HEADER_MAX) onError();
        return;
      }
      const parsed = end + 2 <= PROXY_HEADER_MAX ? parseProxyLine(buffered.subarray(0, end).toString("latin1")) : null;
      if (!parsed) { onError(); return; }
      if (parsed.address) socket[CLIENT_ADDRESS] = parsed.address;
      finish(buffered.subarray(end + 2));
    };
    socket.on("data", onData);
    socket.on("error", onError);
  });
}

export function homeLocatorFromEnv(env, { haTarget, haToken }) {
  const lans = parseSiteList(env.HOME_WEB_SITE_LANS);
  const zones = parseSiteList(env.HOME_WEB_SITE_ZONES ?? "echo=home");
  return createHomeLocator({
    lans,
    zones,
    person: String(env.HOME_WEB_LOCATION_PERSON || "").trim(),
    haTarget,
    haToken,
    status: lans.length ? tailscaleStatusRunner({ bin: env.HOME_WEB_TAILSCALE_BIN || "tailscale" }) : null,
  });
}
