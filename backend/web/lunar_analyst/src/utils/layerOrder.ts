export type LayerState = {
  layer_id: string;
  z_index: number;
};

export type StackPositionMap = Map<string, number>;

export type LayerZPatch = {
  layer_id: string;
  z_index: number;
};

type StackEntry = {
  kind: "scenario" | "base";
  z: number;
  layer_id: string;
};

export function computeDropIndexForRow(
  targetStackPos: number | null | undefined,
  draggingLayerId: string | null,
  stackPositionByLayerId: StackPositionMap,
): number | null {
  if (targetStackPos === null || targetStackPos === undefined) return null;
  if (!draggingLayerId) return targetStackPos;
  const sourceStackPos = stackPositionByLayerId.get(draggingLayerId);
  if (sourceStackPos === null || sourceStackPos === undefined) return targetStackPos;
  if (sourceStackPos < targetStackPos) return targetStackPos + 1;
  if (sourceStackPos > targetStackPos) return targetStackPos;
  return targetStackPos;
}

function currentStackEntriesExcluding(
  layers: LayerState[],
  baseZIndex: number | null,
  excludeLayerId: string | null,
): StackEntry[] {
  const entries: StackEntry[] = layers
    .filter((x) => x.layer_id !== excludeLayerId)
    .map((x) => ({ kind: "scenario", z: Number(x.z_index), layer_id: x.layer_id }));
  if (baseZIndex !== null) {
    entries.push({ kind: "base", z: Number(baseZIndex), layer_id: "__base__" });
  }
  entries.sort((a, b) => b.z - a.z);
  return entries;
}

export function planLayerReorderZPatches(
  layers: LayerState[],
  movingLayerId: string,
  insertStackIndex: number,
  baseZIndex: number | null,
): LayerZPatch[] {
  const topFirst = [...layers].sort((a, b) => b.z_index - a.z_index);
  const target = topFirst.find((x) => x.layer_id === movingLayerId);
  if (!target) return [];

  const stack = currentStackEntriesExcluding(layers, baseZIndex, movingLayerId);
  const clamped = Math.max(0, Math.min(insertStackIndex, stack.length));
  stack.splice(clamped, 0, {
    kind: "scenario",
    z: Number(target.z_index),
    layer_id: target.layer_id,
  });

  const baseIndex = stack.findIndex((entry) => entry.kind === "base");
  const byId = new Map(layers.map((x) => [x.layer_id, x]));
  const patches: LayerZPatch[] = [];

  if (baseIndex < 0) {
    const scenariosTopFirst = stack.filter((entry) => entry.kind === "scenario").map((entry) => entry.layer_id);
    const bottomFirst = [...scenariosTopFirst].reverse();
    for (let i = 0; i < bottomFirst.length; i += 1) {
      const layerId = bottomFirst[i];
      const nextZ = 10 + i * 10;
      if (byId.get(layerId)?.z_index !== nextZ) {
        patches.push({ layer_id: layerId, z_index: nextZ });
      }
    }
    return patches;
  }

  const above = stack
    .slice(0, baseIndex)
    .filter((entry) => entry.kind === "scenario")
    .map((entry) => entry.layer_id);
  const below = stack
    .slice(baseIndex + 1)
    .filter((entry) => entry.kind === "scenario")
    .map((entry) => entry.layer_id);

  let step = 1;
  for (let i = above.length - 1; i >= 0; i -= 1) {
    const layerId = above[i];
    const nextZ = Number(baseZIndex) + 10 * step;
    step += 1;
    if (byId.get(layerId)?.z_index !== nextZ) {
      patches.push({ layer_id: layerId, z_index: nextZ });
    }
  }

  step = 1;
  for (let i = 0; i < below.length; i += 1) {
    const layerId = below[i];
    const nextZ = Number(baseZIndex) - 10 * step;
    step += 1;
    if (byId.get(layerId)?.z_index !== nextZ) {
      patches.push({ layer_id: layerId, z_index: nextZ });
    }
  }

  return patches;
}
