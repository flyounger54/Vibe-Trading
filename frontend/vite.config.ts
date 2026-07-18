import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { visualizer } from "rollup-plugin-visualizer";
import path from "path";

const PROXY_PATHS = [
  "/sessions",
  "/swarm/presets",
  "/swarm/runs",
  "/settings/llm",
  "/settings/data-sources",
  "/mandate",
  "/live",
  "/upload",
  "/shadow-reports",
];

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiTarget = env.VITE_API_URL || "http://127.0.0.1:8899";
  const apiProxy = { target: apiTarget, changeOrigin: true };
  const apiProxyWithHtmlFallback = {
    ...apiProxy,
    bypass(req: { headers: { accept?: string } }) {
      if (req.headers.accept?.includes("text/html")) {
        return "/index.html";
      }
    },
  };

  return {
    plugins: [
      react(),
      ...(process.env.ANALYZE
        ? [visualizer({ open: true, gzipSize: true, filename: "dist/bundle-analysis.html" })]
        : []),
    ],
    resolve: {
      alias: { "@": path.resolve(__dirname, "./src") },
    },
    server: {
      port: 5899,
      proxy: {
        // The frontend API facade uses the versioned base path exclusively.
        // Keep this first so local development exercises the same contract as
        // the production FastAPI static host instead of falling through to the
        // SPA HTML response.
        "^/api/v1(?:/|$)": apiProxy,
        ...Object.fromEntries(PROXY_PATHS.map((p) => [p, apiProxy])),
        // SPA RunDetail page — only the two-segment ``/runs/{id}``
        // form should fall back to ``index.html`` on browser navigation.
        // ``/runs/{id}/code`` and ``/runs/{id}/pine`` are API-only and
        // must keep proxying to the backend even when Accept is text/html.
        "^/runs/[^/]+/?$": apiProxyWithHtmlFallback,
        "/runs": apiProxy,
        "/correlation": apiProxyWithHtmlFallback,
        "^/alpha(?:/|$)": apiProxy,
        "^/strategy(?:/|$)": apiProxy,
        "/ml/": apiProxy,
        // Industry-chain shares its prefix between SPA pages
        // (/industry-chain, /industry-chain/{id}) and the API
        // (list, templates, {id}/analyze, {id}/status). The html-fallback
        // discriminates by Accept header: browser navigations (text/html)
        // get index.html; fetch calls (*/*) proxy to the backend.
        "/industry-chain": apiProxyWithHtmlFallback,
      },
    },
    build: {
      rollupOptions: {
        output: {
          manualChunks: {
            "vendor-react": ["react", "react-dom", "react-router-dom"],
            "vendor-charts": ["echarts"],
          },
        },
      },
    },
  };
});
