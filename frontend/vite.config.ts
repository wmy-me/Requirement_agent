import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  base: "/app-next/",
  plugins: [react()],
  server: { proxy: { "/api": { target: process.env.VITE_API_ORIGIN ?? "http://127.0.0.1:8000", changeOrigin: true } } }
});
