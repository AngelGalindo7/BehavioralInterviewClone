import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    // Dev proxy: forward API and WS calls to local FastAPI instance
    proxy: {
      "/ws": { target: "ws://localhost:8000", ws: true },
      "/session": "http://localhost:8000",
      "/simli": "http://localhost:8000",
      "/health": "http://localhost:8000",
      "/ready": "http://localhost:8000",
      "/auth": "http://localhost:8000",
      "/admin": "http://localhost:8000",
    },
  },
});
