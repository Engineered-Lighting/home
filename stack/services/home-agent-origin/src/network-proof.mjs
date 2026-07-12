import fs from "node:fs";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

const UNREACHABLE_CODES = new Set(["ENETUNREACH", "EHOSTUNREACH"]);
const EXTERNAL_PROBES = Object.freeze([
  ["192.168.0.1", 80],
  ["1.1.1.1", 443],
]);

function hasDefaultIpv4Route(routeTable) {
  const lines = String(routeTable || "").trim().split(/\r?\n/).slice(1);
  return lines.some((line) => {
    const fields = line.trim().split(/\s+/);
    return fields.length >= 8 && fields[1] === "00000000" && fields[7] === "00000000";
  });
}

function isUnreachableErrorCode(code) {
  return UNREACHABLE_CODES.has(String(code || ""));
}

function requireUnreachable(host, port, timeoutMs = 2_000) {
  return new Promise((resolve, reject) => {
    const socket = net.createConnection({ host, port });
    const fail = (code) => {
      socket.destroy();
      const error = new Error("external route proof failed");
      error.code = code;
      reject(error);
    };
    socket.once("connect", () => fail("CONNECTED"));
    socket.once("error", (error) => {
      if (isUnreachableErrorCode(error.code)) {
        socket.destroy();
        resolve();
      }
      else fail(error.code || "SOCKET_ERROR");
    });
    socket.setTimeout(timeoutMs, () => fail("ETIMEDOUT"));
  });
}

async function main() {
  try {
    if (hasDefaultIpv4Route(fs.readFileSync("/proc/net/route", "utf8"))) {
      throw new Error("default route present");
    }
    const response = await fetch("http://bff:8097/healthz", {
      redirect: "error",
      signal: AbortSignal.timeout(3_000),
    });
    if (!response.ok) throw new Error("BFF unavailable");
    for (const [host, port] of EXTERNAL_PROBES) {
      await requireUnreachable(host, port);
    }
    console.log("[home-agent-origin] network_proof_passed");
  } catch {
    console.error("[home-agent-origin] network_proof_failed");
    process.exitCode = 1;
  }
}

const invokedPath = process.argv[1] ? path.resolve(process.argv[1]) : "";
if (invokedPath && invokedPath === fileURLToPath(import.meta.url)) await main();

export { hasDefaultIpv4Route, isUnreachableErrorCode, requireUnreachable };
