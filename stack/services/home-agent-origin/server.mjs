import { configFromEnv, createAgentOrigin } from "./src/origin.mjs";

const config = configFromEnv(process.env);
if (!config.ready) {
  // Deliberately omit configuration values and parse errors. In particular,
  // this process must never become an observability path for OAuth material.
  console.error("[home-agent-origin] configuration_invalid");
  process.exit(1);
}

let server;
try {
  server = createAgentOrigin(config);
  server.once("error", () => {
    console.error("[home-agent-origin] listener_failed");
    process.exit(1);
  });
  server.listen(config.port, config.bindHost, () => {
    console.log("[home-agent-origin] ready");
  });
} catch {
  console.error("[home-agent-origin] startup_failed");
  process.exit(1);
}

function shutdown() {
  server?.close(() => process.exit(0));
  setTimeout(() => process.exit(1), 5_000).unref();
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
