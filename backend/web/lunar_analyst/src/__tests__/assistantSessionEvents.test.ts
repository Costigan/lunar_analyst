import { describe, expect, it } from "vitest";
import {
  reduceAssistantStreamEvent,
  type AssistantDraftState,
} from "../hooks/useAssistantSession";

describe("useAssistantSession stream reducer", () => {
  const now = () => "2026-03-03T12:00:00.000Z";

  it("creates and appends draft deltas", () => {
    const started = reduceAssistantStreamEvent(
      { event: "assistant_turn_started", turn_id: "turn_1" },
      null,
      "session_1",
      now,
    );
    expect(started.nextDraft).toEqual({
      turnId: "turn_1",
      text: "Working...",
      createdAt: "2026-03-03T12:00:00.000Z",
    });

    const delta = reduceAssistantStreamEvent(
      { event: "assistant_delta", turn_id: "turn_1", data: { text_delta: "Hello" } },
      started.nextDraft,
      "session_1",
      now,
    );
    expect(delta.nextDraft).toEqual({
      turnId: "turn_1",
      text: "Hello",
      createdAt: "2026-03-03T12:00:00.000Z",
    });

    const delta2 = reduceAssistantStreamEvent(
      { event: "assistant_delta", turn_id: "turn_1", data: { text_delta: " world" } },
      delta.nextDraft as AssistantDraftState,
      "session_1",
      now,
    );
    expect(delta2.nextDraft?.text).toBe("Hello world");
  });

  it("clears draft and marks refresh on completion", () => {
    const decision = reduceAssistantStreamEvent(
      { event: "assistant_turn_completed", turn_id: "turn_1" },
      { turnId: "turn_1", text: "partial", createdAt: now() },
      "session_1",
      now,
    );
    expect(decision.nextDraft).toBeNull();
    expect(decision.refreshMessages).toBe(true);
    expect(decision.refreshSessions).toBe(true);
  });

  it("emits local assistant error message", () => {
    const decision = reduceAssistantStreamEvent(
      { event: "assistant_error", turn_id: "turn_2", data: { error: "boom" } },
      { turnId: "turn_2", text: "working", createdAt: now() },
      "session_1",
      now,
    );
    expect(decision.nextDraft).toBeNull();
    expect(decision.appendErrorMessage?.content).toBe("Error: boom");
    expect(decision.refreshSessions).toBe(true);
  });

  it("handles scenario-changed notifications", () => {
    const decision = reduceAssistantStreamEvent(
      {
        event: "assistant_scenario_changed",
        data: { scenario_id: "scn_new", dem_extent: [1, 2, 3, 4] },
      },
      null,
      "session_1",
      now,
    );
    expect(decision.scenarioId).toBe("scn_new");
    expect(decision.scenarioExtent).toEqual([1, 2, 3, 4]);
    expect(decision.refreshMessages).toBe(true);
    expect(decision.refreshSessions).toBe(true);
  });

  it("ignores placeholder scenario extents", () => {
    const decision = reduceAssistantStreamEvent(
      {
        event: "assistant_scenario_changed",
        data: { scenario_id: "scn_new", dem_extent: [-1, -1, 1, 1] },
      },
      null,
      "session_1",
      now,
    );
    expect(decision.scenarioId).toBe("scn_new");
    expect(decision.scenarioExtent).toBeNull();
  });

  it("does not reset draft on duplicate turn-started event", () => {
    const draft: AssistantDraftState = {
      turnId: "turn_1",
      text: "partial text",
      createdAt: "2026-03-03T11:00:00.000Z",
    };
    const decision = reduceAssistantStreamEvent(
      { event: "assistant_turn_started", turn_id: "turn_1" },
      draft,
      "session_1",
      now,
    );
    expect(decision.nextDraft).toBe(draft);
  });
});
