export type ScenarioScopedLists<T> = Record<string, T[]>;

function normalizeScenarioId(scenarioId: string | null | undefined): string {
  return typeof scenarioId === "string" ? scenarioId.trim() : "";
}

export function getScenarioScopedList<T>(
  itemsByScenario: ScenarioScopedLists<T>,
  scenarioId: string | null | undefined,
): T[] {
  const key = normalizeScenarioId(scenarioId);
  if (!key) return [];
  return itemsByScenario[key] ?? [];
}

export function updateScenarioScopedList<T>(
  itemsByScenario: ScenarioScopedLists<T>,
  scenarioId: string | null | undefined,
  updater: (current: T[]) => T[],
): ScenarioScopedLists<T> {
  const key = normalizeScenarioId(scenarioId);
  if (!key) return itemsByScenario;
  const current = itemsByScenario[key] ?? [];
  const next = updater(current);
  if (next === current) return itemsByScenario;
  if (next.length === 0) {
    if (!(key in itemsByScenario)) return itemsByScenario;
    const { [key]: _removed, ...rest } = itemsByScenario;
    return rest;
  }
  return {
    ...itemsByScenario,
    [key]: next,
  };
}
