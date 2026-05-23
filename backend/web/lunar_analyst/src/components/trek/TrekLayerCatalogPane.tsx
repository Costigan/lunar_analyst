import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Button, InputGroup } from "@blueprintjs/core";
import {
  listTrekLayers,
  searchTrekLayers,
  type TrekLayerMetadata,
} from "../../services/trekService";

export type TrekOverlayState = {
  layer_id: string;
  metadata: TrekLayerMetadata;
  visible: boolean;
  opacity: number;
  z_index: number;
  style: Record<string, unknown>;
};

export type TrekOverlayPatch = {
  visible?: boolean;
  opacity?: number;
  z_index?: number;
  style?: Record<string, unknown>;
};

type Props = {
  overlays: TrekOverlayState[];
  onAddOverlay: (metadata: TrekLayerMetadata) => void;
  onRemoveOverlay: (layerId: string) => void;
  onUpdateOverlay: (layerId: string, patch: TrekOverlayPatch) => void;
};

function canonicalId(metadata: TrekLayerMetadata): string {
  const uuid = String(metadata.item_UUID || "").trim();
  if (uuid.length > 0) return uuid;
  return String(metadata.productLabel || "").trim();
}

export default function TrekLayerCatalogPane(props: Props): JSX.Element {
  const {
    overlays,
    onAddOverlay,
    onRemoveOverlay,
  } = props;
  const [pattern, setPattern] = useState("");
  const [results, setResults] = useState<TrekLayerMetadata[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>("");

  const activeByCanonical = useMemo(() => {
    const map = new Map<string, TrekOverlayState>();
    for (const overlay of overlays) {
      map.set(canonicalId(overlay.metadata), overlay);
    }
    return map;
  }, [overlays]);

  const refreshList = useCallback(async (force = false) => {
    setLoading(true);
    setError("");
    try {
      const response = await listTrekLayers(force);
      setResults(Array.isArray(response.layers) ? response.layers : []);
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : String(exc);
      setError(message);
    } finally {
      setLoading(false);
    }
  }, []);

  const runSearch = useCallback(async () => {
    const query = pattern.trim();
    if (!query) {
      await refreshList();
      return;
    }
    setLoading(true);
    setError("");
    try {
      const response = await searchTrekLayers(query);
      setResults(Array.isArray(response.layers) ? response.layers : []);
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : String(exc);
      setError(message);
    } finally {
      setLoading(false);
    }
  }, [pattern, refreshList]);

  useEffect(() => {
    void refreshList();
  }, [refreshList]);

  const resultRows = useMemo(() => results.slice(0, 120), [results]);

  return (
    <div className="trek-panel-body">
      <div className="trek-search-row">
        <InputGroup
          value={pattern}
          placeholder="Search Trek layers (AND / OR / NOT)"
          onChange={(event) => setPattern(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              void runSearch();
            }
          }}
        />
        <Button small text="Search" onClick={() => void runSearch()} loading={loading} />
        <Button small text="Refresh" onClick={() => void refreshList(true)} disabled={loading} />
      </div>
      <div className="trek-search-hint">Boolean pattern supports AND / OR / NOT, "-" and parentheses.</div>
      {error ? <div className="trek-search-error">{error}</div> : null}

      <div className="trek-results-list">
        {resultRows.map((layer) => {
          const id = canonicalId(layer);
          const active = activeByCanonical.get(id);
          const title = String(layer.title || layer.productLabel || id);
          const productLabel = String(layer.productLabel || "");
          const serviceTypes = Array.isArray(layer.serviceTypes) ? layer.serviceTypes.join(", ") : "";
          return (
            <div key={id} className="trek-result-row">
              <div className="trek-result-meta">
                <div className="trek-result-title">{title}</div>
                <div className="trek-result-subtitle">
                  {productLabel}
                  {serviceTypes ? ` | ${serviceTypes}` : ""}
                </div>
              </div>
              {active ? (
                <Button small text="Remove" onClick={() => onRemoveOverlay(active.layer_id)} />
              ) : (
                <Button small intent="primary" text="Add" onClick={() => onAddOverlay(layer)} />
              )}
            </div>
          );
        })}
        {resultRows.length === 0 && !loading ? (
          <div className="filtered-list-empty">No Trek layers found.</div>
        ) : null}
      </div>

    </div>
  );
}
