export type ExplorerDragPayload = {
  scenario_id: string;
  product_id: string;
  file_id: string;
};

export function buildExplorerDragPayload(
  scenarioId: string,
  productId: string,
  fileId: string,
): ExplorerDragPayload {
  return {
    scenario_id: scenarioId,
    product_id: productId,
    file_id: fileId,
  };
}

export function toExplorerDragPayloadJson(payload: ExplorerDragPayload): string {
  return JSON.stringify(payload);
}
