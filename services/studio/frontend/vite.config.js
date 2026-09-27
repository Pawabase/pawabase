import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Studio's Python server renders the HTML shell (sillo-inertia) and reads the
// manifest to find the built entry; Vite only builds and serves the scripts.
export default defineConfig({
  plugins: [react()],
  base: "/",
  server: { port: 5173, strictPort: true, cors: true, origin: "http://localhost:5173" },
  build: {
    outDir: "dist",
    manifest: true,
    emptyOutDir: true,
    rollupOptions: { input: "src/main.jsx" },
  },
});
