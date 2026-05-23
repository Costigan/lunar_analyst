import type { CSSProperties, Dispatch, RefObject, SetStateAction } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

export type ResizeSide = "left" | "right" | "assistant_horizontal" | "assistant_vertical" | null;

const LEFT_MIN = 240;
const LEFT_MAX = 760;
const RIGHT_MIN = 260;
const RIGHT_MAX = 860;
const ASSISTANT_DOCK_MIN = 180;
const ASSISTANT_SPLIT_MIN = 20;
const ASSISTANT_SPLIT_MAX = 80;
const NARROW_BREAKPOINT = 767;

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export type WorkspaceLayoutState = {
  isNarrow: boolean;
  leftOpen: boolean;
  rightOpen: boolean;
  leftWidth: number;
  rightWidth: number;
  assistantDockHeight: number;
  assistantSplitPercent: number;
  assistantDockGridRef: RefObject<HTMLDivElement | null>;
  setLeftOpen: Dispatch<SetStateAction<boolean>>;
  setRightOpen: Dispatch<SetStateAction<boolean>>;
  startLeftResize: () => void;
  startRightResize: () => void;
  startAssistantVerticalResize: () => void;
  startAssistantHorizontalResize: () => void;
  workspaceStyle: CSSProperties;
  trekSidePaneStyle: CSSProperties | undefined;
  appShellStyle: CSSProperties;
};

export function useWorkspaceLayout(): WorkspaceLayoutState {
  const [isNarrow, setIsNarrow] = useState(() => window.innerWidth <= NARROW_BREAKPOINT);
  const [leftOpen, setLeftOpen] = useState(() => window.innerWidth > NARROW_BREAKPOINT);
  const [rightOpen, setRightOpen] = useState(() => window.innerWidth > NARROW_BREAKPOINT);
  const [leftWidth, setLeftWidth] = useState(320);
  const [rightWidth, setRightWidth] = useState(320);
  const [assistantDockHeight, setAssistantDockHeight] = useState(() =>
    clamp(Math.round(window.innerHeight * 0.34), ASSISTANT_DOCK_MIN, Math.max(ASSISTANT_DOCK_MIN, window.innerHeight - 180)),
  );
  const [assistantSplitPercent, setAssistantSplitPercent] = useState(50);
  const [resizeSide, setResizeSide] = useState<ResizeSide>(null);
  const assistantDockGridRef = useRef<HTMLDivElement | null>(null);

  const startLeftResize = useCallback(() => {
    setResizeSide("left");
  }, []);

  const startRightResize = useCallback(() => {
    setResizeSide("right");
  }, []);

  const startAssistantVerticalResize = useCallback(() => {
    setResizeSide("assistant_vertical");
  }, []);

  const startAssistantHorizontalResize = useCallback(() => {
    setResizeSide("assistant_horizontal");
  }, []);

  useEffect(() => {
    const onResize = (): void => {
      const narrow = window.innerWidth <= NARROW_BREAKPOINT;
      setIsNarrow(narrow);
      if (!narrow) {
        setLeftOpen(true);
        setRightOpen(true);
      }
      const maxDock = Math.max(ASSISTANT_DOCK_MIN, window.innerHeight - 180);
      setAssistantDockHeight((prev) => clamp(prev, ASSISTANT_DOCK_MIN, maxDock));
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    if (!resizeSide) return;
    const onMove = (event: MouseEvent): void => {
      if (resizeSide === "left") {
        setLeftWidth(clamp(event.clientX, LEFT_MIN, LEFT_MAX));
        return;
      }
      if (resizeSide === "right") {
        setRightWidth(clamp(window.innerWidth - event.clientX, RIGHT_MIN, RIGHT_MAX));
        return;
      }
      if (resizeSide === "assistant_vertical") {
        const maxDock = Math.max(ASSISTANT_DOCK_MIN, window.innerHeight - 180);
        setAssistantDockHeight(clamp(window.innerHeight - event.clientY, ASSISTANT_DOCK_MIN, maxDock));
        return;
      }
      if (resizeSide === "assistant_horizontal") {
        const rect = assistantDockGridRef.current?.getBoundingClientRect();
        if (!rect || rect.width <= 0) return;
        const ratio = ((event.clientX - rect.left) / rect.width) * 100;
        setAssistantSplitPercent(clamp(ratio, ASSISTANT_SPLIT_MIN, ASSISTANT_SPLIT_MAX));
      }
    };
    const onUp = (): void => {
      setResizeSide(null);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [resizeSide]);

  const workspaceStyle = useMemo(() => {
    if (isNarrow) {
      return { gridTemplateColumns: "1fr" } as CSSProperties;
    }
    const leftCol = leftOpen ? `${leftWidth}px` : "0px";
    const leftResizer = leftOpen ? "8px" : "0px";
    const rightResizer = rightOpen ? "8px" : "0px";
    const rightCol = rightOpen ? `${rightWidth}px` : "0px";
    return {
      gridTemplateColumns: `${leftCol} ${leftResizer} 1fr ${rightResizer} ${rightCol}`,
    } as CSSProperties;
  }, [isNarrow, leftOpen, rightOpen, leftWidth, rightWidth]);

  const trekSidePaneStyle = useMemo(() => {
    if (isNarrow) return undefined;
    const leftOffset = leftOpen ? leftWidth + 16 : 8;
    return { left: `${leftOffset}px` } as CSSProperties;
  }, [isNarrow, leftOpen, leftWidth]);

  const appShellStyle = useMemo(
    () => ({
      gridTemplateRows: `auto minmax(0, 1fr) 8px ${assistantDockHeight}px`,
    }),
    [assistantDockHeight],
  );

  return {
    isNarrow,
    leftOpen,
    rightOpen,
    leftWidth,
    rightWidth,
    assistantDockHeight,
    assistantSplitPercent,
    assistantDockGridRef,
    setLeftOpen,
    setRightOpen,
    startLeftResize,
    startRightResize,
    startAssistantVerticalResize,
    startAssistantHorizontalResize,
    workspaceStyle,
    trekSidePaneStyle,
    appShellStyle,
  };
}
