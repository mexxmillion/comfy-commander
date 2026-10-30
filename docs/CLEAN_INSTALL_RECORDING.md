# Clean installation and recording

Use a separate empty folder. Keep the working installation, gallery, credentials
and model files intact. Do not copy its `.env`, `state/`, `.venv` or weights into
the new checkout. Prepare the recording folder first, but film the actual clone
and installation rather than presenting an already initialized app as a first run.

## Before recording

- Install Git and Python 3.12. The Windows controller dependencies include
  `imageio-ffmpeg`; a ComfyUI runtime also needs its own video/custom-node dependencies.
- Use a new browser profile for this recording. Browser drafts and login cookies
  belong to the localhost origin, so the usual browser can restore an old draft
  even when the new app database is empty. Leave that usual profile intact.
- Check existing jobs, workers and paid instances. When ready to cut over, finish
  or reconcile active work before stopping the old controller/local worker.
  Do not connect two workers to the same ComfyUI service. A controller-side
  watchdog does not survive an OS reboot.
- Leave enough disk space for the selected runtime and model profile. Show actual
  download sizes; do not imply that every model is included in the Git clone.
- Pause the recording for password creation, API keys, provider login and model
  access approval. Enter credentials yourself in Setup. Resume on the connected
  status, not on a key or credential file. Keep raw captures private until reviewed.

## Clone and install on camera

In an empty recording folder:

```powershell
git clone --branch main --single-branch https://github.com/mexxmillion/comfy-commander.git app
cd app
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m backend.service_start
```

These are the ordinary installation commands. On a previously configured machine,
use the optional helper below to avoid inheriting old credential/environment overrides.

## Optional isolated recording session

The standard-library helper is available in an existing checkout as
`tools/clean_install_session.py`. It can be copied beside an empty recording
workspace. Run each action separately so the real steps are visible:

```powershell
py -3.12 path\to\clean_install_session.py status --workspace .\commander-recording
py -3.12 path\to\clean_install_session.py clone --workspace .\commander-recording
py -3.12 path\to\clean_install_session.py install --workspace .\commander-recording
py -3.12 path\to\clean_install_session.py start --workspace .\commander-recording
# After local runtime setup and the deliberate port-8000 cutover:
py -3.12 path\to\clean_install_session.py worker --workspace .\commander-recording
```

It clones into `commander-recording/app`, installs that clone's own virtual
environment and starts its localhost controller. Child processes use that clone's
credential paths and HF cache. Provider/LLM keys and old ComfyUI, database and
configuration overrides are not inherited. The helper does not change global
environment variables or existing credentials. Its status check creates no app state.

The clone action refuses an existing `app` folder. Startup refuses an occupied
port instead of opening the previous installation. It never stops another server, starts ComfyUI, downloads model weights, queues
renders or rents GPUs. The separate `worker` action starts a local worker only
when explicitly invoked; use it instead of the ordinary worker shortcut on a
machine with old environment overrides. It may claim work already in this
clone's queue. The `start` action does not automatically open the usual browser;
open the printed URL in the new recording profile.
Starting the app normally initializes its own installation state; the human then
creates the admin password through Setup.

An alternative `--port 8001` is useful for inspecting onboarding alongside an
existing controller. A full local-worker/phone recording should use the normal
port after a deliberate cutover: the simple local-worker launcher targets 8000,
and the existing Tailscale Serve route must match the controller being recorded.

## Record the real onboarding

1. **Empty first run:** show Setup and the new admin-password step. Pause for the
   password; do not reuse or copy the old identity.
2. **Compute choice:** show Local GPU, Local + cloud and Cloud only. Choose the
   route being tested rather than silently connecting an existing worker.
3. **Local setup:** select the ComfyUI installation, or review and prepare an
   empty separate managed runtime. Show missing nodes/models and the selected
   profile before explicit downloads. Start that runtime, recheck imports, and
   connect its worker. If an existing runtime or cached weights are reused, label
   that reuse; it is not a blank-machine installation test.
4. **Connections:** provider keys are needed only for the chosen rental service.
   RunPod needs GraphQL Read/Write and Pod lifecycle access; read-only connection
   checks do not prove creation permission. HF LoRA/model uploads need write access
   to the destination repository. See [token permissions](STARTUP.md#provider-token-permissions).
   Public, ungated weights support anonymous download. Gated/private models need
   an authorized HF token and access approval. AI prompt tools/Creative Engine
   need an optional configured LLM; basic rendering does not.
5. **Defaults and phone:** show 9:16, 0.5 MP and five seconds, then the Settings
   gear. Demonstrate the Phone access section and Tailscale download/setup link.
   Review the private address before publishing it in a video.

## Record workflows and results

- Start with one short authorized generation and show its job ID, worker,
  progress and playable output. Record the selected recipe and actual timings.
- For preprocessing, show the source, Process controls, queued/running state,
  preview and Save, then use the processed reference. Do not suggest preview
  generation was instantaneous; retain real waiting time in the raw footage.
- For Creative Engine, show one brief, three variations, concept review and an
  explicitly queued batch. Keep the returned batch/job IDs; do not submit again
  after a timeout without checking the saved state.
- For character replacement, show exactly one source video and one character
  image. Explain the one-shot SAM3 → inversion → H3 chain through the ComfyUI API.
  Inspect target/mask ownership and the inverted guide, then inspect final motion,
  identity and continuous audio. A completed job is not automatically QC-accepted.
- A technical cutaway can show `POST /prompt`, `/ws` progress and `/history`.
  These are worker-to-ComfyUI calls; the phone talks to Commander. One workflow
  submission is not a claim that uploads and progress need no other API calls.

## Record one GPU rental

Before launch, record the authorized provider/profile, GPU count, hourly ceiling,
total budget and maximum runtime. Setup itself does not authorize a paid rental.

Show **Dispatcher → Rentals → Add GPU**, provider/GPU/profile selection, estimated
download charges and the explicit confirmation. Follow the same rental ID through
container loading, SSH, runtime setup, model sync and worker readiness. Byte progress
and ETA describe model downloads, not the entire startup. Provider stock and network
performance vary; keep cold-start failures and elapsed time in the evidence.

Submit the intended cloud workflow once, show its returned gallery output, and
review playback/audio. If setup fails, show Diagnostics and **Retry setup · same
GPU** where available. That action keeps the original instance and deadline.
For the end of the test, explicitly terminate the recorded test instance and
verify its absence at the provider. Preserve other workers and rentals.

## Evidence and edits

Keep the Git commit, platform/driver/runtime versions, model profile, batch/job
IDs, rental ID, confirmed teardown and result paths with the private recording.
Keep unsuccessful runs as well as successes. Label time-lapse cuts and cached
downloads. Review the export for credentials, private addresses, filenames and
unrelated gallery media before publication. Do not film a preconfigured checkout
as proof of a completely fresh Windows installation.

See [local runtime setup](local-setup.md), [startup/reconnection](STARTUP.md),
[phone access](PHONE_ACCESS.md), [LLM setup](LLM.md) and [MCP](MCP.md).
