import React from "react";
import type { AssistantOutput } from "../../services/assistantService";

type Props = {
  output: AssistantOutput;
};

function resolveBinarySrc(output: AssistantOutput): string | null {
  if (output.storage === "file" && output.file_id) {
    return `/api/v1/files/${encodeURIComponent(output.file_id)}`;
  }
  const base64 = typeof output.data?.base64 === "string" ? output.data.base64 : "";
  if (!base64) return null;
  return `data:${output.mime_type};base64,${base64}`;
}

function renderValue(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (value == null) return "";
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function TableOutput(props: { output: AssistantOutput }): JSX.Element {
  const columns = Array.isArray(props.output.data?.columns) ? props.output.data.columns : [];
  const rows = Array.isArray(props.output.data?.rows) ? props.output.data.rows : [];
  const rowCount = Number(props.output.data?.row_count ?? rows.length);
  const truncated = Boolean(props.output.data?.truncated);
  return (
    <div className="assistant-output assistant-output-table">
      <div className="assistant-output-title">{props.output.title || "Table preview"}</div>
      <div className="assistant-output-table-meta">
        {Number.isFinite(rowCount) ? `${rowCount} row(s)` : `${rows.length} row(s)`}
        {truncated ? " (truncated)" : ""}
      </div>
      <div className="assistant-output-table-wrap">
        <table className="assistant-output-table-grid">
          <thead>
            <tr>
              {columns.map((column, index) => {
                const key =
                  column && typeof column === "object" && typeof (column as { key?: unknown }).key === "string"
                    ? ((column as { key: string }).key || `col_${index}`)
                    : `col_${index}`;
                const label =
                  column && typeof column === "object" && typeof (column as { label?: unknown }).label === "string"
                    ? ((column as { label: string }).label || key)
                    : key;
                return <th key={key}>{label}</th>;
              })}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => {
              const record = row && typeof row === "object" ? (row as Record<string, unknown>) : {};
              return (
                <tr key={`row_${rowIndex}`}>
                  {columns.map((column, columnIndex) => {
                    const key =
                      column && typeof column === "object" && typeof (column as { key?: unknown }).key === "string"
                        ? ((column as { key: string }).key || `col_${columnIndex}`)
                        : `col_${columnIndex}`;
                    return <td key={`${rowIndex}_${key}`}>{renderValue(record[key])}</td>;
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ArtifactCardOutput(props: { output: AssistantOutput }): JSX.Element {
  const summary =
    typeof props.output.data?.summary_text === "string" ? props.output.data.summary_text : props.output.caption || "";
  const keyStats =
    props.output.data?.key_stats && typeof props.output.data.key_stats === "object"
      ? (props.output.data.key_stats as Record<string, unknown>)
      : {};
  return (
    <div className="assistant-output assistant-output-card">
      <div className="assistant-output-title">{props.output.title || "Artifact"}</div>
      {summary ? <div className="assistant-output-card-summary">{summary}</div> : null}
      <div className="assistant-output-card-stats">
        {Object.entries(keyStats).map(([key, value]) => (
          <div key={key} className="assistant-output-card-stat">
            <span className="assistant-output-card-key">{key}</span>
            <span className="assistant-output-card-value">{renderValue(value)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function BinaryOutput(props: { output: AssistantOutput }): JSX.Element {
  const src = resolveBinarySrc(props.output);
  if (!src) {
    return (
      <div className="assistant-output assistant-output-unsupported">
        Unsupported binary payload for {props.output.mime_type}.
      </div>
    );
  }
  const alt =
    typeof props.output.metadata?.alt === "string"
      ? props.output.metadata.alt
      : props.output.title || props.output.caption || props.output.mime_type;
  return (
    <div className="assistant-output assistant-output-image">
      {props.output.title ? <div className="assistant-output-title">{props.output.title}</div> : null}
      <img className="assistant-output-image-el" src={src} alt={alt} loading="lazy" />
      {props.output.caption ? <div className="assistant-output-caption">{props.output.caption}</div> : null}
    </div>
  );
}

export default function AssistantOutputView(props: Props): JSX.Element {
  const { output } = props;
  if (output.kind === "table") {
    return <TableOutput output={output} />;
  }
  if (output.kind === "artifact_card") {
    return <ArtifactCardOutput output={output} />;
  }
  if (
    output.kind === "image" ||
    output.kind === "plot" ||
    output.mime_type.startsWith("image/")
  ) {
    return <BinaryOutput output={output} />;
  }
  return (
    <div className="assistant-output assistant-output-unsupported">
      <div className="assistant-output-title">{output.title || output.kind}</div>
      <pre className="assistant-output-json">{JSON.stringify(output.data, null, 2)}</pre>
    </div>
  );
}
