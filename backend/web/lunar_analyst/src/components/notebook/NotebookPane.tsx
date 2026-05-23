import React, { useEffect, useState } from "react";
import { Button, Spinner } from "@blueprintjs/core";
import {
  getNotebookOpenCapability,
  openMarimoNotebook,
  rememberNotebookOpenCapability,
} from "../../services/marimoService";

type Props = {
  scenarioId: string;
  relativePath: string;
  initialFileUrl?: string;
  modifiedAtUtc?: string | null;
};

export default function NotebookPane(props: Props): JSX.Element {
  const { scenarioId, relativePath, initialFileUrl = "", modifiedAtUtc = null } = props;
  const [iframeUrl, setIframeUrl] = useState(initialFileUrl);
  const [statusText, setStatusText] = useState(initialFileUrl ? "" : "Starting notebook session...");
  const [errorText, setErrorText] = useState("");

  useEffect(() => {
    let cancelled = false;

    if (initialFileUrl) {
      setIframeUrl(initialFileUrl);
      setStatusText("");
      setErrorText("");
      return () => {
        cancelled = true;
      };
    }

    void (async () => {
      const cached = getNotebookOpenCapability(scenarioId, relativePath, modifiedAtUtc);
      if (cached?.status === "not_openable") {
        setErrorText(`Notebook open failed: File is not a Marimo notebook: ${relativePath}`);
        setStatusText("");
        return;
      }
      setStatusText("Starting notebook session...");
      setErrorText("");
      try {
        const response = await openMarimoNotebook({
          scenario_id: scenarioId,
          relative_path: relativePath,
          restart_if_running: true,
        });
        if (cancelled) return;
        rememberNotebookOpenCapability({
          scenarioId,
          relativePath: response.relative_path,
          modifiedAtUtc: response.modified_at_utc || modifiedAtUtc,
          status: "openable",
          checkedAtUtc: new Date().toISOString(),
        });
        setIframeUrl(response.file_url);
        setStatusText("");
      } catch (error) {
        if (cancelled) return;
        const message = error instanceof Error ? error.message : String(error);
        if (isNotebookCapabilityFailure(message)) {
          rememberNotebookOpenCapability({
            scenarioId,
            relativePath,
            modifiedAtUtc,
            status: "not_openable",
            checkedAtUtc: new Date().toISOString(),
          });
        }
        setErrorText(`Notebook open failed: ${message}`);
        setStatusText("");
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [initialFileUrl, modifiedAtUtc, relativePath, scenarioId]);

  if (errorText) {
    return (
      <div className="workspace-tab-panel notebook-pane notebook-pane-state">
        <div className="notebook-pane-title">{relativePath}</div>
        <div className="notebook-pane-error">{errorText}</div>
      </div>
    );
  }

  if (!iframeUrl) {
    return (
      <div className="workspace-tab-panel notebook-pane notebook-pane-state">
        <Spinner size={20} />
        <div className="notebook-pane-status">{statusText}</div>
      </div>
    );
  }

  return (
    <div className="workspace-tab-panel notebook-pane">
      <div className="notebook-pane-toolbar">
        <div className="notebook-pane-title">{relativePath}</div>
        <Button
          small
          minimal
          icon="open-application"
          text="Open Externally"
          onClick={() => window.open(iframeUrl, "_blank", "noopener,noreferrer")}
        />
      </div>
      <iframe
        key={iframeUrl}
        title={`Notebook ${relativePath}`}
        src={iframeUrl}
        className="notebook-pane-frame"
      />
    </div>
  );
}

function isNotebookCapabilityFailure(message: string): boolean {
  const lower = String(message || "").toLowerCase();
  return lower.includes("not a marimo notebook")
    || lower.includes("must be a python file")
    || lower.includes("notebook target not found");
}
