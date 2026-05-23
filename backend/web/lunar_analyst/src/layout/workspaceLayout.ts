import { Actions, DockLocation, Model } from "flexlayout-react";
import type {
  IJsonBorderNode,
  IJsonModel,
  IJsonRowNode,
  IJsonTabNode,
  IJsonTabSetNode,
} from "flexlayout-react";

export const WORKSPACE_LAYOUT_SCHEMA_VERSION = "v8";
export const WORKSPACE_LAYOUT_MODE = "desktop";
export const WORKSPACE_LAYOUT_STORAGE_PREFIX = "lunar-analyst-workspace-layout";
export const WORKSPACE_LAYOUT_STORAGE_KEY =
  `${WORKSPACE_LAYOUT_STORAGE_PREFIX}:${WORKSPACE_LAYOUT_MODE}:${WORKSPACE_LAYOUT_SCHEMA_VERSION}`;

export const WORKSPACE_COMPONENTS = {
  scenarioExplorer: "scenario_explorer",
  moonTrek: "moon_trek",
  nomenclature: "nomenclature",
  map: "map",
  layerManager: "layer_manager",
  tools: "tools",
  jobsActivity: "jobs_activity",
  messages: "messages",
  assistantActivity: "assistant_activity",
  assistant: "assistant",
  assistantWorkspace: "assistant_workspace",
  notebook: "notebook",
  pythonEditor: "python_editor",
  textEditor: "text_editor",
  csvEditor: "csv_editor",
  imageViewer: "image_viewer",
} as const;

export type WorkspaceComponent = (typeof WORKSPACE_COMPONENTS)[keyof typeof WORKSPACE_COMPONENTS];
export type WorkspacePanelId =
  | "scenarioExplorer"
  | "moonTrek"
  | "map"
  | "nomenclature"
  | "layerManager"
  | "tools"
  | "jobsActivity"
  | "messages"
  | "assistantActivity"
  | "assistant";

export const WORKSPACE_REGION_IDS = {
  center: "workspace_region_center",
} as const;

export const ACTIVITY_BAR_ICON_NAMES = {
  [WORKSPACE_COMPONENTS.scenarioExplorer]: "folder-close",
  [WORKSPACE_COMPONENTS.layerManager]: "layers",
  [WORKSPACE_COMPONENTS.moonTrek]: "globe-network",
  [WORKSPACE_COMPONENTS.nomenclature]: "search",
  [WORKSPACE_COMPONENTS.tools]: "build",
  [WORKSPACE_COMPONENTS.jobsActivity]: "timeline-events",
  [WORKSPACE_COMPONENTS.assistantActivity]: "chat",
} as const;

type WorkspacePanelDefinition = {
  id: WorkspacePanelId;
  tabId: string;
  component: WorkspaceComponent;
  label: string;
  defaultRegionId?: string;
  defaultBorderLocation?: "left" | "right" | "bottom";
  fallbackTargetRegionId?: string;
  fallbackDockLocation?: DockLocation;
  siblingPanelIds?: WorkspacePanelId[];
  tabConfig?: Record<string, unknown>;
};

function svgDataUri(svg: string): string {
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
}

function activityIcon(path: string): string {
  return svgDataUri(
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="%23cdd8ea" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${path}</svg>`,
  );
}

const PANEL_ICONS = {
  scenarioExplorer: activityIcon('<path d="M3 6.5h6l2 2H21v9.5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M3 8.5h18"/>'),
  layerManager: activityIcon('<path d="M12 3 3 8l9 5 9-5-9-5Z"/><path d="m3 12 9 5 9-5"/><path d="m3 16 9 5 9-5"/>'),
  moonTrek: activityIcon('<circle cx="12" cy="12" r="8"/><path d="M9 9.5c1.2-1.8 4.6-2 6 .2"/><path d="M8 14.5c1.1 1.7 5.3 1.9 7 .1"/>'),
  nomenclature: activityIcon('<circle cx="10.5" cy="10.5" r="4.5"/><path d="m14 14 5 5"/><path d="M6 10.5h9"/><path d="M10.5 6v9"/>'),
  tools: activityIcon('<path d="m14.5 6.5 3 3"/><path d="m5 19 6.5-6.5 3 3L8 22H5z"/><path d="M14 4a3 3 0 0 1 4.2 4.2l-1.4 1.4-4.2-4.2z"/>'),
  jobsActivity: activityIcon('<path d="M4 5h16"/><path d="M4 12h10"/><path d="M4 19h7"/><circle cx="18" cy="12" r="2"/><circle cx="14" cy="19" r="2"/>'),
  messages: activityIcon('<path d="M5 6h14v9H9l-4 4z"/><path d="M8 10h8"/><path d="M8 13h6"/>'),
  assistantActivity: activityIcon('<path d="M4 5h16v10H8l-4 4z"/><path d="M8 9h8"/><path d="M8 12h8"/>'),
  assistant: activityIcon('<path d="M4 5h16v10H8l-4 4z"/><path d="M8 9h8"/><path d="M8 12h8"/>'),
  notebook: activityIcon('<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 7h6"/><path d="M9 11h6"/><path d="M9 15h4"/>'),
  pythonEditor: activityIcon('<path d="M8 7c0-1.7 1.3-3 3-3h2v4h-2c-1.1 0-2 .9-2 2v1h4v4h-2c-1.7 0-3-1.3-3-3"/><path d="M16 17c0 1.7-1.3 3-3 3h-2v-4h2c1.1 0 2-.9 2-2v-1h-4V9h2c1.7 0 3 1.3 3 3"/>'),
  textEditor: activityIcon('<path d="M6 4h12v16H6z"/><path d="M9 8h6"/><path d="M9 12h6"/><path d="M9 16h4"/>'),
  csvEditor: activityIcon('<rect x="4" y="5" width="16" height="14" rx="2"/><path d="M4 10h16"/><path d="M10 5v14"/><path d="M15 5v14"/>'),
  imageViewer: activityIcon('<rect x="4" y="5" width="16" height="14" rx="2"/><circle cx="9" cy="10" r="1.4"/><path d="m6 16 4-4 3 3 3-2 2 3"/>'),
} as const;

export const WORKSPACE_PANELS: Record<WorkspacePanelId, WorkspacePanelDefinition> = {
  assistantActivity: {
    id: "assistantActivity",
    tabId: "workspace_tab_assistant_activity",
    component: WORKSPACE_COMPONENTS.assistantActivity,
    label: "Assistant",
    defaultBorderLocation: "left",
    fallbackTargetRegionId: WORKSPACE_REGION_IDS.center,
    fallbackDockLocation: DockLocation.LEFT,
    siblingPanelIds: ["scenarioExplorer", "layerManager", "moonTrek", "tools"],
    tabConfig: {
      enableClose: false,
      icon: PANEL_ICONS.assistantActivity,
    },
  },
  scenarioExplorer: {
    id: "scenarioExplorer",
    tabId: "workspace_tab_scenario_explorer",
    component: WORKSPACE_COMPONENTS.scenarioExplorer,
    label: "Scenario Explorer",
    defaultBorderLocation: "left",
    fallbackTargetRegionId: WORKSPACE_REGION_IDS.center,
    fallbackDockLocation: DockLocation.LEFT,
    siblingPanelIds: ["layerManager", "moonTrek", "tools"],
    tabConfig: {
      enableClose: false,
      icon: PANEL_ICONS.scenarioExplorer,
    },
  },
  moonTrek: {
    id: "moonTrek",
    tabId: "workspace_tab_moon_trek",
    component: WORKSPACE_COMPONENTS.moonTrek,
    label: "Map Layers",
    defaultBorderLocation: "left",
    fallbackTargetRegionId: WORKSPACE_REGION_IDS.center,
    fallbackDockLocation: DockLocation.LEFT,
    siblingPanelIds: ["scenarioExplorer", "layerManager", "tools"],
    tabConfig: {
      enableClose: false,
      icon: PANEL_ICONS.moonTrek,
    },
  },
  nomenclature: {
    id: "nomenclature",
    tabId: "workspace_tab_nomenclature",
    component: WORKSPACE_COMPONENTS.nomenclature,
    label: "Nomenclature",
    defaultBorderLocation: "left",
    fallbackTargetRegionId: WORKSPACE_REGION_IDS.center,
    fallbackDockLocation: DockLocation.LEFT,
    siblingPanelIds: ["scenarioExplorer", "layerManager", "moonTrek", "tools"],
    tabConfig: {
      enableClose: false,
      icon: PANEL_ICONS.nomenclature,
    },
  },
  map: {
    id: "map",
    tabId: "workspace_tab_map",
    component: WORKSPACE_COMPONENTS.map,
    label: "Map",
    defaultRegionId: WORKSPACE_REGION_IDS.center,
    tabConfig: {
      enableClose: false,
      enableRenderOnDemand: false,
      enablePopout: false,
      enableRename: false,
    },
  },
  layerManager: {
    id: "layerManager",
    tabId: "workspace_tab_layer_manager",
    component: WORKSPACE_COMPONENTS.layerManager,
    label: "Layer Manager",
    defaultBorderLocation: "left",
    fallbackTargetRegionId: WORKSPACE_REGION_IDS.center,
    fallbackDockLocation: DockLocation.LEFT,
    siblingPanelIds: ["scenarioExplorer", "moonTrek", "tools"],
    tabConfig: {
      enableClose: false,
      icon: PANEL_ICONS.layerManager,
    },
  },
  tools: {
    id: "tools",
    tabId: "workspace_tab_tools",
    component: WORKSPACE_COMPONENTS.tools,
    label: "Tools",
    defaultBorderLocation: "left",
    fallbackTargetRegionId: WORKSPACE_REGION_IDS.center,
    fallbackDockLocation: DockLocation.LEFT,
    siblingPanelIds: ["scenarioExplorer", "layerManager", "moonTrek"],
    tabConfig: {
      enableClose: false,
      icon: PANEL_ICONS.tools,
    },
  },
  jobsActivity: {
    id: "jobsActivity",
    tabId: "workspace_tab_jobs_activity",
    component: WORKSPACE_COMPONENTS.jobsActivity,
    label: "Jobs",
    defaultBorderLocation: "left",
    fallbackTargetRegionId: WORKSPACE_REGION_IDS.center,
    fallbackDockLocation: DockLocation.LEFT,
    siblingPanelIds: ["scenarioExplorer", "layerManager", "moonTrek", "tools", "assistantActivity"],
    tabConfig: {
      enableClose: false,
      icon: PANEL_ICONS.jobsActivity,
    },
  },
  messages: {
    id: "messages",
    tabId: "workspace_tab_messages",
    component: WORKSPACE_COMPONENTS.messages,
    label: "Messages",
    defaultBorderLocation: "bottom",
    fallbackTargetRegionId: WORKSPACE_REGION_IDS.center,
    fallbackDockLocation: DockLocation.BOTTOM,
    tabConfig: { icon: PANEL_ICONS.messages },
  },
  assistant: {
    id: "assistant",
    tabId: "workspace_tab_assistant",
    component: WORKSPACE_COMPONENTS.assistant,
    label: "Assistant",
    defaultBorderLocation: "right",
    fallbackTargetRegionId: WORKSPACE_REGION_IDS.center,
    fallbackDockLocation: DockLocation.RIGHT,
    tabConfig: { icon: PANEL_ICONS.assistant },
  },
};

function createTabJson(definition: WorkspacePanelDefinition): IJsonTabNode {
  return {
    type: "tab",
    id: definition.tabId,
    name: definition.label,
    component: definition.component,
    enableClose: definition.id !== "map",
    enableRename: false,
    enablePopout: false,
    ...(definition.tabConfig || {}),
  };
}

function createBorderJson(
  location: "left" | "right" | "bottom",
  childPanels: WorkspacePanelId[],
  size: number,
  selectedIndex = 0,
): IJsonBorderNode {
  return {
    type: "border",
    location,
    selected: selectedIndex,
    size,
    minSize: location === "bottom" ? 180 : 260,
    children: childPanels.map((panelId) => createTabJson(WORKSPACE_PANELS[panelId])),
  };
}

function createCenterRegionJson(): IJsonTabSetNode {
  return {
    type: "tabset",
    id: WORKSPACE_REGION_IDS.center,
    weight: 100,
    selected: 0,
    active: true,
    enableClose: false,
    enableDrop: true,
    enableDrag: true,
    enableMaximize: true,
    enableDeleteWhenEmpty: false,
    children: [createTabJson(WORKSPACE_PANELS.map)],
  };
}

export const DEFAULT_WORKSPACE_LAYOUT_JSON: IJsonModel = {
  global: {
    rootOrientationVertical: false,
    splitterSize: 8,
    splitterEnableHandle: false,
    enableEdgeDock: true,
    enableRotateBorderIcons: false,
    borderEnableAutoHide: true,
    borderEnableDrop: true,
    borderMinSize: 180,
    borderSize: 300,
    tabBorderWidth: 48,
    tabEnableClose: true,
    tabEnableDrag: true,
    tabEnableRename: false,
    tabEnablePopout: false,
    tabEnablePopoutIcon: false,
    tabSetEnableClose: false,
    tabSetEnableDrag: true,
    tabSetEnableDrop: true,
    tabSetEnableMaximize: true,
    tabSetEnableTabStrip: true,
  },
  borders: [
    createBorderJson("left", ["scenarioExplorer", "layerManager", "moonTrek", "nomenclature", "tools", "jobsActivity", "assistantActivity"], 320, 0),
    createBorderJson("right", ["assistant"], 360, 0),
    createBorderJson("bottom", ["messages"], 260, 0),
  ],
  layout: {
    type: "row",
    id: "workspace_root_row",
    weight: 100,
    children: [createCenterRegionJson()],
  },
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isJsonTabNode(value: unknown): value is IJsonTabNode {
  return isRecord(value) && value.type === "tab";
}

function isJsonTabSetNode(value: unknown): value is IJsonTabSetNode {
  return isRecord(value) && value.type === "tabset" && Array.isArray(value.children);
}

function isJsonBorderNode(value: unknown): value is IJsonBorderNode {
  return isRecord(value) && value.type === "border" && Array.isArray(value.children);
}

function isJsonRowNode(value: unknown): value is IJsonRowNode {
  return isRecord(value) && value.type === "row" && Array.isArray(value.children);
}

function collectTabComponents(node: IJsonRowNode | IJsonTabSetNode | IJsonBorderNode, components: string[]): boolean {
  if (isJsonRowNode(node)) {
    return node.children.every((child) => {
      if (isJsonRowNode(child) || isJsonTabSetNode(child)) {
        return collectTabComponents(child, components);
      }
      return false;
    });
  }
  return node.children.every((child) => {
    if (!isJsonTabNode(child)) return false;
    components.push(String(child.component || ""));
    return true;
  });
}

function isKnownWorkspaceComponent(component: string): component is WorkspaceComponent {
  return Object.values(WORKSPACE_COMPONENTS).includes(component as WorkspaceComponent);
}

function countMapTabs(value: IJsonModel): number {
  const components: string[] = [];
  if (!collectTabComponents(value.layout, components)) return -1;
  for (const border of value.borders || []) {
    if (!isJsonBorderNode(border)) return -1;
    if (!collectTabComponents(border, components)) return -1;
  }
  if (components.some((component) => !isKnownWorkspaceComponent(component))) return -1;
  return components.filter((component) => component === WORKSPACE_COMPONENTS.map).length;
}

export function isValidWorkspaceLayoutJson(value: unknown): value is IJsonModel {
  if (!isRecord(value)) return false;
  if (!isJsonRowNode(value.layout)) return false;
  if (value.borders !== undefined && !Array.isArray(value.borders)) return false;
  if (isRecord(value.popouts) && Object.keys(value.popouts).length > 0) return false;
  return countMapTabs(value as IJsonModel) === 1;
}

export function cleanupLegacyWorkspaceLayoutKeys(storage: Storage | null | undefined): void {
  if (!storage) return;
  const keys: string[] = [];
  for (let index = 0; index < storage.length; index += 1) {
    const key = storage.key(index);
    if (key && key.startsWith(WORKSPACE_LAYOUT_STORAGE_PREFIX) && key !== WORKSPACE_LAYOUT_STORAGE_KEY) {
      keys.push(key);
    }
  }
  for (const key of keys) {
    storage.removeItem(key);
  }
}

export function loadWorkspaceLayoutJson(storage: Storage | null | undefined): IJsonModel {
  cleanupLegacyWorkspaceLayoutKeys(storage);
  if (!storage) return DEFAULT_WORKSPACE_LAYOUT_JSON;
  try {
    const raw = storage.getItem(WORKSPACE_LAYOUT_STORAGE_KEY);
    if (!raw) return DEFAULT_WORKSPACE_LAYOUT_JSON;
    const parsed = JSON.parse(raw) as unknown;
    return isValidWorkspaceLayoutJson(parsed) ? parsed : DEFAULT_WORKSPACE_LAYOUT_JSON;
  } catch {
    return DEFAULT_WORKSPACE_LAYOUT_JSON;
  }
}

export function createWorkspaceLayoutModel(storage: Storage | null | undefined): Model {
  return Model.fromJson(loadWorkspaceLayoutJson(storage));
}

export function saveWorkspaceLayoutModel(model: Model, storage: Storage | null | undefined): void {
  if (!storage) return;
  cleanupLegacyWorkspaceLayoutKeys(storage);
  storage.setItem(WORKSPACE_LAYOUT_STORAGE_KEY, JSON.stringify(model.toJson()));
}

export function resetWorkspaceLayoutModel(storage: Storage | null | undefined): Model {
  if (storage) {
    cleanupLegacyWorkspaceLayoutKeys(storage);
    storage.removeItem(WORKSPACE_LAYOUT_STORAGE_KEY);
  }
  return Model.fromJson(DEFAULT_WORKSPACE_LAYOUT_JSON);
}

function parentRegionIdForPanel(model: Model, tabId: string): string | null {
  const tabNode = model.getNodeById(tabId);
  const parent = tabNode?.getParent();
  return parent ? parent.getId() : null;
}

function defaultTargetIdForPanel(model: Model, definition: WorkspacePanelDefinition): string | undefined {
  if (definition.defaultRegionId) {
    return model.getNodeById(definition.defaultRegionId)?.getId();
  }
  if (definition.defaultBorderLocation) {
    const borderSet = model.getBorderSet();
    return borderSet.getBorders().find((border) => border.getLocation().getName() === definition.defaultBorderLocation)?.getId();
  }
  return undefined;
}

export function ensureWorkspacePanelOpen(model: Model, panelId: WorkspacePanelId): void {
  const definition = WORKSPACE_PANELS[panelId];
  if (model.getNodeById(definition.tabId)) {
    model.doAction(Actions.selectTab(definition.tabId));
    return;
  }

  const siblingParentId = (definition.siblingPanelIds || [])
    .map((siblingId) => parentRegionIdForPanel(model, WORKSPACE_PANELS[siblingId].tabId))
    .find((value): value is string => Boolean(value));

  const defaultTarget = defaultTargetIdForPanel(model, definition);
  const fallbackTarget = definition.fallbackTargetRegionId
    ? model.getNodeById(definition.fallbackTargetRegionId)?.getId()
    : undefined;

  const targetId = siblingParentId || defaultTarget || fallbackTarget || model.getFirstTabSet()?.getId();
  const location =
    siblingParentId || defaultTarget
      ? DockLocation.CENTER
      : definition.fallbackDockLocation || DockLocation.CENTER;

  if (!targetId) return;
  model.doAction(Actions.addNode(createTabJson(definition), targetId, location, -1, true));
}

type DynamicWorkspaceTabOptions = {
  id: string;
  name: string;
  component: WorkspaceComponent;
  targetRegionId?: string;
  icon?: string;
  config?: Record<string, unknown>;
  enableClose?: boolean;
  enableRenderOnDemand?: boolean;
};

export function ensureDynamicWorkspaceTab(model: Model, options: DynamicWorkspaceTabOptions): void {
  if (model.getNodeById(options.id)) {
    model.doAction(Actions.selectTab(options.id));
    return;
  }
  const targetId = model.getNodeById(options.targetRegionId || WORKSPACE_REGION_IDS.center)?.getId()
    || model.getFirstTabSet()?.getId();
  if (!targetId) return;
  model.doAction(
    Actions.addNode(
      {
        type: "tab",
        id: options.id,
        name: options.name,
        component: options.component,
        icon: options.icon,
        config: options.config,
        enableClose: options.enableClose ?? true,
        enableRename: false,
        enablePopout: false,
        enableRenderOnDemand: options.enableRenderOnDemand ?? true,
      },
      targetId,
      DockLocation.CENTER,
      -1,
      true,
    ),
  );
}

export const WORKSPACE_DYNAMIC_IDS = {
  notebookPrefix: "workspace_tab_notebook:",
  pythonEditorPrefix: "workspace_tab_python_editor:",
  textEditorPrefix: "workspace_tab_text_editor:",
  csvEditorPrefix: "workspace_tab_csv_editor:",
  imageViewerPrefix: "workspace_tab_image_viewer:",
  assistantExpanded: "workspace_tab_assistant_response_expanded",
  assistantWorkspace: "workspace_tab_assistant_workspace",
} as const;

export const WORKSPACE_DYNAMIC_ICONS = {
  notebook: PANEL_ICONS.notebook,
  pythonEditor: PANEL_ICONS.pythonEditor,
  textEditor: PANEL_ICONS.textEditor,
  csvEditor: PANEL_ICONS.csvEditor,
  imageViewer: PANEL_ICONS.imageViewer,
  assistant: PANEL_ICONS.assistant,
  assistantWorkspace: PANEL_ICONS.assistantActivity,
} as const;
