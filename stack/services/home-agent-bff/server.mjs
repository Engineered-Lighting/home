import { configFromEnv, createBff } from "./src/bff.mjs";

const config = configFromEnv();
const server = createBff(config);

server.listen(config.port, config.bindHost, () => {
  const state = config.ready ? "ready" : "UNCONFIGURED (private routes fail closed)";
  process.stdout.write(`[home-agent-bff] ${state} on ${config.bindHost}:${config.port}\n`);
});

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => server.close(() => process.exit(0)));
}
