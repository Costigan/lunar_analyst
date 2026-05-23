import React, { useEffect, useMemo, useState } from "react";
import { InputGroup, Checkbox, Button, MenuItem } from "@blueprintjs/core";
import { Suggest, type ItemRenderer } from "@blueprintjs/select";
import PatternCombobox from "../common/PatternCombobox";
import FilteredTreeTable, { type ExplorerTreeRow } from "./FilteredTreeTable";
import { listExplorerNodes, listScenarios, type ExplorerNode, type ScenarioSummary } from "../../services/scenarioService";

function fmtDate(ts: string | undefined): string {
  return typeof ts === "string" && ts.length >= 10 ? ts.slice(0, 10) : "-";
}

function fmtBytes(v: number | undefined): string {
  const n = Number(v || 0);
  if (!Number.isFinite(n) || n <= 0) return "-";
  if (n >= 1024 * 1024 * 1024) return `${(n / (1024 * 1024 * 1024)).toFixed(1)}G`;
  if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)}M`;
  if (n >= 1024) return `${(n / 1024).toFixed(1)}K`;
  return `${n}B`;
}

function scenarioSearchText(sc: ScenarioSummary): string {
  return [
    sc.name,
    sc.scenario_id,
    sc.scenario_root,
    sc.directory,
    sc.primary_dem_path,
    "dem.tif",
    "hillshade.tif",
    "primary dem",
    "hillshade",
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

function nodeSearchText(node: ExplorerNode): string {
  return [
    node.name,
    node.relative_path,
    node.node_type,
    node.kind,
    node.subkind,
    `${node.kind || ""}/${node.subkind || ""}`,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

type ScenarioItem = {
  value: string;
  label: string;
  searchText: string;
};

const renderScenario: ItemRenderer<ScenarioItem> = (item, { handleClick, handleFocus, modifiers }) => {
  if (!modifiers.matchesPredicate) {
    return null;
  }
  return (
    <MenuItem
      active={modifiers.active}
      disabled={modifiers.disabled}
      key={item.value}
      onClick={handleClick}
      onFocus={handleFocus}
      text={item.label}
    />
  );
};

type Props = {
  activeScenarioId: string | null;
  onActiveScenarioChange: (scenarioId: string) => void;
  refreshToken?: number;
  onAddLayer?: (row: ExplorerTreeRow) => void;
  onOpenFile?: (row: ExplorerTreeRow) => void;
  onOpenNotebook?: (row: ExplorerTreeRow) => void;
  onOpenPythonFile?: (row: ExplorerTreeRow) => void;
  onOpenPythonEntry?: (row: ExplorerTreeRow) => void;
  onCreateNotebook?: (scenarioId: string) => Promise<void>;
  onCreatePythonFile?: (scenarioId: string) => Promise<void>;
};

export default function ScenarioExplorerPane(props: Props): JSX.Element {
  const {
    activeScenarioId,
    onActiveScenarioChange,
    refreshToken = 0,
    onAddLayer,
    onOpenFile,
    onOpenNotebook,
    onOpenPythonFile,
    onOpenPythonEntry,
    onCreateNotebook,
    onCreatePythonFile,
  } = props;

  const [scenarios, setScenarios] = useState<ScenarioSummary[]>([]);
  const [nodesByScenarioId, setNodesByScenarioId] = useState<Map<string, ExplorerNode[]>>(new Map());
  const [filterText, setFilterText] = useState("");
  const [showHidden, setShowHidden] = useState(false);
  const [visibleCols, setVisibleCols] = useState<Set<string>>(new Set());
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  const [marimoStatusMessage, setMarimoStatusMessage] = useState("");
  const [marimoErrorMessage, setMarimoErrorMessage] = useState("");

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const loaded = await listScenarios();
      if (cancelled) return;
      setScenarios(loaded);
      setExpandedIds((prev) => {
        const next = new Set(prev);
        for (const sc of loaded) next.add(`sc:${sc.scenario_id}`);
        return next;
      });
      if (!activeScenarioId && loaded.length > 0) {
        onActiveScenarioChange(loaded[0].scenario_id);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [activeScenarioId, onActiveScenarioChange, refreshToken]);

  useEffect(() => {
    if (!activeScenarioId) return;
    let cancelled = false;
    void (async () => {
      const nodes = await listExplorerNodes(activeScenarioId, showHidden);
      if (cancelled) return;
      setNodesByScenarioId((prev) => {
        const next = new Map(prev);
        next.set(activeScenarioId, nodes || []);
        return next;
      });
    })();
    return () => {
      cancelled = true;
    };
  }, [activeScenarioId, showHidden, refreshToken]);

  const scenarioItems = useMemo(
    () =>
      [...scenarios]
        .sort((a, b) => a.name.localeCompare(b.name))
        .map((sc) => ({
          value: sc.scenario_id,
          label: sc.name,
          searchText: scenarioSearchText(sc),
        })),
    [scenarios],
  );

  const rows = useMemo((): ExplorerTreeRow[] => {
    const visibleScenarios = scenarios.filter((sc) => !activeScenarioId || sc.scenario_id === activeScenarioId);
    const result: ExplorerTreeRow[] = [];

    for (const sc of visibleScenarios.sort((a, b) => a.name.localeCompare(b.name))) {
      const scRowId = `sc:${sc.scenario_id}`;
      result.push({
        id: scRowId,
        parentId: "",
          scenarioId: sc.scenario_id,
          name: sc.name,
          modifiedAtUtc: sc.created_at_utc,
          searchText: scenarioSearchText(sc),
        sortKey: `0:${sc.name}`,
        active: sc.scenario_id === activeScenarioId,
        cells: {
          type: "Scenario",
          created: fmtDate(sc.created_at_utc),
          size: fmtBytes(sc.size_bytes),
          notes: sc.directory || "-",
        },
      });

      const nodes = nodesByScenarioId.get(sc.scenario_id) || [];
      for (const node of nodes) {
        if (node.node_type === "scenario") continue;
        const relativePath = String(node.relative_path || "");
        const parentPath = String(node.parent_relative_path || "");
        const rowId = `node:${sc.scenario_id}:${relativePath}`;
        const parentId = parentPath ? `node:${sc.scenario_id}:${parentPath}` : scRowId;
        const typeLabel =
          node.node_type === "collection"
            ? "Collection"
            : node.node_type === "folder"
              ? "Folder"
              : node.kind && node.subkind
                ? `${node.kind}/${node.subkind}`
                : "File";

        result.push({
          id: rowId,
          parentId,
          scenarioId: sc.scenario_id,
          name: node.name,
          relativePath: node.relative_path,
          modifiedAtUtc: node.modified_at_utc,
          searchText: nodeSearchText(node),
          sortKey: `${node.node_type === "folder" || node.node_type === "collection" ? "0" : "1"}:${String(node.name || "")}`,
          node: {
            node_type: node.node_type,
            name: node.name,
            product_id: node.product_id,
            file_id: node.file_id,
            is_renderable: node.is_renderable,
            kind: node.kind,
            subkind: node.subkind,
          },
          cells: {
            type: typeLabel,
            created: fmtDate(node.modified_at_utc || node.created_at_utc),
            size: fmtBytes(node.size_bytes),
            notes: node.relative_path || "-",
          },
        });
      }
    }
    return result;
  }, [scenarios, activeScenarioId, nodesByScenarioId]);

  const activeScenario = scenarios.find(s => s.scenario_id === activeScenarioId);

  const handleCreateNotebook = async (): Promise<void> => {
    if (!activeScenarioId) return;
    setMarimoStatusMessage(`Creating notebook for ${activeScenario?.name || activeScenarioId}...`);
    setMarimoErrorMessage("");
    try {
      await onCreateNotebook?.(activeScenarioId);
      setMarimoStatusMessage(`Notebook opened for ${activeScenario?.name || activeScenarioId}.`);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setMarimoErrorMessage(`Open in Marimo failed: ${message}`);
      setMarimoStatusMessage("");
    }
  };

  const handleCreatePythonFile = async (): Promise<void> => {
    if (!activeScenarioId) return;
    setMarimoStatusMessage(`Creating Python file for ${activeScenario?.name || activeScenarioId}...`);
    setMarimoErrorMessage("");
    try {
      await onCreatePythonFile?.(activeScenarioId);
      setMarimoStatusMessage(`Python file opened for ${activeScenario?.name || activeScenarioId}.`);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setMarimoErrorMessage(`Create Python file failed: ${message}`);
      setMarimoStatusMessage("");
    }
  };

  return (
    <div className="scenario-explorer-pane">
      <div className="explorer-controls">
        <>
          <label className="pattern-combobox-label">Scenario</label>
          <Suggest<ScenarioItem>
            inputValueRenderer={(item) => item.label}
            items={scenarioItems}
            itemRenderer={renderScenario}
            onItemSelect={(item) => onActiveScenarioChange(item.value)}
            noResults={<MenuItem disabled={true} text="No results." />}
            inputProps={{ placeholder: "Type scenario pattern" }}
            selectedItem={scenarioItems.find((i) => i.value === activeScenarioId)}
          />
          <label className="pattern-combobox-label" style={{ marginTop: "8px" }}>
            Filter
          </label>
          <InputGroup
            placeholder="Type pattern (token substring match)"
            value={filterText}
            onChange={(event) => setFilterText(event.target.value)}
          />
          <Checkbox
            style={{ marginTop: "8px" }}
            label="Show hidden/system files"
            checked={showHidden}
            onChange={(event) => setShowHidden(event.currentTarget.checked)}
          />
          <div className="explorer-action-row">
            <Button
              small
              style={{ marginTop: "8px" }}
              text="New Notebook"
              onClick={() => {
                void handleCreateNotebook();
              }}
              disabled={!activeScenarioId}
            />
            <Button
              small
              style={{ marginTop: "8px" }}
              text="New Python File"
              onClick={() => {
                void handleCreatePythonFile();
              }}
              disabled={!activeScenarioId}
            />
          </div>
        </>
        {marimoStatusMessage ? <div className="explorer-marimo-status">{marimoStatusMessage}</div> : null}
        {marimoErrorMessage ? <div className="explorer-marimo-error">{marimoErrorMessage}</div> : null}
      </div>
      <div className="explorer-columns">
        {[
          { key: "type", label: "Type" },
          { key: "created", label: "Created" },
          { key: "size", label: "Size" },
          { key: "notes", label: "Notes" },
        ].map((col) => (
          <Checkbox
            key={col.key}
            inline
            label={col.label}
            checked={visibleCols.has(col.key)}
            onChange={(event) => {
              setVisibleCols((prev) => {
                const next = new Set(prev);
                if (event.currentTarget.checked) next.add(col.key);
                else next.delete(col.key);
                return next;
              });
            }}
          />
        ))}
      </div>
      <FilteredTreeTable
        rows={rows}
        filterText={filterText}
        expandedIds={expandedIds}
        visibleCols={visibleCols}
        onToggleRow={(rowId) => {
          setExpandedIds((prev) => {
            const next = new Set(prev);
            if (next.has(rowId)) next.delete(rowId);
            else next.add(rowId);
            return next;
          });
        }}
        onActivateRow={(row) => {
          if (row.scenarioId && row.scenarioId !== activeScenarioId) {
            onActiveScenarioChange(row.scenarioId);
          }
        }}
        onAddLayer={onAddLayer}
        onOpenFile={onOpenFile}
        onOpenNotebook={onOpenNotebook}
        onOpenPythonFile={onOpenPythonFile}
        onOpenPythonEntry={onOpenPythonEntry}
      />
    </div>
  );
}
