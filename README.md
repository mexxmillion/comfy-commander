# Tensor Master Community

Tensor Master Community is a self-hosted, agent-friendly control plane for
ComfyUI. It keeps jobs, GPU workers, model profiles, provider credentials and
render receipts behind one focused web app.

This competition edition intentionally ships a small public surface:

- Qwen Image 2.1 image generation and reference editing
- MiniMax H3 Ref2VA INT8, Singularity and FastH3 Omni profiles
- native, public ACC and bring-your-own 4/8-step Turbo recipes
- RunPod and Vast.ai worker launch controls with explicit cost limits
- optional Hugging Face credentials for gated and private models
- optional OpenRouter prompt enhancement
- a reviewed SAM3 to H3 character-replacement workflow

Private identities, personal LoRAs, Eros, Dolores and other studio-only assets
are not part of this repository.

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

The runtime model inventory is pinned by repository revision, size and SHA-256
in `runtime/h3/models.lock.json`. The public profile list is intentionally
smaller than the production studio.

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
