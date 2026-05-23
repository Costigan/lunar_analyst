import React, { useMemo, useState } from "react";
import { Button, InputGroup } from "@blueprintjs/core";
import {
  nearbyNomenclature,
  resolveNomenclature,
  searchNomenclature,
  type NomenclatureFeature,
} from "../../services/nomenclatureService";

type Props = {
  getMapCenter: () => [number, number] | null;
  onZoomToExtent: (extent: [number, number, number, number], maxZoom?: number) => void;
};

function featureExtent(feature: NomenclatureFeature): [number, number, number, number] | null {
  const region = feature.location?.region;
  if (region) {
    return [region.min_x, region.min_y, region.max_x, region.max_y];
  }
  const center = feature.location?.center;
  if (center) {
    return [center.x - 1000, center.y - 1000, center.x + 1000, center.y + 1000];
  }
  return null;
}

export default function NomenclaturePane(props: Props): JSX.Element {
  const [query, setQuery] = useState("");
  const [featureType, setFeatureType] = useState("");
  const [searchResults, setSearchResults] = useState<NomenclatureFeature[]>([]);
  const [searchBusy, setSearchBusy] = useState(false);
  const [searchError, setSearchError] = useState("");

  const [xText, setXText] = useState("");
  const [yText, setYText] = useState("");
  const [radiusText, setRadiusText] = useState("");
  const [nearbyBusy, setNearbyBusy] = useState(false);
  const [nearbyError, setNearbyError] = useState("");
  const [nearbyResults, setNearbyResults] = useState<NomenclatureFeature[]>([]);

  const runSearch = async (): Promise<void> => {
    const q = query.trim();
    if (!q) return;
    setSearchBusy(true);
    setSearchError("");
    try {
      const results = await searchNomenclature(q, {
        featureType: featureType.trim() || undefined,
        limit: 25,
      });
      setSearchResults(results);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setSearchError(message);
      setSearchResults([]);
    } finally {
      setSearchBusy(false);
    }
  };

  const runNearby = async (): Promise<void> => {
    const x = Number(xText);
    const y = Number(yText);
    if (!Number.isFinite(x) || !Number.isFinite(y)) {
      setNearbyError("Nearby requires numeric x and y in ESRI:103878.");
      return;
    }
    const radius = Number(radiusText);
    setNearbyBusy(true);
    setNearbyError("");
    try {
      const results = await nearbyNomenclature(x, y, {
        featureType: featureType.trim() || undefined,
        limit: 25,
        radiusM: Number.isFinite(radius) && radius > 0 ? radius : undefined,
      });
      setNearbyResults(results);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setNearbyError(message);
      setNearbyResults([]);
    } finally {
      setNearbyBusy(false);
    }
  };

  const setFromMapCenter = (): void => {
    const center = props.getMapCenter();
    if (!center) return;
    setXText(center[0].toFixed(3));
    setYText(center[1].toFixed(3));
  };

  const mergedRows = useMemo(() => {
    if (nearbyResults.length > 0) return nearbyResults;
    return searchResults;
  }, [nearbyResults, searchResults]);

  const gotoFeature = async (feature: NomenclatureFeature): Promise<void> => {
    try {
      const resolved = await resolveNomenclature(feature.name, feature.feature_type || undefined);
      const extent = featureExtent(resolved);
      if (!extent) return;
      props.onZoomToExtent(extent, 11);
    } catch {
      const extent = featureExtent(feature);
      if (!extent) return;
      props.onZoomToExtent(extent, 11);
    }
  };

  return (
    <div className="nomenclature-panel-body">
      <div className="trek-search-row">
        <InputGroup
          value={query}
          placeholder="Find lunar feature"
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") void runSearch();
          }}
        />
        <Button small text="Search" loading={searchBusy} onClick={() => void runSearch()} />
      </div>

      <div className="trek-search-row" style={{ marginTop: 8 }}>
        <InputGroup
          value={featureType}
          placeholder="Type filter (optional): Crater, Mare, Mons"
          onChange={(event) => setFeatureType(event.target.value)}
        />
      </div>

      <div className="trek-search-row" style={{ marginTop: 8 }}>
        <InputGroup value={xText} placeholder="x" onChange={(event) => setXText(event.target.value)} />
        <InputGroup value={yText} placeholder="y" onChange={(event) => setYText(event.target.value)} />
      </div>
      <div className="trek-search-row" style={{ marginTop: 8 }}>
        <InputGroup value={radiusText} placeholder="radius_m (optional)" onChange={(event) => setRadiusText(event.target.value)} />
        <Button small text="From Map" onClick={setFromMapCenter} />
        <Button small text="Nearby" loading={nearbyBusy} onClick={() => void runNearby()} />
      </div>

      {searchError ? <div className="trek-search-error">{searchError}</div> : null}
      {nearbyError ? <div className="trek-search-error">{nearbyError}</div> : null}

      <div className="trek-results-list" style={{ marginTop: 10 }}>
        {mergedRows.map((item) => {
          const subtitle = [item.feature_type || "Unknown", item.distance_m ? `${item.distance_m.toFixed(1)} m` : ""]
            .filter(Boolean)
            .join(" | ");
          return (
            <div key={`${item.feature_id}`} className="trek-result-row">
              <div className="trek-result-meta">
                <div className="trek-result-title">{item.name}</div>
                <div className="trek-result-subtitle">{subtitle}</div>
              </div>
              <Button small text="Go To" onClick={() => void gotoFeature(item)} />
            </div>
          );
        })}
        {mergedRows.length === 0 ? <div className="filtered-list-empty">No nomenclature results.</div> : null}
      </div>
    </div>
  );
}
