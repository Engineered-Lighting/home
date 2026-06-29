import { defineConfig } from "vitest/config";
import type { ProxyOptions } from "vite";
import react from "@vitejs/plugin-react";

type ProxyTarget = {
  env: string;
  fallback: string;
};

const targets: Record<string, ProxyTarget> = {
  "/proxy/ha": { env: "HOME2_HA_TARGET", fallback: "http://192.168.0.125:8123" },
  "/proxy/metrics": { env: "HOME2_METRICS_TARGET", fallback: "http://192.168.0.100:8092" },
  "/proxy/intelligence": { env: "HOME2_INTELLIGENCE_TARGET", fallback: "http://192.168.0.100:8095" },
  "/proxy/tracker": { env: "HOME2_TRACKER_TARGET", fallback: "http://192.168.0.100:8098" },
  "/proxy/supervisor": { env: "HOME2_SUPERVISOR_TARGET", fallback: "http://192.168.0.100:8093" },
  "/proxy/video-labeler": { env: "HOME2_VIDEO_LABELER_TARGET", fallback: "http://192.168.0.100:8099" },
  "/proxy/vision": { env: "HOME2_VISION_TARGET", fallback: "http://192.168.0.100:8091" }
};

function proxyFor(prefix: string, target: ProxyTarget): ProxyOptions {
  return {
    target: process.env[target.env] || target.fallback,
    changeOrigin: true,
    ws: true,
    secure: false,
    rewrite: (path: string) => path.replace(new RegExp(`^${prefix}`), ""),
    configure: (proxy: { on: (event: "proxyReq", callback: (proxyReq: { removeHeader: (header: string) => void }) => void) => void }) => {
      proxy.on("proxyReq", (proxyReq) => {
        proxyReq.removeHeader("cookie");
      });
    }
  };
}

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5174,
    strictPort: true,
    proxy: Object.fromEntries(
      Object.entries(targets).map(([prefix, target]) => [prefix, proxyFor(prefix, target)])
    )
  },
  preview: {
    host: "127.0.0.1",
    port: 4174,
    strictPort: true
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"]
  }
});
