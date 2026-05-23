import React, { useMemo } from "react";
import { Tree, type TreeNodeInfo, Menu, MenuItem, showContextMenu } from "@blueprintjs/core";
import { buildVisibleTreeRowIds } from "../../utils/treeVisibility";
import { filterMatch, type MatchResult, tokenizeFilter } from "../../utils/filterMatch";
import { buildExplorerDragPayload, toExplorerDragPayloadJson } from "../../utils/dragPayload";

export type ExplorerTreeRow = {
  id: string;
  parentId: string | null;
  scenarioId: string;
  name: string;
  relativePath?: string;
  modifiedAtUtc?: string;
  active: boolean;
  searchText: string;
  sortKey: string;
  node?: {
    node_type: string;
    name: string;
    product_id?: string;
    file_id?: string;
    is_renderable?: boolean;
    kind?: string;
    subkind?: string;
  };
  cells: {
    type: string;
    created: string;
    size: string;
    notes: string;
  };
};

export type Props = {
  rows: ExplorerTreeRow[];
  filterText: string;
  expandedIds: Set<string>;
  visibleCols: Set<string>;
  onToggleRow: (rowId: string) => void;
  onActivateRow: (row: ExplorerTreeRow) => void;
  onAddLayer?: (row: ExplorerTreeRow) => void;
  onOpenFile?: (row: ExplorerTreeRow) => void;
  onOpenNotebook?: (row: ExplorerTreeRow) => void;
  onOpenPythonFile?: (row: ExplorerTreeRow) => void;
  onOpenPythonEntry?: (row: ExplorerTreeRow) => void;
};

function HighlightedText({ text, match }: { text: string; match: MatchResult }): JSX.Element {
  if (!match.matched || match.indices.length === 0) {
    return <>{text}</>;
  }

  const chunks: JSX.Element[] = [];
  let lastIndex = 0;

  match.indices.forEach(([start, end], idx) => {
    if (start > lastIndex) {
      chunks.push(<React.Fragment key={`text-${idx}`}>{text.slice(lastIndex, start)}</React.Fragment>);
    }
    chunks.push(
      <mark key={`match-${idx}`} className="filter-hit">
        {text.slice(start, end)}
      </mark>
    );
    lastIndex = end;
  });

  if (lastIndex < text.length) {
    chunks.push(<React.Fragment key="text-end">{text.slice(lastIndex)}</React.Fragment>);
  }

  return <>{chunks}</>;
}

const BlueprintExplorer = (props: Props) => {
  const { rows, filterText, expandedIds, visibleCols, onToggleRow, onActivateRow, onAddLayer, onOpenFile, onOpenNotebook, onOpenPythonFile, onOpenPythonEntry } = props;

  const nodes = useMemo(() => {
    const childrenMap = new Map<string, ExplorerTreeRow[]>();
    rows.forEach(row => {
      const pid = row.parentId || "";
      if (!childrenMap.has(pid)) childrenMap.set(pid, []);
      childrenMap.get(pid)!.push(row);
    });

    const buildNode = (row: ExplorerTreeRow): TreeNodeInfo<ExplorerTreeRow> | null => {
      const rowMatch = filterMatch(row.searchText || row.name, filterText);
      const childNodes: TreeNodeInfo<ExplorerTreeRow>[] = (childrenMap.get(row.id) || [])
        .sort((a, b) => a.sortKey.localeCompare(b.sortKey))
        .map(buildNode)
        .filter((n): n is TreeNodeInfo<ExplorerTreeRow> => n !== null);

      const isVisible = filterText.length === 0 || rowMatch.matched || childNodes.length > 0;
      if (!isVisible) return null;

      const isDraggableFile =
        row.node?.node_type === "file" &&
        Boolean(row.node?.product_id) &&
        Boolean(row.node?.file_id) &&
        Boolean(row.node?.is_renderable);
      const isOpenableFile = row.node?.node_type === "file" && Boolean(row.relativePath);
      const isPythonFile =
        row.node?.node_type === "file"
        && Boolean(row.relativePath)
        && row.name.toLowerCase().endsWith(".py");

      const labelContent = (
        <div 
          className="tree-row-grid" 
          draggable={isDraggableFile}
          onDoubleClick={() => {
            if (isOpenableFile) {
              onOpenFile?.(row);
            }
          }}
          onDragStart={(event) => {
            if (!isDraggableFile || !row.node?.product_id || !row.node?.file_id) return;
            const payload = buildExplorerDragPayload(row.scenarioId, row.node.product_id, row.node.file_id);
            event.dataTransfer.setData("application/x-lunar-product", toExplorerDragPayloadJson(payload));
            event.dataTransfer.setData("text/plain", row.node.name || row.name);
          }}
          onContextMenu={(event) => {
            event.preventDefault();
            showContextMenu({
              content: (
                <Menu>
                  {isOpenableFile ? (
                    <MenuItem
                      icon="document-open"
                      text="Open"
                      onClick={() => onOpenFile?.(row)}
                    />
                  ) : null}
                  {isDraggableFile && (
                    <MenuItem 
                      icon="add" 
                      text="Add to Layer List (Top)" 
                      onClick={() => onAddLayer?.(row)} 
                    />
                  )}
                  {isPythonFile ? (
                    <MenuItem
                      icon="application"
                      text="Open as Notebook"
                      onClick={() => onOpenNotebook?.(row)}
                    />
                  ) : null}
                  {isPythonFile ? (
                    <MenuItem
                      icon="code"
                      text="Open as Python File"
                      onClick={() => onOpenPythonFile?.(row)}
                    />
                  ) : null}
                  <MenuItem icon="info-sign" text="Properties" disabled />
                </Menu>
              ),
              targetOffset: { left: event.clientX, top: event.clientY },
            });
          }}
        >
          <div className="col-name-text">
             <HighlightedText text={row.name} match={rowMatch} />
          </div>
          <div className="col-type-text" data-col-hidden={visibleCols.has("type") ? "false" : "true"}>
            {row.cells.type}
          </div>
          <div className="col-created-text" data-col-hidden={visibleCols.has("created") ? "false" : "true"}>
            {row.cells.created}
          </div>
          <div className="col-size-text" data-col-hidden={visibleCols.has("size") ? "false" : "true"}>
            {row.cells.size}
          </div>
          <div className="col-notes-text" data-col-hidden={visibleCols.has("notes") ? "false" : "true"}>
            {row.cells.notes}
          </div>
        </div>
      );

      return {
        id: row.id,
        nodeData: row,
        icon: row.node?.node_type === "folder" || row.node?.node_type === "collection" ? "folder-close" : (row.cells.type === "Scenario" ? "database" : "document"),
        label: labelContent,
        isExpanded: filterText.length > 0 || expandedIds.has(row.id),
        isSelected: row.active,
        childNodes: childNodes.length > 0 ? childNodes : undefined,
        hasCaret: childNodes.length > 0,
      };
    };

    return (childrenMap.get("") || [])
      .sort((a, b) => a.sortKey.localeCompare(b.sortKey))
      .map(buildNode)
      .filter((n): n is TreeNodeInfo<ExplorerTreeRow> => n !== null);
  }, [rows, filterText, expandedIds, visibleCols, onAddLayer, onActivateRow, onOpenFile, onOpenNotebook, onOpenPythonEntry, onOpenPythonFile]);

  return (
    <div className="scenario-explorer bp6-explorer" role="treegrid">
       <div className="tree-grid-header bp6-tree-header">
        <div className="col-name">Name</div>
        <div className="col-type" data-col-hidden={visibleCols.has("type") ? "false" : "true"}>Type</div>
        <div className="col-created" data-col-hidden={visibleCols.has("created") ? "false" : "true"}>Created</div>
        <div className="col-size" data-col-hidden={visibleCols.has("size") ? "false" : "true"}>Size</div>
        <div className="col-notes" data-col-hidden={visibleCols.has("notes") ? "false" : "true"}>Notes</div>
      </div>
      <div className="bp6-tree-container">
        {nodes.length > 0 ? (
          <Tree 
            compact
            contents={nodes} 
            onNodeClick={(node) => onActivateRow(node.nodeData!)}
            onNodeCollapse={(node) => onToggleRow(node.id as string)}
            onNodeExpand={(node) => onToggleRow(node.id as string)}
          />
        ) : (
          <div className="filtered-list-empty">No matching items.</div>
        )}
      </div>
    </div>
  );
};

export default function FilteredTreeTable(props: Props): JSX.Element {
  return <BlueprintExplorer {...props} />;
}
