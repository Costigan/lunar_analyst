import React from "react";
import type { TabNode } from "flexlayout-react";
import AssistantWorkspacePane from "../components/assistant/AssistantWorkspacePane";
import ScenarioExplorerPane from "../components/explorer/ScenarioExplorerPane";
import type { ExplorerTreeRow } from "../components/explorer/FilteredTreeTable";
import JobsManagerPane from "../components/jobs/JobsManagerPane";
import NotebookPane from "../components/notebook/NotebookPane";
import PythonEditorPane from "../components/editor/PythonEditorPane";
import TextEditorPane from "../components/editor/TextEditorPane";
import CsvEditorPane from "../components/editor/CsvEditorPane";
import ImageViewerPane from "../components/viewer/ImageViewerPane";
import LayerManagerPane from "../components/layers/LayerManagerPane";
import TrekLayerCatalogPane, {
  type TrekOverlayPatch,
  type TrekOverlayState,
} from "../components/trek/TrekLayerCatalogPane";
import NomenclaturePane from "../components/nomenclature/NomenclaturePane";
import type { MapController } from "../map/mapController";
import MapViewport from "../map/MapViewport";
import type { ColormapDefinition } from "../services/lunarAnalystService";
import type {
  AssistantAccessMode,
  AssistantConfirmation,
  AssistantMessage,
  AssistantSession,
} from "../services/assistantService";
import type { ScenarioLayerState } from "../services/scenarioService";
import type { TrekLayerMetadata } from "../services/trekService";
import { WORKSPACE_COMPONENTS } from "./workspaceLayout";

type PanelFactoryProps = {
  activeScenarioId: string | null;
  statusText: string;
  errorText: string;
  projectionReady: boolean;
  projection: React.ComponentProps<typeof MapViewport>["projection"] | null;
  center: [number, number];
  zoom: number;
  hillshadeUrl: string;
  hillshadeOpacity: number;
  moonTrekCapabilitiesUrl: string;
  moonTrekLayerId: string;
  moonTrekMatrixSet: string;
  moonTrekStyle: string;
  extraZoomLevels: number;
  baseLayerVisible: boolean;
  scenarioLayers: ScenarioLayerState[];
  colormaps: ColormapDefinition[];
  trekOverlays: TrekOverlayState[];
  onMapControllerReady: (controller: MapController | null) => void;
  onScenarioLayersChange: React.Dispatch<React.SetStateAction<ScenarioLayerState[]>>;
  onActiveScenarioChange: (scenarioId: string) => void;
  onBaseLayerVisibleChange: (visible: boolean) => void;
  refreshScenarioLayers: (scenarioId?: string | null) => Promise<void>;
  scenarioExplorerRefreshToken: number;
  onAddLayerFromExplorer: (row: ExplorerTreeRow) => void;
  onOpenFileFromExplorer: (row: ExplorerTreeRow) => void;
  onOpenNotebookFromExplorer: (row: ExplorerTreeRow) => void;
  onCreateNotebookForScenario: (scenarioId: string) => Promise<void>;
  onOpenPythonFileFromExplorer: (row: ExplorerTreeRow) => void;
  onOpenScenarioPythonEntryFromExplorer: (row: ExplorerTreeRow) => void;
  onCreatePythonFileForScenario: (scenarioId: string) => Promise<void>;
  onAddTrekOverlay: (metadata: TrekLayerMetadata) => void;
  onRemoveTrekOverlay: (layerId: string) => void;
  onUpdateTrekOverlay: (layerId: string, patch: TrekOverlayPatch) => void;
  getMapCenter: () => [number, number] | null;
  onZoomToExtent: (extent: [number, number, number, number], maxZoom?: number) => void;
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
  assistantMessages: AssistantMessage[];
  pendingAssistantConfirmation: AssistantConfirmation | null;
  assistantPromptDraft: string;
  onAssistantPromptDraftChange: (prompt: string) => void;
  onAssistantCreateSession: (title: string) => void;
  onAssistantSelectSession: (sessionId: string) => void;
  onAssistantSelectProvider: (providerId: string) => void;
  onAssistantSelectModel: (modelId: string) => void;
  onAssistantSelectThinking: (value: string) => void;
  onAssistantSelectAccessMode: (accessMode: AssistantAccessMode) => void;
  onAssistantCompactSession: (sessionId: string) => void;
  onAssistantSubmitPrompt: () => void;
  jobsDrafts: Record<string, Record<string, unknown>>;
  onJobsDraftsChange: React.Dispatch<React.SetStateAction<Record<string, Record<string, unknown>>>>;
  onAssistantResolveConfirmation: (
    decision: "allow_once" | "always_allow_action_type" | "deny_once",
  ) => void;
  onOpenAssistantWorkspace: () => void;
  onFocusSidebarAssistant: () => void;
};

export function createPanelFactory(props: PanelFactoryProps): (node: TabNode) => React.ReactNode {
  return (node: TabNode): React.ReactNode => {
    const component = node.getComponent();
    const title = node.getName();

    const wrapPrimaryPanel = (content: React.ReactNode): JSX.Element => (
      <div className="workspace-primary-panel-shell">
        <div className="workspace-primary-panel-title">{title}</div>
        <div className="workspace-primary-panel-body">{content}</div>
      </div>
    );

    if (component === WORKSPACE_COMPONENTS.scenarioExplorer) {
      return wrapPrimaryPanel(
        <ScenarioExplorerPane
          activeScenarioId={props.activeScenarioId}
          onActiveScenarioChange={props.onActiveScenarioChange}
          refreshToken={props.scenarioExplorerRefreshToken}
          onAddLayer={props.onAddLayerFromExplorer}
          onOpenFile={props.onOpenFileFromExplorer}
          onOpenNotebook={props.onOpenNotebookFromExplorer}
          onOpenPythonFile={props.onOpenPythonFileFromExplorer}
          onOpenPythonEntry={props.onOpenScenarioPythonEntryFromExplorer}
          onCreateNotebook={props.onCreateNotebookForScenario}
          onCreatePythonFile={props.onCreatePythonFileForScenario}
        />,
      );
    }

    if (component === WORKSPACE_COMPONENTS.moonTrek) {
      return wrapPrimaryPanel(
        <TrekLayerCatalogPane
          overlays={props.trekOverlays}
          onAddOverlay={props.onAddTrekOverlay}
          onRemoveOverlay={props.onRemoveTrekOverlay}
          onUpdateOverlay={props.onUpdateTrekOverlay}
        />,
      );
    }

    if (component === WORKSPACE_COMPONENTS.nomenclature) {
      return wrapPrimaryPanel(
        <NomenclaturePane
          getMapCenter={props.getMapCenter}
          onZoomToExtent={props.onZoomToExtent}
        />,
      );
    }

    if (component === WORKSPACE_COMPONENTS.notebook) {
      const config = (node.getConfig() || {}) as {
        scenarioId?: string;
        relativePath?: string;
        initialFileUrl?: string;
        modifiedAtUtc?: string | null;
      };
      if (!config.scenarioId || !config.relativePath) {
        return <div className="workspace-tab-panel">Notebook tab is missing notebook metadata.</div>;
      }
      return (
        <NotebookPane
          scenarioId={config.scenarioId}
          relativePath={config.relativePath}
          initialFileUrl={config.initialFileUrl}
          modifiedAtUtc={config.modifiedAtUtc}
        />
      );
    }

    if (component === WORKSPACE_COMPONENTS.pythonEditor) {
      const config = (node.getConfig() || {}) as {
        scenarioId?: string;
        relativePath?: string;
        initialContent?: string;
        modifiedAtUtc?: string | null;
      };
      if (!config.scenarioId || !config.relativePath) {
        return <div className="workspace-tab-panel">Python editor tab is missing file metadata.</div>;
      }
      return (
        <PythonEditorPane
          scenarioId={config.scenarioId}
          relativePath={config.relativePath}
          initialContent={config.initialContent}
          modifiedAtUtc={config.modifiedAtUtc}
        />
      );
    }

    if (component === WORKSPACE_COMPONENTS.textEditor) {
      const config = (node.getConfig() || {}) as {
        scenarioId?: string;
        relativePath?: string;
        initialContent?: string;
        modifiedAtUtc?: string | null;
      };
      if (!config.scenarioId || !config.relativePath) {
        return <div className="workspace-tab-panel">Text editor tab is missing file metadata.</div>;
      }
      return (
        <TextEditorPane
          scenarioId={config.scenarioId}
          relativePath={config.relativePath}
          initialContent={config.initialContent}
          modifiedAtUtc={config.modifiedAtUtc}
        />
      );
    }

    if (component === WORKSPACE_COMPONENTS.csvEditor) {
      const config = (node.getConfig() || {}) as {
        scenarioId?: string;
        relativePath?: string;
        initialContent?: string;
        modifiedAtUtc?: string | null;
      };
      if (!config.scenarioId || !config.relativePath) {
        return <div className="workspace-tab-panel">CSV editor tab is missing file metadata.</div>;
      }
      return (
        <CsvEditorPane
          scenarioId={config.scenarioId}
          relativePath={config.relativePath}
          initialContent={config.initialContent}
          modifiedAtUtc={config.modifiedAtUtc}
        />
      );
    }

    if (component === WORKSPACE_COMPONENTS.imageViewer) {
      const config = (node.getConfig() || {}) as {
        scenarioId?: string;
        relativePath?: string;
      };
      if (!config.scenarioId || !config.relativePath) {
        return <div className="workspace-tab-panel">Image viewer tab is missing file metadata.</div>;
      }
      return (
        <ImageViewerPane
          scenarioId={config.scenarioId}
          relativePath={config.relativePath}
        />
      );
    }

    if (component === WORKSPACE_COMPONENTS.map) {
      return (
        <section className="workspace-tab-panel map-pane react-map-pane">
          {props.projectionReady && props.projection ? (
            <MapViewport
              projection={props.projection}
              center={props.center}
              zoom={props.zoom}
              hillshadeUrl={props.hillshadeUrl}
              hillshadeOpacity={props.hillshadeOpacity}
              moonTrekCapabilitiesUrl={props.moonTrekCapabilitiesUrl}
              moonTrekLayerId={props.moonTrekLayerId}
              moonTrekMatrixSet={props.moonTrekMatrixSet}
              moonTrekStyle={props.moonTrekStyle}
              extraZoomLevels={props.extraZoomLevels}
              baseLayerVisible={props.baseLayerVisible}
              scenarioLayers={props.scenarioLayers}
              trekOverlays={props.trekOverlays}
              colormaps={props.colormaps}
              onControllerReady={props.onMapControllerReady}
            />
          ) : (
            <div className="map-loading">Initializing map projection...</div>
          )}
          <div id="error-banner" className="error-banner" hidden={!props.errorText}>
            {props.errorText}
          </div>
          <div id="map-status" className="map-status-banner" hidden={!props.statusText}>
            {props.statusText}
          </div>
        </section>
      );
    }

    if (component === WORKSPACE_COMPONENTS.tools) {
      return wrapPrimaryPanel(
        <JobsManagerPane
          mode="tools"
          activeScenarioId={props.activeScenarioId}
          draftParamsByKey={props.jobsDrafts}
          onDraftParamsByKeyChange={props.onJobsDraftsChange}
        />,
      );
    }

    if (component === WORKSPACE_COMPONENTS.assistantActivity) {
      return <div className="workspace-tab-panel assistant-activity-launcher">Assistant workspace opens in the center view.</div>;
    }

    if (component === WORKSPACE_COMPONENTS.layerManager) {
      return wrapPrimaryPanel(
        <LayerManagerPane
          activeScenarioId={props.activeScenarioId}
          onActiveScenarioChange={props.onActiveScenarioChange}
          baseLayerVisible={props.baseLayerVisible}
          onBaseLayerVisibleChange={props.onBaseLayerVisibleChange}
          layers={props.scenarioLayers}
          colormaps={props.colormaps}
          onLayersChange={props.onScenarioLayersChange}
          refreshScenarioLayers={props.refreshScenarioLayers}
          trekOverlays={props.trekOverlays}
          onUpdateTrekOverlay={props.onUpdateTrekOverlay}
          onRemoveTrekOverlay={props.onRemoveTrekOverlay}
        />,
      );
    }

    if (component === WORKSPACE_COMPONENTS.jobsActivity) {
      return wrapPrimaryPanel(
        <JobsManagerPane
          mode="jobs"
          activeScenarioId={props.activeScenarioId}
          draftParamsByKey={props.jobsDrafts}
          onDraftParamsByKeyChange={props.onJobsDraftsChange}
        />,
      );
    }

    if (component === WORKSPACE_COMPONENTS.messages) {
      return (
        <div className="workspace-tab-panel">
          <JobsManagerPane
            mode="messages"
            activeScenarioId={props.activeScenarioId}
            draftParamsByKey={props.jobsDrafts}
            onDraftParamsByKeyChange={props.onJobsDraftsChange}
          />
        </div>
      );
    }

    if (component === WORKSPACE_COMPONENTS.assistant) {
      return (
        <div className="workspace-tab-panel assistant-sidebar-panel">
          <AssistantWorkspacePane
            sessions={props.assistantSessions}
            activeSessionId={props.activeAssistantSessionId}
            activeScenarioId={props.activeScenarioId}
            providerOptions={props.assistantProviderOptions}
            selectedProviderId={props.assistantProviderId}
            modelOptions={props.assistantModelOptions}
            selectedModelId={props.assistantModelId}
            thinkingOptions={props.assistantThinkingOptions}
            selectedThinkingValue={props.assistantThinkingValue}
            thinkingEnabled={props.assistantThinkingEnabled}
            accessModeOptions={props.assistantAccessModeOptions}
            selectedAccessMode={props.assistantAccessMode}
            accessModeEnabled={props.assistantAccessModeEnabled}
            pendingConfirmation={props.pendingAssistantConfirmation}
            prompt={props.assistantPromptDraft}
            messages={props.assistantMessages}
            onPromptChange={props.onAssistantPromptDraftChange}
            onCreateSession={props.onAssistantCreateSession}
            onSelectSession={props.onAssistantSelectSession}
            onSelectProvider={props.onAssistantSelectProvider}
            onSelectModel={props.onAssistantSelectModel}
            onSelectThinking={props.onAssistantSelectThinking}
            onSelectAccessMode={props.onAssistantSelectAccessMode}
            onCompactSession={props.onAssistantCompactSession}
            onSubmitPrompt={props.onAssistantSubmitPrompt}
            onResolveConfirmation={props.onAssistantResolveConfirmation}
          />
        </div>
      );
    }

    if (component === WORKSPACE_COMPONENTS.assistantWorkspace) {
      return (
        <div className="workspace-tab-panel assistant-workspace-panel">
          <AssistantWorkspacePane
            sessions={props.assistantSessions}
            activeSessionId={props.activeAssistantSessionId}
            activeScenarioId={props.activeScenarioId}
            providerOptions={props.assistantProviderOptions}
            selectedProviderId={props.assistantProviderId}
            modelOptions={props.assistantModelOptions}
            selectedModelId={props.assistantModelId}
            thinkingOptions={props.assistantThinkingOptions}
            selectedThinkingValue={props.assistantThinkingValue}
            thinkingEnabled={props.assistantThinkingEnabled}
            accessModeOptions={props.assistantAccessModeOptions}
            selectedAccessMode={props.assistantAccessMode}
            accessModeEnabled={props.assistantAccessModeEnabled}
            pendingConfirmation={props.pendingAssistantConfirmation}
            prompt={props.assistantPromptDraft}
            messages={props.assistantMessages}
            onPromptChange={props.onAssistantPromptDraftChange}
            onCreateSession={props.onAssistantCreateSession}
            onSelectSession={props.onAssistantSelectSession}
            onSelectProvider={props.onAssistantSelectProvider}
            onSelectModel={props.onAssistantSelectModel}
            onSelectThinking={props.onAssistantSelectThinking}
            onSelectAccessMode={props.onAssistantSelectAccessMode}
            onCompactSession={props.onAssistantCompactSession}
            onSubmitPrompt={props.onAssistantSubmitPrompt}
            onResolveConfirmation={props.onAssistantResolveConfirmation}
            onFocusSidebarAssistant={props.onFocusSidebarAssistant}
          />
        </div>
      );
    }

    return <div className="workspace-tab-panel">Unsupported panel: {String(component || node.getName())}</div>;
  };
}
