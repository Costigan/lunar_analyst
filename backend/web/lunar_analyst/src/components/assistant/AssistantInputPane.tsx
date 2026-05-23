import React, { useMemo } from "react";
import { Button, HTMLSelect, Intent, TextArea } from "@blueprintjs/core";
import type {
  AssistantAccessMode,
  AssistantConfirmation,
  AssistantSession,
} from "../../services/assistantService";
import { actionLabel } from "../../utils/assistantPolicy";

type Props = {
  sessions: AssistantSession[];
  activeSessionId: string | null;
  activeScenarioId: string | null;
  providerOptions: { value: string; label: string }[];
  selectedProviderId: string;
  modelOptions: string[];
  selectedModelId: string;
  thinkingOptions: { value: string; label: string }[];
  selectedThinkingValue: string;
  thinkingEnabled: boolean;
  accessModeOptions: { value: AssistantAccessMode; label: string }[];
  selectedAccessMode: AssistantAccessMode;
  accessModeEnabled: boolean;
  pendingConfirmation: AssistantConfirmation | null;
  prompt: string;
  onPromptChange: (prompt: string) => void;
  onCreateSession: (title: string) => void;
  onSelectSession: (sessionId: string) => void;
  onSelectProvider: (providerId: string) => void;
  onSelectModel: (modelId: string) => void;
  onSelectThinking: (value: string) => void;
  onSelectAccessMode: (mode: AssistantAccessMode) => void;
  onCompactSession: (sessionId: string) => void;
  onSubmitPrompt: (prompt: string) => void;
  onResolveConfirmation: (decision: "allow_once" | "always_allow_action_type" | "deny_once") => void;
};

export default function AssistantInputPane(props: Props): JSX.Element {
  const {
    sessions,
    activeSessionId,
    activeScenarioId,
    providerOptions,
    selectedProviderId,
    modelOptions,
    selectedModelId,
    thinkingOptions,
    selectedThinkingValue,
    thinkingEnabled,
    accessModeOptions,
    selectedAccessMode,
    accessModeEnabled,
    pendingConfirmation,
    prompt,
    onPromptChange,
    onCreateSession,
    onSelectSession,
    onSelectProvider,
    onSelectModel,
    onSelectThinking,
    onSelectAccessMode,
    onCompactSession,
    onSubmitPrompt,
    onResolveConfirmation,
  } = props;
  const sortedSessions = useMemo(
    () =>
      [...sessions].sort((a, b) =>
        String(b.last_message_at_utc || b.updated_at_utc).localeCompare(String(a.last_message_at_utc || a.updated_at_utc)),
      ),
    [sessions],
  );
  const selectableModels = useMemo(() => {
    const seen = new Set<string>();
    const out: string[] = [];
    const add = (raw: string): void => {
      const value = String(raw || "").trim();
      if (!value || seen.has(value)) return;
      seen.add(value);
      out.push(value);
    };
    modelOptions.forEach(add);
    add(selectedModelId);
    return out;
  }, [modelOptions, selectedModelId]);
  const canSubmit = Boolean(activeSessionId && prompt.trim().length > 0);

  const submit = () => {
    const text = prompt.trim();
    if (!text) return;
    onSubmitPrompt(text);
  };

  const onPromptKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) {
      return;
    }
    event.preventDefault();
    submit();
  };

  const createSessionTitle = () => {
    const stamp = new Date().toISOString().replace("T", " ").slice(0, 19);
    onCreateSession(`Assistant Session ${stamp}`);
  };

  return (
    <div className="assistant-panel assistant-input-panel">
      <div className="assistant-panel-titlebar">
        <div className="assistant-titlebar-left">
          <div className="assistant-active-scenario-title">Scenario: {activeScenarioId || "(none)"}</div>
        </div>
        <div className="assistant-titlebar-controls">
          <div className="assistant-titlebar-control-row assistant-titlebar-control-row-primary">
            <HTMLSelect
              small
              title="Assistant provider"
              value={selectedProviderId || providerOptions[0]?.value || ""}
              onChange={(event) => onSelectProvider(event.target.value)}
              options={providerOptions}
              disabled={providerOptions.length === 0}
            />
            <HTMLSelect
              small
              title="Model"
              value={selectedModelId || selectableModels[0] || ""}
              onChange={(event) => onSelectModel(event.target.value)}
              options={selectableModels.map((model) => ({ value: model, label: model }))}
              disabled={selectableModels.length === 0}
            />
            <HTMLSelect
              small
              title="Session"
              value={activeSessionId || ""}
              onChange={(event) => onSelectSession(event.target.value)}
              options={sortedSessions.map((session) => ({
                value: session.session_id,
                label: session.title,
              }))}
              disabled={sortedSessions.length === 0}
            />
          </div>
          <div className="assistant-titlebar-control-row assistant-titlebar-control-row-secondary">
            <HTMLSelect
              small
              title="Access mode"
              value={selectedAccessMode}
              onChange={(event) => onSelectAccessMode(event.target.value as AssistantAccessMode)}
              options={accessModeOptions}
              disabled={!accessModeEnabled || accessModeOptions.length === 0}
            />
            <HTMLSelect
              small
              title="Thinking"
              value={selectedThinkingValue}
              onChange={(event) => onSelectThinking(event.target.value)}
              options={thinkingOptions}
              disabled={!thinkingEnabled || thinkingOptions.length === 0}
            />
            <Button small intent={Intent.PRIMARY} text="New Session" onClick={createSessionTitle} />
            {activeSessionId ? (
              <Button small text="Compact" onClick={() => onCompactSession(activeSessionId)} />
            ) : null}
          </div>
        </div>
      </div>

      <div className="assistant-panel-body assistant-input-main-row">
        <TextArea
          id="assistant-prompt"
          className="assistant-prompt-inline"
          fill
          growVertically
          rows={6}
          value={prompt}
          onChange={(event) => onPromptChange(event.target.value)}
          onKeyDown={onPromptKeyDown}
          placeholder="Ask a question or issue a command in prose..."
        />
        <Button className="assistant-send-inline" intent={Intent.PRIMARY} text="Send" onClick={submit} disabled={!canSubmit} />
      </div>

      {pendingConfirmation ? (
        <div className="assistant-confirmation-box assistant-panel-body">
          <div className="assistant-confirmation-title">Confirmation Required</div>
          <div className="assistant-confirmation-body">
            {actionLabel(pendingConfirmation.action_type)}: {pendingConfirmation.tool_name}
          </div>
          <div className="assistant-confirmation-actions">
            <Button text="Allow Once" intent={Intent.PRIMARY} onClick={() => onResolveConfirmation("allow_once")} />
            <Button text="Always Allow Type" onClick={() => onResolveConfirmation("always_allow_action_type")} />
            <Button text="Deny" intent={Intent.DANGER} onClick={() => onResolveConfirmation("deny_once")} />
          </div>
        </div>
      ) : null}
    </div>
  );
}
