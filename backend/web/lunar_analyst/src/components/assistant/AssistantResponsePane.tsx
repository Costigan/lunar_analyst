import React from "react";
import { Button } from "@blueprintjs/core";
import type { AssistantMessage } from "../../services/assistantService";
import AssistantTranscript from "./AssistantTranscript";

type Props = {
  messages: AssistantMessage[];
  expanded?: boolean;
  onOpenExpanded?: () => void;
  onShowCompact?: () => void;
};

export default function AssistantResponsePane(props: Props): JSX.Element {
  const { messages, expanded = false, onOpenExpanded, onShowCompact } = props;
  return (
    <div className="assistant-panel assistant-response-panel">
      <div className="assistant-panel-titlebar assistant-response-titlebar">
        <div className="assistant-titlebar-left">
          <div className="assistant-active-scenario-title">
            {expanded ? "Expanded assistant response" : "Assistant response"}
          </div>
        </div>
        <div className="assistant-titlebar-controls">
          {expanded ? (
            <Button small text="Show Sidebar View" onClick={onShowCompact} />
          ) : (
            <Button small text="Open Assistant Workspace" onClick={onOpenExpanded} />
          )}
        </div>
      </div>
      <div className="assistant-panel-body assistant-response-body">
        <AssistantTranscript messages={messages} />
      </div>
    </div>
  );
}
