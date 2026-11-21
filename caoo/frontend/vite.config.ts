import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Frontend 5173, API 8001.
const api = process.env.CAOO_API ?? "http://127.0.0.1:8001";

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true, proxy: { "/api": api } },
});
