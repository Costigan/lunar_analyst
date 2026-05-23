import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchTrekLayerFeatures, listTrekLayers, searchTrekLayers } from "../services/trekService";

describe("trekService", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("lists trek layers with force refresh query", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          layers: [],
          count: 0,
          cached: false,
          fetched_at_utc: "2026-03-02T00:00:00Z",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await listTrekLayers(true);

    expect(result.count).toBe(0);
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/trek/layers?force_refresh=true", expect.anything());
  });

  it("searches trek layers by pattern", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          layers: [{ item_UUID: "uuid_1", productLabel: "Layer_A" }],
          count: 1,
          cached: true,
          fetched_at_utc: "2026-03-02T00:00:01Z",
          pattern: "a AND b",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await searchTrekLayers("a AND b");

    expect(result.count).toBe(1);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/trek/layers:search?pattern=a+AND+b",
      expect.anything(),
    );
  });

  it("fetches trek layer features through backend proxy", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          product_label: "EnduranceA_Path_SouthernTraverse_v2_SP",
          source_root_url: "https://trek.nasa.gov/moon/trekarcgis2/rest/services/EnduranceA_Path_SouthernTraverse_v2_SP/MapServer",
          layer_ids: [0],
          feature_collection: { type: "FeatureCollection", features: [] },
          feature_count: 0,
          cached: false,
          fetched_at_utc: "2026-03-02T00:00:02Z",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await fetchTrekLayerFeatures("EnduranceA_Path_SouthernTraverse_v2_SP", 0, true);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/trek/layers/EnduranceA_Path_SouthernTraverse_v2_SP/features?layer_id=0&force_refresh=true",
      expect.anything(),
    );
  });
});
