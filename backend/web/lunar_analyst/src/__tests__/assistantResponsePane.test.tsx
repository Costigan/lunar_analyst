import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import AssistantResponsePane from "../components/assistant/AssistantResponsePane";

describe("AssistantResponsePane", () => {
  it("renders typed outputs alongside message text", () => {
    const html = renderToStaticMarkup(
      <AssistantResponsePane
        messages={[
          {
            message_id: "msg_1",
            session_id: "session_1",
            role: "assistant",
            content: "Table preview ready.",
            created_at_utc: "2026-03-06T12:00:00Z",
            turn_id: "turn_1",
            metadata: {},
            outputs: [
              {
                output_id: "out_table",
                kind: "table",
                mime_type: "application/vnd.lunar-analyst.table+json",
                storage: "inline",
                title: "stats.csv sample",
                caption: null,
                file_id: null,
                data: {
                  columns: [{ key: "value", label: "Value", dtype: "number" }],
                  rows: [{ value: 1 }],
                  row_count: 1,
                  truncated: false,
                },
                metadata: {},
              },
            ],
          },
        ]}
      />,
    );

    expect(html).toContain("Table preview ready.");
    expect(html).toContain("stats.csv sample");
    expect(html).toContain("Value");
    expect(html).toContain("1 row(s)");
  });
});
