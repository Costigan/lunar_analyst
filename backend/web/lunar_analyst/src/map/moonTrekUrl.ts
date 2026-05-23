export const DEFAULT_MOON_TREK_TILE_BASE_URL = "https://trek.nasa.gov/tiles/Moon/SP";

const MOON_TREK_TILE_MARKER = "/tiles/moon/sp/";

function trimTrailingSlashes(path: string): string {
  return path.replace(/[\\/]+$/g, "");
}

function extractTileBasePath(pathname: string): string | null {
  const lower = pathname.toLowerCase();
  const markerIndex = lower.indexOf(MOON_TREK_TILE_MARKER);
  if (markerIndex < 0) return null;
  const end = markerIndex + MOON_TREK_TILE_MARKER.length - 1;
  return trimTrailingSlashes(pathname.slice(0, end));
}

export function resolveMoonTrekTileBaseUrl(capabilitiesUrl?: string): string {
  const raw = String(capabilitiesUrl || "").trim();
  if (!raw) return DEFAULT_MOON_TREK_TILE_BASE_URL;

  try {
    const parsed = new URL(raw);
    const pathBase = extractTileBasePath(parsed.pathname);
    if (!pathBase) {
      return DEFAULT_MOON_TREK_TILE_BASE_URL;
    }
    return `${parsed.origin}${pathBase}`;
  } catch {
    return DEFAULT_MOON_TREK_TILE_BASE_URL;
  }
}
