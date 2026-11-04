import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The Readpath server (FastAPI) serves the built files from web/dist and the
// JSON API under /api. During development, Vite proxies /api to it.
export default defineConfig({
  base: "/",
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
