import React, { useEffect, useState } from "react";
import { Button, Intent, Spinner } from "@blueprintjs/core";
import { readScenarioEditableFile, updateScenarioEditableFile } from "../../services/scenarioService";

type Props = {
  scenarioId: string;
  relativePath: string;
  initialContent?: string;
  modifiedAtUtc?: string | null;
};

export default function TextEditorPane(props: Props): JSX.Element {
  const { scenarioId, relativePath, initialContent = "", modifiedAtUtc = null } = props;
  const [content, setContent] = useState(initialContent);
  const [savedContent, setSavedContent] = useState(initialContent);
  const [statusText, setStatusText] = useState(initialContent ? "" : "Loading text file...");
  const [errorText, setErrorText] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    if (initialContent) {
      return;
    }
    void (async () => {
      try {
        const response = await readScenarioEditableFile(scenarioId, relativePath);
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

  const handleSave = async (): Promise<void> => {
    setSaving(true);
    setErrorText("");
    setStatusText("Saving...");
    try {
      const response = await updateScenarioEditableFile(scenarioId, relativePath, content);
      setSavedContent(response.content);
      setStatusText(`Saved ${response.relative_path}`);
    } catch (error) {
      setErrorText(error instanceof Error ? error.message : String(error));
      setStatusText("");
    } finally {
      setSaving(false);
    }
  };

  if (errorText && !content) {
    return (
      <div className="workspace-tab-panel text-editor-pane text-editor-pane-state">
        <div className="python-editor-error">{errorText}</div>
      </div>
    );
  }

  if (!content && statusText) {
    return (
      <div className="workspace-tab-panel text-editor-pane text-editor-pane-state">
        <Spinner size={20} />
        <div className="python-editor-status">{statusText}</div>
      </div>
    );
  }

  const dirty = content !== savedContent;

  return (
    <div className="workspace-tab-panel text-editor-pane">
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
      {errorText && content ? <div className="python-editor-error-banner">{errorText}</div> : null}
      <div className="text-editor-surface">
        <textarea
          className="text-editor-textarea"
          spellCheck={false}
          value={content}
          onChange={(event) => setContent(event.target.value)}
        />
      </div>
    </div>
  );
}
