import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { TanStackRouterVite } from "@tanstack/router-plugin/vite";
import tsconfigPaths from "vite-tsconfig-paths";
import tailwindcss from "@tailwindcss/vite";
import path from "path";

export default defineConfig({
  root: ".",
  plugins: [
    TanStackRouterVite({ 
      target: "react", 
      autoCodeSplitting: true,
      routesDirectory: path.resolve(__dirname, "./routes")
    }),
    tailwindcss(),
    react(),
    tsconfigPaths(),
  ],
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
