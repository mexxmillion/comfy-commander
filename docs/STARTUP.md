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
