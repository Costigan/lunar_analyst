import { describe, expect, it } from "vitest";
import {
  allTokensMatch,
  filterMatch,
  highlightMaskForTokens,
  tokenizeFilter,
} from "../utils/filterMatch";

describe("filterMatch", () => {
  it("tokenizes lowercase terms", () => {
    expect(tokenizeFilter("  Alpha   beta  ")).toEqual(["alpha", "beta"]);
  });

  it("matches by token substring (case-insensitive)", () => {
    expect(allTokensMatch(["psr"], "generate psr raster [System]")).toBe(true);
    expect(allTokensMatch(["psr"], "test_lightmap_streaming.py [Notebook]")).toBe(false);
    expect(allTokensMatch(["gen", "ras"], "generate psr raster [System]")).toBe(true);
  });

  it("requires every query token to match some candidate token", () => {
    expect(allTokensMatch(["psr", "sys"], "generate psr raster [System]")).toBe(true);
    expect(allTokensMatch(["psr", "zzz"], "generate psr raster [System]")).toBe(false);
  });

  it("builds highlight mask for matching token substrings", () => {
    const text = "Generate PSR Raster";
    const mask = highlightMaskForTokens(text, ["psr"]);
    const pIndex = text.toLowerCase().indexOf("psr");
    expect(mask[pIndex]).toBe(true);
    expect(mask[pIndex + 1]).toBe(true);
    expect(mask[pIndex + 2]).toBe(true);
    expect(mask[0]).toBe(false);
  });

  it("returns filterMatch ranges for token-substring hits", () => {
    const result = filterMatch("Generate PSR Raster", "psr ras");
    expect(result.matched).toBe(true);
    expect(result.indices.length).toBeGreaterThan(0);
  });

  it("rejects non-adjacent character subsequence-only matches", () => {
    const result = filterMatch("test_lightmap_streaming.py [Notebook]", "psr");
    expect(result.matched).toBe(false);
  });
});
