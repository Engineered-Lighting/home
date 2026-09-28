import { pathToFileURL } from "node:url";
import { createVictoriaLinkRuntime } from "./src/victoria-link-runtime.mjs";
import { loadVictoriaLinkProvision } from "./src/victoria-link-provision.mjs";

export async function startVictoriaLinkServer(file) {
  const provision=loadVictoriaLinkProvision(file);
  const runtime=createVictoriaLinkRuntime(provision);
  const servers=[runtime.browserServer,runtime.ingressServer];
  const stop=async()=>{
    await Promise.all(servers.map(server=>new Promise(resolve=>server.close(resolve))));
    runtime.close();
  };
  try {
    for(const [server,binding] of [[runtime.browserServer,provision.browserListener],
      [runtime.ingressServer,provision.ingressListener]]) {
      await new Promise((resolve,reject)=>{
        const failed=error=>{server.off("listening",ready);reject(error);};
        const ready=()=>{server.off("error",failed);resolve();};
        server.once("error",failed);server.once("listening",ready);
        server.listen(binding.port,binding.address);
      });
    }
    return Object.freeze({stop});
  } catch(error) {await stop();throw error;}
}

if(process.argv[1] && import.meta.url===pathToFileURL(process.argv[1]).href) {
  try {
    const runtime=await startVictoriaLinkServer(process.env.HOME_AGENT_VICTORIA_LINK_CONFIG_FILE);
    process.stdout.write("Victoria linking listeners ready\n");
    let stopping=false;
    for(const signal of ["SIGINT","SIGTERM"]) process.on(signal,()=>{
      if(stopping)return;
      stopping=true;
      const deadline=Date.now()+15000;
      const watchdog=setTimeout(()=>process.exit(1),15000);
      const finish=async()=>{
        try {await runtime.stop();clearTimeout(watchdog);process.exit(0);}
        catch {
          if(Date.now()<deadline)setTimeout(finish,100);
          else process.exit(1);
        }
      };
      void finish();
    });
  } catch {
    // Configuration and TLS errors may contain credentials or private paths.
    process.stderr.write("Victoria linking startup failed\n");process.exitCode=78;
  }
}
