# Tensor Master Community

Tensor Master Community is a self-hosted, agent-friendly control plane for
ComfyUI. It keeps jobs, GPU workers, model profiles, provider credentials and
render receipts behind one focused web app.

This competition edition intentionally ships a small public surface:

- Qwen Image 2.1 image generation and reference editing
- MiniMax H3 Ref2VA INT8, Singularity and FastH3 Omni profiles
- native, public ACC and bring-your-own 4/8-step Turbo recipes
- RunPod and Vast.ai worker launch controls with explicit cost limits
- optional Hugging Face credentials plus pinned personal-LoRA deployment
- a BYOK OpenRouter studio copilot with Gemini and DeepSeek Flash choices
- a reviewed SAM3 to H3 character-replacement workflow

Private identities, Eros, Dolores and other studio-only assets are not part of
this repository. A user's own LoRAs can be registered at runtime without being
committed to the project.

## Quick start

Python 3.12 is required.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/setup`. Create the local admin password, then
connect only the services you use. Connecting a provider validates and saves a
key; it does **not** rent a GPU.

Docker is the shortest controller-only demo:

```bash
cp .env.example .env
docker compose up --build
```

The published port is bound to localhost by default. Put an authenticated HTTPS
proxy or private network in front of it before allowing remote access.

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

## Drive it with Codex or another agent

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

## License and upstream terms

Tensor Master Community code is GPL-3.0-or-later; see [LICENSE](LICENSE). Models
and runtime dependencies retain their own licenses. In particular, the SAM3
checkpoint has upstream terms that each user must review and accept.
