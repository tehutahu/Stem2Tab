import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const port = Number(process.env.WEB_PORT) || 4173;
const apiProxyTarget = process.env.API_PROXY_TARGET || "http://api:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port,
    proxy: {
      "/api": {
        target: apiProxyTarget,
        changeOrigin: true,
      },
    },
  },
  preview: {
    host: true,
    port,
    proxy: {
      "/api": {
        target: apiProxyTarget,
        changeOrigin: true,
      },
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
