# Local GPU setup

Open **Setup → Local GPU** (or **Settings → Compute**). Local + cloud uses the
same checks. Choosing a profile checks only that profile's weights; it never
starts a download, GPU rental or render.

1. If needed, install [ComfyUI](https://comfy.org/download/) on the GPU computer.
   Commander is the controller; it does not include a local ComfyUI runtime.
   Alternatively, expand **Prepare / update a separate Commander runtime on
   Windows**. Choose an empty destination and a base profile, review the pinned
   core/node/Python plan, then explicitly confirm installation. This downloads
   several GB of software into an isolated environment. It never overwrites a
   personal ComfyUI installation or starts a worker/render/rental. Repairs are
   allowed only in a folder marked as Commander-managed; changed source files
   are backed up under `source-backups`, and model/input/output data are retained.
   Stop that managed ComfyUI before repairing it. Source backups do not roll back
   Python package changes. After software preparation, download the profile's
   model set, double-click `Start ComfyUI.cmd` in that runtime folder, then recheck
   loaded nodes and connect a worker. Do not treat software installation as a
   successful render test. Driver or Windows wheel incompatibilities may still
   require manual attention.
2. Sign in to Commander and select the full ComfyUI folder containing `main.py`
   and `custom_nodes`. A portable parent containing `ComfyUI` also works.
   Enter the host's path even when accessing setup from a phone.
3. Select **H3 References · starter** or **Qwen Image · starter**. The checklist
   shows diffusion weights, text encoders, VAEs and matching acceleration LoRAs,
   with download sizes and exact destinations beneath `ComfyUI/models`.
4. Review the sources and licenses, check the download confirmation, and click
   **Download missing models**. New files are checked against the pinned size
   and SHA-256 before publication. Existing models are never replaced.
   Download progress reports the current file and completed files. Large files
   can take several minutes. Retry after a network failure; partial downloads
   remain in `.commander-model-downloads` for Hugging Face's resume support.
5. For gated models, accept the repository license in your browser and add your
   Hugging Face token in **Connections** yourself. Tokens are not put in URLs.
6. Expand **Custom nodes & worker connection**. Install the listed runtime
   components and dependencies before trying a job. Folder checks alone cannot
   prove successful imports. Restart ComfyUI and check again to inspect loaded
   node classes on the configured local ComfyUI service.

## Runtime compatibility

Use the reviewed revisions in [sources.lock.json](../runtime/h3/sources.lock.json)
and compatible dependencies in
[python-runtime.lock.json](../runtime/h3/python-runtime.lock.json).
The public runtime also includes `h3_beta57.py`, `viggle_turbo.py`, and the
packages under `runtime/h3/source-overlays/custom_nodes`. Components marked
`patch` in the source lock require the corresponding file under
`runtime/h3/patches`; copying an upstream node folder alone is insufficient.
Install dependencies into **ComfyUI's Python environment**, not just Commander's.
Do not replace an existing ComfyUI installation with cloud bootstrap scripts:
those scripts target isolated Linux rental workers.

Motion control adds SAM3 weights. Frames uses its matching FL2V weights and
LoRAs. Character Swap, sparse attention and reference processing are optional
add-on sets. Installing an add-on alone does not install the base H3 profile.
The checklist does not search `extra_model_paths.yaml`; models stored there may
already work in ComfyUI, but must be reviewed manually rather than redownloaded.

## Connect a worker

Keep ComfyUI bound to localhost. In the private Commander `.env`, configure
`COMFYUI_URL` for the running local server and `COMFYUI_INPUT_DIR` for its input
folder; set `COMFYUI_ROOT` and `MODEL_DIRECTORIES` to the installation root and
its models folder. `MODEL_DIRECTORIES` uses a JSON array of path strings.
Restart the controller after changing these settings. Selecting a model-download
folder in setup does not silently reconfigure an already running worker.

From the activated Commander environment, start one local worker:

For normal daily use, double-click **Start Commander.cmd**, start your configured
ComfyUI, then double-click **Start Local Worker.cmd**. The worker launcher always
targets this local Commander and preserves its task checkpoint. It refuses to
compete with a worker already connected to the same ComfyUI through a different
dispatcher. Drain and stop that other worker before switching. Existing Windows
startup entries for another dispatcher should be disabled or changed by their
owner, otherwise they can reconnect it again after sign-in.

For a manually configured worker instead:

```powershell
$env:TENSOR_WORKER_ID = 'local-gpu-1'
$env:TENSOR_WORKER_NAME = 'Local GPU'
python -m backend.worker --local-gpu 1
```

The worker reads the local controller credential file itself. Do not paste that
credential into a command or chat. Confirm that Dispatcher shows the worker
online and ready before submitting one small job. Model presence, loaded node
classes, and successful rendering are separate checks.
