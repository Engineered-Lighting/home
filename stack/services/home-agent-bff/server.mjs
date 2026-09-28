import { configFromEnv, createBff } from "./src/bff.mjs";
import { createEchoLinkRuntime } from "./src/echo-link-runtime.mjs";
import { loadEchoLinkProvision } from "./src/echo-link-provision.mjs";

const config = configFromEnv();
const provisionFile = process.env.HOME_AGENT_SHARED_LINK_CONFIG_FILE;
const runtime = provisionFile ? createEchoLinkRuntime(config, loadEchoLinkProvision(provisionFile)) : null;
const server = runtime?.server ?? createBff(config);

server.listen(config.port, config.bindHost, () => {
  const state = config.ready ? "ready" : "UNCONFIGURED (private routes fail closed)";
  process.stdout.write(`[home-agent-bff] ${state} on ${config.bindHost}:${config.port}\n`);
});

let stopping = false;
for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => {
    if (stopping) return;
    stopping = true;
    server.close(() => {
      const deadline = Date.now() + 15000;
      const finish = () => {
        try { runtime?.close(); process.exit(0); }
        catch {
          if (Date.now() < deadline) setTimeout(finish, 100);
          else {
            process.stderr.write("[home-agent-bff] shutdown incomplete; durable cleanup retained\n");
            process.exit(1);
          }
        }
      };
      finish();
    });
  });
}
