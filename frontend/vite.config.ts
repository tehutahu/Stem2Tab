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
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
  },
});

