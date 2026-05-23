const ACTIVE_SCENARIO_STORAGE_KEY = "lunar-analyst-active-scenario-id";

export type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem">;

function normalizeScenarioId(raw: string | null | undefined): string | undefined {
  if (typeof raw !== "string") return undefined;
  const trimmed = raw.trim();
  return trimmed.length > 0 ? trimmed : undefined;
}

export function scenarioIdFromQuery(search: string): string | undefined {
  return normalizeScenarioId(new URLSearchParams(search).get("scenario_id"));
}

export function readPersistedScenarioId(storage: StorageLike | null | undefined): string | undefined {
  if (!storage) return undefined;
  try {
    return normalizeScenarioId(storage.getItem(ACTIVE_SCENARIO_STORAGE_KEY));
  } catch {
    return undefined;
  }
}

export function selectBootstrapScenarioId(args: {
  locationSearch: string;
  storage: StorageLike | null | undefined;
}): string | undefined {
  return scenarioIdFromQuery(args.locationSearch) ?? readPersistedScenarioId(args.storage);
}

export function persistActiveScenarioId(
  scenarioId: string | null | undefined,
  storage: StorageLike | null | undefined,
): void {
  if (!storage) return;
  try {
    const normalized = normalizeScenarioId(scenarioId);
    if (normalized) {
      storage.setItem(ACTIVE_SCENARIO_STORAGE_KEY, normalized);
      return;
    }
    storage.removeItem(ACTIVE_SCENARIO_STORAGE_KEY);
  } catch {
    // Ignore storage failures; they should not block workspace state changes.
  }
}

