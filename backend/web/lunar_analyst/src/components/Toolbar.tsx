import React from "react";
import { Button, Navbar, Alignment, Tag, Popover, Menu, MenuItem } from "@blueprintjs/core";
import type { ThemeOption } from "../AppLayout";

type ToolbarProps = {
  activeScenarioText: string;
  statusText: string;
  onShowScenarioExplorer: () => void;
  onResetLayout: () => void;
  onReportAssistantBug: () => void;
  theme?: ThemeOption;
  onThemeChange?: (theme: ThemeOption) => void;
};

const THEME_LABELS: Record<ThemeOption, { label: string; icon: any }> = {
  dark: { label: "Dark", icon: "moon" },
  light: { label: "Light", icon: "flash" },
  "high-contrast": { label: "High Contrast", icon: "contrast" },
  ocean: { label: "Ocean", icon: "water" },
  forest: { label: "Forest", icon: "tree" },
  sepia: { label: "Sepia", icon: "tint" },
};

export default function Toolbar(props: ToolbarProps): JSX.Element {
  const {
    activeScenarioText,
    statusText,
    onShowScenarioExplorer,
    onResetLayout,
    onReportAssistantBug,
    theme = "dark",
    onThemeChange,
  } = props;

  const isDark = theme !== "light" && theme !== "sepia";
  const navBackground = isDark ? "rgba(8, 14, 24, 0.85)" : "rgba(255, 255, 255, 0.85)";
  const textColor = isDark ? "#f6f8ff" : "inherit";

  const themeMenu = (
    <Menu>
      {(Object.keys(THEME_LABELS) as ThemeOption[]).map((t) => (
        <MenuItem
          key={t}
          icon={THEME_LABELS[t].icon}
          text={THEME_LABELS[t].label}
          active={theme === t}
          onClick={() => onThemeChange?.(t)}
        />
      ))}
    </Menu>
  );

  const mainMenu = (
    <Menu>
      <MenuItem icon="folder-open" text="Show Scenario Explorer" onClick={onShowScenarioExplorer} />
      <MenuItem icon="reset" text="Reset Layout" onClick={onResetLayout} />
      <MenuItem icon="issue" text="Report Assistant Bug" onClick={onReportAssistantBug} />
    </Menu>
  );

  return (
    <Navbar className="toolbar" style={{ background: navBackground, backdropFilter: "blur(4px)" }}>
      <Navbar.Group align={Alignment.LEFT}>
        <Popover content={mainMenu} placement="bottom-start">
          <Button minimal icon="menu" className="toolbar-toggle">
            Menu
          </Button>
        </Popover>
        <Navbar.Divider />
        <Navbar.Heading className="title" style={{ color: textColor }}>
          Lunar Analyst <span style={{ opacity: 0.7, fontWeight: 400, marginLeft: 6 }}>Scenario Workspace</span>
        </Navbar.Heading>
      </Navbar.Group>
      <Navbar.Group align={Alignment.RIGHT}>
        <Tag
          minimal
          className="active-scenario"
          style={{
            background: isDark ? "rgba(255,255,255,0.1)" : "rgba(0,0,0,0.05)",
            color: textColor,
            border: "1px solid rgba(128,128,128,0.2)",
          }}
        >
          {activeScenarioText}
        </Tag>
        <Navbar.Divider />
        <span id="status" style={{ fontSize: "0.75rem", marginRight: "10px", opacity: 0.8 }}>
          {statusText}
        </span>

        <Popover content={themeMenu} placement="bottom-end">
          <Button minimal icon={THEME_LABELS[theme].icon} title="Switch theme" className="toolbar-toggle" />
        </Popover>

        <Navbar.Divider />
        <Button minimal icon="reset" onClick={onResetLayout} className="toolbar-toggle">
          Reset Layout
        </Button>
      </Navbar.Group>
    </Navbar>
  );
}
