import React, { useCallback, useMemo, useRef, useState } from "react";
import { Actions, Layout } from "flexlayout-react";
import type { Action, ITabRenderValues, Model, TabNode } from "flexlayout-react";
import { Icon, Tooltip } from "@blueprintjs/core";
import AssistantBugReportDialog from "./components/assistant/AssistantBugReportDialog";
import Toolbar from "./components/Toolbar";
import { useAssistantSession } from "./hooks/useAssistantSession";
import { useBackendEventStreams } from "./hooks/useBackendEventStreams";
import { useScenarioWorkspace } from "./hooks/useScenarioWorkspace";
import { createPanelFactory } from "./layout/PanelFactory";
import {
  ACTIVITY_BAR_ICON_NAMES,
  WORKSPACE_COMPONENTS,
  WORKSPACE_DYNAMIC_ICONS,
  WORKSPACE_DYNAMIC_IDS,
  WORKSPACE_PANELS,
  createWorkspaceLayoutModel,
  ensureDynamicWorkspaceTab,
  ensureWorkspacePanelOpen,
  resetWorkspaceLayoutModel,
  saveWorkspaceLayoutModel,
} from "./layout/workspaceLayout";
import type { MapController } from "./map/mapController";
import type { ExplorerTreeRow } from "./components/explorer/FilteredTreeTable";
import { createMarimoNotebookForScenario, openMarimoNotebook } from "./services/marimoService";
import { captureAssistantBugReport } from "./services/assistantService";
import { createScenarioPythonFile } from "./services/scenarioService";

export type ThemeOption = "dark" | "light" | "high-contrast" | "ocean" | "forest" | "sepia";

const IMAGE_EXTENSIONS = new Set([".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".tif", ".tiff"]);

function flexLayoutThemeClass(theme: ThemeOption): string {
  if (theme === "light" || theme === "sepia") return "flexlayout__theme_light";
  return "flexlayout__theme_dark";
}

export default function AppLayout(): JSX.Element {
  const mapControllerRef = useRef<MapController | null>(null);
  const [scenarioExplorerRefreshToken, setScenarioExplorerRefreshToken] = useState(0);
  const [layoutModel, setLayoutModel] = useState<Model>(() => createWorkspaceLayoutModel(window.localStorage));
  const [assistantPromptDraft, setAssistantPromptDraft] = useState("");
  const [jobsDrafts, setJobsDrafts] = useState<Record<string, Record<string, unknown>>>({});
  const [showAssistantBugReportDialog, setShowAssistantBugReportDialog] = useState(false);
  const [assistantBugReportText, setAssistantBugReportText] = useState("");
  const [assistantBugReportSubmitting, setAssistantBugReportSubmitting] = useState(false);
  const [assistantBugReportErrorText, setAssistantBugReportErrorText] = useState<string | null>(null);

  const [theme, setTheme] = useState<ThemeOption>(() => {
    const saved = localStorage.getItem("lunar-analyst-theme") as ThemeOption;
    const valid: ThemeOption[] = ["dark", "light", "high-contrast", "ocean", "forest", "sepia"];
    return valid.includes(saved) ? saved : "light";
  });

  const handleThemeChange = useCallback((next: ThemeOption) => {
    setTheme(next);
    localStorage.setItem("lunar-analyst-theme", next);
  }, []);

  const scenarioWorkspace = useScenarioWorkspace(mapControllerRef);
  const assistantSession = useAssistantSession({
    activeScenarioIdRef: scenarioWorkspace.activeScenarioIdRef,
    baseLayerVisible: scenarioWorkspace.baseLayerVisible,
    onScenarioChange: scenarioWorkspace.handleActiveScenarioChange,
    mapControllerRef,
  });

  const refreshScenarioExplorer = useCallback(() => {
    setScenarioExplorerRefreshToken((value) => value + 1);
  }, []);

  useBackendEventStreams({
    activeScenarioId: scenarioWorkspace.activeScenarioId,
    activeScenarioIdRef: scenarioWorkspace.activeScenarioIdRef,
    activeAssistantSessionId: assistantSession.activeAssistantSessionId,
    mapControllerRef,
    refreshScenarioLayers: scenarioWorkspace.refreshScenarioLayers,
    refreshScenarioExplorer,
    onAssistantEvent: assistantSession.handleAssistantStreamEvent,
  });

  const handleMapControllerReady = useCallback((controller: MapController | null) => {
    mapControllerRef.current = controller;
  }, []);

  const getMapCenter = useCallback((): [number, number] | null => {
    const center = mapControllerRef.current?.getMap().getView().getCenter();
    if (!center || center.length < 2) return null;
    return [Number(center[0]), Number(center[1])];
  }, []);

  const zoomToExtent = useCallback((extent: [number, number, number, number], maxZoom?: number): void => {
    mapControllerRef.current?.fitExtent(extent, { maxZoom, paddingPx: 32 });
  }, []);

  const handleAssistantSubmitPrompt = useCallback(() => {
    const prompt = assistantPromptDraft.trim();
    if (!prompt) return;
    assistantSession.handleAssistantSubmitPrompt(prompt);
    setAssistantPromptDraft("");
  }, [assistantPromptDraft, assistantSession]);

  const focusPanel = useCallback((panelId: keyof typeof WORKSPACE_PANELS) => {
    ensureWorkspacePanelOpen(layoutModel, panelId);
  }, [layoutModel]);

  const handleOpenAssistantWorkspace = useCallback(() => {
    ensureDynamicWorkspaceTab(layoutModel, {
      id: WORKSPACE_DYNAMIC_IDS.assistantWorkspace,
      name: "Assistant",
      component: WORKSPACE_COMPONENTS.assistantWorkspace,
      icon: WORKSPACE_DYNAMIC_ICONS.assistantWorkspace,
      enableRenderOnDemand: false,
    });
  }, [layoutModel]);

  const handleOpenExpandedAssistantResponse = useCallback(() => {
    handleOpenAssistantWorkspace();
  }, [handleOpenAssistantWorkspace]);

  const handleFocusSidebarAssistant = useCallback(() => {
    focusPanel("assistant");
  }, [focusPanel]);

  const latestAssistantTurnId = useMemo(() => {
    for (let idx = assistantSession.assistantDisplayMessages.length - 1; idx >= 0; idx -= 1) {
      const turnId = String(assistantSession.assistantDisplayMessages[idx]?.turn_id || "").trim();
      if (turnId) {
        return turnId;
      }
    }
    return null;
  }, [assistantSession.assistantDisplayMessages]);

  const assistantBugReportProgramState = useMemo(
    () => ({
      active_scenario_id: scenarioWorkspace.activeScenarioId,
      active_assistant_session_id: assistantSession.activeAssistantSessionId,
      active_assistant_turn_id: latestAssistantTurnId,
      active_provider_id: assistantSession.assistantProviderId || null,
      active_model_id: assistantSession.assistantModelId || null,
      active_panel: "assistant",
      assistant_prompt_draft: assistantPromptDraft || null,
      workspace_state: {
        assistant_sessions: assistantSession.assistantSessions.length,
        assistant_messages: assistantSession.assistantDisplayMessages.length,
        theme,
      },
    }),
    [
      assistantPromptDraft,
      assistantSession.activeAssistantSessionId,
      assistantSession.assistantDisplayMessages.length,
      assistantSession.assistantModelId,
      assistantSession.assistantProviderId,
      assistantSession.assistantSessions.length,
      latestAssistantTurnId,
      scenarioWorkspace.activeScenarioId,
      theme,
    ],
  );

  const handleOpenAssistantBugReport = useCallback(() => {
    setAssistantBugReportErrorText(null);
    setAssistantBugReportText("");
    setShowAssistantBugReportDialog(true);
  }, []);

  const handleCloseAssistantBugReport = useCallback(() => {
    if (assistantBugReportSubmitting) return;
    setShowAssistantBugReportDialog(false);
    setAssistantBugReportErrorText(null);
  }, [assistantBugReportSubmitting]);

  const handleSubmitAssistantBugReport = useCallback(() => {
    const sessionId = assistantSession.activeAssistantSessionId;
    const reportText = assistantBugReportText.trim();
    if (!sessionId || !reportText || assistantBugReportSubmitting) {
      return;
    }
    setAssistantBugReportSubmitting(true);
    setAssistantBugReportErrorText(null);
    void (async () => {
      try {
        await captureAssistantBugReport(sessionId, {
          report_text: reportText,
          program_state: assistantBugReportProgramState,
        });
        setShowAssistantBugReportDialog(false);
        setAssistantBugReportText("");
      } catch (error) {
        setAssistantBugReportErrorText(
          error instanceof Error ? error.message : "Failed to capture assistant bug report.",
        );
      } finally {
        setAssistantBugReportSubmitting(false);
      }
    })();
  }, [
    assistantBugReportSubmitting,
    assistantBugReportText,
    assistantBugReportProgramState,
    assistantSession.activeAssistantSessionId,
  ]);

  const handleCreateNotebookForScenario = useCallback(async (scenarioId: string) => {
    const response = await createMarimoNotebookForScenario(scenarioId);
    ensureDynamicWorkspaceTab(layoutModel, {
      id: `${WORKSPACE_DYNAMIC_IDS.notebookPrefix}${scenarioId}:${response.relative_path}`,
      name: response.file_name,
      component: WORKSPACE_COMPONENTS.notebook,
      icon: WORKSPACE_DYNAMIC_ICONS.notebook,
      config: {
        scenarioId,
        relativePath: response.relative_path,
        initialFileUrl: response.file_url,
        modifiedAtUtc: response.modified_at_utc,
      },
      enableRenderOnDemand: false,
    });
    refreshScenarioExplorer();
  }, [layoutModel, refreshScenarioExplorer]);

  const openPythonEditorTab = useCallback((
    scenarioId: string,
    relativePath: string,
    options?: { initialContent?: string; modifiedAtUtc?: string | null; tabName?: string },
  ) => {
    ensureDynamicWorkspaceTab(layoutModel, {
      id: `${WORKSPACE_DYNAMIC_IDS.pythonEditorPrefix}${scenarioId}:${relativePath}`,
      name: options?.tabName || relativePath.split("/").pop() || relativePath,
      component: WORKSPACE_COMPONENTS.pythonEditor,
      icon: WORKSPACE_DYNAMIC_ICONS.pythonEditor,
      config: {
        scenarioId,
        relativePath,
        initialContent: options?.initialContent,
        modifiedAtUtc: options?.modifiedAtUtc,
      },
      enableRenderOnDemand: false,
    });
  }, [layoutModel]);

  const openTextEditorTab = useCallback((
    scenarioId: string,
    relativePath: string,
    options?: { initialContent?: string; modifiedAtUtc?: string | null; tabName?: string },
  ) => {
    ensureDynamicWorkspaceTab(layoutModel, {
      id: `${WORKSPACE_DYNAMIC_IDS.textEditorPrefix}${scenarioId}:${relativePath}`,
      name: options?.tabName || relativePath.split("/").pop() || relativePath,
      component: WORKSPACE_COMPONENTS.textEditor,
      icon: WORKSPACE_DYNAMIC_ICONS.textEditor,
      config: {
        scenarioId,
        relativePath,
        initialContent: options?.initialContent,
        modifiedAtUtc: options?.modifiedAtUtc,
      },
      enableRenderOnDemand: false,
    });
  }, [layoutModel]);

  const openCsvEditorTab = useCallback((
    scenarioId: string,
    relativePath: string,
    options?: { initialContent?: string; modifiedAtUtc?: string | null; tabName?: string },
  ) => {
    ensureDynamicWorkspaceTab(layoutModel, {
      id: `${WORKSPACE_DYNAMIC_IDS.csvEditorPrefix}${scenarioId}:${relativePath}`,
      name: options?.tabName || relativePath.split("/").pop() || relativePath,
      component: WORKSPACE_COMPONENTS.csvEditor,
      icon: WORKSPACE_DYNAMIC_ICONS.csvEditor,
      config: {
        scenarioId,
        relativePath,
        initialContent: options?.initialContent,
        modifiedAtUtc: options?.modifiedAtUtc,
      },
      enableRenderOnDemand: false,
    });
  }, [layoutModel]);

  const openImageViewerTab = useCallback((scenarioId: string, relativePath: string, tabName?: string) => {
    ensureDynamicWorkspaceTab(layoutModel, {
      id: `${WORKSPACE_DYNAMIC_IDS.imageViewerPrefix}${scenarioId}:${relativePath}`,
      name: tabName || relativePath.split("/").pop() || relativePath,
      component: WORKSPACE_COMPONENTS.imageViewer,
      icon: WORKSPACE_DYNAMIC_ICONS.imageViewer,
      config: {
        scenarioId,
        relativePath,
      },
      enableRenderOnDemand: false,
    });
  }, [layoutModel]);

  const handleOpenNotebookFromExplorer = useCallback((row: ExplorerTreeRow) => {
    const relativePath = String(row.relativePath || "").trim();
    if (!row.scenarioId || !relativePath) return;
    ensureDynamicWorkspaceTab(layoutModel, {
      id: `${WORKSPACE_DYNAMIC_IDS.notebookPrefix}${row.scenarioId}:${relativePath}`,
      name: row.name,
      component: WORKSPACE_COMPONENTS.notebook,
      icon: WORKSPACE_DYNAMIC_ICONS.notebook,
      config: {
        scenarioId: row.scenarioId,
        relativePath,
        modifiedAtUtc: row.modifiedAtUtc,
      },
      enableRenderOnDemand: false,
    });
  }, [layoutModel]);

  const handleOpenPythonFileFromExplorer = useCallback((row: ExplorerTreeRow) => {
    const relativePath = String(row.relativePath || "").trim();
    if (!row.scenarioId || !relativePath) return;
    openPythonEditorTab(row.scenarioId, relativePath, {
      tabName: row.name,
      modifiedAtUtc: row.modifiedAtUtc,
    });
  }, [openPythonEditorTab]);

  const handleOpenScenarioPythonEntryFromExplorer = useCallback((row: ExplorerTreeRow) => {
    const relativePath = String(row.relativePath || "").trim();
    if (!row.scenarioId || !relativePath) return;
    void (async () => {
      try {
        const response = await openMarimoNotebook({
          scenario_id: row.scenarioId,
          relative_path: relativePath,
          restart_if_running: true,
        });
        ensureDynamicWorkspaceTab(layoutModel, {
          id: `${WORKSPACE_DYNAMIC_IDS.notebookPrefix}${row.scenarioId}:${response.relative_path}`,
          name: response.file_name,
          component: WORKSPACE_COMPONENTS.notebook,
          icon: WORKSPACE_DYNAMIC_ICONS.notebook,
          config: {
            scenarioId: row.scenarioId,
            relativePath: response.relative_path,
            initialFileUrl: response.file_url,
            modifiedAtUtc: response.modified_at_utc,
          },
          enableRenderOnDemand: false,
        });
      } catch {
        openPythonEditorTab(row.scenarioId, relativePath, {
          tabName: row.name,
          modifiedAtUtc: row.modifiedAtUtc,
        });
      }
    })();
  }, [layoutModel, openPythonEditorTab]);

  const handleOpenFileFromExplorer = useCallback((row: ExplorerTreeRow) => {
    const relativePath = String(row.relativePath || "").trim();
    if (!row.scenarioId || !relativePath) return;
    const lowerName = row.name.toLowerCase();

    if (lowerName.endsWith(".mo.py") || lowerName.endsWith(".py")) {
      handleOpenScenarioPythonEntryFromExplorer(row);
      return;
    }
    if (lowerName.endsWith(".csv")) {
      openCsvEditorTab(row.scenarioId, relativePath, {
        tabName: row.name,
        modifiedAtUtc: row.modifiedAtUtc,
      });
      return;
    }
    if (lowerName.endsWith(".txt")) {
      openTextEditorTab(row.scenarioId, relativePath, {
        tabName: row.name,
        modifiedAtUtc: row.modifiedAtUtc,
      });
      return;
    }
    const lastDot = lowerName.lastIndexOf(".");
    const extension = lastDot >= 0 ? lowerName.slice(lastDot) : "";
    if (IMAGE_EXTENSIONS.has(extension)) {
      openImageViewerTab(row.scenarioId, relativePath, row.name);
      return;
    }
  }, [handleOpenScenarioPythonEntryFromExplorer, openCsvEditorTab, openImageViewerTab, openTextEditorTab]);

  const handleCreatePythonFileForScenario = useCallback(async (scenarioId: string) => {
    const response = await createScenarioPythonFile(scenarioId, "script");
    openPythonEditorTab(scenarioId, response.relative_path, {
      tabName: response.file_name,
      initialContent: response.content,
      modifiedAtUtc: response.modified_at_utc,
    });
    refreshScenarioExplorer();
  }, [openPythonEditorTab, refreshScenarioExplorer]);

  const activeScenarioText = scenarioWorkspace.activeScenarioId
    ? `Scenario: ${scenarioWorkspace.activeScenarioId}`
    : "Scenario: loading...";

  const panelFactory = useMemo(
    () =>
      createPanelFactory({
        activeScenarioId: scenarioWorkspace.activeScenarioId,
        statusText: scenarioWorkspace.statusText,
        errorText: scenarioWorkspace.errorText,
        projectionReady: Boolean(scenarioWorkspace.projection),
        projection: scenarioWorkspace.projection,
        center: scenarioWorkspace.center,
        zoom: scenarioWorkspace.zoom,
        hillshadeUrl: scenarioWorkspace.hillshadeUrl,
        hillshadeOpacity: scenarioWorkspace.hillshadeOpacity,
        moonTrekCapabilitiesUrl: scenarioWorkspace.moonTrekCapabilitiesUrl,
        moonTrekLayerId: scenarioWorkspace.moonTrekLayerId,
        moonTrekMatrixSet: scenarioWorkspace.moonTrekMatrixSet,
        moonTrekStyle: scenarioWorkspace.moonTrekStyle,
        extraZoomLevels: scenarioWorkspace.extraZoomLevels,
        baseLayerVisible: scenarioWorkspace.baseLayerVisible,
        scenarioLayers: scenarioWorkspace.scenarioLayers,
        colormaps: scenarioWorkspace.colormaps,
        trekOverlays: scenarioWorkspace.trekOverlays,
        onMapControllerReady: handleMapControllerReady,
        onScenarioLayersChange: scenarioWorkspace.setScenarioLayers,
        onActiveScenarioChange: scenarioWorkspace.handleActiveScenarioChange,
        onBaseLayerVisibleChange: scenarioWorkspace.handleBaseLayerVisibleChange,
        refreshScenarioLayers: scenarioWorkspace.refreshScenarioLayers,
        scenarioExplorerRefreshToken,
        onAddLayerFromExplorer: (row) => {
          void scenarioWorkspace.handleAddLayerFromExplorer(row);
        },
        onOpenFileFromExplorer: handleOpenFileFromExplorer,
        onOpenNotebookFromExplorer: handleOpenNotebookFromExplorer,
        onCreateNotebookForScenario: handleCreateNotebookForScenario,
        onOpenPythonFileFromExplorer: handleOpenPythonFileFromExplorer,
        onOpenScenarioPythonEntryFromExplorer: handleOpenScenarioPythonEntryFromExplorer,
        onCreatePythonFileForScenario: handleCreatePythonFileForScenario,
        onAddTrekOverlay: scenarioWorkspace.handleAddTrekOverlay,
        onRemoveTrekOverlay: scenarioWorkspace.handleRemoveTrekOverlay,
        onUpdateTrekOverlay: scenarioWorkspace.handleUpdateTrekOverlay,
        getMapCenter,
        onZoomToExtent: zoomToExtent,
        assistantSessions: assistantSession.assistantSessions,
        activeAssistantSessionId: assistantSession.activeAssistantSessionId,
        assistantProviderOptions: assistantSession.assistantProviderOptions,
        assistantProviderId: assistantSession.assistantProviderId,
        assistantModelOptions: assistantSession.assistantModelOptions,
        assistantModelId: assistantSession.assistantModelId,
        assistantThinkingOptions: assistantSession.assistantThinkingOptions,
        assistantThinkingValue: assistantSession.assistantThinkingValue,
        assistantThinkingEnabled: assistantSession.assistantThinkingEnabled,
        assistantAccessModeOptions: assistantSession.assistantAccessModeOptions,
        assistantAccessMode: assistantSession.assistantAccessMode,
        assistantAccessModeEnabled: assistantSession.assistantAccessModeEnabled,
        assistantMessages: assistantSession.assistantDisplayMessages,
        pendingAssistantConfirmation: assistantSession.pendingAssistantConfirmation,
        assistantPromptDraft,
        onAssistantPromptDraftChange: setAssistantPromptDraft,
        onAssistantCreateSession: assistantSession.handleAssistantCreateSession,
        onAssistantSelectSession: assistantSession.handleAssistantSelectSession,
        onAssistantSelectProvider: assistantSession.handleAssistantSelectProvider,
        onAssistantSelectModel: assistantSession.setAssistantModelId,
        onAssistantSelectThinking: assistantSession.handleAssistantSelectThinking,
        onAssistantSelectAccessMode: assistantSession.handleAssistantSelectAccessMode,
        onAssistantCompactSession: assistantSession.handleAssistantCompactSession,
        onAssistantSubmitPrompt: handleAssistantSubmitPrompt,
        jobsDrafts,
        onJobsDraftsChange: setJobsDrafts,
        onAssistantResolveConfirmation: assistantSession.handleAssistantResolveConfirmation,
        onOpenExpandedAssistantResponse: handleOpenExpandedAssistantResponse,
        onOpenAssistantWorkspace: handleOpenAssistantWorkspace,
        onFocusSidebarAssistant: handleFocusSidebarAssistant,
      }),
    [
      assistantPromptDraft,
      assistantSession,
      handleAssistantSubmitPrompt,
      handleMapControllerReady,
      handleCreateNotebookForScenario,
      handleCreatePythonFileForScenario,
      handleFocusSidebarAssistant,
      handleOpenAssistantWorkspace,
      handleOpenExpandedAssistantResponse,
      handleOpenNotebookFromExplorer,
      handleOpenPythonFileFromExplorer,
      handleOpenScenarioPythonEntryFromExplorer,
      jobsDrafts,
      scenarioExplorerRefreshToken,
      scenarioWorkspace,
    ],
  );

  const handleLayoutAction = useCallback((action: Action): Action | undefined => {
    if (action.type === Actions.SELECT_TAB && action.data?.tabNode === WORKSPACE_PANELS.assistantActivity.tabId) {
      handleOpenAssistantWorkspace();
      return undefined;
    }
    if (action.type === Actions.SELECT_TAB && action.data?.node === WORKSPACE_PANELS.assistantActivity.tabId) {
      handleOpenAssistantWorkspace();
      return undefined;
    }
    if (action.type === Actions.SELECT_TAB) {
      const selectedTabId = String(action.data?.tabNode || action.data?.node || "").trim();
      if (selectedTabId) {
        const selectedNode = layoutModel.getNodeById(selectedTabId);
        const config = (selectedNode?.getConfig() || {}) as { scenarioId?: string };
        const scenarioId = String(config.scenarioId || "").trim();
        if (scenarioId && scenarioId !== scenarioWorkspace.activeScenarioIdRef.current) {
          scenarioWorkspace.handleActiveScenarioChange(scenarioId);
        }
      }
    }
    if (action.type === Actions.DELETE_TAB && action.data?.node === WORKSPACE_PANELS.map.tabId) {
      return undefined;
    }
    if (action.type === Actions.DELETE_TABSET) {
      const target = layoutModel.getNodeById(String(action.data?.node || ""));
      const containsMap = Boolean(
        target?.getChildren().some((child) => child.getId() === WORKSPACE_PANELS.map.tabId),
      );
      if (containsMap) {
        return undefined;
      }
    }
    return action;
  }, [handleOpenAssistantWorkspace, layoutModel, scenarioWorkspace]);

  const handleLayoutModelChange = useCallback((model: Model) => {
    saveWorkspaceLayoutModel(model, window.localStorage);
  }, []);

  const handleResetLayout = useCallback(() => {
    const nextModel = resetWorkspaceLayoutModel(window.localStorage);
    saveWorkspaceLayoutModel(nextModel, window.localStorage);
    setLayoutModel(nextModel);
  }, []);

  const handleRenderTab = useCallback((node: TabNode, renderValues: ITabRenderValues) => {
    const component = node.getComponent();
    const label = node.getName();
    const iconName =
      component && component in ACTIVITY_BAR_ICON_NAMES
        ? ACTIVITY_BAR_ICON_NAMES[component as keyof typeof ACTIVITY_BAR_ICON_NAMES]
        : undefined;

    if (!iconName) {
      return;
    }

    renderValues.leading = (
      <Tooltip content={label} placement="right">
        <span className="workspace-activity-icon" aria-label={label} role="img">
          <Icon icon={iconName} size={18} />
        </span>
      </Tooltip>
    );
  }, []);

  return (
    <div
      className={`app-shell react-app-shell ${theme === "light" || theme === "sepia" ? "bp6-body" : "bp6-dark"} theme-${theme}`}
    >
      <Toolbar
        activeScenarioText={activeScenarioText}
        statusText={scenarioWorkspace.statusText}
        onShowScenarioExplorer={() => focusPanel("scenarioExplorer")}
        onResetLayout={handleResetLayout}
        onReportAssistantBug={handleOpenAssistantBugReport}
        theme={theme}
        onThemeChange={handleThemeChange}
      />

      <AssistantBugReportDialog
        isOpen={showAssistantBugReportDialog}
        reportText={assistantBugReportText}
        submitting={assistantBugReportSubmitting}
        errorText={assistantBugReportErrorText}
        activeSessionId={assistantSession.activeAssistantSessionId}
        activeScenarioId={scenarioWorkspace.activeScenarioId}
        activeTurnId={latestAssistantTurnId}
        activeProviderId={assistantSession.assistantProviderId}
        activeModelId={assistantSession.assistantModelId}
        programState={assistantBugReportProgramState}
        onClose={handleCloseAssistantBugReport}
        onReportTextChange={setAssistantBugReportText}
        onSubmit={handleSubmitAssistantBugReport}
      />

      <div className={`layout-host workspace-layout-shell ${flexLayoutThemeClass(theme)}`}>
        <Layout
          model={layoutModel}
          factory={panelFactory}
          onAction={handleLayoutAction}
          onModelChange={handleLayoutModelChange}
          onRenderTab={handleRenderTab}
          supportsPopout={false}
          realtimeResize
        />
      </div>
    </div>
  );
}
