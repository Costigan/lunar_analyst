import React, { useEffect, useMemo, useRef, useState } from "react";
import { Button, Intent, Spinner } from "@blueprintjs/core";
import {
  lintScenarioPythonFile,
  listScenarioPythonEntries,
  readScenarioPythonFile,
  updateScenarioPythonFile,
} from "../../services/scenarioService";
import { runTool } from "../../services/toolService";

type Props = {
  scenarioId: string;
  relativePath: string;
  initialContent?: string;
  modifiedAtUtc?: string | null;
};

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function highlightPython(source: string): string {
  const escaped = escapeHtml(source);
  return escaped
    .replace(/(&quot;.*?&quot;|'.*?')/g, '<span class="py-token-string">$1</span>')
    .replace(/\b(def|class|return|if|elif|else|for|while|import|from|as|try|except|finally|with|lambda|pass|break|continue|raise|yield|True|False|None)\b/g, '<span class="py-token-keyword">$1</span>')
    .replace(/\b([0-9]+(?:\.[0-9]+)?)\b/g, '<span class="py-token-number">$1</span>')
    .replace(/(^|\s)(#[^\n]*)/gm, '$1<span class="py-token-comment">$2</span>');
}

export default function PythonEditorPane(props: Props): JSX.Element {
  const { scenarioId, relativePath, initialContent = "", modifiedAtUtc = null } = props;
  const [content, setContent] = useState(initialContent);
  const [savedContent, setSavedContent] = useState(initialContent);
  const [statusText, setStatusText] = useState(initialContent ? "" : "Loading Python file...");
  const [errorText, setErrorText] = useState("");
  const [busyAction, setBusyAction] = useState<"" | "save" | "lint" | "run">("");
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const highlightRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    if (initialContent) {
      return;
    }
    void (async () => {
      try {
        const response = await readScenarioPythonFile(scenarioId, relativePath);
        if (cancelled) return;
        setContent(response.content);
        setSavedContent(response.content);
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

  const dirty = content !== savedContent;

  const highlightedContent = useMemo(() => `${highlightPython(content)}\n`, [content]);

  const syncScroll = (): void => {
    if (!textareaRef.current || !highlightRef.current) return;
    highlightRef.current.scrollTop = textareaRef.current.scrollTop;
    highlightRef.current.scrollLeft = textareaRef.current.scrollLeft;
  };

  const handleSave = async (): Promise<void> => {
    setBusyAction("save");
    setErrorText("");
    setStatusText("Saving...");
    try {
      const response = await updateScenarioPythonFile(scenarioId, relativePath, content);
      setSavedContent(response.content);
      setStatusText(`Saved ${response.relative_path}`);
    } catch (error) {
      setErrorText(error instanceof Error ? error.message : String(error));
      setStatusText("");
    } finally {
      setBusyAction("");
    }
  };

  const handleLint = async (): Promise<void> => {
    setBusyAction("lint");
    setErrorText("");
    setStatusText("Running lint...");
    try {
      if (dirty) {
        await updateScenarioPythonFile(scenarioId, relativePath, content);
        setSavedContent(content);
      }
      const result = await lintScenarioPythonFile(scenarioId, relativePath);
      setStatusText(result.ok ? "Lint passed." : "Lint failed. See Messages.");
      if (!result.ok && result.stderr) {
        setErrorText(result.stderr);
      }
    } catch (error) {
      setErrorText(error instanceof Error ? error.message : String(error));
      setStatusText("");
    } finally {
      setBusyAction("");
    }
  };

  const handleRun = async (): Promise<void> => {
    setBusyAction("run");
    setErrorText("");
    setStatusText("Queueing script run...");
    try {
      if (dirty) {
        await updateScenarioPythonFile(scenarioId, relativePath, content);
        setSavedContent(content);
      }
      const entries = await listScenarioPythonEntries(scenarioId);
      const match = entries.find((entry) => entry.relative_path.toLowerCase() === relativePath.toLowerCase());
      if (!match) {
        throw new Error(`Scenario Python entry not found: ${relativePath}`);
      }
      const result = await runTool("/api/v1/jobs/run-notebook-definition", {
        scenario_id: scenarioId,
        notebook_job_id: match.notebook_job_id,
        params: {},
        runtime_mode: "osgeo",
      });
      const jobId = String(result.job_id || result.run_id || "").trim();
      setStatusText(jobId ? `Script queued as ${jobId}. See Messages.` : "Script queued. See Messages.");
    } catch (error) {
      setErrorText(error instanceof Error ? error.message : String(error));
      setStatusText("");
    } finally {
      setBusyAction("");
    }
  };

  if (errorText && !content) {
    return (
      <div className="workspace-tab-panel python-editor-pane python-editor-pane-state">
        <div className="python-editor-error">{errorText}</div>
      </div>
    );
  }

  if (!content && statusText) {
    return (
      <div className="workspace-tab-panel python-editor-pane python-editor-pane-state">
        <Spinner size={20} />
        <div className="python-editor-status">{statusText}</div>
      </div>
    );
  }

  return (
    <div className="workspace-tab-panel python-editor-pane">
      <div className="python-editor-toolbar">
        <div className="python-editor-title-group">
          <div className="python-editor-title">{relativePath}</div>
          <div className="python-editor-subtitle">
            {dirty ? "Unsaved changes" : `Saved${modifiedAtUtc ? ` • ${modifiedAtUtc}` : ""}`}
          </div>
        </div>
        <div className="python-editor-actions">
          <Button
            small
            intent={Intent.NONE}
            text="Save"
            onClick={() => {
              void handleSave();
            }}
            loading={busyAction === "save"}
          />
          <Button
            small
            intent={Intent.NONE}
            text="Lint"
            onClick={() => {
              void handleLint();
            }}
            loading={busyAction === "lint"}
          />
          <Button
            small
            intent={Intent.PRIMARY}
            text="Run Script"
            onClick={() => {
              void handleRun();
            }}
            loading={busyAction === "run"}
          />
        </div>
      </div>
      {statusText ? <div className="python-editor-status-banner">{statusText}</div> : null}
      {errorText && content ? <div className="python-editor-error-banner">{errorText}</div> : null}
      <div className="python-editor-surface">
        <pre
          ref={highlightRef}
          className="python-editor-highlight"
          aria-hidden="true"
          dangerouslySetInnerHTML={{ __html: highlightedContent }}
        />
        <textarea
          ref={textareaRef}
          className="python-editor-textarea"
          spellCheck={false}
          value={content}
          onChange={(event) => setContent(event.target.value)}
          onScroll={syncScroll}
        />
      </div>
    </div>
  );
}
