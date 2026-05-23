import type { Dispatch, MutableRefObject, SetStateAction } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { MapController } from "../map/mapController";
import {
  compactAssistantSession,
  createAssistantSession,
  createAssistantTurn,
  listAssistantProviderCatalog,
  listAssistantMessages,
  listAssistantSessions,
  resolveAssistantConfirmation,
  type AssistantAccessMode,
  type AssistantConfirmation,
  type AssistantMessage,
  type AssistantProviderInfo,
  type AssistantSession,
} from "../services/assistantService";
import type { AssistantWsEvent } from "../services/assistantWsClient";

const EXTRA_OPENAI_MODELS = ["gpt-5-mini", "gpt-5-nano", "gpt-5.2-codex"] as const;
const DEFAULT_ASSISTANT_ACCESS_MODE: AssistantAccessMode = "mcp_only";

function dedupeStrings(values: readonly string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const item of values) {
    const value = String(item || "").trim();
    if (!value || seen.has(value)) continue;
    seen.add(value);
    out.push(value);
  }
  return out;
}

function isAssistantAccessMode(value: unknown): value is AssistantAccessMode {
  return value === "mcp_only" || value === "scenario_root";
}

function parseExtent(data: Record<string, unknown> | undefined): [number, number, number, number] | null {
  const raw = Array.isArray(data?.dem_extent) ? data.dem_extent : null;
  if (!raw || raw.length !== 4) return null;
  const extent = raw.map((item) => Number(item)) as [number, number, number, number];
  if (extent.some((item) => !Number.isFinite(item))) return null;
  const isPlaceholder =
    Math.abs(extent[0] + 1) <= 1e-9 &&
    Math.abs(extent[1] + 1) <= 1e-9 &&
    Math.abs(extent[2] - 1) <= 1e-9 &&
    Math.abs(extent[3] - 1) <= 1e-9;
  if (isPlaceholder) return null;
  return extent;
}

export type AssistantDraftState = {
  turnId: string;
  text: string;
  createdAt: string;
};

export type AssistantStreamDecision = {
  nextDraft: AssistantDraftState | null;
  appendErrorMessage: AssistantMessage | null;
  scenarioId: string | null;
  scenarioExtent: [number, number, number, number] | null;
  refreshMessages: boolean;
  refreshSessions: boolean;
};

export function reduceAssistantStreamEvent(
  event: AssistantWsEvent,
  prevDraft: AssistantDraftState | null,
  sessionId: string | null,
  nowIso: () => string = () => new Date().toISOString(),
): AssistantStreamDecision {
  const base: AssistantStreamDecision = {
    nextDraft: prevDraft,
    appendErrorMessage: null,
    scenarioId: null,
    scenarioExtent: null,
    refreshMessages: false,
    refreshSessions: false,
  };

  if (event.event === "assistant_turn_started") {
    const turnId = String(event.turn_id || "").trim();
    if (!turnId) return base;
    if (prevDraft && prevDraft.turnId === turnId) {
      return base;
    }
    return {
      ...base,
      nextDraft: {
        turnId,
        text: "Working...",
        createdAt: nowIso(),
      },
    };
  }

  if (event.event === "assistant_scenario_changed") {
    const scenarioId = String(event.data?.scenario_id || "").trim() || null;
    return {
      ...base,
      scenarioId,
      scenarioExtent: parseExtent(event.data),
      refreshMessages: true,
      refreshSessions: true,
    };
  }

  if (event.event === "assistant_delta") {
    const delta = String(event.data?.text_delta || "");
    const turnId = String(event.turn_id || "").trim();
    if (!delta) return base;
    if (prevDraft && prevDraft.turnId === turnId) {
      return {
        ...base,
        nextDraft: {
          ...prevDraft,
          text: prevDraft.text === "Working..." ? delta : `${prevDraft.text}${delta}`,
        },
      };
    }
    return {
      ...base,
      nextDraft: {
        turnId: turnId || "draft",
        text: delta,
        createdAt: nowIso(),
      },
    };
  }

  if (
    event.event === "assistant_turn_completed" ||
    event.event === "assistant_confirmation_required" ||
    event.event === "assistant_confirmation_resolved"
  ) {
    const turnId = String(event.turn_id || "").trim();
    const nextDraft = !prevDraft
      ? prevDraft
      : (!turnId || prevDraft.turnId === turnId ? null : prevDraft);
    return {
      ...base,
      nextDraft,
      refreshMessages: true,
      refreshSessions: true,
    };
  }

  if (event.event === "assistant_error") {
    const turnId = String(event.turn_id || "").trim();
    const errorText = String(event.data?.error || "Assistant request failed.");
    const nextDraft = !prevDraft
      ? prevDraft
      : (!turnId || prevDraft.turnId === turnId ? null : prevDraft);
    const appendErrorMessage = sessionId
        ? {
            message_id: `local_error_${Date.now()}`,
            session_id: sessionId,
            role: "assistant" as const,
            content: `Error: ${errorText}`,
            created_at_utc: nowIso(),
            turn_id: turnId || null,
            metadata: { local_error: true },
            outputs: [],
          }
        : null;
    return {
      ...base,
      nextDraft,
      appendErrorMessage,
      refreshSessions: true,
    };
  }

  return base;
}

export type AssistantSessionState = {
  assistantSessions: AssistantSession[];
  activeAssistantSessionId: string | null;
  assistantProviderOptions: { value: string; label: string }[];
  assistantProviderId: string;
  assistantModelOptions: string[];
  assistantModelId: string;
  assistantThinkingOptions: { value: string; label: string }[];
  assistantThinkingValue: string;
  assistantThinkingEnabled: boolean;
  assistantAccessModeOptions: { value: AssistantAccessMode; label: string }[];
  assistantAccessMode: AssistantAccessMode;
  assistantAccessModeEnabled: boolean;
  assistantDisplayMessages: AssistantMessage[];
  pendingAssistantConfirmation: AssistantConfirmation | null;
  setAssistantModelId: Dispatch<SetStateAction<string>>;
  handleAssistantSelectThinking: (value: string) => void;
  handleAssistantCreateSession: (title: string) => void;
  handleAssistantSelectSession: (sessionId: string) => void;
  handleAssistantSelectProvider: (providerId: string) => void;
  handleAssistantSelectAccessMode: (mode: AssistantAccessMode) => void;
  handleAssistantCompactSession: (sessionId: string) => void;
  handleAssistantSubmitPrompt: (prompt: string) => void;
  handleAssistantResolveConfirmation: (
    decision: "allow_once" | "always_allow_action_type" | "deny_once",
  ) => void;
  handleAssistantStreamEvent: (event: AssistantWsEvent) => void;
};

type UseAssistantSessionArgs = {
  activeScenarioIdRef: MutableRefObject<string | null>;
  baseLayerVisible: boolean;
  onScenarioChange: (scenarioId: string) => void;
  mapControllerRef: MutableRefObject<MapController | null>;
};

export function useAssistantSession(args: UseAssistantSessionArgs): AssistantSessionState {
  const { activeScenarioIdRef, baseLayerVisible, onScenarioChange, mapControllerRef } = args;

  const [assistantSessions, setAssistantSessions] = useState<AssistantSession[]>([]);
  const [activeAssistantSessionId, setActiveAssistantSessionId] = useState<string | null>(null);
  const [assistantProviders, setAssistantProviders] = useState<AssistantProviderInfo[]>([]);
  const [assistantCatalogDefaultProviderId, setAssistantCatalogDefaultProviderId] = useState<string>("");
  const [assistantCatalogDefaultModelId, setAssistantCatalogDefaultModelId] = useState<string>("");
  const [assistantProviderId, setAssistantProviderId] = useState<string>("");
  const [assistantModelId, setAssistantModelId] = useState<string>("");
  const [assistantThinkingValue, setAssistantThinkingValue] = useState<string>("");
  const [assistantAccessMode, setAssistantAccessMode] = useState<AssistantAccessMode>(DEFAULT_ASSISTANT_ACCESS_MODE);
  const [assistantMessages, setAssistantMessages] = useState<AssistantMessage[]>([]);
  const [assistantDraft, setAssistantDraft] = useState<AssistantDraftState | null>(null);
  const [pendingAssistantConfirmation, setPendingAssistantConfirmation] = useState<AssistantConfirmation | null>(null);
  const assistantDraftRef = useRef<AssistantDraftState | null>(null);

  useEffect(() => {
    assistantDraftRef.current = assistantDraft;
  }, [assistantDraft]);

  const refreshAssistantSessions = useCallback(async () => {
    const sessions = await listAssistantSessions();
    setAssistantSessions(sessions);
    if (!activeAssistantSessionId && sessions.length > 0) {
      setActiveAssistantSessionId(sessions[0].session_id);
    }
  }, [activeAssistantSessionId]);

  const refreshAssistantMessages = useCallback(async (sessionId: string | null) => {
    if (!sessionId) {
      setAssistantMessages([]);
      return;
    }
    const messages = await listAssistantMessages(sessionId);
    setAssistantMessages(messages);
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const catalog = await listAssistantProviderCatalog();
        const providers = catalog.providers;
        if (cancelled) return;
        setAssistantProviders(providers);
        const catalogDefaultProvider = String(catalog.default_provider_id || "").trim();
        const catalogDefaultModel = String(catalog.default_model_id || "").trim();
        setAssistantCatalogDefaultProviderId(catalogDefaultProvider);
        setAssistantCatalogDefaultModelId(catalogDefaultModel);
        const preferredProvider =
          providers.find((provider) => provider.provider_id === catalogDefaultProvider) ||
          providers[0];
        setAssistantProviderId((current) => {
          const active = String(current || "").trim();
          if (active && providers.some((provider) => provider.provider_id === active)) return active;
          return String(preferredProvider?.provider_id || "").trim();
        });
      } catch (error) {
        if (cancelled) return;
        console.warn("[lunar-analyst][assistant] provider catalog fetch failed", error);
        setAssistantProviders([]);
        setAssistantCatalogDefaultProviderId("");
        setAssistantCatalogDefaultModelId("");
        setAssistantProviderId("");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const provider = assistantProviders.find((item) => item.provider_id === assistantProviderId);
    const providerDefault = isAssistantAccessMode(provider?.access_mode)
      ? provider.access_mode
      : DEFAULT_ASSISTANT_ACCESS_MODE;
    setAssistantAccessMode(providerDefault);
  }, [assistantProviders, assistantProviderId]);

  const assistantModelOptions = useMemo(() => {
    const selected = assistantProviders.find((provider) => provider.provider_id === assistantProviderId);
    const models = Array.isArray(selected?.models) ? selected.models : [];
    const fallback = dedupeStrings([...EXTRA_OPENAI_MODELS]);
    return dedupeStrings(models.length > 0 ? models : fallback);
  }, [assistantProviders, assistantProviderId]);

  useEffect(() => {
    if (assistantModelOptions.length === 0) {
      setAssistantModelId("");
      return;
    }
    setAssistantModelId((current) => {
      const selectedProvider = assistantProviders.find((provider) => provider.provider_id === assistantProviderId);
      const providerDefaultModel = String(selectedProvider?.default_model || "").trim();
      const catalogDefaultApplies =
        assistantCatalogDefaultProviderId &&
        assistantCatalogDefaultProviderId === assistantProviderId &&
        assistantCatalogDefaultModelId &&
        assistantModelOptions.includes(assistantCatalogDefaultModelId);
      if (catalogDefaultApplies) return assistantCatalogDefaultModelId;
      if (providerDefaultModel && assistantModelOptions.includes(providerDefaultModel)) {
        return providerDefaultModel;
      }
      const active = String(current || "").trim();
      if (active && assistantModelOptions.includes(active)) return active;
      return assistantModelOptions[0];
    });
  }, [
    assistantCatalogDefaultModelId,
    assistantCatalogDefaultProviderId,
    assistantModelOptions,
    assistantProviderId,
    assistantProviders,
  ]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const sessions = await listAssistantSessions();
        if (cancelled) return;
        if (sessions.length === 0) {
          const created = await createAssistantSession("Lunar Analyst Assistant");
          if (cancelled) return;
          setAssistantSessions([created]);
          setActiveAssistantSessionId(created.session_id);
          await refreshAssistantMessages(created.session_id);
          return;
        }
        setAssistantSessions(sessions);
        const chosen = sessions[0].session_id;
        setActiveAssistantSessionId(chosen);
        await refreshAssistantMessages(chosen);
      } catch (error) {
        console.warn("[lunar-analyst][assistant] session bootstrap failed", error);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [refreshAssistantMessages]);

  const assistantProviderOptions = useMemo(
    () =>
      assistantProviders.map((provider) => ({
        value: provider.provider_id,
        label: provider.provider_id,
      })),
    [assistantProviders],
  );

  const selectedAssistantProvider = useMemo(
    () => assistantProviders.find((provider) => provider.provider_id === assistantProviderId) || null,
    [assistantProviders, assistantProviderId],
  );

  const selectedAssistantModelMetadata = useMemo(
    () => selectedAssistantProvider?.model_metadata?.[assistantModelId] ?? null,
    [assistantModelId, selectedAssistantProvider],
  );

  const assistantThinkingOptions = useMemo(() => {
    const mode = selectedAssistantModelMetadata?.thinking_mode ?? "none";
    if (mode === "level") {
      return [
        { value: "", label: "Default Thinking" },
        { value: "false", label: "Thinking Off" },
        { value: "low", label: "Think Low" },
        { value: "medium", label: "Think Medium" },
        { value: "high", label: "Think High" },
      ];
    }
    if (mode === "boolean") {
      return [
        { value: "", label: "Default Thinking" },
        { value: "false", label: "Thinking Off" },
        { value: "true", label: "Thinking On" },
      ];
    }
    return [];
  }, [selectedAssistantModelMetadata]);

  const assistantThinkingEnabled = assistantThinkingOptions.length > 0;

  useEffect(() => {
    if (!assistantThinkingEnabled) {
      setAssistantThinkingValue("");
      return;
    }
    const allowedValues = new Set(assistantThinkingOptions.map((item) => item.value));
    setAssistantThinkingValue((current) => (allowedValues.has(current) ? current : ""));
  }, [assistantThinkingEnabled, assistantThinkingOptions]);

  const assistantAccessModeEnabled =
    selectedAssistantProvider?.execution_mode === "external_mcp_agent";

  const assistantAccessModeOptions = useMemo(
    () => [
      { value: "mcp_only" as AssistantAccessMode, label: "Safe (MCP only)" },
      { value: "scenario_root" as AssistantAccessMode, label: "Scenario Files" },
    ],
    [],
  );

  const assistantDisplayMessages = useMemo(() => {
    if (!assistantDraft || assistantDraft.text.trim().length === 0) {
      return assistantMessages;
    }
    const draftMessage: AssistantMessage = {
      message_id: `draft_${assistantDraft.turnId}`,
      session_id: activeAssistantSessionId || "draft",
      role: "assistant",
      content: assistantDraft.text,
      created_at_utc: assistantDraft.createdAt,
      turn_id: assistantDraft.turnId,
      metadata: { draft: true },
      outputs: [],
    };
    return [...assistantMessages, draftMessage];
  }, [assistantDraft, assistantMessages, activeAssistantSessionId]);

  const handleAssistantCreateSession = useCallback((title: string) => {
    void (async () => {
      try {
        const created = await createAssistantSession(title);
        setAssistantSessions((prev) => [created, ...prev]);
        setActiveAssistantSessionId(created.session_id);
        setAssistantMessages([]);
        setPendingAssistantConfirmation(null);
      } catch (error) {
        console.warn("[lunar-analyst][assistant] create session failed", error);
      }
    })();
  }, []);

  const handleAssistantSelectSession = useCallback((sessionId: string) => {
    setActiveAssistantSessionId(sessionId);
    setPendingAssistantConfirmation(null);
    void refreshAssistantMessages(sessionId);
  }, [refreshAssistantMessages]);

  const handleAssistantSelectProvider = useCallback((providerId: string) => {
    setAssistantProviderId(providerId);
    const provider = assistantProviders.find((item) => item.provider_id === providerId);
    const providerDefault = isAssistantAccessMode(provider?.access_mode)
      ? provider.access_mode
      : DEFAULT_ASSISTANT_ACCESS_MODE;
    setAssistantAccessMode(providerDefault);
  }, [assistantProviders]);

  const handleAssistantSelectAccessMode = useCallback((mode: AssistantAccessMode) => {
    if (!isAssistantAccessMode(mode)) return;
    setAssistantAccessMode(mode);
  }, []);

  const handleAssistantSelectThinking = useCallback((value: string) => {
    setAssistantThinkingValue(String(value || ""));
  }, []);

  const handleAssistantCompactSession = useCallback((sessionId: string) => {
    void (async () => {
      try {
        await compactAssistantSession(sessionId);
        await refreshAssistantMessages(sessionId);
      } catch (error) {
        console.warn("[lunar-analyst][assistant] compact failed", error);
      }
    })();
  }, [refreshAssistantMessages]);

  const normalizedThinking = useMemo(() => {
    if (!assistantThinkingEnabled) return null;
    if (assistantThinkingValue === "true") return true;
    if (assistantThinkingValue === "false") return false;
    if (
      assistantThinkingValue === "low" ||
      assistantThinkingValue === "medium" ||
      assistantThinkingValue === "high"
    ) {
      return assistantThinkingValue;
    }
    return null;
  }, [assistantThinkingEnabled, assistantThinkingValue]);

  const handleAssistantSubmitPrompt = useCallback((prompt: string) => {
    const sessionId = activeAssistantSessionId;
    if (!sessionId) return;
    setPendingAssistantConfirmation(null);
    void (async () => {
      try {
        const response = await createAssistantTurn(
          sessionId,
          prompt,
          activeScenarioIdRef.current,
          null,
          baseLayerVisible,
          assistantProviderId || null,
          assistantModelId || null,
          assistantAccessModeEnabled ? assistantAccessMode : null,
          normalizedThinking,
        );
        if (response.assistant_message) {
          setAssistantMessages((prev) => [...prev, response.assistant_message as AssistantMessage]);
        } else {
          await refreshAssistantMessages(sessionId);
        }
        await refreshAssistantMessages(sessionId);
        setPendingAssistantConfirmation(response.confirmation || null);
        await refreshAssistantSessions();
      } catch (error) {
        console.warn("[lunar-analyst][assistant] turn failed", error);
      }
    })();
  }, [
    activeAssistantSessionId,
    activeScenarioIdRef,
    assistantAccessMode,
    assistantAccessModeEnabled,
    assistantModelId,
    normalizedThinking,
    assistantProviderId,
    baseLayerVisible,
    refreshAssistantMessages,
    refreshAssistantSessions,
  ]);

  const handleAssistantResolveConfirmation = useCallback((decision: "allow_once" | "always_allow_action_type" | "deny_once") => {
    const sessionId = activeAssistantSessionId;
    const confirmationId = pendingAssistantConfirmation?.confirmation_id;
    if (!sessionId || !confirmationId) return;
    void (async () => {
      try {
        const response = await resolveAssistantConfirmation(sessionId, confirmationId, decision);
        if (response.assistant_message) {
          setAssistantMessages((prev) => [...prev, response.assistant_message as AssistantMessage]);
        } else {
          await refreshAssistantMessages(sessionId);
        }
        await refreshAssistantMessages(sessionId);
        setPendingAssistantConfirmation(null);
        await refreshAssistantSessions();
      } catch (error) {
        console.warn("[lunar-analyst][assistant] confirmation failed", error);
      }
    })();
  }, [activeAssistantSessionId, pendingAssistantConfirmation, refreshAssistantMessages, refreshAssistantSessions]);

  const handleAssistantStreamEvent = useCallback((event: AssistantWsEvent) => {
    const currentDraft = assistantDraftRef.current;
    const decision = reduceAssistantStreamEvent(
      event,
      currentDraft,
      activeAssistantSessionId,
    );

    if (decision.nextDraft !== currentDraft) {
      setAssistantDraft(decision.nextDraft);
      assistantDraftRef.current = decision.nextDraft;
    }

    if (decision.appendErrorMessage) {
      setAssistantMessages((prev) => [...prev, decision.appendErrorMessage as AssistantMessage]);
    }

    if (decision.scenarioId) {
      onScenarioChange(decision.scenarioId);
    }

    if (decision.scenarioExtent) {
      mapControllerRef.current?.fitExtent(decision.scenarioExtent);
    }

    if (decision.refreshMessages) {
      void refreshAssistantMessages(activeAssistantSessionId);
    }

    if (decision.refreshSessions) {
      void refreshAssistantSessions();
    }
  }, [
    activeAssistantSessionId,
    mapControllerRef,
    onScenarioChange,
    refreshAssistantMessages,
    refreshAssistantSessions,
  ]);

  return {
    assistantSessions,
    activeAssistantSessionId,
    assistantProviderOptions,
    assistantProviderId,
    assistantModelOptions,
    assistantModelId,
    assistantThinkingOptions,
    assistantThinkingValue,
    assistantThinkingEnabled,
    assistantAccessModeOptions,
    assistantAccessMode,
    assistantAccessModeEnabled,
    assistantDisplayMessages,
    pendingAssistantConfirmation,
    setAssistantModelId,
    handleAssistantSelectThinking,
    handleAssistantCreateSession,
    handleAssistantSelectSession,
    handleAssistantSelectProvider,
    handleAssistantSelectAccessMode,
    handleAssistantCompactSession,
    handleAssistantSubmitPrompt,
    handleAssistantResolveConfirmation,
    handleAssistantStreamEvent,
  };
}
