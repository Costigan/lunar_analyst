# ADR.0011: AI Assistant & Model Context Protocol (MCP) Integration

## Status
Accepted

## Context
Lunar Analyst provides a wide array of specialized lunar analysis tools, including job orchestration (horizons, hillshades), product catalog queries, and layer styling. To make these capabilities accessible via natural language and to support integration with external AI agents (like Codex CLI and Gemini CLI), a standardized tool-calling and interaction model was required.

The system needed to support:
1.  **Internal Assistant:** A built-in chat interface for direct user interaction.
2.  **External Agents:** CLI-based or remote agents that need to plan and execute multi-step analysis workflows.
3.  **Safety & Guardrails:** A robust confirmation policy for "mutating" actions (e.g., launching an expensive job or deleting a file).
4.  **Flexible Providers:** Support for local (Ollama) and remote (OpenAI, Anthropic, Google) LLM providers.

## Decision
We adopted the **Model Context Protocol (MCP)** as the authoritative integration layer for tool exposure and execution.

### 1. Unified Tool Registry
All capabilities are registered in a central `tool_registry.py`. This registry defines:
-   **Metadata:** Tool names, descriptions, and JSON-RPC argument schemas.
-   **Action Types:** Each tool is mapped to an `AssistantConfirmationActionType` (e.g., `launch_job`, `update_layer_state`, `import_file`).
-   **Implementation:** Tools are thin wrappers around existing backend services (`scenario_service`, `job_service`, etc.) to ensure consistent business logic.

### 2. MCP Transport
Lunar Analyst exposes its tool registry via three MCP transports:
-   **HTTP JSON-RPC:** For direct request/response cycles (`/api/v1/mcp`).
-   **SSE (Server-Sent Events):** For persistent web-client or external agent streaming (`/api/v1/mcp/sse`).
-   **stdio:** For local process integration (via `python -m backend.tools.run_mcp_server`).

### 3. Assistant Interaction Model
The built-in assistant supports two execution modes:
-   **`tool_loop`:** The backend manages an iterative loop where the LLM proposes tool calls, the system executes them (subject to confirmation), and the results are fed back to the model.
-   **`external_mcp_agent`:** The backend launches a subprocess (e.g., `codex` or `gemini`) and provides it with MCP server credentials. The external agent then plans and executes tools autonomously.

### 4. Mutation Safety & Confirmation Policy
To prevent unintended actions, the system enforces a strict confirmation gate:
-   **Read-Only Tools:** (e.g., `scenario.list`, `product.files`) execute immediately.
-   **Mutating Tools:** Require an explicit `_confirmed: true` argument in the MCP call.
-   **Internal UI Gate:** For the built-in assistant, mutating calls pause the turn and present a "Confirmation Required" UI to the user.
-   **Policy Persistence:** Users can authorize an action "Once," "Always allow this type in this session," or "Deny."

### 5. Access Modes for External Agents
External CLI agents can be launched in two modes:
-   **`mcp_only`:** The agent has no direct filesystem access; it must interact solely through MCP tools.
-   **`scenario_root`:** The agent's working directory is set to the active scenario's root (under a validated allowlist). This allows the agent to read/write files directly while still using MCP for backend orchestration.

## Consequences
-   **Consistency:** Both the UI assistant and external CLI agents use the exact same tool definitions and implementation.
-   **Extensibility:** New features can be exposed to all AI interfaces simultaneously by adding a single entry to the tool registry.
-   **Security:** The confirmation policy provides a central point for auditing and governing AI actions.
-   **Decoupling:** The backend remains the authoritative control plane, while LLMs are treated as replaceable "reasoning engines."
-   **Complexity:** Managing session state (message history, confirmation policies, and runtime access modes) adds significant statefulness to the backend.
