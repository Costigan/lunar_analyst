import React, { useMemo, useState } from "react";
import { Button, InputGroup, Intent } from "@blueprintjs/core";
import type { AssistantSession } from "../../services/assistantService";

type Props = {
  sessions: AssistantSession[];
  activeSessionId: string | null;
  onSelectSession: (sessionId: string) => void;
  onCreateSession: (title: string) => void;
  onCompactSession: (sessionId: string) => void;
};

export default function AssistantSessionList(props: Props): JSX.Element {
  const { sessions, activeSessionId, onSelectSession, onCreateSession, onCompactSession } = props;
  const [title, setTitle] = useState("New Assistant Session");
  const sorted = useMemo(
    () =>
      [...sessions].sort((a, b) =>
        String(b.last_message_at_utc || b.updated_at_utc).localeCompare(String(a.last_message_at_utc || a.updated_at_utc)),
      ),
    [sessions],
  );

  return (
    <div className="assistant-session-list">
      <label className="pattern-combobox-label" htmlFor="assistant-session-title">Session Title</label>
      <InputGroup
        id="assistant-session-title"
        value={title}
        onChange={(event) => setTitle(event.target.value)}
        placeholder="Session title"
      />
      <div className="assistant-session-actions">
        <Button
          intent={Intent.PRIMARY}
          text="Create Session"
          onClick={() => onCreateSession(title.trim() || "Assistant Session")}
        />
      </div>
      <div className="assistant-session-items">
        {sorted.map((session) => (
          <div key={session.session_id} className={`assistant-session-item ${activeSessionId === session.session_id ? "active" : ""}`}>
            <button
              type="button"
              className="assistant-session-select"
              onClick={() => onSelectSession(session.session_id)}
            >
              <span className="assistant-session-title">{session.title}</span>
              <span className="assistant-session-meta">{session.updated_at_utc}</span>
            </button>
            <button
              type="button"
              className="assistant-session-compact"
              onClick={() => onCompactSession(session.session_id)}
              title="Compact session context"
            >
              Compact
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
