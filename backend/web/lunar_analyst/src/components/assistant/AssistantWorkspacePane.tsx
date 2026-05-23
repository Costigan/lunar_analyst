import React, { useEffect, useMemo, useState } from "react";
import { Button } from "@blueprintjs/core";
import type {
  AssistantAccessMode,
  AssistantConfirmation,
  AssistantMessage,
  AssistantSession,
} from "../../services/assistantService";
import AssistantInputPane from "./AssistantInputPane";
import AssistantTranscript from "./AssistantTranscript";

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
  messages: AssistantMessage[];
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
  onFocusSidebarAssistant?: () => void;
};

export default function AssistantWorkspacePane(props: Props): JSX.Element {
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
    messages,
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
    onFocusSidebarAssistant,
  } = props;

  const [inputPaneHeight, setInputPaneHeight] = useState(240);
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    if (!dragging) return undefined;

    const onPointerMove = (event: PointerEvent) => {
      const viewportHeight = window.innerHeight || 900;
      const nextHeight = Math.max(180, Math.min(Math.round(viewportHeight * 0.55), viewportHeight - event.clientY - 80));
      setInputPaneHeight(nextHeight);
    };
    const stopDragging = () => setDragging(false);
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", stopDragging);
    return () => {
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", stopDragging);
    };
  }, [dragging]);

  const transcriptTitle = useMemo(
    () => (activeSessionId ? "Assistant conversation" : "Assistant conversation"),
    [activeSessionId],
  );

  return (
    <div className="assistant-workspace">
      <div className="assistant-workspace-header">
        <div className="assistant-workspace-title">{transcriptTitle}</div>
        <div className="assistant-workspace-actions">
          {onFocusSidebarAssistant ? <Button small text="Show Sidebar Panels" onClick={onFocusSidebarAssistant} /> : null}
        </div>
      </div>
      <div className="assistant-workspace-transcript">
        <AssistantTranscript messages={messages} />
      </div>
      <div
        className={`assistant-workspace-resizer ${dragging ? "dragging" : ""}`}
        onPointerDown={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        role="separator"
        aria-orientation="horizontal"
        aria-label="Resize assistant input"
      />
      <div className="assistant-workspace-input" style={{ height: `${inputPaneHeight}px` }}>
        <AssistantInputPane
          sessions={sessions}
          activeSessionId={activeSessionId}
          activeScenarioId={activeScenarioId}
          providerOptions={providerOptions}
          selectedProviderId={selectedProviderId}
          modelOptions={modelOptions}
          selectedModelId={selectedModelId}
          thinkingOptions={thinkingOptions}
          selectedThinkingValue={selectedThinkingValue}
          thinkingEnabled={thinkingEnabled}
          accessModeOptions={accessModeOptions}
          selectedAccessMode={selectedAccessMode}
          accessModeEnabled={accessModeEnabled}
          pendingConfirmation={pendingConfirmation}
          prompt={prompt}
          onPromptChange={onPromptChange}
          onCreateSession={onCreateSession}
          onSelectSession={onSelectSession}
          onSelectProvider={onSelectProvider}
          onSelectModel={onSelectModel}
          onSelectThinking={onSelectThinking}
          onSelectAccessMode={onSelectAccessMode}
          onCompactSession={onCompactSession}
          onSubmitPrompt={onSubmitPrompt}
          onResolveConfirmation={onResolveConfirmation}
        />
      </div>
    </div>
  );
}
