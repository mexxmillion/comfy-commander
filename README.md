# Comfy Commander by Tensor Master

[![License: AGPL v3 or later](https://img.shields.io/badge/License-AGPL_v3_or_later-663399.svg)](LICENSE)
[![Tests](https://github.com/mexxmillion/tensor-master-community/actions/workflows/ci.yml/badge.svg)](https://github.com/mexxmillion/tensor-master-community/actions/workflows/ci.yml)

**Run ComfyUI work locally or on rented GPUs from one polished web app—and let
Codex, Claude, or another agent drive the safe parts through MCP.**

Comfy Commander is a self-hosted control plane for ComfyUI. It keeps jobs,
workers, curated model profiles, provider credentials, cost limits, and render
receipts behind one interface that works on desktop or phone.

It is an independent community project built on ComfyUI, not an official
Comfy.org product. The Tensor Master name remains on the execution engine and
API internals so existing workers and integrations do not break.

The Community Edition intentionally stays focused:

- Qwen Image 2.1 image generation and reference editing
- MiniMax H3 Ref2VA INT8, Singularity and FastH3 Omni profiles
- native and LightX2V 4/8-step Ref2V Turbo recipes
- RunPod and Vast.ai worker launch controls with explicit cost limits
- optional Hugging Face credentials plus pinned personal-LoRA deployment
- a BYOK OpenRouter studio copilot with Gemini and DeepSeek Flash choices
- a reviewed SAM3 to H3 character-replacement workflow
- a bundled MCP server for status, workflow analysis, preparation, submission,
  and job monitoring

Private identities, Eros, Dolores and other studio-only assets are not part of
this repository. A user's own LoRAs can be registered at runtime without being
committed to the project.

## Run it in five minutes

Requirements: Windows 10/11, Git, and Python 3.12. A GPU is **not** required to
open the controller or complete onboarding.

```powershell
git clone https://github.com/mexxmillion/tensor-master-community.git
cd tensor-master-community
powershell -ExecutionPolicy Bypass -File .\tools\first_run.ps1
```

Open [http://127.0.0.1:8000/setup](http://127.0.0.1:8000/setup), then follow the
five screens:

1. Create the local admin password.
2. Choose local, cloud, or hybrid compute.
3. Connect only the providers you want.
4. Review the Qwen Image and MiniMax H3 starter profiles.
5. Open the command center.

First run starts with **no credentials, no jobs, no workers, no private models,
and no cloud spend**. Connecting RunPod or Vast.ai validates a credential; it
does not rent anything. GPU rental always has a separate offer, price, lifetime,
and confirmation step.

Ref2V defaults to the matching LightX2V 4-step or 8-step LoRA at strength 1.0,
using Euler / simple sampling. Sparse attention uses ComfyUI Model Sparse Attention with SLA, VSA or SOL. It is
off by default for Ref2V; FastH3 Omni enables VSA by default. Cloud Ref2V
profiles include both pinned LoRAs; local workers need the corresponding files
in their ComfyUI `models/loras` directory. Saved recipes retain their recorded
LoRA and VSA selections.

Cloud worker packaging currently requires a working local ComfyUI installation
with the runtime node sources used by `runtime/h3/build_local.ps1`. Before
starting the controller, set `COMFYUI_ROOT` to that installation's directory
(containing `custom_nodes`) and `COMFYUI_PYTHON` to its Python executable.
Restart the web server after changing these environment variables. The
controller's own Python environment is not a substitute for the ComfyUI runtime.
Missing packaging prerequisites stop the launch before a GPU rental starts.

FFmpeg for video previews is installed with Commander through `imageio-ffmpeg`.
An existing system FFmpeg is preferred when available.

### Tested hardware floor

The curated profiles are usable on tested 16 GB GPUs, including RTX 5070 Ti and
RTX 5080-class cards with 32 GB system RAM. A 24 GB GPU is recommended for more
headroom, not required. Long 15–20 second video jobs around 1.2 MP may exhaust
memory on a 16 GB/32 GB machine; shorten the clip, reduce resolution, or choose
a roomier worker.

### Docker controller demo

Docker is an alternative controller-only launch:

```bash
cp .env.example .env
docker compose up --build
```

The published port remains localhost-only. Do not expose it directly through a
router.

## Test it as a completely new user

Use a fresh clone on another computer and run `tools/first_run.ps1`. Do not copy
the first machine's `state/`, `.env`, `.venv`, or `models/` directories. This
tests the real onboarding path:

- hardware and local ComfyUI detection;
- password creation and reconnect;
- provider-key validation or environment-variable discovery;
- empty worker/model/job states;
- mobile access through that machine's own Tailscale hostname;
- MCP discovery from an agent running on that machine.

After completing the local password screen, add provider credentials only if
you want to test those paths. Existing provider keys can be reused; they do not
need to be regenerated.

## Open it securely on a phone

The recommended personal deployment uses
[Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve). Keep the
controller bound to `127.0.0.1:8000`; Serve adds a private HTTPS `*.ts.net` URL
that is reachable only through the tailnet and remains subject to its access
rules.

Install Tailscale on the controller and phone, sign both into the same tailnet,
create the Tensor Master password once on the host, then run:

```powershell
.\tools\share_phone.ps1
```

The equivalent direct command is:

```powershell
tailscale serve --bg --yes http://127.0.0.1:8000
tailscale serve status
```

Alternatively, open **Setup & Models → Phone access**, sign in, and click
**Enable private phone access**. The section shows a Copy URL button once
Tailscale Serve points to this controller. Existing HTTPS routes are preserved.

Open the printed or copied HTTPS URL on the phone and sign in. Do not use Telnet, expose
port 8000 on a router, change the Compose binding to all interfaces, or use
Tailscale Funnel for normal private access. Funnel makes the service public.
See [the complete phone-access and troubleshooting guide](docs/PHONE_ACCESS.md).

## First-run onboarding

The setup page handles four independent credentials:

| Service | Why it is used | Required? |
|---|---|---|
| RunPod | launch compatible GPU workers | one provider is optional |
| Vast.ai | search and launch compatible marketplace workers | one provider is optional |
| Hugging Face | gated SAM3/model downloads and the user's own repos | recommended for character replacement |
| OpenRouter | prompt enhancement on the controller | optional |

Keys are validated server-side, written to the configured private credential
directory with restrictive permissions, and never returned by the API. Do not
put credentials in prompts, Comfy graphs, recipes or agent instructions.

Keys may instead come from system or container environment variables. These
are read at process startup and take precedence over private files:

| Service | Environment variable |
|---|---|
| RunPod | `RUNPOD_API_KEY` |
| Vast.ai | `VAST_API_KEY` |
| Hugging Face | `HF_TOKEN` (or `HUGGINGFACE_TOKEN`) |
| OpenRouter | `OPENROUTER_API_KEY` |

The setup API reports only `environment` or `private file` as the source; it
never returns the value. Removing a saved local credential cannot remove a
system-provided variable. Restart the controller after changing its environment.

`/setup` remains available after onboarding as **Setup & Models**. An admin can
replace or remove locally stored credentials there. Provider-side revocation is
still performed at the provider.

## Personal LoRAs and cloud workers

Setup & Models supports two explicit routes:

- choose a LoRA already under a configured ComfyUI `models/loras` directory,
  upload it to the user's Hugging Face repository, verify the remote copy, and
  pin it for selected community profiles;
- register an existing Hugging Face LoRA by repository, file and immutable
  commit SHA.

The private runtime manifest stores only repository coordinates, immutable
revision, size, SHA-256 and selected profiles. Tokens and model bytes are never
stored in Git. New runtime bundles include the manifest; workers download the
LoRA into their own `models/loras` tree. Already-running workers do not change.
Unregistering removes the entry from future workers but deletes neither local
nor Hugging Face files.

For Docker, set `COMFYUI_MODELS_DIR` in `.env` to a host models directory. It is
mounted read-only; an upload is a deliberate copy to Hugging Face, not a local
move or deletion.

## Built-in studio copilot

The **AI** button opens the same copilot on the dashboard and Setup & Models.
It uses the controller-owned OpenRouter credential and lets the user choose:

- `google/gemini-3.8-flash` for a broad multimodal studio planner;
- `deepseek/deepseek-v4.1-flash-1017` for an economical agent workflow.

The model never receives the OpenRouter key. It can inspect an attached ComfyUI
JSON workflow, identify node classes and model references, and draft a custom
deployment profile. Unknown custom-node repositories, licenses, model sources,
checksums and download paths stay marked `unresolved`; they are not guessed.

The copilot can propose queueing the attached graph, open the correct rental
profile, or navigate to setup. Proposals appear as confirmation cards. Graphs
still pass the normal node/model allowlists, and rentals still require a live
offer plus the existing price, fleet and lifetime confirmations. There is no
shell tool and no automatic credential, LoRA-upload or rental action.

## Character replacement

The public workflow uses the source performance without asking H3 to infer the
actor from image anchors:

1. Prepare scene-matched identity and wardrobe references with Qwen Image.
2. Normalize the source once and plan non-overlapping frame intervals.
3. Upload an interval, then call
   `POST /api/v1/community/character-replacement/prepare` with its
   `h3_mobile/...` filename and a precise description of the performer.
4. Submit the returned graph to `POST /api/v1/jobs`.
5. Visually review both the SAM3 mask and target-only inverted guide. Check
   ownership at occlusions, limbs, exits, returns and nearby people.
6. Use the accepted guide as the aligned H3 Ref2VA video reference. The default
   path uses no `MiniMaxH3AddGuide` image nodes.
7. Render exact intervals, trim padding, inspect expressions, choreography and
   seams, concatenate, and mux the original continuous audio.

The stop after SAM3 is intentional: a completed track is not automatically an
accepted track. SAM3 weights require accepting their upstream license on
Hugging Face.

## Drive it with Codex, Claude, or another MCP agent

The repository includes a real MCP server. After the controller is installed,
add this local stdio server to your MCP client, replacing the path:

```json
{
  "mcpServers": {
    "comfy-commander": {
      "command": "C:\\path\\to\\tensor-master-community\\.venv\\Scripts\\python.exe",
      "args": ["-m", "backend.app.mcp_server"],
      "cwd": "C:\\path\\to\\tensor-master-community"
    }
  }
}
```

The agent can inspect readiness and profiles, analyze untrusted workflow JSON,
prepare the character-replacement graph, and monitor known jobs. Queue mutation
is disabled by default. To enable it, add
`"env": {"COMMANDER_MCP_ALLOW_SUBMIT": "1"}` to that server entry; every
submission still requires the exact confirmation phrase exposed by the tool and
still passes the controller's graph/model/path validation.

MCP cannot rent GPUs, change credentials, upload LoRAs, install custom nodes, or
delete queue entries in this release. Those remain authenticated human actions
in the web UI. See [the complete MCP setup and security guide](docs/MCP.md).

Repository behavior is in [AGENTS.md](AGENTS.md). A reusable operational skill
lives in [.agents/skills/tensor-master-community/SKILL.md](.agents/skills/tensor-master-community/SKILL.md).
An agent can discover the narrow API at:

- `GET /api/v1/community/agent-manifest`
- `GET /api/v1/community/profiles`
- `GET /api/v1/community/workflows/character-replacement`
- `GET /api/v1/agent/models`
- `GET /health`

The normal submission loop is: inspect profiles, prepare or supply an
allowlisted graph, submit once, retain the returned job ID, poll that job, then
review outputs. A timed-out submission may have succeeded, so reconcile before
retrying. Rentals always require an authenticated human session and explicit
price and lifetime limits.

The included local CLI reads the controller credential without printing it:

```powershell
python scripts/tensor_master_client.py profiles
python scripts/tensor_master_client.py prepare-replacement --source h3_mobile/shot.mkv --target "performer in the red jacket" --output prepared.json
python scripts/tensor_master_client.py submit job.json
python scripts/tensor_master_client.py status job-123
```

## Architecture

```text
Browser / agent
      |
      v
Tensor Master (FastAPI + SQLite)
  | queue, receipts, allowlists, credentials
  +---- local worker ----> local ComfyUI
  +---- rented worker ---> pinned ComfyUI + profile models
```

Workers register, heartbeat, claim compatible leased tasks, submit the graph to
their own ComfyUI API and return output receipts. Tensor Master never exposes a
rented ComfyUI port as the product UI. Arbitrary user graphs are rejected; each
supported workflow has an allowlist and bounded inputs.

The base runtime inventory is pinned by repository revision, size and SHA-256
in `runtime/h3/models.lock.json`. User entries live in the private
`state/community-loras.json` manifest and use the same immutable fields. The
public profile list is intentionally smaller than the production studio.

## Development

```powershell
$env:TEMP="$PWD\.test-tmp"
$env:TMP=$env:TEMP
.\.venv\Scripts\python.exe -m pytest --basetemp .test-tmp\pytest -q
```

Never run production experiments by weakening a ComfyUI node allowlist or by
replacing a secure launcher. Rendering, renting GPUs and posting competition
entries are separate explicit actions.

## License, commercial use and upstream terms

Comfy Commander Community code is **AGPL-3.0-or-later**; see
[LICENSE](LICENSE) and [LICENSING.md](LICENSING.md). You may use it commercially
under the AGPL, but modified network services and distributed derivatives must
provide their corresponding source under the AGPL. Organizations that want to
build a proprietary or closed-source product should contact the copyright
holder about a separate commercial license.

The AGPL covers this project's original code, not its name, logo, downloaded
models or third-party components. See [TRADEMARKS.md](TRADEMARKS.md) and
[NOTICE.md](NOTICE.md). Models and runtime dependencies retain their own terms.
In particular, the SAM3 checkpoint has upstream terms that each user must
review and accept.
