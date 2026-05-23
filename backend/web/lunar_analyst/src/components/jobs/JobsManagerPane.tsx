import React, { useEffect, useMemo, useRef, useState } from "react";
import { Button, InputGroup, Intent } from "@blueprintjs/core";
import FilteredList from "../common/FilteredList";
import type { ToolDefinition } from "../../services/toolService";
import { cancelRun, getRunLogs, getRunStatus, listTools, runTool } from "../../services/toolService";
import { clearWorkspaceMessages, getScenario, listWorkspaceMessages, type WorkspaceMessageEntry } from "../../services/scenarioService";
import { connectEventsSocket } from "../../services/wsClient";
import {
  applyJobEventToRun,
  buildJobEventMessage,
  buildParameterRows,
  buildSnapshotRows,
  buildJobTemplate,
  buildLaunchPayload,
  cloneRecord,
  createRunRecord,
  deriveDurationMs,
  formatDurationMs,
  formatTimestamp,
  getValueAtPath,
  isTerminalRunStatus,
  normalizePercent,
  normalizeRunStatus,
  orderJobDefinitions,
  setValueAtPath,
  statusLabel,
  summarizeJobResult,
  typeHintIsBoolean,
  typeHintIsNumeric,
  typeHintIsObject,
  upsertRecentRuns,
  validateDraftParams,
  type JobRunRecord,
  type JobRunStatus,
  type ParameterRow,
  type JobTemplateContext,
} from "../../utils/jobsManager";

type Props = {
  activeScenarioId: string | null;
  draftParamsByKey: Record<string, Record<string, unknown>>;
  onDraftParamsByKeyChange: React.Dispatch<React.SetStateAction<Record<string, Record<string, unknown>>>>;
  mode?: "tools" | "jobs" | "messages";
};

const MAX_RECENT_RUNS = 15;

type MessageStreamEntry = {
  key: string;
  type: "workspace" | "stdout" | "stderr" | "marker";
  timestamp: string;
  source: string;
  text: string;
  level?: WorkspaceMessageEntry["level"];
};

type TranscriptRunPhase = "open" | "draining" | "closed";

type TranscriptRunItem = {
  id: string;
  kind: "run";
  timestamp: string;
  runId: string;
  title: string;
  status: JobRunStatus;
  phase: TranscriptRunPhase;
  lines: MessageStreamEntry[];
};

type TranscriptWorkspaceItem = {
  id: string;
  kind: "workspace";
  timestamp: string;
  entry: WorkspaceMessageEntry;
};

type TranscriptItem = TranscriptRunItem | TranscriptWorkspaceItem;

type WsLogLineEvent = {
  runId: string;
  stream: "stdout" | "stderr";
  line: string;
};

function buildTemplateContextFromScenario(
  scenario: { directory?: string; primary_dem_path?: string } | null,
): JobTemplateContext {
  const scenarioRootDir = String(scenario?.directory || "").trim() || null;
  if (!scenarioRootDir) {
    return { scenarioRootDir: null, demPath: null };
  }
  const root = scenarioRootDir.replace(/\\/g, "/").replace(/\/+$/g, "");
  const primaryDemPath = String(scenario?.primary_dem_path || "dem.tif").replace(/^\/+/, "");
  return {
    scenarioRootDir,
    demPath: `${root}/${primaryDemPath}`,
  };
}

function draftKeyForScenario(scenarioId: string | null, definitionId: string): string {
  return `${scenarioId || ""}::${definitionId}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function defaultValueForTypeHint(typeHint: string): unknown {
  if (typeHintIsBoolean(typeHint)) return false;
  if (typeHintIsNumeric(typeHint)) return 0;
  if (typeHintIsObject(typeHint)) return {};
  return "";
}

function statusEventName(status: JobRunStatus): string {
  if (status === "running") return "job_started";
  if (status === "completed") return "job_completed";
  if (status === "failed") return "job_failed";
  if (status === "cancelled") return "job_cancelled";
  return "job_queued";
}

function toStringLines(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => String(item));
}

function runStatusClassName(status: JobRunStatus): string {
  if (status === "queued") return "jobs-status-queued";
  if (status === "running") return "jobs-status-running";
  if (status === "completed") return "jobs-status-completed";
  if (status === "failed") return "jobs-status-failed";
  if (status === "cancelled") return "jobs-status-cancelled";
  return "jobs-status-idle";
}

function isoNow(): string {
  return new Date().toISOString();
}

function buildTranscriptEntryFromWorkspace(entry: WorkspaceMessageEntry): MessageStreamEntry {
  return {
    key: `workspace-${entry.entry_id}-${entry.created_at_utc}`,
    type: "workspace",
    timestamp: entry.created_at_utc,
    source: entry.source,
    text: entry.text,
    level: entry.level,
  };
}

function buildTranscriptItemFromWorkspace(entry: WorkspaceMessageEntry): TranscriptWorkspaceItem {
  return {
    id: `workspace-${entry.entry_id}-${entry.created_at_utc}`,
    kind: "workspace",
    timestamp: entry.created_at_utc,
    entry,
  };
}

function buildMarkerEntry(text: string, source = "script"): MessageStreamEntry {
  return {
    key: `marker-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    type: "marker",
    timestamp: isoNow(),
    source,
    text,
  };
}

function buildWorkspaceEntryFromJobEvent(
  eventName: string,
  scenarioId: string,
  data: Record<string, unknown>,
): WorkspaceMessageEntry | null {
  const event = String(eventName || "").trim().toLowerCase();
  const kind = String(data.event_kind || "").trim().toLowerCase();
  if (event === "job_progress" && kind === "log_line") return null;
  const jobId = String(data.job_id || "").trim();
  const jobType = String(data.job_type || "").trim();
  const label = jobId || jobType || "job";
  const percent = Number(data.percent);
  const message = String(data.message || "").trim();
  let level: WorkspaceMessageEntry["level"] = "info";
  let text = "";
  if (event === "job_queued") {
    text = `${label}: queued`;
  } else if (event === "job_started") {
    text = `${label}: started`;
  } else if (event === "job_progress") {
    const suffix = Number.isFinite(percent) ? ` (${Math.round(percent)}%)` : "";
    text = `${label}: ${message || "progress update"}${suffix}`;
  } else if (event === "job_completed") {
    level = "success";
    text = `${label}: completed`;
  } else if (event === "job_failed") {
    level = "error";
    text = `${label}: failed${message ? ` - ${message}` : ""}`;
  } else if (event === "job_cancelled") {
    level = "warning";
    const reason = String(data.reason || "").trim();
    text = `${label}: cancelled${reason ? ` - ${reason}` : ""}`;
  } else {
    return null;
  }
  const timestamp = isoNow();
  return {
    entry_id: `ws-${event}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    scenario_id: scenarioId,
    created_at_utc: timestamp,
    level,
    source: "job",
    text,
  };
}

function parseWsLogLineEvent(eventName: string, data: Record<string, unknown>): WsLogLineEvent | null {
  if (String(eventName || "").trim().toLowerCase() !== "job_progress") return null;
  if (String(data.event_kind || "").trim().toLowerCase() !== "log_line") return null;
  const runId = String(data.job_id || "").trim();
  const streamRaw = String(data.log_stream || "").trim().toLowerCase();
  if (!runId || (streamRaw !== "stdout" && streamRaw !== "stderr")) return null;
  return {
    runId,
    stream: streamRaw,
    line: String(data.log_line || ""),
  };
}

function isNotebookLikeRun(run: JobRunRecord | null): boolean {
  if (!run) return false;
  if (String(run.definitionId || "").startsWith("notebook:")) return true;
  return String(run.title || "").trim().toLowerCase() === "run_notebook_definition";
}

function reconcileRunMetadata(
  run: JobRunRecord,
  data: Record<string, unknown>,
): JobRunRecord {
  const jobType = String(data.job_type || data.handler_name || "").trim();
  const notebookJobId = String(data.notebook_job_id || "").trim();
  const nextTitle = jobType || run.title;
  const nextDefinitionId = run.definitionId
    || (notebookJobId ? `notebook:${notebookJobId}` : null)
    || (jobType === "run_notebook_definition" ? "notebook:run_notebook_definition" : null);
  if (nextTitle === run.title && nextDefinitionId === run.definitionId) {
    return run;
  }
  return {
    ...run,
    title: nextTitle,
    definitionId: nextDefinitionId,
  };
}

function extractCombinedStreamTails(payload: Record<string, unknown> | null): {
  stdoutTail: string[];
  stderrTail: string[];
  stdoutTotal: number;
  stderrTotal: number;
  isFinal: boolean;
} {
  if (!payload) {
    return { stdoutTail: [], stderrTail: [], stdoutTotal: 0, stderrTotal: 0, isFinal: false };
  }
  const streamName = String(payload.stream || "").trim().toLowerCase();
  if (streamName !== "combined") {
    return { stdoutTail: [], stderrTail: [], stdoutTotal: 0, stderrTotal: 0, isFinal: false };
  }
  const streams = isRecord(payload.streams) ? payload.streams : {};
  const stdout = isRecord(streams.stdout) ? streams.stdout : {};
  const stderr = isRecord(streams.stderr) ? streams.stderr : {};
  return {
    stdoutTail: toStringLines(stdout.tail),
    stderrTail: toStringLines(stderr.tail),
    stdoutTotal: Number(stdout.total_lines || 0) || 0,
    stderrTotal: Number(stderr.total_lines || 0) || 0,
    isFinal: Boolean(payload.is_final),
  };
}

function formatCombinedLogTail(payload: Record<string, unknown> | null): string {
  const { stdoutTail, stderrTail } = extractCombinedStreamTails(payload);
  const sections: string[] = [];
  if (stdoutTail.length > 0) {
    sections.push("[stdout]");
    sections.push(...stdoutTail);
  }
  if (stderrTail.length > 0) {
    sections.push("[stderr]");
    sections.push(...stderrTail);
  }
  return sections.join("\n");
}

function flattenTranscriptItems(items: TranscriptItem[]): MessageStreamEntry[] {
  const lines: MessageStreamEntry[] = [];
  for (const item of items) {
    if (item.kind === "workspace") {
      lines.push(buildTranscriptEntryFromWorkspace(item.entry));
      continue;
    }
    lines.push(...item.lines);
  }
  return lines;
}

function delayMs(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, Math.max(0, ms));
  });
}

export default function JobsManagerPane(props: Props): JSX.Element {
  const {
    activeScenarioId,
    draftParamsByKey,
    onDraftParamsByKeyChange,
    mode = "tools",
  } = props;
  const [definitions, setDefinitions] = useState<ToolDefinition[]>([]);
  const [selectedDefinitionId, setSelectedDefinitionId] = useState("");
  const [filterText, setFilterText] = useState("");
  const [showParamsModal, setShowParamsModal] = useState(false);
  const [modalDraftParams, setModalDraftParams] = useState<Record<string, unknown> | null>(null);
  const [rowJsonDrafts, setRowJsonDrafts] = useState<Record<string, string>>({});
  const [rowJsonErrors, setRowJsonErrors] = useState<Record<string, string>>({});
  const [showAdvancedJson, setShowAdvancedJson] = useState(false);
  const [advancedJsonText, setAdvancedJsonText] = useState("{}");
  const [advancedJsonError, setAdvancedJsonError] = useState("");
  const [paramValidationErrors, setParamValidationErrors] = useState<string[]>([]);
  const [paneNotice, setPaneNotice] = useState("");
  const [currentRunId, setCurrentRunId] = useState<string | null>(null);
  const [recentRuns, setRecentRuns] = useState<JobRunRecord[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [runLogPayloads, setRunLogPayloads] = useState<Record<string, Record<string, unknown>>>({});
  const [transcriptItems, setTranscriptItems] = useState<TranscriptItem[]>([]);
  const [toolsListHeight, setToolsListHeight] = useState(180);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [templateContext, setTemplateContext] = useState<JobTemplateContext>({
    scenarioRootDir: null,
    demPath: null,
  });

  const currentRunIdRef = useRef<string | null>(null);
  const recentRunsRef = useRef<JobRunRecord[]>([]);
  const seenWorkspaceEntryIdsRef = useRef<Set<string>>(new Set());
  const consoleLineCursorRef = useRef<Record<string, { stdout: number; stderr: number }>>({});
  const inFlightDrainRunIdsRef = useRef<Set<string>>(new Set());
  const isMountedRef = useRef(true);
  const messagesBodyRef = useRef<HTMLElement | null>(null);
  const shouldStickMessagesRef = useRef(true);
  const toolsListDragRef = useRef<{ startY: number; startHeight: number } | null>(null);
  useEffect(() => {
    currentRunIdRef.current = currentRunId;
  }, [currentRunId]);
  useEffect(() => {
    recentRunsRef.current = recentRuns;
  }, [recentRuns]);
  useEffect(() => () => {
    isMountedRef.current = false;
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
        const [loaded, scenario] = await Promise.all([
        listTools(activeScenarioId || undefined),
        activeScenarioId ? getScenario(activeScenarioId).catch(() => null) : Promise.resolve(null),
      ]);
      if (cancelled) return;
      const ordered = orderJobDefinitions(loaded);
      setDefinitions(ordered);
      setTemplateContext(buildTemplateContextFromScenario(scenario));
      setSelectedDefinitionId((current) => {
        if (current && ordered.some((definition) => definition.job_definition_id === current)) return current;
        return ordered[0]?.job_definition_id || "";
      });
    })();
    return () => {
      cancelled = true;
    };
  }, [activeScenarioId]);

  useEffect(() => {
    setCurrentRunId(null);
    setSelectedRunId(null);
    setRecentRuns([]);
    setRunLogPayloads({});
    setPaneNotice("");
    setTranscriptItems([]);
    seenWorkspaceEntryIdsRef.current = new Set();
    consoleLineCursorRef.current = {};
    inFlightDrainRunIdsRef.current = new Set();
  }, [activeScenarioId]);

  const appendWorkspaceEntries = React.useCallback((entries: WorkspaceMessageEntry[]) => {
    if (entries.length === 0) return;
    setTranscriptItems((prev) => [
      ...prev,
      ...entries.map((entry) => buildTranscriptItemFromWorkspace(entry)),
    ]);
  }, []);

  useEffect(() => {
    if (!activeScenarioId || mode !== "messages") return;
    let cancelled = false;
    const loadMessages = async () => {
      try {
        const entries = await listWorkspaceMessages(activeScenarioId);
        if (cancelled) return;
        const unseen: WorkspaceMessageEntry[] = [];
        entries.forEach((entry) => {
          const key = `${entry.entry_id}-${entry.created_at_utc}`;
          if (seenWorkspaceEntryIdsRef.current.has(key)) return;
          seenWorkspaceEntryIdsRef.current.add(key);
          unseen.push(entry);
        });
        if (unseen.length > 0) {
          appendWorkspaceEntries(unseen);
        }
      } catch {
        // best effort
      }
    };
    void loadMessages();
    return () => {
      cancelled = true;
    };
  }, [activeScenarioId, appendWorkspaceEntries, mode]);

  const selectedDefinition = useMemo(
    () => definitions.find((definition) => definition.job_definition_id === selectedDefinitionId) || null,
    [definitions, selectedDefinitionId],
  );

  const selectedDraftKey = useMemo(
    () => (selectedDefinition ? draftKeyForScenario(activeScenarioId, selectedDefinition.job_definition_id) : ""),
    [activeScenarioId, selectedDefinition],
  );

  useEffect(() => {
    if (!selectedDefinition || !selectedDraftKey) return;
    onDraftParamsByKeyChange((prev) => {
      if (prev[selectedDraftKey]) return prev;
      return {
        ...prev,
        [selectedDraftKey]: buildJobTemplate(selectedDefinition, activeScenarioId, templateContext),
      };
    });
  }, [selectedDefinition, selectedDraftKey, activeScenarioId, onDraftParamsByKeyChange, templateContext]);

  const selectedDraftParams = useMemo(() => {
    if (!selectedDefinition || !selectedDraftKey) return {};
    const existing = draftParamsByKey[selectedDraftKey];
    if (existing) return existing;
    return buildJobTemplate(selectedDefinition, activeScenarioId, templateContext);
  }, [selectedDefinition, selectedDraftKey, draftParamsByKey, activeScenarioId, templateContext]);

  const startConsoleStream = React.useCallback((run: JobRunRecord) => {
    setTranscriptItems((prev) => {
      if (prev.some((item) => item.kind === "run" && item.runId === run.runId)) {
        return prev.map((item) => (
          item.kind === "run" && item.runId === run.runId
            ? {
              ...item,
              status: run.status,
              title: run.title || item.title,
              phase: item.phase === "closed" ? "closed" : "open",
            }
            : item
        ));
      }
      consoleLineCursorRef.current[run.runId] = { stdout: 0, stderr: 0 };
      const startedAt = isoNow();
      return [
        ...prev,
        {
          id: `run-${run.runId}`,
          kind: "run",
          timestamp: startedAt,
          runId: run.runId,
          title: run.title || run.runId,
          status: run.status,
          phase: "open",
          lines: [
            {
              key: `marker-start-${run.runId}`,
              type: "marker",
              timestamp: startedAt,
              source: "script",
              text: `[script start] ${run.title || run.runId}`,
            },
          ],
        },
      ];
    });
  }, []);

  const appendRunLogDelta = React.useCallback((runId: string, payload: Record<string, unknown>) => {
    const { stdoutTail, stderrTail, stdoutTotal, stderrTotal } = extractCombinedStreamTails(payload);
    const cursor = consoleLineCursorRef.current[runId] || { stdout: 0, stderr: 0 };
    const nextEntries: MessageStreamEntry[] = [];
    const stdoutDelta = Math.max(0, stdoutTotal - cursor.stdout);
    const stderrDelta = Math.max(0, stderrTotal - cursor.stderr);
    if (stdoutDelta > 0) {
      stdoutTail.slice(-stdoutDelta).forEach((line, index) => {
        nextEntries.push({
          key: `stdout-${runId}-${cursor.stdout + index + 1}`,
          type: "stdout",
          timestamp: isoNow(),
          source: "stdout",
          text: line,
        });
      });
    }
    if (stderrDelta > 0) {
      stderrTail.slice(-stderrDelta).forEach((line, index) => {
        nextEntries.push({
          key: `stderr-${runId}-${cursor.stderr + index + 1}`,
          type: "stderr",
          timestamp: isoNow(),
          source: "stderr",
          text: line,
        });
      });
    }
    if (nextEntries.length > 0) {
      setTranscriptItems((prev) => prev.map((item) => (
        item.kind === "run" && item.runId === runId
          ? { ...item, lines: [...item.lines, ...nextEntries] }
          : item
      )));
    }
    consoleLineCursorRef.current[runId] = { stdout: stdoutTotal, stderr: stderrTotal };
  }, []);

  const appendRunLogLine = React.useCallback((runId: string, stream: "stdout" | "stderr", line: string) => {
    const cursor = consoleLineCursorRef.current[runId] || { stdout: 0, stderr: 0 };
    const nextCursor = {
      stdout: cursor.stdout + (stream === "stdout" ? 1 : 0),
      stderr: cursor.stderr + (stream === "stderr" ? 1 : 0),
    };
    const sequence = stream === "stdout" ? nextCursor.stdout : nextCursor.stderr;
    const entry: MessageStreamEntry = {
      key: `${stream}-${runId}-${sequence}`,
      type: stream,
      timestamp: isoNow(),
      source: stream,
      text: line,
    };
    setTranscriptItems((prev) => prev.map((item) => (
      item.kind === "run" && item.runId === runId
        ? { ...item, lines: [...item.lines, entry] }
        : item
    )));
    consoleLineCursorRef.current[runId] = nextCursor;
  }, []);

  const closeConsoleStream = React.useCallback((run: JobRunRecord) => {
    setTranscriptItems((prev) => prev.map((item) => {
      if (item.kind !== "run" || item.runId !== run.runId) return item;
      if (item.phase === "closed") {
        return { ...item, status: run.status, title: run.title || item.title };
      }
      const endedAt = isoNow();
      return {
        ...item,
        status: run.status,
        title: run.title || item.title,
        phase: "closed",
        lines: [
          ...item.lines,
          {
            key: `marker-end-${run.runId}`,
            type: "marker",
            timestamp: endedAt,
            source: "script",
            text: `[script end] ${run.title || item.title} ${statusLabel(run.status).toLowerCase()}`,
          },
        ],
      };
    }));
  }, []);

  const beginRunDrain = React.useCallback((run: JobRunRecord) => {
    if (inFlightDrainRunIdsRef.current.has(run.runId)) return;
    inFlightDrainRunIdsRef.current.add(run.runId);
    setTranscriptItems((prev) => prev.map((item) => (
      item.kind === "run" && item.runId === run.runId
        ? { ...item, status: run.status, title: run.title || item.title, phase: item.phase === "closed" ? "closed" : "draining" }
        : item
    )));
    void (async () => {
      try {
        let drainedFinal = false;
        let attempts = 0;
        while (!drainedFinal && isMountedRef.current) {
          const payload = await getRunLogs(run.runId, { stream: "combined", headLines: 0, tailLines: 2000 });
          const normalized = isRecord(payload) ? payload : {};
          setRunLogPayloads((prev) => ({ ...prev, [run.runId]: normalized }));
          appendRunLogDelta(run.runId, normalized);
          drainedFinal = Boolean(normalized.is_final);
          if (!drainedFinal) {
            attempts += 1;
            await delayMs(attempts < 20 ? 120 : 250);
          }
        }
      } catch {
        // best effort
      } finally {
        if (isMountedRef.current) {
          closeConsoleStream(run);
        }
        inFlightDrainRunIdsRef.current.delete(run.runId);
      }
    })();
  }, [appendRunLogDelta, closeConsoleStream]);

  useEffect(() => {
    if (!activeScenarioId) return;
    const endpoint = `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}/api/v1/events?cursor=latest`;
    const ws = connectEventsSocket(endpoint, {
      onError: () => {
        console.warn("[lunar-analyst][jobs] ws error");
      },
      onEvent: (payload) => {
        if (!payload.scenario_id || payload.scenario_id !== activeScenarioId) return;
        if (!String(payload.event || "").startsWith("job_")) return;
        const payloadData = isRecord(payload.data) ? payload.data : {};
        const liveWorkspaceEntry = buildWorkspaceEntryFromJobEvent(payload.event, activeScenarioId, payloadData);
        if (liveWorkspaceEntry) {
          const key = `${liveWorkspaceEntry.entry_id}-${liveWorkspaceEntry.created_at_utc}`;
          if (!seenWorkspaceEntryIdsRef.current.has(key)) {
            seenWorkspaceEntryIdsRef.current.add(key);
            appendWorkspaceEntries([liveWorkspaceEntry]);
          }
        }
        const logLineEvent = parseWsLogLineEvent(payload.event, payloadData);
        const eventJobId = String(payloadData.job_id || "").trim();
        if (logLineEvent) {
          const now = Date.now();
          setNowMs(now);
          setRecentRuns((prev) => {
            const existing = prev.find((item) => item.runId === logLineEvent.runId) || null;
            const baseRun = existing || createRunRecord({
              runId: logLineEvent.runId,
              scenarioId: activeScenarioId,
              definitionId: null,
              title: String(payloadData.job_type || payloadData.handler_name || logLineEvent.runId),
              status: "running",
              paramsSnapshot: {},
              nowMs: now,
              latestMessage: "",
            });
            const reconciled = reconcileRunMetadata(baseRun, payloadData);
            const updated = { ...reconciled, status: "running", updatedAtMs: now };
            if (isNotebookLikeRun(updated)) {
              startConsoleStream(updated);
            }
            return upsertRecentRuns(prev, updated, MAX_RECENT_RUNS);
          });
          if (!currentRunIdRef.current) {
            setCurrentRunId(logLineEvent.runId);
          }
          appendRunLogLine(logLineEvent.runId, logLineEvent.stream, logLineEvent.line);
          return;
        }
        if (!eventJobId) return;
        const now = Date.now();
        setNowMs(now);
        setRecentRuns((prev) => {
          const existing = prev.find((item) => item.runId === eventJobId) || null;
          const baseRun = existing || createRunRecord({
            runId: eventJobId,
            scenarioId: activeScenarioId,
            definitionId: null,
            title: String(payloadData.job_type || payloadData.handler_name || eventJobId),
            status: normalizeRunStatus(payloadData.status, "running"),
            paramsSnapshot: {},
            nowMs: now,
            latestMessage: "",
          });
          const reconciled = reconcileRunMetadata(baseRun, payloadData);
          const updated = applyJobEventToRun(reconciled, payload.event, payloadData, now);
          if (isNotebookLikeRun(updated) && (updated.status === "queued" || updated.status === "running")) {
            startConsoleStream(updated);
          } else if (isNotebookLikeRun(updated) && isTerminalRunStatus(updated.status)) {
            startConsoleStream(updated);
            beginRunDrain(updated);
          }
          return upsertRecentRuns(prev, updated, MAX_RECENT_RUNS);
        });
        if (!currentRunIdRef.current) {
          setCurrentRunId(eventJobId);
        }
        if (payload.event === "job_failed") {
          setCurrentRunId(eventJobId);
          setSelectedRunId(eventJobId);
        }
      },
    });
    return () => ws.close();
  }, [activeScenarioId, appendRunLogLine, appendWorkspaceEntries, beginRunDrain, startConsoleStream]);

  useEffect(() => {
    if (!activeScenarioId) return;
    let cancelled = false;
    const pollStatus = async () => {
      const activeRunIds = recentRunsRef.current
        .filter((run) => run.status === "queued" || run.status === "running")
        .map((run) => run.runId);
      if (activeRunIds.length === 0) return;
      try {
        const results = await Promise.all(
          activeRunIds.map(async (runId) => ({ runId, payload: await getRunStatus(runId) })),
        );
        if (cancelled) return;
        const now = Date.now();
        setNowMs(now);
        setRecentRuns((prev) => {
          let next = prev;
          for (const item of results) {
            const payload = isRecord(item.payload) ? item.payload : {};
            const nextStatus = normalizeRunStatus(payload.status, "queued");
            const eventName = statusEventName(nextStatus);
            const existing = next.find((run) => run.runId === item.runId) || null;
            const baseRun = existing || createRunRecord({
              runId: item.runId,
              scenarioId: activeScenarioId,
              definitionId: null,
              title: String(payload.job_type || item.runId),
              status: nextStatus,
              paramsSnapshot: {},
              nowMs: now,
              latestMessage: "",
            });
            const reconciled = reconcileRunMetadata(baseRun, payload);
            const updated = applyJobEventToRun(reconciled, eventName, { ...payload, job_id: item.runId }, now);
            if (isNotebookLikeRun(updated) && (updated.status === "queued" || updated.status === "running")) {
              startConsoleStream(updated);
            } else if (isNotebookLikeRun(updated) && isTerminalRunStatus(updated.status)) {
              startConsoleStream(updated);
              beginRunDrain(updated);
            }
            next = upsertRecentRuns(next, updated, MAX_RECENT_RUNS);
          }
          return next;
        });
      } catch {
        // Best-effort fallback when websocket job status events are missed.
      }
    };
    void pollStatus();
    const timer = window.setInterval(() => {
      void pollStatus();
    }, 1500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [activeScenarioId, beginRunDrain, startConsoleStream]);

  const currentRun = useMemo(() => {
    if (currentRunId) {
      const byId = recentRuns.find((run) => run.runId === currentRunId);
      if (byId) return byId;
    }
    return recentRuns[0] || null;
  }, [recentRuns, currentRunId]);

  const selectedRun = useMemo(() => {
    if (selectedRunId) {
      const byId = recentRuns.find((run) => run.runId === selectedRunId);
      if (byId) return byId;
    }
    return currentRun;
  }, [selectedRunId, recentRuns, currentRun]);

  const activeTranscriptRun = useMemo(
    () => transcriptItems.find((item) => item.kind === "run" && item.phase !== "closed") as TranscriptRunItem | undefined,
    [transcriptItems],
  );

  const visibleTranscriptItems = useMemo(() => {
    const firstUnfinishedIndex = transcriptItems.findIndex(
      (item) => item.kind === "run" && item.phase !== "closed",
    );
    if (firstUnfinishedIndex < 0) return transcriptItems;
    return transcriptItems.slice(0, firstUnfinishedIndex + 1);
  }, [transcriptItems]);

  const visibleTranscriptLines = useMemo(
    () => flattenTranscriptItems(visibleTranscriptItems),
    [visibleTranscriptItems],
  );

  const queuedTranscriptCount = Math.max(0, transcriptItems.length - visibleTranscriptItems.length);

  useEffect(() => {
    if (mode !== "messages") return;
    const body = messagesBodyRef.current;
    if (!body || !shouldStickMessagesRef.current) return;
    body.scrollTop = body.scrollHeight;
  }, [mode, visibleTranscriptLines.length]);

  useEffect(() => {
    if (mode === "messages") return;
    if (!selectedRun) return;
    let cancelled = false;
    const runId = selectedRun.runId;
    const pollLogs = async () => {
      try {
        const payload = await getRunLogs(runId, { stream: "combined", headLines: 0, tailLines: 120 });
        if (cancelled) return;
        const normalized = isRecord(payload) ? payload : {};
        setRunLogPayloads((prev) => ({ ...prev, [runId]: normalized }));
      } catch {
        // Best-effort log polling.
      }
    };
    void pollLogs();
    if (isTerminalRunStatus(selectedRun.status)) {
      return () => {
        cancelled = true;
      };
    }
    const timer = window.setInterval(() => {
      void pollLogs();
    }, 1500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [mode, selectedRun]);

  useEffect(() => {
    if (!currentRun || isTerminalRunStatus(currentRun.status)) return;
    const timerId = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(timerId);
  }, [currentRun]);

  useEffect(() => {
    const handleMouseMove = (event: MouseEvent): void => {
      const drag = toolsListDragRef.current;
      if (!drag) return;
      const nextHeight = Math.max(96, Math.min(320, drag.startHeight + (event.clientY - drag.startY)));
      setToolsListHeight(nextHeight);
    };
    const handleMouseUp = (): void => {
      toolsListDragRef.current = null;
      document.body.classList.remove("jobs-tools-resizing");
    };
    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("mouseup", handleMouseUp);
    return () => {
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
    };
  }, []);

  const items = useMemo(
    () => definitions.map((definition) => ({
      value: definition.job_definition_id,
      label: `${definition.title} [${definition.job_type === "notebook" ? "Notebook" : "Tool"}]`,
      searchText: `${definition.title} ${definition.job_type} ${definition.job_definition_id}`.toLowerCase(),
    })),
    [definitions],
  );

  const modalRows = useMemo(
    () => buildParameterRows(selectedDefinition, modalDraftParams || {}),
    [selectedDefinition, modalDraftParams],
  );

  const selectedRunSnapshotRows = useMemo(
    () => (selectedRun ? buildSnapshotRows(selectedRun.paramsSnapshot) : []),
    [selectedRun],
  );

  const isCurrentRunCancelable = Boolean(
    currentRun && (currentRun.status === "queued" || currentRun.status === "running"),
  );

  const openParamsModal = () => {
    if (!selectedDefinition || !selectedDraftKey) return;
    const initialDraft = cloneRecord(selectedDraftParams);
    setModalDraftParams(initialDraft);
    setRowJsonDrafts({});
    setRowJsonErrors({});
    setParamValidationErrors([]);
    setShowAdvancedJson(false);
    setAdvancedJsonText(JSON.stringify(initialDraft, null, 2));
    setAdvancedJsonError("");
    setShowParamsModal(true);
  };

  const closeParamsModal = () => {
    setShowParamsModal(false);
    setModalDraftParams(null);
    setRowJsonDrafts({});
    setRowJsonErrors({});
    setParamValidationErrors([]);
    setShowAdvancedJson(false);
    setAdvancedJsonText("{}");
    setAdvancedJsonError("");
  };

  useEffect(() => {
    if (!showParamsModal) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeParamsModal();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [showParamsModal]);

  const setModalValue = (row: ParameterRow, value: unknown) => {
    setModalDraftParams((prev) => (prev ? setValueAtPath(prev, row.path, value) : prev));
  };

  const handleObjectRowChange = (row: ParameterRow, text: string) => {
    setRowJsonDrafts((prev) => ({ ...prev, [row.key]: text }));
    if (!text.trim()) {
      setModalValue(row, {});
      setRowJsonErrors((prev) => {
        const next = { ...prev };
        delete next[row.key];
        return next;
      });
      return;
    }
    try {
      const parsed = JSON.parse(text) as unknown;
      if (!isRecord(parsed) && !Array.isArray(parsed)) {
        throw new Error("Object JSON is required.");
      }
      setModalValue(row, parsed);
      setRowJsonErrors((prev) => {
        const next = { ...prev };
        delete next[row.key];
        return next;
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : "Invalid JSON";
      setRowJsonErrors((prev) => ({ ...prev, [row.key]: message }));
    }
  };

  const handleAdvancedJsonChange = (text: string) => {
    setAdvancedJsonText(text);
    if (!text.trim()) {
      setAdvancedJsonError("JSON payload is required.");
      return;
    }
    try {
      const parsed = JSON.parse(text) as unknown;
      if (!isRecord(parsed)) {
        setAdvancedJsonError("Root payload must be a JSON object.");
        return;
      }
      setModalDraftParams(parsed);
      setRowJsonErrors({});
      setRowJsonDrafts({});
      setAdvancedJsonError("");
      setParamValidationErrors([]);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Invalid JSON";
      setAdvancedJsonError(message);
    }
  };

  const handleResetParams = () => {
    if (!selectedDefinition) return;
    const reset = buildJobTemplate(selectedDefinition, activeScenarioId, templateContext);
    setModalDraftParams(reset);
    setRowJsonDrafts({});
    setRowJsonErrors({});
    setParamValidationErrors([]);
    setAdvancedJsonText(JSON.stringify(reset, null, 2));
    setAdvancedJsonError("");
  };

  const handleApplyParams = () => {
    if (!selectedDefinition || !selectedDraftKey || !modalDraftParams) return;
    const validationErrors = validateDraftParams(selectedDefinition, modalDraftParams);
    const jsonRowErrors = Object.values(rowJsonErrors);
    const merged = [...jsonRowErrors, ...validationErrors];
    if (advancedJsonError) merged.unshift(advancedJsonError);
    if (merged.length > 0) {
      setParamValidationErrors(Array.from(new Set(merged)));
      return;
    }
    onDraftParamsByKeyChange((prev) => ({
      ...prev,
      [selectedDraftKey]: cloneRecord(modalDraftParams),
    }));
    closeParamsModal();
  };

  const renderParameterValueEditor = (row: ParameterRow): JSX.Element => {
    const value = getValueAtPath(modalDraftParams || {}, row.path);
    const isNull = value === null;
    if (typeHintIsBoolean(row.typeHint)) {
      return (
        <div className={`jobs-param-editor ${row.readOnly ? "readonly" : ""}`}>
          <label className="jobs-boolean-editor">
            <input
              type="checkbox"
              checked={Boolean(value)}
              disabled={row.readOnly || isNull}
              onChange={(event) => setModalValue(row, event.target.checked)}
            />
            <span>{Boolean(value) ? "true" : "false"}</span>
          </label>
          {row.nullable ? (
            <label className="jobs-null-toggle">
              <input
                type="checkbox"
                checked={isNull}
                disabled={row.readOnly}
                onChange={(event) => {
                  if (event.target.checked) {
                    setModalValue(row, null);
                  } else {
                    setModalValue(row, false);
                  }
                }}
              />
              null
            </label>
          ) : null}
        </div>
      );
    }

    if (typeHintIsNumeric(row.typeHint)) {
      const displayValue = typeof value === "number" && Number.isFinite(value) ? String(value) : "";
      return (
        <div className={`jobs-param-editor ${row.readOnly ? "readonly" : ""}`}>
          <input
            type="number"
            value={displayValue}
            disabled={row.readOnly || isNull}
            onChange={(event) => {
              const raw = event.target.value.trim();
              if (!raw) {
                setModalValue(row, row.nullable ? null : 0);
                return;
              }
              const parsed = Number(raw);
              if (Number.isFinite(parsed)) {
                setModalValue(row, parsed);
              }
            }}
          />
          {row.nullable ? (
            <label className="jobs-null-toggle">
              <input
                type="checkbox"
                checked={isNull}
                disabled={row.readOnly}
                onChange={(event) => {
                  if (event.target.checked) {
                    setModalValue(row, null);
                  } else {
                    setModalValue(row, 0);
                  }
                }}
              />
              null
            </label>
          ) : null}
        </div>
      );
    }

    if (typeHintIsObject(row.typeHint) || isRecord(value) || Array.isArray(value)) {
      const jsonDraft = rowJsonDrafts[row.key] ?? JSON.stringify(value ?? defaultValueForTypeHint(row.typeHint), null, 2);
      return (
        <div className={`jobs-param-editor ${row.readOnly ? "readonly" : ""}`}>
          <textarea
            className="jobs-param-json-editor"
            rows={3}
            value={jsonDraft}
            disabled={row.readOnly}
            onChange={(event) => handleObjectRowChange(row, event.target.value)}
          />
          {rowJsonErrors[row.key] ? (
            <div className="jobs-params-error">{rowJsonErrors[row.key]}</div>
          ) : null}
        </div>
      );
    }

    return (
      <div className={`jobs-param-editor ${row.readOnly ? "readonly" : ""}`}>
        <input
          type="text"
          value={typeof value === "string" ? value : (value ?? "").toString()}
          disabled={row.readOnly || isNull}
          onChange={(event) => setModalValue(row, event.target.value)}
        />
        {row.nullable ? (
          <label className="jobs-null-toggle">
            <input
              type="checkbox"
              checked={isNull}
              disabled={row.readOnly}
              onChange={(event) => {
                if (event.target.checked) {
                  setModalValue(row, null);
                } else {
                  setModalValue(row, defaultValueForTypeHint(row.typeHint));
                }
              }}
            />
            null
          </label>
        ) : null}
      </div>
    );
  };

  const handleLaunch = () => {
    if (!selectedDefinition) {
      setPaneNotice("Run failed: no tool is selected.");
      return;
    }
    const validation = validateDraftParams(selectedDefinition, selectedDraftParams);
    if (validation.length > 0) {
      setPaneNotice(validation[0]);
      openParamsModal();
      setParamValidationErrors(validation);
      return;
    }

    void (async () => {
      try {
        const routePathRaw = String(selectedDefinition.route_path || "").trim();
        const routePath =
          routePathRaw || (selectedDefinition.job_type === "notebook" ? "/api/v1/jobs/run-notebook-definition" : "");
        if (!routePath) {
          throw new Error(`Tool route path is missing for ${selectedDefinition.job_definition_id}.`);
        }
        setPaneNotice(`Launching ${selectedDefinition.title}...`);
        const payload = buildLaunchPayload(selectedDefinition, selectedDraftParams);
        const result = await runTool(routePath, payload);
        const jobId = String(result.job_id || "").trim();
        if (!jobId) throw new Error("Run response missing job_id.");

        const now = Date.now();
        const launchStatus = normalizeRunStatus(result.status, "queued");
        const seedEvent = launchStatus === "running" ? "job_started" : "job_queued";
        const seedData = isRecord(result) ? result : {};

        const initialRun = createRunRecord({
          runId: jobId,
          scenarioId: activeScenarioId,
          definitionId: selectedDefinition.job_definition_id,
          title: selectedDefinition.title,
          status: launchStatus,
          paramsSnapshot: payload,
          nowMs: now,
          percent: normalizePercent(result.percent),
          latestMessage: buildJobEventMessage(seedEvent, seedData),
          resultSummary: summarizeJobResult(result.result),
        });

        const seededRun = applyJobEventToRun(initialRun, seedEvent, seedData, now);
        if (isNotebookLikeRun(seededRun) && (seededRun.status === "queued" || seededRun.status === "running")) {
          startConsoleStream(seededRun);
        } else if (isNotebookLikeRun(seededRun) && isTerminalRunStatus(seededRun.status)) {
          startConsoleStream(seededRun);
          beginRunDrain(seededRun);
        }
        setRecentRuns((prev) => upsertRecentRuns(prev, seededRun, MAX_RECENT_RUNS));
        setCurrentRunId(jobId);
        setSelectedRunId(jobId);
        setPaneNotice("");
        setNowMs(now);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        console.error("[lunar-analyst][jobs] launch failed", {
          tool: selectedDefinition?.job_definition_id,
          routePath: selectedDefinition?.route_path,
          message,
        });
        setPaneNotice(`Run failed: ${message}`);
      }
    })();
  };

  const handleCancel = () => {
    if (!currentRun || !isCurrentRunCancelable) return;
    void (async () => {
      try {
        const result = await cancelRun(currentRun.runId);
        const now = Date.now();
        const payloadData = isRecord(result) ? result : {};
        const updateData = {
          ...payloadData,
          job_id: String(payloadData.job_id || currentRun.runId),
        };
        setRecentRuns((prev) => {
          const existing = prev.find((item) => item.runId === currentRun.runId) || currentRun;
          const updated = applyJobEventToRun(existing, "job_cancelled", updateData, now);
          if (isNotebookLikeRun(updated)) {
            startConsoleStream(updated);
            beginRunDrain(updated);
          }
          return upsertRecentRuns(prev, updated, MAX_RECENT_RUNS);
        });
        setPaneNotice("");
        setNowMs(now);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setPaneNotice(`Cancel failed: ${message}`);
      }
    })();
  };

  const handleClearHistory = () => {
    if (showMessagesPane && activeScenarioId) {
      void clearWorkspaceMessages(activeScenarioId);
      setTranscriptItems([]);
      seenWorkspaceEntryIdsRef.current = new Set();
      consoleLineCursorRef.current = {};
      inFlightDrainRunIdsRef.current = new Set();
      return;
    }
    setRecentRuns([]);
    setCurrentRunId(null);
    setSelectedRunId(null);
    setPaneNotice("");
  };

  const currentRunStatus: JobRunStatus = currentRun?.status || "idle";
  const currentRunPercent = currentRun?.percent ?? null;
  const currentDurationMs = currentRun
    ? deriveDurationMs(currentRun.startedAtMs, currentRun.finishedAtMs, nowMs)
    : null;
  const currentRunMessage = currentRun?.latestMessage || "No active run";

  const runDetailsSummary = selectedRun
    ? {
      duration: formatDurationMs(deriveDurationMs(selectedRun.startedAtMs, selectedRun.finishedAtMs, nowMs)),
      requestedAt: formatTimestamp(selectedRun.requestedAtMs),
      startedAt: formatTimestamp(selectedRun.startedAtMs),
      finishedAt: formatTimestamp(selectedRun.finishedAtMs),
    }
    : null;
  const selectedRunLogs = selectedRun ? formatCombinedLogTail(runLogPayloads[selectedRun.runId] || null) : "";
  const showToolLauncher = mode === "tools";
  const showMessagesPane = mode === "messages";
  const messagesPaneStatus = activeTranscriptRun?.status || currentRunStatus;
  const messagesPanePercent = activeTranscriptRun?.status ? (recentRuns.find((run) => run.runId === activeTranscriptRun.runId)?.percent ?? currentRunPercent) : currentRunPercent;
  const messageProgressWidth = messagesPanePercent ?? (messagesPaneStatus === "running" ? 35 : 0);

  return (
    <div className={`layer-panel-body jobs-pane jobs-pane-${mode}`}>
      {showToolLauncher ? (
        <div className="jobs-tools-layout">
          <div className="jobs-tools-list-pane" style={{ height: `${toolsListHeight}px` }}>
            <label className="pattern-combobox-label" htmlFor="job-filter-input">
              Tool Filter
            </label>
            <InputGroup
              id="job-filter-input"
              placeholder="Filter tools"
              value={filterText}
              onChange={(event) => setFilterText(event.target.value)}
            />

            <FilteredList
              items={items}
              filterText={filterText}
              value={selectedDefinitionId}
              onValueChange={(value) => setSelectedDefinitionId(value)}
            />
          </div>
          <div
            className="jobs-tools-splitter"
            onMouseDown={(event) => {
              toolsListDragRef.current = { startY: event.clientY, startHeight: toolsListHeight };
              document.body.classList.add("jobs-tools-resizing");
              event.preventDefault();
            }}
          />
          <div className="jobs-tools-detail-pane">
            <div className="job-actions">
              <Button
                intent={Intent.PRIMARY}
                onClick={handleLaunch}
                disabled={!selectedDefinition}
                text="Run Tool"
              />
              <Button
                intent={Intent.DANGER}
                onClick={handleCancel}
                disabled={!isCurrentRunCancelable}
                text="Cancel Run"
              />
              <Button
                intent={Intent.NONE}
                onClick={openParamsModal}
                disabled={!selectedDefinition}
                text="Parameters"
              />
              <Button
                intent={Intent.NONE}
                onClick={handleClearHistory}
                disabled={recentRuns.length === 0}
                text="Clear History"
              />
            </div>

            {paneNotice ? (
              <div className="jobs-pane-notice" role="status">{paneNotice}</div>
            ) : null}

            <section className="jobs-current-card" aria-live="polite">
              <div className="jobs-current-head">
                <div className="jobs-current-title">
                  {currentRun ? currentRun.title : "Active Run"}
                </div>
                <span className={`jobs-status-chip ${runStatusClassName(currentRunStatus)}`}>
                  {statusLabel(currentRunStatus)}
                </span>
              </div>
              <div className="jobs-current-subtitle">
                {currentRun ? `Run ID: ${currentRun.runId}` : "No active run"}
              </div>
              <div className={`jobs-progress-track ${currentRunPercent === null && currentRunStatus === "running" ? "indeterminate" : ""}`}>
                <div className="jobs-progress-fill" style={{ width: `${currentRunPercent ?? 0}%` }} />
              </div>
              <div className="jobs-current-meta">
                <span className="jobs-current-metric">Progress {currentRunPercent === null ? "--" : `${currentRunPercent}%`}</span>
                <span className="jobs-current-metric">Elapsed {formatDurationMs(currentDurationMs)}</span>
              </div>
              <div className="jobs-current-message">{currentRunMessage}</div>
            </section>
          </div>
        </div>
      ) : (
        <div className="jobs-pane-header-row">
          <div className="jobs-pane-header-title">{showMessagesPane ? "Messages" : "Jobs"}</div>
          {showMessagesPane ? (
            <div className="messages-pane-titlebar-meta">
              {queuedTranscriptCount > 0 ? (
                <span className="messages-pane-held-count">{queuedTranscriptCount} queued</span>
              ) : null}
              <div className={`messages-pane-titlebar-progress ${messagesPaneStatus === "running" && messagesPanePercent === null ? "indeterminate" : ""}`}>
                <div className="messages-pane-titlebar-progress-fill" style={{ width: `${messageProgressWidth}%` }} />
              </div>
              <span className={`jobs-status-chip ${runStatusClassName(messagesPaneStatus)}`}>
                {statusLabel(messagesPaneStatus)}
              </span>
            </div>
          ) : null}
          <Button
            small
            intent={Intent.NONE}
            onClick={handleClearHistory}
            disabled={showMessagesPane ? transcriptItems.length === 0 : recentRuns.length === 0}
            text="Clear History"
          />
        </div>
      )}

      {paneNotice && !showToolLauncher ? (
        <div className="jobs-pane-notice" role="status">{paneNotice}</div>
      ) : null}

      {showMessagesPane ? (
        <section
          ref={(node) => {
            messagesBodyRef.current = node;
          }}
          className="messages-pane-body"
          aria-live="polite"
          onScroll={(event) => {
            const target = event.currentTarget;
            const remaining = target.scrollHeight - target.scrollTop - target.clientHeight;
            shouldStickMessagesRef.current = remaining <= 8;
          }}
        >
          {visibleTranscriptLines.length === 0 ? (
            <div className="jobs-empty">No messages yet.</div>
          ) : (
            <div className="messages-transcript">
              {visibleTranscriptLines.map((entry) => (
                <div
                  key={entry.key}
                  className={`messages-line ${
                    entry.type === "workspace"
                      ? `messages-line-${entry.level || "info"}`
                      : `messages-line-${entry.type}`
                  }`}
                >
                  <span className="messages-line-timestamp">{entry.timestamp}</span>
                  <span className="messages-line-source">{entry.source}</span>
                  <span className="messages-line-text">{entry.text}</span>
                </div>
              ))}
            </div>
          )}
        </section>
      ) : (
        <>
          {!showToolLauncher ? (
            <section className="jobs-current-card" aria-live="polite">
              <div className="jobs-current-head">
                <div className="jobs-current-title">
                  {currentRun ? currentRun.title : "Active Run"}
                </div>
                <span className={`jobs-status-chip ${runStatusClassName(currentRunStatus)}`}>
                  {statusLabel(currentRunStatus)}
                </span>
              </div>
              <div className="jobs-current-subtitle">
                {currentRun ? `Run ID: ${currentRun.runId}` : "No active run"}
              </div>
              <div className={`jobs-progress-track ${currentRunPercent === null && currentRunStatus === "running" ? "indeterminate" : ""}`}>
                <div className="jobs-progress-fill" style={{ width: `${currentRunPercent ?? 0}%` }} />
              </div>
              <div className="jobs-current-meta">
                <span className="jobs-current-metric">Progress {currentRunPercent === null ? "--" : `${currentRunPercent}%`}</span>
                <span className="jobs-current-metric">Elapsed {formatDurationMs(currentDurationMs)}</span>
              </div>
              <div className="jobs-current-message">{currentRunMessage}</div>
            </section>
          ) : null}

          <section className="jobs-recent-section">
            <div className="jobs-section-title">Recent Runs</div>
            <div className="jobs-runs-list">
              {recentRuns.length === 0 ? (
                <div className="jobs-empty">No runs in this session.</div>
              ) : recentRuns.map((run) => {
                const isSelected = selectedRun?.runId === run.runId;
                const runDuration = deriveDurationMs(run.startedAtMs, run.finishedAtMs, nowMs);
                return (
                  <button
                    key={run.runId}
                    type="button"
                    className={`jobs-run-row ${isSelected ? "selected" : ""}`}
                    onClick={() => {
                      setSelectedRunId(run.runId);
                    }}
                  >
                    <div className="jobs-run-row-head">
                      <span className="jobs-run-row-title">{run.title}</span>
                      <span className={`jobs-status-chip ${runStatusClassName(run.status)}`}>
                        {statusLabel(run.status)}
                      </span>
                    </div>
                    <div className="jobs-run-row-meta">
                      <span className="jobs-run-row-id">{run.runId}</span>
                      <span>{run.percent === null ? "--" : `${run.percent}%`}</span>
                      <span>{formatDurationMs(runDuration)}</span>
                    </div>
                  </button>
                );
              })}
            </div>
          </section>
        </>
      )}

      {!showMessagesPane ? (
      <section className="jobs-details-section">
        {selectedRun ? (
            <div className="jobs-details-panel">
                <>
                  <div className="jobs-section-title">Summary</div>
                  <table className="jobs-summary-table">
                    <tbody>
                      <tr>
                        <th>Status</th>
                        <td>
                          <span className={`jobs-status-chip ${runStatusClassName(selectedRun.status)}`}>
                            {statusLabel(selectedRun.status)}
                          </span>
                        </td>
                      </tr>
                      <tr>
                        <th>Percent</th>
                        <td>{selectedRun.percent === null ? "--" : `${selectedRun.percent}%`}</td>
                      </tr>
                      <tr>
                        <th>Queued At</th>
                        <td>{runDetailsSummary?.requestedAt || "--"}</td>
                      </tr>
                      <tr>
                        <th>Started At</th>
                        <td>{runDetailsSummary?.startedAt || "--"}</td>
                      </tr>
                      <tr>
                        <th>Finished At</th>
                        <td>{runDetailsSummary?.finishedAt || "--"}</td>
                      </tr>
                      <tr>
                        <th>Duration</th>
                        <td>{runDetailsSummary?.duration || "--"}</td>
                      </tr>
                      <tr>
                        <th>Result</th>
                        <td>{selectedRun.resultSummary || "--"}</td>
                      </tr>
                    </tbody>
                  </table>

                  <div className="jobs-section-title">Tool Arguments</div>
                  <table className="jobs-snapshot-table">
                    <thead>
                      <tr>
                        <th>Parameter</th>
                        <th>Value</th>
                        <th>Type</th>
                      </tr>
                    </thead>
                    <tbody>
                      {selectedRunSnapshotRows.map((row) => (
                        <tr key={`snapshot-${row.key}`}>
                          <td>{row.key}</td>
                          <td>{row.valueText}</td>
                          <td>{row.typeHint}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>

              <div className="jobs-section-title">Live Logs</div>
              <textarea
                className="job-params jobs-advanced-json"
                rows={12}
                value={selectedRunLogs || "(no logs yet)"}
                readOnly
              />

              <div className="jobs-section-title">Messages</div>
              <div className="jobs-messages">
                {selectedRun.messages.length === 0 ? (
                  <div className="jobs-empty">No updates for this run.</div>
                ) : selectedRun.messages.map((message) => (
                  <details key={message.id} className={`jobs-message jobs-message-${message.level}`}>
                    <summary>
                      <span>{formatTimestamp(message.timestampMs)}</span>
                      <span>{message.text}</span>
                    </summary>
                    <pre>{JSON.stringify(message.raw, null, 2)}</pre>
                  </details>
                ))}
              </div>
            </div>
          ) : (
            <div className="jobs-empty">Select a run to view details.</div>
          )}
      </section>
      ) : null}

      {showToolLauncher && showParamsModal ? (
        <div
          className="jobs-modal-backdrop"
          onClick={(event) => {
            if (event.target === event.currentTarget) closeParamsModal();
          }}
        >
          <div className="jobs-modal" role="dialog" aria-modal="true" aria-label="Edit tool parameters">
            <div className="jobs-modal-header">
              <div className="jobs-modal-title">Tool Parameters</div>
              <div className="jobs-modal-subtitle">
                {selectedDefinition ? `${selectedDefinition.title} (${selectedDefinition.job_definition_id})` : ""}
              </div>
            </div>

            {paramValidationErrors.length > 0 ? (
              <div className="jobs-params-error-list">
                {paramValidationErrors.map((error) => (
                  <div key={error} className="jobs-params-error">{error}</div>
                ))}
              </div>
            ) : null}

            <div className="jobs-modal-body">
              <table className="jobs-params-table">
                <thead>
                  <tr>
                    <th>Parameter</th>
                    <th>Value</th>
                    <th>Type</th>
                    <th>Required</th>
                    <th>Notes</th>
                  </tr>
                </thead>
                <tbody>
                  {modalRows.map((row) => (
                    <tr key={`param-${row.key}`}>
                      <td>{row.name}</td>
                      <td>{renderParameterValueEditor(row)}</td>
                      <td>{row.typeHint || "--"}</td>
                      <td>{row.required ? "Yes" : "No"}</td>
                      <td>{row.note || "--"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>

              <div className="jobs-modal-advanced">
                <button
                  type="button"
                  className="toolbar-toggle"
                  onClick={() => {
                    const next = !showAdvancedJson;
                    setShowAdvancedJson(next);
                    if (next && modalDraftParams) {
                      setAdvancedJsonText(JSON.stringify(modalDraftParams, null, 2));
                    }
                  }}
                >
                  {showAdvancedJson ? "Hide Debug JSON" : "Show Debug JSON"}
                </button>
                {showAdvancedJson ? (
                  <div className="jobs-advanced-editor">
                    <textarea
                      className="job-params jobs-advanced-json"
                      rows={8}
                      value={advancedJsonText}
                      onChange={(event) => handleAdvancedJsonChange(event.target.value)}
                    />
                    {advancedJsonError ? (
                      <div className="jobs-params-error">{advancedJsonError}</div>
                    ) : null}
                  </div>
                ) : null}
              </div>
            </div>

            <div className="jobs-modal-actions">
              <button type="button" className="toolbar-toggle" onClick={handleResetParams}>Reset</button>
              <div className="jobs-modal-actions-right">
                <button type="button" className="toolbar-toggle" onClick={closeParamsModal}>Cancel</button>
                <button type="button" className="toolbar-toggle" onClick={handleApplyParams}>Apply</button>
              </div>
            </div>
          </div>
        </div>
      ) : null}

      <div className="job-active">Status: {statusLabel(currentRunStatus)}</div>
    </div>
  );
}
