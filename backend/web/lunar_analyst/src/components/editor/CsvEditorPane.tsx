import React, { useEffect, useMemo, useState } from "react";
import { Button, InputGroup, Intent, Spinner } from "@blueprintjs/core";
import { readScenarioEditableFile, updateScenarioEditableFile } from "../../services/scenarioService";

type Props = {
  scenarioId: string;
  relativePath: string;
  initialContent?: string;
  modifiedAtUtc?: string | null;
};

type SortDirection = "asc" | "desc";

function parseCsv(input: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let value = "";
  let inQuotes = false;

  for (let i = 0; i < input.length; i += 1) {
    const char = input[i];
    const next = input[i + 1];

    if (char === "\"") {
      if (inQuotes && next === "\"") {
        value += "\"";
        i += 1;
      } else {
        inQuotes = !inQuotes;
      }
      continue;
    }
    if (char === "," && !inQuotes) {
      row.push(value);
      value = "";
      continue;
    }
    if ((char === "\n" || char === "\r") && !inQuotes) {
      if (char === "\r" && next === "\n") {
        i += 1;
      }
      row.push(value);
      rows.push(row);
      row = [];
      value = "";
      continue;
    }
    value += char;
  }

  if (value.length > 0 || row.length > 0) {
    row.push(value);
    rows.push(row);
  }
  return rows;
}

function serializeCsv(rows: string[][]): string {
  return rows
    .map((row) =>
      row
        .map((cell) => {
          if (/[",\n\r]/.test(cell)) {
            return `"${cell.replaceAll("\"", "\"\"")}"`;
          }
          return cell;
        })
        .join(","),
    )
    .join("\n");
}

function padRows(rows: string[][]): string[][] {
  const width = rows.reduce((max, row) => Math.max(max, row.length), 0);
  return rows.map((row) => {
    const next = [...row];
    while (next.length < width) next.push("");
    return next;
  });
}

export default function CsvEditorPane(props: Props): JSX.Element {
  const { scenarioId, relativePath, initialContent = "", modifiedAtUtc = null } = props;
  const [rawContent, setRawContent] = useState(initialContent);
  const [savedContent, setSavedContent] = useState(initialContent);
  const [rows, setRows] = useState<string[][]>(initialContent ? padRows(parseCsv(initialContent)) : []);
  const [statusText, setStatusText] = useState(initialContent ? "" : "Loading CSV file...");
  const [errorText, setErrorText] = useState("");
  const [saving, setSaving] = useState(false);
  const [searchText, setSearchText] = useState("");
  const [sortColumn, setSortColumn] = useState<number | null>(null);
  const [sortDirection, setSortDirection] = useState<SortDirection>("asc");
  const [columnOrder, setColumnOrder] = useState<number[]>([]);
  const [dragColumn, setDragColumn] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    if (initialContent) {
      return;
    }
    void (async () => {
      try {
        const response = await readScenarioEditableFile(scenarioId, relativePath);
        if (cancelled) return;
        const parsed = padRows(parseCsv(response.content));
        setRows(parsed);
        setRawContent(response.content);
        setSavedContent(response.content);
        setColumnOrder(parsed[0] ? parsed[0].map((_, index) => index) : []);
        setStatusText("");
      } catch (error) {
        if (cancelled) return;
        setErrorText(error instanceof Error ? error.message : String(error));
        setStatusText("");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [initialContent, relativePath, scenarioId]);

  useEffect(() => {
    if (!initialContent) return;
    const parsed = padRows(parseCsv(initialContent));
    setRows(parsed);
    setColumnOrder(parsed[0] ? parsed[0].map((_, index) => index) : []);
  }, [initialContent]);

  const dirty = rawContent !== savedContent;

  const headerRow = rows[0] || [];
  const bodyRows = rows.slice(1);

  const displayedRows = useMemo(() => {
    const loweredSearch = searchText.trim().toLowerCase();
    let nextRows = bodyRows.map((row, rowIndex) => ({ row, rowIndex }));

    if (loweredSearch) {
      nextRows = nextRows.filter(({ row }) => row.some((cell) => cell.toLowerCase().includes(loweredSearch)));
    }

    if (sortColumn !== null) {
      nextRows = [...nextRows].sort((left, right) => {
        const a = (left.row[sortColumn] || "").toLowerCase();
        const b = (right.row[sortColumn] || "").toLowerCase();
        const comparison = a.localeCompare(b, undefined, { numeric: true });
        return sortDirection === "asc" ? comparison : -comparison;
      });
    }

    return nextRows;
  }, [bodyRows, searchText, sortColumn, sortDirection]);

  const orderedColumns = columnOrder.length > 0 ? columnOrder : headerRow.map((_, index) => index);

  const updateCell = (rowIndex: number, columnIndex: number, value: string): void => {
    setRows((current) => {
      const next = current.map((row) => [...row]);
      while (next[rowIndex].length <= columnIndex) {
        next[rowIndex].push("");
      }
      next[rowIndex][columnIndex] = value;
      const serialized = serializeCsv(next);
      setRawContent(serialized);
      return next;
    });
  };

  const handleSave = async (): Promise<void> => {
    setSaving(true);
    setErrorText("");
    setStatusText("Saving...");
    try {
      const content = serializeCsv(rows);
      const response = await updateScenarioEditableFile(scenarioId, relativePath, content);
      setRawContent(response.content);
      setSavedContent(response.content);
      setStatusText(`Saved ${response.relative_path}`);
    } catch (error) {
      setErrorText(error instanceof Error ? error.message : String(error));
      setStatusText("");
    } finally {
      setSaving(false);
    }
  };

  if (errorText && rows.length === 0) {
    return (
      <div className="workspace-tab-panel csv-editor-pane csv-editor-pane-state">
        <div className="python-editor-error">{errorText}</div>
      </div>
    );
  }

  if (rows.length === 0 && statusText) {
    return (
      <div className="workspace-tab-panel csv-editor-pane csv-editor-pane-state">
        <Spinner size={20} />
        <div className="python-editor-status">{statusText}</div>
      </div>
    );
  }

  return (
    <div className="workspace-tab-panel csv-editor-pane">
      <div className="python-editor-toolbar">
        <div className="python-editor-title-group">
          <div className="python-editor-title">{relativePath}</div>
          <div className="python-editor-subtitle">
            {dirty ? "Unsaved changes" : `Saved${modifiedAtUtc ? ` • ${modifiedAtUtc}` : ""}`}
          </div>
        </div>
        <div className="python-editor-actions">
          <InputGroup
            leftIcon="search"
            placeholder="Search rows"
            value={searchText}
            onChange={(event) => setSearchText(event.target.value)}
          />
          <Button
            small
            intent={Intent.PRIMARY}
            text="Save"
            onClick={() => {
              void handleSave();
            }}
            loading={saving}
          />
        </div>
      </div>
      {statusText ? <div className="python-editor-status-banner">{statusText}</div> : null}
      {errorText && rows.length > 0 ? <div className="python-editor-error-banner">{errorText}</div> : null}
      <div className="csv-editor-grid-shell">
        <div className="csv-editor-grid">
          <table>
            <thead>
              <tr>
                {orderedColumns.map((columnIndex) => (
                  <th
                    key={columnIndex}
                    draggable
                    onDragStart={() => setDragColumn(columnIndex)}
                    onDragOver={(event) => event.preventDefault()}
                    onDrop={() => {
                      if (dragColumn === null || dragColumn === columnIndex) return;
                      setColumnOrder((current) => {
                        const next = [...current];
                        const from = next.indexOf(dragColumn);
                        const to = next.indexOf(columnIndex);
                        if (from < 0 || to < 0) return current;
                        next.splice(from, 1);
                        next.splice(to, 0, dragColumn);
                        return next;
                      });
                      setDragColumn(null);
                    }}
                  >
                    <button
                      type="button"
                      className="csv-editor-header-button"
                      onClick={() => {
                        if (sortColumn === columnIndex) {
                          setSortDirection((current) => (current === "asc" ? "desc" : "asc"));
                          return;
                        }
                        setSortColumn(columnIndex);
                        setSortDirection("asc");
                      }}
                    >
                      <span>{headerRow[columnIndex] || `Column ${columnIndex + 1}`}</span>
                      {sortColumn === columnIndex ? <span>{sortDirection === "asc" ? "▲" : "▼"}</span> : null}
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {displayedRows.map(({ row, rowIndex }) => (
                <tr key={`${rowIndex}:${row.join("|")}`}>
                  {orderedColumns.map((columnIndex) => (
                    <td key={columnIndex}>
                      <input
                        className="csv-editor-cell-input"
                        value={row[columnIndex] || ""}
                        onChange={(event) => updateCell(rowIndex + 1, columnIndex, event.target.value)}
                      />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
