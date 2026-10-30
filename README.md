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
seven screens:

1. Create the local admin password.
2. Choose local, cloud, or hybrid compute.
3. Connect only the providers you want.
4. Review the Qwen Image and MiniMax H3 starter profiles.
5. Connect your phone through Tailscale, or skip for now.
6. Import and validate an H3 rental profile, or skip for now.
7. Open the command center.

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

Cloud worker packaging uses pinned public source archives and the bundled
reference-preprocessing nodes. A local ComfyUI installation is not required for
cloud-only operation. No model weights are downloaded onto the controller by
packaging; rental workers sync their selected model profile under the existing
cost and lifetime limits. Source archives are cached under `state/`.

For an explicitly managed local runtime snapshot, set
`CLOUD_RUNTIME_SOURCE=local`, `COMFYUI_ROOT` and `COMFYUI_PYTHON` in `.env`.
This optional path captures your local runtime; the default is the public bundle.

Cloud rentals also require a dedicated SSH key pair. **Setup & Models → Compute →
Cloud SSH access** shows the exact human-run key creation command and checks that
both files exist. To use an existing dedicated key, set `CLOUD_SSH_KEY_FILE` in
`.env` and restart the controller. Keys are never generated, copied or uploaded
by the assistant. A passphrase-protected key must be loaded into your SSH agent
for unattended worker setup.

H3 includes **Reference video** and **First / last frame (FLF2V)** modes.
FLF2V supports a first frame, last frame, both, or text only. Its rental profile
pins the public FL2VA INT8 model and matching 4/8-step LightX2V FL2V LoRAs.

Commander owns its gallery database (`state/dispatcher.sqlite3`), uploaded
references (`state/comfy-input`), and returned cloud media (`state/cloud-outputs`).
Cloud workers fetch only the inputs in their leased task and return finished
media before reporting completion. Gallery history and files remain on the
controller after a rented worker is removed. Local ComfyUI directories are
optional integration settings, not required application storage.

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

## H3 profile import assistant

Setup & Models includes a Profiles step for local ComfyUI API JSON. Validation
checks the supported H3 graph and compares model references against the selected
pinned rental runtime and human-registered LoRAs. Save a reviewed profile; only
profiles whose dependencies resolve appear in rental choices. Unknown sources
remain drafts. Importing never runs a workflow, rents hardware, or uploads files.

The optional OpenRouter assistant appears only inside this step. It explains
validation and asks whether missing models already have licensed Hugging Face
sources or require a human-managed upload. The existing LoRA management forms
handle uploads and immutable registration. Other model types and custom nodes
require reviewed deployment support. No global AI companion is shown.

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


### Returning to Settings

Open **⚙ Settings** from the desktop navigation or the phone's Dispatcher menu. Settings provides direct tabs for generation defaults, compute/SSH diagnostics, provider connections, private phone access, reviewed workflow profiles, and Hugging Face LoRA upload/registration. Guided onboarding remains available at `/setup`.

Generation defaults are validated before saving to the controller's `state/preferences.json` and shared across desktop and phone. Existing drafts and job recipes take precedence; use **Start a fresh form with saved defaults** to begin with the saved values. Changing a render target never rents hardware. Phone addresses are detected from Tailscale rather than accepted as arbitrary overrides. LoRA uploads still require the human's confirmation. New Creative Engine jobs use the same 16 GB minimum as Generate; explicitly configured and existing jobs retain their requirements.


### One-shot H3 Replacement

Select **Replacement**, add exactly one source video and one replacement image, and describe the target person. **Run H3** submits one job through `/api/v1/jobs`: the controller composes the approved SAM3 detect/track branch, inverts only the tracked person's pixels, and feeds those frames directly into the H3 video reference. The background and image references remain intact. Audio lock turns on when Replacement is selected and can be changed explicitly. The completed job retains the SAM3 mask and inverted-guide evidence alongside the final H3 video. Generation runs without an intermediate approval pause; completion still requires visual/audio QC. The separate preparation endpoint remains available for a guided preview workflow.

Bulk routing now preserves each task's existing pool, worker preference and architecture unless a different value is chosen. A VRAM-only change therefore keeps local-only LoRAs on the local pool. Failed changes remain selected and show the server's rejection reason.


### Password changes and recovery

Open **Settings → Password & sign in → Change password**, enter your current password and the new password twice, then click **Update password & sign out devices**. Changing a password signs out every saved device, while preserving worker credentials and the installation identity.

If the password is forgotten, click **Forgot password?** on that same page. Recovery is available only on the host PC at `http://127.0.0.1:8000/settings#access`, requires the existing local recovery key, and shows its configured file location without displaying its contents. Phone/tailnet users are directed back to the host computer. Recovery attempts are rate limited; there is no unauthenticated reset or automatic key replacement.


### H3 video controls

Generate exposes three H3 modes: **References** (text alone or optional images/video/audio), **Frames** (optional start/end images, using the FLF2V profile), and **Motion control** (exactly one character image plus one source video, using the one-shot SAM3 → target inversion → H3 replacement chain). Motion control enables audio lock when selected. The old Auto/Standard/Replacement prompt toggles are replaced by these input-specific modes. Ordinary image references are soft conditioning; use Frames when the first or last frame must be explicitly supplied.

The main controls are duration, aspect ratio, megapixels, quality and adaptive bitrate/encoding. Preview uses the matching 4-step Turbo LoRA (the existing FLF2V recipe uses five sampling steps), Normal uses the 8-step Turbo LoRA, and HQ uses 20 RES-multistep steps without the automatic Turbo LoRA. Presets choose the public Ref2V/FLF2V base as appropriate. Standard encoding uses H.264 CRF 23 and High uses CRF 18; these are adaptive-bitrate quality settings, not fixed Mbps limits. Model, sampler, sparse attention, seed and custom LoRAs remain under Advanced. Prompt enhancement and Creative Engine are visible directly beneath the prompt; provider options and batch history expand below them. Recipes, drafts and shared defaults retain the chosen quality/encoding settings.


### Optional character adapter and fresh-machine media checks

Motion control includes **Character Swap v1.5 LoRA**, off by default. Enabling it adds the pinned public Akatz adapter at strength 1.0 to the Ref2V model chain, while retaining SAM3 tracking and source-audio lock. **Use v1.5 starter profile** applies a short 0.5 MP / 5-second Normal recipe without submitting a job; **Copy profile JSON** copies the settings recipe. This adapter is optional and does not guarantee identity retention on long shots. Its upstream model license applies separately.

The Ref2V cloud profile downloads the pinned adapter for future use; weights are not committed to Git. Fresh controllers can inspect reference audio/video using the packaged FFmpeg binary when system ffprobe is absent. Adding a validated Vast.ai or RunPod key in onboarding automatically prepares the shared worker SSH key. The second provider reuses it. **Settings → Compute → Prepare cloud access** is available for retrying an older or incomplete setup. The private key stays on that PC, protected for its local user, and existing keys are never overwritten. This does not launch paid capacity.


Rental defaults live in **Settings → Rentals**: supported GPU class, minimum
VRAM, maximum duration, hourly price, and total launch budget. Use **Validate**
to check values without saving, or **Validate & save limits** for future rentals.
Offer lists and both provider managers enforce these limits; existing rentals
keep their confirmed settings. Budget estimates include GPU time and model-sync
charges across the selected quantity. Provider adjustments and storage can add
charges; installation fleet, hourly-price and lifetime caps remain authoritative.
Onboarding and Settings share a compact desktop/phone layout.


Motion control starts with an editable replacement prompt, Normal / 8 steps,
0.5 MP, portrait 9:16, five seconds and audio lock. Configure the prompt,
replacement quality and reference preparation in **Settings → Defaults**.
Replacement graphs include a bounded video resize stage before reference decoding:
fit the selected output canvas without stretching, 24 fps, and the requested H3
frame window. Originals stay intact. **Process** on a video offers a CPU preview
and explicit Save; browser previews use MP4 while the prepared reference retains
PCM audio. Source crop/trim settings and continuous audio are preserved.
The inspector separates requested duration/aspect from aligned output pixels and
frames; SAM3 tracking progress is distinct from H3 sampling steps and timing.
