"""Human-triggered local model setup, limited to the public pinned catalogue."""
import json
import os
import shutil
from pathlib import Path
from threading import Lock, Thread
from urllib.parse import quote
from urllib.parse import urlsplit

import httpx

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from runtime.h3.sync_models import LOCK_FILE, target_path, verify_artifact
from .config import get_settings
from .credential_paths import provider_secret
from .rental_ui import require_admin

router = APIRouter(prefix="/api/v1/setup/local-models")
SETS = {
    "h3": ("H3 References · starter", {"common", "ref2v-int8", "lightx2v-ref2v"}),
    "qwen": ("Qwen Image · starter", {"qwen-image-21"}),
    "singularity": ("H3 Singularity", {"common", "ref2v-singularity", "lightx2v-ref2v"}),
    "motion": ("H3 Motion control", {"common", "ref2v-int8", "lightx2v-ref2v", "sam3"}),
    "frames": ("H3 Start / end frames", {"common", "flf2v-int8", "lightx2v-fl2v"}),
    "omni": ("FastH3 Omni", {"common", "fasth3-omni"}),
    "isolate": ("Reference processing · optional", {"reference-isolate"}),
    "swap": ("Character Swap v1.5 · optional", {"character-swap-v15"}),
    "sparse": ("Ref2V sparse attention · optional", {"vsa"}),
}
LOCK = Lock()
STATE = {"status": "idle"}


def node_checks(root, model_set):
    sources = json.loads(LOCK_FILE.with_name("sources.lock.json").read_text(encoding="utf-8"))
    names = ["viggle_turbo.py"] if model_set == "qwen" else [
        "h3_beta57.py", "comfyui-kjnodes", "comfyui-videohelpersuite",
        "comfyui-minimaxrefpack", "ComfyUI-PromptDirector", "ComfyUI-H3-NativeAudioLock",
        "ComfyUI-TensorMaster-Preprocessors"]
    if model_set == "sparse":
        names.append("ComfyUI-Ref2VA-VSA")
    if model_set == "isolate":
        names = ["ComfyUI-TensorMaster-Preprocessors"]
    installed = {path.name.casefold() for path in (root / "custom_nodes").iterdir()}
    rows = []
    for name in names:
        source = sources["custom_nodes"].get(name, {})
        rows.append({"name": name, "found": name.casefold() in installed,
                     "url": source.get("url") or "https://github.com/mexxmillion/tensor-master-community/tree/main/runtime/h3",
                     "note": "Requires Commander's runtime patches / overlays" if source.get("patch") or source.get("overlay") or not source else "Install the pinned runtime version",
                     "revision": source.get("revision") or source.get("version")})
    required = {"TextEncodeQwenImage21", "ViggleTurboLora"} if model_set == "qwen" else {
        "MiniMaxH3ReferenceToVideo", "MiniMaxH3ReferencePack", "MiniMaxH3NativeAudioLock", "ResolutionSelector"}
    if model_set == "motion":
        required |= {"SAM3_Detect", "SAM3_VideoTrack", "SAM3_TrackToMask", "TMPrepareVideo"}
    if model_set == "frames":
        required.discard("MiniMaxH3ReferenceToVideo")
        required.add("MiniMaxH3ImageToVideo")
    if model_set == "isolate":
        required = {"TMIsolateReference", "TMDepthReference"}
    service = urlsplit(get_settings().comfyui_url)
    runtime = {"status": "unavailable", "missing_classes": [], "note": "Start local ComfyUI, then check again. Folder presence alone does not prove nodes loaded successfully."}
    if service.scheme == "http" and service.hostname in {"localhost", "127.0.0.1", "::1"} and not service.username:
        try:
            response = httpx.get(get_settings().comfyui_url.rstrip("/") + "/object_info", timeout=3)
            response.raise_for_status()
            missing = sorted(required - response.json().keys())
            runtime = {"status": "missing_nodes" if missing else "nodes_loaded", "missing_classes": missing,
                       "note": "Checks the configured local ComfyUI service. Folder names and loaded nodes do not verify model integrity or full workflow compatibility."}
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            pass
    return rows, runtime


class Selection(BaseModel):
    root: str = Field(min_length=1, max_length=2048)
    model_set: str = "h3"


def config_path():
    return get_settings().preferences_file.with_name("local-models.json")


def comfy_root(value):
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("Choose an absolute ComfyUI folder on the Commander host.")
    path = path.resolve()
    if not (path / "main.py").is_file() and (path / "ComfyUI/main.py").is_file():
        path = path / "ComfyUI"
    if not (path / "main.py").is_file() or not (path / "custom_nodes").is_dir():
        raise ValueError("Choose the ComfyUI folder containing main.py and custom_nodes (or its portable parent). Install ComfyUI first if it is missing.")
    if (path / "models").resolve() != path / "models":
        raise ValueError("The models folder redirects elsewhere. Select a ComfyUI installation with a local models folder.")
    return path


def artifacts(model_set):
    if model_set not in SETS:
        raise ValueError("Choose a supported model set.")
    return [item for item in json.loads(LOCK_FILE.read_text(encoding="utf-8"))["artifacts"]
            if item["group"] in SETS[model_set][1]]


def inventory(selection):
    root = comfy_root(selection.root)
    rows = []
    for index, item in enumerate(artifacts(selection.model_set)):
        destination = target_path(root / "models", item)
        found = destination.is_file() and destination.stat().st_size == item["size"]
        rows.append({"id": index, "file": item["file"], "group": item["group"], "bytes": item["size"],
                     "destination": str(destination), "status": "found" if found else "conflict" if destination.exists() else "missing",
                     "url": f"https://huggingface.co/{item['repo']}/resolve/{item['revision']}/{quote(item['file'], safe='/')}",
                     "repository_url": f"https://huggingface.co/{item['repo']}"})
    missing = sum(row["bytes"] for row in rows if row["status"] == "missing")
    nodes, runtime = node_checks(root, selection.model_set)
    return {"root": str(root), "model_set": selection.model_set, "files": rows, "missing_bytes": missing,
            "nodes": nodes, "runtime": runtime,
            "free_bytes": shutil.disk_usage(root).free,
            "note": "Found means filename and size match; downloaded files are SHA-256 verified. Custom nodes and worker connection must also be configured. Extra model search paths are not scanned."}


def download_missing(selection, rows):
    try:
        from huggingface_hub import hf_hub_download
        root = comfy_root(selection.root)
        token = provider_secret("huggingface", get_settings().huggingface_token_file) or False
        for row, item in zip(rows, artifacts(selection.model_set)):
            if row["status"] != "missing":
                continue
            with LOCK:
                STATE["current_file"] = item["file"]
            destination = target_path(root / "models", item)
            # Isolated staging keeps unverified bytes out of ComfyUI's model lists.
            stage = root / ".commander-model-downloads" / item["sha256"]
            if stage.resolve() != stage:
                raise ValueError("Unsafe download staging path")
            downloaded = Path(hf_hub_download(repo_id=item["repo"], filename=item["file"],
                              revision=item["revision"], local_dir=stage, token=token))
            if downloaded.resolve() != (stage / item["file"]).resolve():
                raise ValueError("Unexpected download path")
            verify_artifact(downloaded, item)
            destination = target_path(root / "models", item)
            destination.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive publication: an existing user model is never replaced.
            os.link(downloaded, destination)
            downloaded.unlink()
            with LOCK:
                STATE["completed_files"] += 1
        with LOCK:
            STATE.update(status="completed", current_file=None)
    except Exception:
        # Library errors can include signed URLs or auth data. Keep them private.
        with LOCK:
            STATE.update(status="failed", error="Download stopped. Check disk space, write permissions and network. For gated models, accept the repository license on Hugging Face and connect your token in Connections, then retry. Existing files were not replaced.")


@router.get("")
def saved_setup(request: Request):
    require_admin(request)
    settings = get_settings()
    saved = json.loads(config_path().read_text(encoding="utf-8")) if config_path().is_file() else {}
    return {"root": saved.get("root", str(settings.comfyui_root or "")),
            "model_set": saved.get("model_set", "h3"),
            "sets": [{"id": key, "label": value[0]} for key, value in SETS.items()],
            "download": dict(STATE)}


@router.post("/check")
def check(selection: Selection, request: Request):
    require_admin(request, mutation=True)
    try:
        data = inventory(selection)
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"root": data["root"], "model_set": selection.model_set}), encoding="utf-8")
        return data
    except (ValueError, OSError):
        raise HTTPException(400, "Check the selected ComfyUI folder and model set. Use the folder containing main.py and custom_nodes; portable parents are accepted.") from None


@router.post("/download", status_code=202)
def start_download(selection: Selection, request: Request):
    require_admin(request, mutation=True)
    with LOCK:
        if STATE["status"] == "downloading":
            raise HTTPException(409, "A model download is already running. Follow its progress before starting another.")
        try:
            data = inventory(selection)
        except (ValueError, OSError):
            raise HTTPException(400, "Check the ComfyUI folder and selected model set.") from None
        if any(row["status"] == "conflict" for row in data["files"]):
            raise HTTPException(409, "A destination contains a file with a different size. Review it manually; Commander never overwrites existing models.")
        if data["free_bytes"] < data["missing_bytes"] + 1024**3:
            raise HTTPException(400, "Not enough free disk space. Leave at least 1 GB beyond the listed missing models.")
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"root": data["root"], "model_set": selection.model_set}), encoding="utf-8")
        STATE.clear()
        STATE.update(status="downloading", root=data["root"], model_set=selection.model_set,
                     total_files=sum(row["status"] == "missing" for row in data["files"]), completed_files=0)
        Thread(target=download_missing, args=(selection, data["files"]), daemon=True).start()
        return dict(STATE)
