import { defineConfig, type ProxyOptions } from "vite";
import react from "@vitejs/plugin-react";

const port = Number(process.env.WEB_PORT) || 4173;
const apiProxyTarget = process.env.API_PROXY_TARGET || "http://api:8000";
const apiProxy: ProxyOptions = {
  target: apiProxyTarget,
  changeOrigin: true,
  configure(proxy) {
    proxy.on("error", (_error, _request, response) => {
      if ("writeHead" in response && !response.headersSent) {
        response.writeHead(503, { "content-type": "application/json" });
        response.end(JSON.stringify({ detail: { code: "api_unavailable", message: "Start or restore the local API service." } }));
      }
    });
  },
};

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port,
    proxy: {
      "/api": apiProxy,
    },
  },
  preview: {
    host: true,
    port,
    proxy: {
      "/api": apiProxy,
    },
  },
  build: {
    // AlphaTab is loaded on demand by the score viewer. Its standalone bundle
    // is intentionally larger than Vite's default warning threshold.
    chunkSizeWarningLimit: 1400,
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
  },
});
