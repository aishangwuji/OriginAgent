import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
import { resolveDevServerPorts } from "./src/devServerConfig";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const target = env.ORIGINAGENT_API_URL ?? "http://127.0.0.1:8765";
  const wsTarget = target.replace(/^http/, "ws");
  const { port, hmrPort } = resolveDevServerPorts(env);
  const animalIslandTestAliases =
    mode === "test"
      ? [
          {
            find: "animal-island-ui/style",
            replacement: path.resolve(__dirname, "./src/tests/mocks/empty-style.ts"),
          },
          {
            find: "animal-island-ui",
            replacement: path.resolve(__dirname, "./src/tests/mocks/animal-island-ui.tsx"),
          },
        ]
      : [];

  return {
    plugins: [react()],
    resolve: {
      alias: [{ find: "@", replacement: path.resolve(__dirname, "./src") }, ...animalIslandTestAliases],
    },
    optimizeDeps: {
      // Radix dialog was introduced mid-session for the mobile sidebar sheet.
      // When Vite re-optimizes it on a running dev server, the browser can race
      // and request stale chunk paths from `.vite/deps`. Excluding it keeps dev
      // reloads stable instead of rewriting those chunk filenames under us.
      exclude: ["@radix-ui/react-dialog"],
    },
    build: {
      outDir: path.resolve(__dirname, "../OriginAgent/web/dist"),
      emptyOutDir: true,
      sourcemap: false,
    },
    server: {
      host: "127.0.0.1",
      port,
      strictPort: true,
      // Move Vite's HMR socket to a dedicated port so it doesn't collide with
      // the ``/`` proxy below (Vite HMR and the OriginAgent ws upgrade both sit on
      // the root path, which triggers spurious write-after-end errors as each
      // side tries to close the other's socket).
      hmr: {
        host: "127.0.0.1",
        port: hmrPort,
      },
      proxy: {
        "/webui": { target, changeOrigin: true },
        "/api": { target, changeOrigin: true },
        "/auth": { target, changeOrigin: true },
        // Forward only WebSocket upgrades on ``/`` to the originagent gateway;
        // plain HTTP GETs on ``/`` must stay with Vite so it can serve the SPA.
        // ``bypass`` returning the original URL skips the proxy for that
        // request; returning undefined lets the proxy (and ws upgrade handler)
        // take it.
        "/": {
          target: wsTarget,
          ws: true,
          changeOrigin: true,
          bypass: (req) =>
            req.headers.upgrade === "websocket" ? undefined : req.url,
        },
      },
    },
    test: {
      environment: "happy-dom",
      globals: true,
      setupFiles: ["./src/tests/setup.ts"],
      alias: animalIslandTestAliases,
    },
  };
});
