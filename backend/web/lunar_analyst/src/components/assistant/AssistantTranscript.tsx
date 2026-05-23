import React from "react";
import type { AssistantMessage } from "../../services/assistantService";
import AssistantOutputView from "./AssistantOutputView";

type Props = {
  messages: AssistantMessage[];
};

export default function AssistantTranscript(props: Props): JSX.Element {
  const { messages } = props;

  return (
    <div className="assistant-response-log">
      {messages.length === 0 ? (
        <div className="assistant-response-empty">No assistant messages yet.</div>
      ) : (
        messages.map((msg) => (
          <div key={msg.message_id} className={`assistant-message assistant-message-${msg.role}`}>
            <div className="assistant-message-header">
              <span className="assistant-message-role-group">
                <span className="assistant-message-role">{msg.role}</span>
                {msg.metadata?.fallback_used ? (
                  <span className="assistant-message-tag assistant-message-tag-fallback" title={String(msg.metadata?.fallback_kind || "")}>
                    fallback
                  </span>
                ) : null}
              </span>
              <span className="assistant-message-time">{msg.created_at_utc}</span>
            </div>
            <pre className="assistant-message-content">{msg.content}</pre>
            {Array.isArray(msg.outputs) && msg.outputs.length > 0 ? (
              <div className="assistant-message-outputs">
                {msg.outputs.map((output) => (
                  <AssistantOutputView key={output.output_id} output={output} />
                ))}
              </div>
            ) : null}
          </div>
        ))
      )}
    </div>
  );
}
