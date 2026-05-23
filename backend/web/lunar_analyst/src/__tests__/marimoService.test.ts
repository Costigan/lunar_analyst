import { afterEach, describe, expect, it, vi } from "vitest";
import {
  buildMarimoFileUrl,
  createMarimoNotebookForScenario,
  getNotebookOpenCapability,
  launchMarimoForScenario,
  rememberNotebookOpenCapability,
} from "../services/marimoService";

class MemoryStorage implements Storage {
  private readonly values = new Map<string, string>();

  get length(): number {
    return this.values.size;
  }

  clear(): void {
    this.values.clear();
  }

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  key(index: number): string | null {
    return Array.from(this.values.keys())[index] ?? null;
  }

  removeItem(key: string): void {
    this.values.delete(key);
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }
}

describe("marimoService", () => {
  const localStorage = new MemoryStorage();

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it("launches marimo with scenario-scoped restart payload", async () => {
    vi.stubGlobal("window", { location: { origin: "http://127.0.0.1:5173" }, localStorage });
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          status: "running",
          mode: "launch",
          base_url: "http://127.0.0.1:2718",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await launchMarimoForScenario("scn_demo");

    expect(result.status).toBe("running");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/marimo/launch");
    expect(options.method).toBe("POST");
    expect(JSON.parse(String(options.body))).toEqual({
      scenario_id: "scn_demo",
      restart_if_running: true,
    });
  });

  it("appends the requested notebook file to the marimo URL", () => {
    vi.stubGlobal("window", { location: { origin: "http://127.0.0.1:5173" }, localStorage });
    const nextUrl = buildMarimoFileUrl("http://127.0.0.1:2718/?token=abc", "/e/projects/demo/terrain.mo.py");
    const parsed = new URL(nextUrl);

    expect(parsed.origin).toBe("http://127.0.0.1:2718");
    expect(parsed.searchParams.get("token")).toBe("abc");
    expect(parsed.searchParams.get("file")).toBe("/e/projects/demo/terrain.mo.py");
  });

  it("creates a new scenario notebook through the open-notebook endpoint", async () => {
    vi.stubGlobal("window", { location: { origin: "http://127.0.0.1:5173" }, localStorage });
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          status: "ready",
          scenario_id: "scn_demo",
          relative_path: "notebooks/notebook_20260407_120000.mo.py",
          absolute_file_path: "/e/projects/demo/notebooks/notebook_20260407_120000.mo.py",
          file_url: "http://127.0.0.1:2718/?file=D%3A%5Cprojects%5Cdemo%5Cnotebooks%5Cnotebook_20260407_120000.mo.py",
          file_name: "notebook_20260407_120000.mo.py",
          notebook_capability: "marimo_notebook",
          created_new: true,
          modified_at_utc: "2026-04-07T12:00:00Z",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await createMarimoNotebookForScenario("scn_demo");

    expect(result.created_new).toBe(true);
    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/marimo/open-notebook");
    expect(JSON.parse(String(options.body))).toEqual({
      scenario_id: "scn_demo",
      create_new: true,
      restart_if_running: true,
    });
  });

  it("stores notebook capability cache entries keyed by file change signal", () => {
    vi.stubGlobal("window", { location: { origin: "http://127.0.0.1:5173" }, localStorage });
    rememberNotebookOpenCapability({
      scenarioId: "scn_demo",
      relativePath: "notebooks/demo.mo.py",
      modifiedAtUtc: "2026-04-07T12:00:00Z",
      status: "openable",
      checkedAtUtc: "2026-04-07T12:05:00Z",
    });

    expect(getNotebookOpenCapability("scn_demo", "notebooks/demo.mo.py", "2026-04-07T12:00:00Z")?.status).toBe("openable");
    expect(getNotebookOpenCapability("scn_demo", "notebooks/demo.mo.py", "2026-04-07T12:09:00Z")).toBeNull();
  });
});
