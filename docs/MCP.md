# Drive Comfy Commander with MCP

The bundled MCP server is a narrow agent interface over the local controller.
It never exposes credentials and it has no tools for rentals, credential
changes, LoRA uploads, node installation, or destructive queue administration.

## Local stdio setup

Follow [installation and startup](STARTUP.md), complete the password wizard, and
start Commander. Then point an
MCP-capable client at the Python executable inside this clone:

```json
{
  "mcpServers": {
    "comfy-commander": {
      "command": "C:\\path\\to\\comfy-commander\\.venv\\Scripts\\python.exe",
      "args": ["-m", "backend.app.mcp_server"],
      "cwd": "C:\\path\\to\\comfy-commander"
    }
  }
}
```

Read, analysis, profile, and preparation tools work immediately against this
installation's local database. Set the client's working directory to the clone
and restart the MCP subprocess after changing configuration. An LLM/OpenRouter
key is not required for these tools. To permit job
queueing, add this environment entry to that MCP server configuration:

```json
"env": {"COMMANDER_MCP_ALLOW_SUBMIT": "1"}
```

The queue tool still requires the exact confirmation phrase shown in its schema.
Submitting a job never rents a GPU. A timed-out submission must be reconciled by
job ID before retrying.

## Verify the advertised transport

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest tests/test_mcp.py tests/test_mcp_stdio.py -q
```

The stdio regression starts the real MCP subprocess with an isolated empty
database, initializes a client session, discovers all six tools, reads status,
profiles and the profiles resource, then verifies a submission is rejected
without local opt-in. It creates no jobs, credentials or GPU rentals. A client
with submission enabled still needs an authorized render and an online worker;
transport discovery alone is not a render smoke test.

## Private Streamable HTTP

For another controller on the same tailnet, start the MCP server separately:

```powershell
$env:COMMANDER_MCP_TRANSPORT='streamable-http'
$env:COMMANDER_MCP_PORT='8015'
.\.venv\Scripts\python.exe -m backend.app.mcp_server
```

Keep it bound to localhost and publish it through a dedicated Tailscale Serve
HTTPS port. This first competition build relies on the tailnet trust boundary;
do not expose it with Funnel or a router port forward. Public/internet hosting
needs MCP OAuth protected-resource metadata and scoped tokens.

## Tool boundaries

- `commander_status`, `list_profiles`, `analyze_workflow_json`, and
  `get_job_status` are read-only.
- `prepare_character_replacement` creates a validated graph but queues nothing.
- `submit_workflow` is additive, locally opt-in, and confirmation-gated.
- Provider keys are read only by the controller, never by the MCP client.
- Arbitrary workflow JSON is untrusted and still passes the controller's graph,
  model, runtime, and path validation.
