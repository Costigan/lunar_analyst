import { apiJson } from "./apiClient";

export type AssistantActionType =
  | "launch_job"
  | "import_file"
  | "move_path"
  | "update_layer_state"
  | "delete_artifact"
  | "write_notebook";

export type AssistantRole = "system" | "user" | "assistant" | "tool";
export type AssistantAccessMode = "mcp_only" | "scenario_root";
export type AssistantThinkingMode = "none" | "boolean" | "level";

export type AssistantOutputKind = "image" | "table" | "plot" | "artifact_card" | "map_view";

export type AssistantOutput = {
  output_id: string;
  kind: AssistantOutputKind;
  mime_type: string;
  storage: "inline" | "file";
  title?: string | null;
  caption?: string | null;
  file_id?: string | null;
  data: Record<string, unknown>;
  metadata: Record<string, unknown>;
};

export type AssistantMessage = {
  message_id: string;
  session_id: string;
  role: AssistantRole;
  content: string;
  created_at_utc: string;
  turn_id: string | null;
  metadata: Record<string, unknown>;
  outputs: AssistantOutput[];
};

export type AssistantPolicy = {
  always_allow_action_types: AssistantActionType[];
};

export type AssistantSession = {
  session_id: string;
  title: string;
  created_at_utc: string;
  updated_at_utc: string;
  last_message_at_utc: string | null;
  policy: AssistantPolicy;
};

export type AssistantConfirmation = {
  confirmation_id: string;
  session_id: string;
  turn_id: string;
  action_type: AssistantActionType;
  tool_name: string;
  arguments: Record<string, unknown>;
  status: string;
  requested_at_utc: string;
  resolved_at_utc: string | null;
  resolution: string | null;
};

export type AssistantToolCall = {
  tool_call_id: string;
  session_id: string;
  turn_id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  status: string;
  created_at_utc: string;
  completed_at_utc: string | null;
  result: Record<string, unknown>;
  error: string | null;
  action_type: AssistantActionType | null;
  outputs: AssistantOutput[];
};

export type AssistantTurn = {
  turn_id: string;
  session_id: string;
  user_message_id: string;
  status: "queued" | "running" | "completed" | "failed" | "confirmation_required";
  provider_id: string | null;
  model_id: string | null;
  created_at_utc: string;
  updated_at_utc: string;
  error: string | null;
  usage: Record<string, unknown>;
};

export type AssistantProviderInfo = {
  provider_id: string;
  kind: string;
  execution_mode: string;
  access_mode?: AssistantAccessMode | null;
  available: boolean;
  default_model: string | null;
  models: string[];
  model_metadata?: Record<
    string,
    {
      capabilities: string[];
      thinking_mode: AssistantThinkingMode;
    }
  >;
  notes: string;
};

export type AssistantProviderCatalog = {
  default_provider_id: string | null;
  default_model_id: string | null;
  providers: AssistantProviderInfo[];
};

export type AssistantBugReportProgramState = {
  active_scenario_id?: string | null;
  active_assistant_session_id?: string | null;
  active_assistant_turn_id?: string | null;
  active_provider_id?: string | null;
  active_model_id?: string | null;
  active_panel?: string | null;
  assistant_prompt_draft?: string | null;
  workspace_state?: Record<string, unknown>;
};

export type AssistantBugReport = {
  bug_report_id: string;
  created_at_utc: string;
  report_text: string;
  assistant_session_id?: string | null;
  assistant_turn_id?: string | null;
  assistant_provider_id?: string | null;
  assistant_model_id?: string | null;
  scenario_id?: string | null;
  assistant_context: Record<string, unknown>;
  program_state: AssistantBugReportProgramState;
  log_excerpt: string[];
  redactions_applied: boolean;
};

export type AssistantBugReportResponse = {
  bug_report: AssistantBugReport;
  bundle_path: string;
};

export type AssistantBugReportRequest = {
  report_text: string;
  program_state: AssistantBugReportProgramState;
};

export type AssistantBugReportSummary = {
  bug_report_id: string;
  created_at_utc: string;
  report_text: string;
  assistant_session_id?: string | null;
  assistant_turn_id?: string | null;
  scenario_id?: string | null;
  bundle_path: string;
};

export type AssistantBugReportList = {
  bug_reports: AssistantBugReportSummary[];
};

export type AssistantCreateTurnResponse = {
  turn: AssistantTurn;
  assistant_message: AssistantMessage | null;
  confirmation: AssistantConfirmation | null;
  tool_calls: AssistantToolCall[];
};

export type AssistantDecision = "allow_once" | "always_allow_action_type" | "deny_once";

export async function listAssistantSessions(): Promise<AssistantSession[]> {
  const payload = await apiJson<{ sessions: AssistantSession[] }>("/api/v1/assistant/sessions");
  return Array.isArray(payload.sessions) ? payload.sessions : [];
}

export async function createAssistantSession(title: string): Promise<AssistantSession> {
  return apiJson<AssistantSession>("/api/v1/assistant/sessions", {
    method: "POST",
    body: { title },
  });
}

export async function listAssistantMessages(sessionId: string): Promise<AssistantMessage[]> {
  const payload = await apiJson<{ messages: AssistantMessage[] }>(
    `/api/v1/assistant/sessions/${encodeURIComponent(sessionId)}/messages`,
  );
  return Array.isArray(payload.messages) ? payload.messages : [];
}

export async function createAssistantTurn(
  sessionId: string,
  prompt: string,
  scenarioId: string | null,
  constraints: string | null,
  baseLayerVisible: boolean | null,
  providerId?: string | null,
  modelId?: string | null,
  accessMode?: AssistantAccessMode | null,
  thinking?: boolean | "low" | "medium" | "high" | null,
): Promise<AssistantCreateTurnResponse> {
  return apiJson<AssistantCreateTurnResponse>(
    `/api/v1/assistant/sessions/${encodeURIComponent(sessionId)}/turns`,
    {
      method: "POST",
      body: {
        prompt,
        scenario_id: scenarioId || null,
        constraints: constraints ?? null,
        base_layer_visible: baseLayerVisible,
        provider_id: providerId ?? null,
        model_id: modelId ?? null,
        access_mode: accessMode ?? null,
        thinking: thinking ?? null,
      },
    },
  );
}

export async function listAssistantProviderCatalog(): Promise<AssistantProviderCatalog> {
  const payload = await apiJson<{
    default_provider_id?: string | null;
    default_model_id?: string | null;
    providers?: AssistantProviderInfo[];
  }>("/api/v1/assistant/providers");
  return {
    default_provider_id:
      typeof payload.default_provider_id === "string" ? payload.default_provider_id : null,
    default_model_id:
      typeof payload.default_model_id === "string" ? payload.default_model_id : null,
    providers: Array.isArray(payload.providers) ? payload.providers : [],
  };
}

export async function listAssistantProviders(): Promise<AssistantProviderInfo[]> {
  const payload = await listAssistantProviderCatalog();
  return payload.providers;
}

export async function resolveAssistantConfirmation(
  sessionId: string,
  confirmationId: string,
  decision: AssistantDecision,
): Promise<AssistantCreateTurnResponse> {
  return apiJson<AssistantCreateTurnResponse>(
    `/api/v1/assistant/sessions/${encodeURIComponent(sessionId)}/confirmations/${encodeURIComponent(confirmationId)}`,
    {
      method: "POST",
      body: { decision },
    },
  );
}

export async function compactAssistantSession(sessionId: string): Promise<void> {
  await apiJson(
    `/api/v1/assistant/sessions/${encodeURIComponent(sessionId)}:compact`,
    {
      method: "POST",
      body: { max_messages_to_compact: 80 },
    },
  );
}

export async function captureAssistantBugReport(
  sessionId: string,
  request: AssistantBugReportRequest,
): Promise<AssistantBugReportResponse> {
  return apiJson<AssistantBugReportResponse>(
    `/api/v1/assistant/sessions/${encodeURIComponent(sessionId)}/bug-reports`,
    {
      method: "POST",
      body: request,
    },
  );
}

export async function listAssistantBugReports(): Promise<AssistantBugReportSummary[]> {
  const payload = await apiJson<AssistantBugReportList>("/api/v1/assistant/bug-reports");
  return Array.isArray(payload.bug_reports) ? payload.bug_reports : [];
}
