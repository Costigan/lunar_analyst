# LLM Assistant Hand Testing

## 1. Scope
This checklist validates the implemented LLM assistant + MCP integration in Lunar Analyst:
- Assistant prose commands (read-only and mutating).
- Assistant capability and artifact descriptions.
- Confirmation policy behavior.
- Session persistence/resume and context compaction.
- Local/remote provider plumbing.
- MCP HTTP and stdio tool access.

## 2. Preconditions
1. Use the required Python environment:
```powershell
cmd /c "D:\projects\env_311\Scripts\activate.bat && python --version"
```
2. Confirm assistant config is enabled in `config/lunar_analyst.toml`:
- `[backend.llm].enabled = true`
- `[backend.mcp].enabled = true`
3. If testing local Ollama, ensure Ollama is running and model is available:
```powershell
ollama list
```

## 3. Start Lunar Analyst
1. Start backend from repo root:
```powershell
cmd /c "D:\projects\env_311\Scripts\activate.bat && python -m uvicorn backend.api.app:app --host 127.0.0.1 --port 8000 --reload"
```
2. Open the app:
- `http://127.0.0.1:8000/lunar_analyst/`

## 4. UI Hand Tests

### HT-01: Assistant Panels Render
1. Open the right pane.
2. Verify two sections exist:
- `Assistant Input`
- `Assistant Response`

Expected:
- Both sections are visible and usable.

### HT-02: Session Create/Select
1. In `Assistant Input`, create a new session.
2. Select a different session (if present), then switch back.

Expected:
- Session list updates.
- Selected session controls current message history in `Assistant Response`.

### HT-03: Capabilities Description (Read-Only)
1. Submit prompt:
```text
describe capabilities
```

Expected:
- Turn completes without confirmation prompt.
- Assistant response describes Lunar Analyst capabilities.

### HT-03B: Scenario Switch via Assistant
1. Submit prompt:
```text
set scenario <scenario name fragment>
```

Expected:
- If uniquely matched, assistant switches scenario immediately.
- Scenario Explorer selection updates.
- Map zooms to scenario DEM extent.
- Assistant history contains an explicit scenario-change audit/system message.
- If ambiguous, assistant returns candidate scenarios and asks to disambiguate.
- If no match, scenario remains unchanged and assistant suggests candidates.

### HT-04: Mutating Command Requires Confirmation
1. Submit prompt:
```text
launch job ping {"message":"hello from assistant"}
```

Expected:
- `Confirmation Required` UI appears.
- Action type corresponds to job launch.

### HT-05: Confirmation Decisions
1. In confirmation UI, click `Allow Once`.
2. Submit the same prompt again.
3. Click `Always Allow Type`.
4. Submit the same prompt again.
5. Submit one more mutating prompt and click `Deny`.

Expected:
- `Allow Once`: executes once.
- `Always Allow Type`: subsequent same action type in this session runs without confirmation.
- `Deny`: assistant reports no changes made.

### HT-06: Artifact Description
1. Submit prompt:
```text
list products
```
2. From returned product id, submit:
```text
files for product <product_id>
```
3. From returned file id, submit one:
```text
describe geotiff file <file_id>
```
4. If CSV exists, submit:
```text
describe table file <file_id>
```
5. If plot/image exists, submit:
```text
describe plot file <file_id>
```

Expected:
- Assistant returns concise descriptions with key stats/warnings/source context when available.

### HT-06B: Script/Notebook List + Run
1. Submit:
```text
list scripts
```
2. Submit:
```text
run script \"<relative/path.py>\"
```
3. Submit:
```text
get logs <run_id> head 20 tail 40 combined
```

Expected:
- Script list returns scenario-local recursive `.py` entries.
- First run asks for confirmation (script-specific approval scope).
- Run returns `run_id`.
- Log retrieval returns head/tail slices and total size/line metadata.

### HT-07: Session Resume and Persistence
1. Refresh browser page.
2. Re-open same assistant session.
3. Restart backend process.
4. Re-open app and same session.

Expected:
- Session and messages remain available after refresh and backend restart.

### HT-08: Compaction
1. In session list, click `Compact` for an active session with multiple turns.

Expected:
- No error.
- A new `system` summary message appears in history for compacted context.

## 5. Assistant API Hand Tests

### API-01: Provider Catalog
```powershell
Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8000/api/v1/assistant/providers"
```

Expected:
- Returns configured providers and model lists.

### API-02: Session + Turn + Confirmation Flow
1. Create session:
```powershell
$session = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/assistant/sessions" -ContentType "application/json" -Body '{"title":"API Hand Test"}'
$sid = $session.session_id
```
2. Start mutating turn:
```powershell
$turn = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/assistant/sessions/$sid/turns" -ContentType "application/json" -Body '{"prompt":"launch job ping {\"message\":\"api\"}"}'
$cid = $turn.confirmation.confirmation_id
```
3. Approve:
```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/assistant/sessions/$sid/confirmations/$cid" -ContentType "application/json" -Body '{"decision":"allow_once"}'
```

Expected:
- First turn returns `confirmation_required`.
- Confirmation approval returns completed turn and assistant message.

### API-03: Remote Provider + Usage/Cache Telemetry (Optional)
1. Enable one remote provider in `config/lunar_analyst.toml` and set its API key env var (for example `OPENAI_API_KEY`).
2. Create a turn with explicit provider:
```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/assistant/sessions/$sid/turns" -ContentType "application/json" -Body '{"prompt":"Describe capabilities briefly.","provider_id":"openai"}'
```

Expected:
- Completed turn includes usage fields under `turn.usage`, including:
- `prompt_tokens`
- `completion_tokens`
- `cached_prompt_tokens`
- `cache_attempted`
- `cache_applied`

## 6. MCP Hand Tests

### MCP-01: HTTP Initialize
```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/mcp" -ContentType "application/json" -Body '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'
```

Expected:
- JSON-RPC result with `serverInfo.name = "lunar-analyst-mcp"`.

### MCP-02: HTTP List Tools
```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/mcp" -ContentType "application/json" -Body '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
```

Expected:
- Tool list includes read-only and mutating tools.
- Mutating tools include confirmation annotation metadata.

### MCP-03: HTTP Call Read-Only Tool
```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/mcp" -ContentType "application/json" -Body '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"capabilities.describe","arguments":{}}}'
```

Expected:
- `result.isError = false`
- Structured content returned.

### MCP-04: HTTP Mutating Tool Confirmation Contract
1. Call mutating tool without confirmation flag:
```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/mcp" -ContentType "application/json" -Body '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"job.launch","arguments":{"handler_name":"ping","params":{"message":"mcp"}}}}'
```
2. Call same tool with explicit confirmation marker:
```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/mcp" -ContentType "application/json" -Body '{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"job.launch","arguments":{"_confirmed":true,"handler_name":"ping","params":{"message":"mcp"}}}}'
```

Expected:
- First call returns JSON-RPC error `confirmation_required`.
- Second call succeeds.

### MCP-05: stdio Transport
```powershell
@'
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}
{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"capabilities.describe","arguments":{}}}
'@ | cmd /c "D:\projects\env_311\Scripts\activate.bat && python -m backend.tools.run_mcp_server"
```

Expected:
- One JSON response per input line on stdout.

## 7. Quick Regression Commands
1. Backend assistant/MCP tests:
```powershell
cmd /c "D:\projects\env_311\Scripts\activate.bat && set PYTHONDONTWRITEBYTECODE=1 && python -m pytest backend/tests/contract/test_phase6_assistant_api.py backend/tests/contract/test_phase6_assistant_ws.py backend/tests/contract/test_phase6_mcp_http.py backend/tests/contract/test_phase6_openapi_assistant.py backend/tests/worker/test_assistant_session_store.py backend/tests/worker/test_assistant_policy_service.py backend/tests/worker/test_assistant_token_cache.py backend/tests/worker/test_mcp_tool_registry.py -q"
```
2. Frontend tests:
```powershell
npm run test
```
3. Frontend build:
```powershell
npm run build:map
```

## 8. Notes
- Assistant timestamps use UTC string format `YYYY-MM-DDTHH-MM-SS`.
- Assistant store path is configured by `[backend.llm].session_store_path`.
- If provider calls fail, verify provider enablement, model name, and required API key env var.
