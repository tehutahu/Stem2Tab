import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const port = Number(process.env.WEB_PORT) || 4173;

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port,
  },
  preview: {
    host: true,
    port,
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
  },
});

