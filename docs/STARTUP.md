# Start and reconnect Commander

## First installation

Install Git and Python 3.12. From the cloned repository in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m backend.service_start
```

Open `http://127.0.0.1:8000/setup` and create the admin password on the host PC.
These commands do not need a PowerShell execution-policy change. The older
`tools/first_run.ps1` remains an installation helper; do not rerun installation
just to reconnect after a reboot.

For a separate empty checkout or a recorded first run, follow the
[clean-install recording guide](CLEAN_INSTALL_RECORDING.md). It explains browser
and credential isolation without wiping an existing installation.

## Daily startup

1. Double-click **Start Commander.cmd** in the repository folder.
2. For local work, start the ComfyUI installation configured in Settings/`.env`.
   A prepared managed runtime contains its own **Start ComfyUI.cmd**.
3. Double-click **Start Local Worker.cmd** in the Commander folder.
4. In **Dispatcher → Workers**, confirm a current heartbeat and Ready/Busy.

The web launcher starts only the localhost controller and opens Generate. The
worker launcher targets this local controller, uses a stable identity and
retains task checkpoints. Neither launcher resets passwords, copies keys,
installs dependencies or rents GPUs. A connected worker can resume its existing
task and claim jobs already queued for it.

To check the server without opening a browser:

```powershell
.\.venv\Scripts\python.exe -m backend.service_start --status
```

Cloud-only use needs Commander and its chosen provider connection; it does not
need a local ComfyUI or local worker. A web-only restart preserves stored jobs;
after an OS reboot, inspect existing rentals at the provider before launching
another. Do not assume a controller-side watchdog survived the reboot.

## Stop Commander

Double-click **Kill Commander.cmd** in the installation folder and confirm Y.
Finish active jobs first. It stops only that checkout's web server and local
worker bridges, with process IDs and a result message. It leaves ComfyUI,
cloud workers, rental managers and deadline watchdogs running, and preserves
saved settings, credentials, outputs and checkpoints. Rental billing continues
until the rental is terminated. Other Commander checkouts are not stopped.

For a fresh-clone cutover, run this from the old installation, then start the new
clone. The new controller uses its own saved state; review existing rentals in
the old installation/provider before switching.

## Worker missing after reboot

- Confirm ComfyUI responds on the configured localhost address.
- Open **Start Local Worker.cmd**. It refuses a duplicate or competing worker.
- If another dispatcher owns that ComfyUI, drain and stop its worker first;
  finish/reconcile active work before switching. Do not start two bridges to
  the same ComfyUI. Older Windows startup tasks may point to the other dispatcher;
  their owner should disable/change those entries for future sign-ins.
- A paused/draining worker stays paused. Resume it in Dispatcher when ready.
- A saved checkpoint resumes only after the controller accepts its task lease;
  it is not a reason to manually submit another render.

Logs are in `state/community-controller.out.log`,
`state/community-controller.err.log`, `state/community-worker.out.log` and
`state/community-worker.err.log`. Keep logs private when they include job or
machine details. Do not share credential files.

## Configuration and two local GPUs

The private `.env` can set `COMFYUI_URL`, `COMFYUI_ROOT`,
`COMFYUI_INPUT_DIR` and JSON-array `MODEL_DIRECTORIES`. Selecting a model-download
folder does not silently reconfigure a running worker. Restart Commander and
the relevant worker after changing their configuration, once active work is safe.

The simple worker shortcut connects GPU 1. For two GPUs, run separate ComfyUI
servers bound to localhost (for example ports 8188 and 8189), each assigned its
GPU. Start a distinct worker identity for each in separate terminals, using
`python -m backend.worker --local-gpu 1` / `--local-gpu 2`, and set each process's
`COMFYUI_URL`, `TENSOR_WORKER_ID`, `TENSOR_MASTER_URL` and
`TENSOR_WORKER_STATE_DIR`. Keep IDs and checkpoints distinct. Provider workers
also register individually and are routed through the same queue.

## Defaults and phone access

The built-in canvas is **9:16, 0.5 MP, five seconds**. Automatic draft recovery
keeps prompts/references while using current canvas defaults; switching Video
and Image no longer leaks square/1 MP settings. Explicit Edit/rerun restores the
selected job's settings. Change shared defaults deliberately in Settings → Defaults.

For a phone, keep the PC awake and Commander/Tailscale running. Open the complete
private HTTPS URL copied from Settings → Phone access, including its port.
[Phone troubleshooting](PHONE_ACCESS.md).


### Rental startup failures

Dispatcher opens the complete queue; the compact row below it provides Workers,
Rentals, Monitor, Activity and Benchmarks. Rentals retains failed attempts even
when automatic cleanup has terminated the instance. Use **Diagnostics & logs**
for lifecycle, setup, model download, worker and ComfyUI output. Logs are redacted;
review them before sharing. A failed startup is not a successful worker connection.
Worker runtime mismatches, lost heartbeats and disappearance are shown prominently.

The rental picker defaults to RTX 5090 and also offers RTX 5080 (16 GB).
Budget, hourly price and lifetime caps still apply. Model downloads finishing does
not mean startup has completed. Windows shell scripts are normalized to Linux line
endings during upload so local editor settings cannot break cloud startup.

Before allocation Commander parses the normalized startup scripts with Bash
(included in Git for Windows). Linux setup checks CUDA before downloading model
weights. Startup waits for ComfyUI's loopback API to report a CUDA device before
starting the worker, and the controller verifies the expected profile runtime.
Setup failures retain their stage, error and logs; Commander explicitly reports
when its cleanup terminated the instance. Price and lifetime safeguards remain active.


During model sync the active rental shows transferred GiB, total profile size,
files verified, the current file, measured transfer speed and a rough download
ETA. The estimate starts after transfer samples arrive and excludes subsequent
integrity verification and worker startup. Files count as ready only after their
size/checksum checks pass. Cached or resumed bytes count toward completion.
Failed-rental history starts collapsed and keeps your expansion choice during
live refresh; new failure alerts and retained diagnostics remain available.


Runtime installation and model sync run independently of the SSH connection,
under a remote process lock. If the connection resets, Commander reconnects to
the same instance for up to two minutes; it does not allocate a replacement.
Transient model download failures receive at most two retries using partial
cached files. Access/credential and integrity failures require diagnosis.
A deterministic setup failure is retained as **Needs attention**, with logs and
the original automatic termination deadline. It remains billable during that
repair window; terminate it yourself if you do not want to keep it. The original
price, budget and lifetime limits are never extended by recovery. Container image
loading is distinct from SSH readiness, runtime setup, model sync and worker readiness.


Change **Settings → Rentals → Maximum GPU price** and click **Validate & save
limits** to update the hourly ceiling for new Vast.ai and RunPod rentals. This
applies immediately and persists across restart. Existing rental records keep
their confirmed rate, total budget and original deadline. The installation's
fleet hourly/count and lifetime safeguards remain outer limits.

Both providers require CUDA 13 or newer for the bundled CUDA 13 image. RunPod
availability and creation use the same minimum CUDA, CPU, RAM, network and disk
requirements. An exact CUDA 13.0 match would incorrectly exclude newer compatible
hosts and report no capacity even when the general GPU catalogue shows stock.


Use **Retry setup · same GPU** on a retained failure after reviewing its logs.
This uploads the current supported setup and resumes the original instance;
it cannot create another GPU or extend the deadline. Installed packages and
model cache are retained. Package downloads use longer timeouts/retries, and
setup can safely reuse the already checked-out pinned ComfyUI source.


Hugging Face credentials are optional for public, ungated model downloads.
Without a configured token workers download anonymously and retain revision,
size and checksum verification. Gated/private files still require an authorized
read token and any required access approval. Cached files are verified locally.
The public worker does not require the private mobile extension status route.


## Provider token permissions

Before pasting a token in **Setup → Connections** or **Settings → Connections**:

- **RunPod:** in the console's API Keys settings, enable **GraphQL Read/Write**
  for `api.runpod.io/graphql`. Commander creates Pods through GraphQL and uses
  Pod read/delete access for lifecycle checks and termination. Serverless
  endpoint permissions are separate; endpoint-only or read-only access is
  insufficient. If creation reports `UNAUTHORIZED` while reads work, review
  these scopes. Check the provider console before retrying an uncertain creation.
  [RunPod permission guide](https://www.runpod.io/blog/scoped-api-keys-runpod).
- **Hugging Face downloads:** public, ungated models need no token. Gated/private
  models need read access plus any required model access approval.
- **Hugging Face LoRA/model uploads:** the token needs **write access to the
  destination repository**, and the account must be allowed to write there.
  Prefer a fine-grained token scoped to that repository; organization policy
  may also require approval. Read-only tokens cannot upload.
  [HF token guide](https://huggingface.co/docs/hub/security-tokens).

Connection checks perform read-only validation. They do not prove permission to
create a paid Pod or upload a file, and do not perform either operation as a test.
Keep tokens in the setup form, never in workflow graphs, screenshots or Git.


### Where credentials go

Keys saved in Setup are stored on the computer running Commander, in its
installation-owned credential directory (or an explicitly configured private
location). They are not stored in a Tensor Master account. Validation and API
operations send the relevant credential to its provider. For cloud model
access, the configured HF token is also transferred over SSH to the rented
worker; public, ungated downloads can run without it. Use a token scoped to
the required repositories and operations. Do not describe credentials as
"never sent anywhere" or assume that connection success proves write access.

## Rental tunnel recovery

RunPod workers reach Commander through a private reverse SSH tunnel. New Pods
use SSH keepalives on both ends so a disconnected session releases its remote
forwarding port instead of blocking reconnection. Recovery retries on the same
Pod without restarting ComfyUI. A port-8000 forwarding conflict is recorded in
the rental lifecycle log, and termination records include the failure reason.
The configured unresponsive timeout and original lifetime limit still apply;
these changes do not extend billing limits. Existing Pods need a new SSH-server
configuration to receive the server-side keepalive change; updating Commander
alone does not retrofit their running SSH daemon.
