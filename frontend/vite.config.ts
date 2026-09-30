import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";
import { viteSingleFile } from "vite-plugin-singlefile";

export default defineConfig(({ command }) => ({
  plugins: command === "build" ? [react(), viteSingleFile()] : [react()],
  server: {
    port: 5170,
    strictPort: true,
    // Allows access via a Cloudflare quick tunnel (see cloudflared) for
    // remote/cross-network access during development. The leading dot
    // matches any *.trycloudflare.com subdomain, since a quick tunnel gets
    // a new random subdomain each time it's started.
    allowedHosts: [".trycloudflare.com"],
    proxy: {
      "/api": {
        target: "http://localhost:8001",
        changeOrigin: true,
        secure: false,
      },
    },
  },
}));
