import { afterEach, describe, expect, it, vi } from "vitest";
import {
  captureAssistantBugReport,
  createAssistantSession,
  createAssistantTurn,
  listAssistantProviderCatalog,
  listAssistantProviders,
  listAssistantSessions,
  resolveAssistantConfirmation,
} from "../services/assistantService";

describe("assistantService", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("lists sessions from API envelope", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ sessions: [{ session_id: "as_1", title: "A", created_at_utc: "t", updated_at_utc: "t", last_message_at_utc: null, policy: { always_allow_action_types: [] } }] }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const sessions = await listAssistantSessions();
    expect(sessions).toHaveLength(1);
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/assistant/sessions", expect.anything());
  });

  it("posts create session payload", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ session_id: "as_1", title: "Test", created_at_utc: "t", updated_at_utc: "t", last_message_at_utc: null, policy: { always_allow_action_types: [] } }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const session = await createAssistantSession("Test");
    expect(session.session_id).toBe("as_1");
    const [, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(options.method).toBe("POST");
    expect(JSON.parse(String(options.body))).toEqual({ title: "Test" });
  });

  it("posts turn and confirmation payloads", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ turn: { turn_id: "turn_1", session_id: "as_1", user_message_id: "msg_1", status: "completed", provider_id: null, model_id: null, created_at_utc: "t", updated_at_utc: "t", error: null, usage: {} }, assistant_message: null, confirmation: null, tool_calls: [] }), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ turn: { turn_id: "turn_1", session_id: "as_1", user_message_id: "msg_1", status: "completed", provider_id: null, model_id: null, created_at_utc: "t", updated_at_utc: "t", error: null, usage: {} }, assistant_message: null, confirmation: null, tool_calls: [] }), { status: 200 }),
      );
    vi.stubGlobal("fetch", fetchMock);
    await createAssistantTurn("as_1", "hello", "scn_a", null, true, "ollama", "qwen3.5:35b-a3b");
    await resolveAssistantConfirmation("as_1", "cnf_1", "allow_once");
    const [, turnOptions] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(String(turnOptions.body))).toEqual({
      prompt: "hello",
      scenario_id: "scn_a",
      constraints: null,
      base_layer_visible: true,
      provider_id: "ollama",
      model_id: "qwen3.5:35b-a3b",
      access_mode: null,
      thinking: null,
    });
    const [, confirmOptions] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(JSON.parse(String(confirmOptions.body))).toEqual({ decision: "allow_once" });
  });

  it("posts turn payload with access mode override", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({
        turn: {
          turn_id: "turn_1",
          session_id: "as_1",
          user_message_id: "msg_1",
          status: "completed",
          provider_id: "codex_cli",
          model_id: "gpt-5-codex",
          created_at_utc: "t",
          updated_at_utc: "t",
          error: null,
          usage: {},
        },
        assistant_message: null,
        confirmation: null,
        tool_calls: [],
      }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    await createAssistantTurn(
      "as_1",
      "hello",
      "scn_a",
      null,
      true,
      "codex_cli",
      "gpt-5-codex",
      "scenario_root",
    );
    const [, turnOptions] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(String(turnOptions.body))).toEqual({
      prompt: "hello",
      scenario_id: "scn_a",
      constraints: null,
      base_layer_visible: true,
      provider_id: "codex_cli",
      model_id: "gpt-5-codex",
      access_mode: "scenario_root",
      thinking: null,
    });
  });

  it("posts turn payload with thinking override", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({
        turn: {
          turn_id: "turn_1",
          session_id: "as_1",
          user_message_id: "msg_1",
          status: "completed",
          provider_id: "ollama",
          model_id: "gpt-oss:20b",
          created_at_utc: "t",
          updated_at_utc: "t",
          error: null,
          usage: {},
        },
        assistant_message: null,
        confirmation: null,
        tool_calls: [],
      }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    await createAssistantTurn("as_1", "hello", "scn_a", null, true, "ollama", "gpt-oss:20b", null, "high");
    const [, turnOptions] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(String(turnOptions.body))).toEqual({
      prompt: "hello",
      scenario_id: "scn_a",
      constraints: null,
      base_layer_visible: true,
      provider_id: "ollama",
      model_id: "gpt-oss:20b",
      access_mode: null,
      thinking: "high",
    });
  });

  it("posts assistant bug report payload", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({
        bug_report: {
          bug_report_id: "br_1",
          created_at_utc: "t",
          report_text: "it failed",
          assistant_session_id: "as_1",
          assistant_turn_id: "turn_1",
          assistant_provider_id: "codex_cli",
          assistant_model_id: "gpt-5-codex",
          scenario_id: "scn_a",
          assistant_context: {},
          program_state: { active_panel: "assistant", workspace_state: {} },
          log_excerpt: ["line 1"],
          redactions_applied: true,
        },
        bundle_path: "/tmp/bug-report.json",
      }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    await captureAssistantBugReport("as_1", {
      report_text: "it failed",
      program_state: {
        active_scenario_id: "scn_a",
        active_assistant_session_id: "as_1",
        active_assistant_turn_id: "turn_1",
        active_provider_id: "codex_cli",
        active_model_id: "gpt-5-codex",
        active_panel: "assistant",
        assistant_prompt_draft: "hello",
        workspace_state: { theme: "dark" },
      },
    });
    const [, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(options.method).toBe("POST");
    expect(JSON.parse(String(options.body))).toEqual({
      report_text: "it failed",
      program_state: {
        active_scenario_id: "scn_a",
        active_assistant_session_id: "as_1",
        active_assistant_turn_id: "turn_1",
        active_provider_id: "codex_cli",
        active_model_id: "gpt-5-codex",
        active_panel: "assistant",
        assistant_prompt_draft: "hello",
        workspace_state: { theme: "dark" },
      },
    });
  });

  it("lists provider catalog from API envelope", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({
        providers: [
          {
            provider_id: "ollama",
            kind: "local",
            execution_mode: "tool_loop",
            available: true,
            default_model: "qwen3.5:35b-a3b",
            models: ["qwen3.5:35b-a3b", "qwen2.5-coder:7b"],
            notes: "",
          },
        ],
      }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const providers = await listAssistantProviders();
    expect(providers).toHaveLength(1);
    expect(providers[0].provider_id).toBe("ollama");
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/assistant/providers", expect.anything());
  });

  it("lists provider catalog defaults from API envelope", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({
        default_provider_id: "openai",
        default_model_id: "gpt-4.1-mini",
        providers: [
          {
            provider_id: "openai",
            kind: "remote",
            execution_mode: "tool_loop",
            available: true,
            default_model: "gpt-4.1-mini",
            models: ["gpt-4.1-mini"],
            model_metadata: {
              "gpt-4.1-mini": {
                capabilities: [],
                thinking_mode: "none",
              },
            },
            notes: "",
          },
        ],
      }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const catalog = await listAssistantProviderCatalog();
    expect(catalog.default_provider_id).toBe("openai");
    expect(catalog.default_model_id).toBe("gpt-4.1-mini");
    expect(catalog.providers).toHaveLength(1);
    expect(catalog.providers[0].model_metadata?.["gpt-4.1-mini"]?.thinking_mode).toBe("none");
  });
});
