import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";
import { existsSync, readFileSync } from "node:fs";

const DEFAULT_WEB_BASE = "/lunar_analyst/";

function normalizeMountPath(rawValue: string | null | undefined): string {
  if (!rawValue) return DEFAULT_WEB_BASE;
  let mountPath = rawValue.trim();
  if (!mountPath) return DEFAULT_WEB_BASE;
  if (!mountPath.startsWith("/")) {
    mountPath = `/${mountPath}`;
  }
  mountPath = mountPath.replace(/\/+$/, "");
  if (!mountPath) return DEFAULT_WEB_BASE;
  return `${mountPath}/`;
}

function resolveConfigPath(): string {
  const envOverride = process.env.LUNAR_ANALYST_CONFIG_TOML;
  if (envOverride && envOverride.trim()) {
    return resolve(envOverride.trim());
  }
  return resolve(__dirname, "../../../config/lunar_analyst.toml");
}

function readMountPathFromToml(configPath: string): string | null {
  const text = readFileSync(configPath, "utf-8");
  const lines = text.split(/\r?\n/);
  let section = "";
  for (const rawLine of lines) {
    const line = rawLine.split("#", 1)[0].trim();
    if (!line) continue;
    const sectionMatch = line.match(/^\[(.+)\]$/);
    if (sectionMatch) {
      section = sectionMatch[1].trim();
      continue;
    }
    if (section !== "backend.web") continue;
    const kvMatch = line.match(/^([A-Za-z0-9_.-]+)\s*=\s*(.+)$/);
    if (!kvMatch) continue;
    const key = kvMatch[1].trim();
    const valueRaw = kvMatch[2].trim();
    if (key !== "mount_path") continue;
    const quoted = valueRaw.match(/^"(.*)"$/);
    if (!quoted) return null;
    return quoted[1];
  }
  return null;
}

function resolveWebBase(): string {
  const configPath = resolveConfigPath();
  if (!existsSync(configPath)) return DEFAULT_WEB_BASE;
  try {
    return normalizeMountPath(readMountPathFromToml(configPath));
  } catch {
    return DEFAULT_WEB_BASE;
  }
}

export default defineConfig({
  plugins: [react()],
  base: resolveWebBase(),
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: true,
    rollupOptions: {
      input: resolve(__dirname, "index.react.html"),
    },
  },
  server: {
    port: 5173,
    strictPort: true,
  },
  preview: {
    port: 4173,
    strictPort: true,
  },
  resolve: {
    alias: {
      "@": resolve(__dirname, "src"),
    },
  },
});
