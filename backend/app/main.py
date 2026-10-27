"""Tensor Master web monitor and private worker API."""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.middleware.gzip import GZipMiddleware

from . import character_replacement, creative, krea, ltx, qwen_prompt, reference_preprocess
from .community import router as community_router, setup_status
from .config import get_settings
from .creative_prompts import PROMPT_VERSION
from .generator import router as generator_router
from .h3_media import h3_input_paths
from .identity import authority
from .job_edit import edit_setup
from .job_spec import job_spec
from .media import router as media_router
from .prompt_secret import configured as prompt_secret_configured
from .providers.base import OfferSearch
from .providers.vast import VastProvider
from .recipes import normalize_recipe
from .rental_ui import (
    TERMINAL as TERMINAL_RENTAL_STATES,
)
from .rental_ui import (
    all_current as managed_rental_snapshots,
)
from .rental_ui import (
    require_admin,
    upgrade_legacy_response,
)
from .rental_ui import (
    router as rental_router,
)
from .store import DEFAULT_PRIORITY, MAX_PRIORITY, MIN_PRIORITY, QueueStore
from .version import TENSOR_MASTER_VERSION

BASE_DIR = Path(__file__).resolve().parent


class GalleryCompression(GZipMiddleware):
    async def __call__(self, scope, receive, send):
        if scope.get('path') in {'/api/v1/outputs', '/api/v1/generator/library', '/api/v1/generator/status'}:
            await super().__call__(scope, receive, send)
        else:
            await self.app(scope, receive, send)


app = FastAPI(title="Tensor Master", version=TENSOR_MASTER_VERSION)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
app.include_router(rental_router)
app.include_router(generator_router)
app.include_router(media_router)
app.include_router(community_router)
app.add_middleware(GalleryCompression, minimum_size=1000)


@app.middleware('http')
async def migrate_device_session(request: Request, call_next):
    response = await call_next(request)
    try:
        return upgrade_legacy_response(request, response)
    except (OSError, RuntimeError, sqlite3.Error):
        # Preserve a still-valid legacy cookie if persistence is briefly unavailable.
        return response


@lru_cache
def get_store() -> QueueStore:
    return QueueStore(get_settings().database_url)


def require_token(request: Request, authorization: Annotated[str | None, Header()] = None,
                  store: QueueStore = Depends(get_store)) -> dict:
    supplied = authorization.removeprefix("Bearer ") if authorization else None
    try:
        principal = authority(get_settings().token_file).principal(supplied)
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        raise HTTPException(503, "Saved Tensor Master credential is unavailable; restore the identity store.") from exc
    if not principal:
        raise HTTPException(401, "Worker credential is invalid or revoked")
    if principal['role'] == 'worker':
        path = request.url.path
        if path.startswith('/api/v1/workers/') and path != '/api/v1/workers/register':
            if request.path_params.get('worker_id') != principal['subject']:
                raise HTTPException(403, "This worker cannot access another worker")
        elif path.startswith('/api/v1/tasks/'):
            execution = store.task_execution(request.path_params.get('task_id', ''))
            if not execution or execution['worker_id'] != principal['subject']:
                raise HTTPException(403, "This task belongs to another worker")
        elif path != '/api/v1/workers/register':
            raise HTTPException(403, "Worker credentials cannot administer the farm")
    elif principal['role'] != 'bootstrap':
        raise HTTPException(403, "This service credential cannot access worker APIs")
    request.state.principal = principal
    return principal


AUTH = Depends(require_token)


def controller_pocket_bridge_token() -> str | None:
    try:
        return authority(get_settings().token_file).setting('pocket')
    except (OSError, RuntimeError, sqlite3.Error):
        return None


def require_pocket_or_token(authorization: Annotated[str | None, Header()] = None, request: Request = None) -> None:
    if not authorization:
        if request is None:
            raise HTTPException(401, 'Sign in to Tensor Master')
        require_admin(request)
        if request.method not in {'GET', 'HEAD'}:
            require_admin(request, mutation=True)
        return
    supplied = authorization.removeprefix("Bearer ") if authorization else None
    unavailable = False
    try:
        principal = authority(get_settings().token_file).principal(supplied)
    except (OSError, RuntimeError, sqlite3.Error):
        principal = None
        unavailable = True
    if principal and principal['role'] in {'bootstrap', 'pocket'}:
        return
    if unavailable:
        raise HTTPException(503, "Saved Tensor Master credential is unavailable; restore the identity store.")
    raise HTTPException(401, "Pocket credential is invalid or revoked")


POCKET_AUTH = Depends(require_pocket_or_token)
STORE = Depends(get_store)

VAST_GPU_PROFILES = {
    "rtx5090": ("RTX_5090", 32),
    "rtxpro6000ws": ("RTX_PRO_6000_WS", 96),
}


TRIAL_DIR = Path(__file__).resolve().parents[2] / "state"
TRIAL_OUTPUT_DIR = TRIAL_DIR / "h3-cloud-output"
TRIAL_VIDEOS = {
    "cold": "tensor_h3_smoke_00001-audio.mp4",
    "repeat": "tensor_h3_smoke_00002-audio.mp4",
}
TRIAL_LOGS = {"prepare": "h3-prepare.log", "models": "h3-sync.log", "comfy": "h3-comfy.log"}


def _seconds(trial: dict, start: str, end: str) -> float | None:
    if start not in trial or end not in trial:
        return None
    return max(0, (datetime.fromisoformat(trial[end]) - datetime.fromisoformat(trial[start])).total_seconds())


def cloud_trial_snapshot(path: Path | None = None) -> dict | None:
    """Read a benchmark record without exposing its credentials or stale SSH access."""
    path = path or TRIAL_DIR / "h3-cloud-trial.json"
    try:
        trial = json.loads(path.read_text(encoding="utf-8"))
        started = datetime.fromisoformat(trial["rented_at"])
        ended = datetime.fromisoformat(trial.get("destroyed_at", datetime.now(UTC).isoformat()))
        elapsed = max(0, (ended - started).total_seconds())
        phases = (
            ("destroyed_at", "Finished"),
            ("warm_done_at", "Warm render complete"),
            ("warm_started_at", "Second render running"),
            ("cold_done_at", "Cold render complete"),
            ("cold_started_at", "First render running"),
            ("comfy_ready_at", "Ready to generate"),
            ("models_ready_at", "Starting ComfyUI"),
            ("runtime_ready_at", "Syncing models"),
            ("ssh_ready_at", "Preparing runtime"),
        )
        destroyed = "destroyed_at" in trial
        return {
            "instance_id": trial["instance_id"],
            "gpu": trial["gpu"],
            "phase": next((label for key, label in phases if key in trial), "Booting / pulling image"),
            "destroyed": destroyed,
            "outcome": "Rendered two clips" if "warm_done_at" in trial else ("Stopped before ready" if destroyed else "In progress"),
            "elapsed_minutes": elapsed / 60,
            "estimated_cost": elapsed / 3600 * trial["price_per_hour_estimate"],
            "price_per_hour": trial["price_per_hour_estimate"],
            "models_downloaded_gb": trial.get("models_downloaded_gb"),
            "ssh_host": None if destroyed else trial.get("ssh_host"),
            "ssh_port": None if destroyed else trial.get("ssh_port"),
            "ssh_command_pc": (
                f'ssh -i "{Path(__file__).resolve().parents[2] / "state" / "h3-vast-smoke"}" '
                f'-p {trial["ssh_port"]} root@{trial["ssh_host"]}'
                if not destroyed and trial.get("ssh_host") and trial.get("ssh_port") else None
            ),
            "deadline": trial["watchdog_deadline"],
            "boot_seconds": _seconds(trial, "rented_at", "ssh_ready_at"),
            "runtime_seconds": _seconds(trial, "ssh_ready_at", "runtime_ready_at"),
            "model_seconds": _seconds(trial, "runtime_ready_at", "models_ready_at"),
            "comfy_seconds": _seconds(trial, "models_ready_at", "comfy_ready_at"),
            "ready_seconds": _seconds(trial, "rented_at", "comfy_ready_at"),
            "local_cold_s": trial.get("local_cold_s"),
            "local_repeat_s": trial.get("local_repeat_s"),
            "cloud_cold_s": trial.get("cloud_cold_s"),
            "cloud_repeat_s": trial.get("cloud_repeat_s"),
        }
    except (FileNotFoundError, KeyError, ValueError, TypeError, json.JSONDecodeError):
        return None


def cloud_trial_history() -> list[dict]:
    records = []
    for path in sorted(TRIAL_DIR.glob("h3-cloud-trial*.json")):
        record = cloud_trial_snapshot(path)
        if record:
            records.append(record)
    return records


@app.get("/benchmarks/h3/videos/{run}")
async def cloud_trial_video(run: str) -> FileResponse:
    filename = TRIAL_VIDEOS.get(run)
    if filename is None or not (TRIAL_OUTPUT_DIR / filename).is_file():
        raise HTTPException(status_code=404)
    return FileResponse(TRIAL_OUTPUT_DIR / filename, media_type="video/mp4", filename=filename)


@app.get("/benchmarks/h3/logs/{kind}")
async def cloud_trial_log(kind: str) -> PlainTextResponse:
    filename = TRIAL_LOGS.get(kind)
    if filename is None or not (TRIAL_OUTPUT_DIR / filename).is_file():
        raise HTTPException(status_code=404)
    content = (TRIAL_OUTPUT_DIR / filename).read_text(encoding="utf-8", errors="replace")[-250_000:]
    for secret_file in (TRIAL_DIR.parent / "hf_read.txt", TRIAL_DIR.parent / "vast.txt"):
        if secret_file.is_file():
            secret = secret_file.read_text(encoding="utf-8").strip()
            if secret:
                content = content.replace(secret, "[REDACTED]")
    return PlainTextResponse(content)


class JobSubmission(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    graph: dict[str, dict[str, Any]]
    recipe: dict | None = None
    policy: Literal["local", "standard", "any"] = "local"
    min_vram_gb: int = Field(default=24, ge=1, le=192)
    architecture: Literal["any", "ampere", "ada", "blackwell"] = "any"
    # Higher number = picked up sooner; 100 is medium, 1 is the lowest.
    priority: int = Field(default=DEFAULT_PRIORITY, ge=MIN_PRIORITY, le=MAX_PRIORITY)


class WorkerRegistration(BaseModel):
    worker_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{3,128}$")
    name: str = Field(min_length=1, max_length=128)
    provider: Literal["Local", "Vast.ai", "Runpod", "DigitalOcean"]
    gpu: str = Field(min_length=1, max_length=128)
    architecture: Literal["ampere", "ada", "blackwell"]
    vram_gb: int = Field(ge=1, le=192)
    runtime: str = Field(default="pocket-h3-local", max_length=64)
    price_per_hour: float = Field(default=0, ge=0)
    capabilities: list[Literal['reference-preprocess-v1']] = Field(default_factory=list, max_length=1)


class TaskUpdate(BaseModel):
    lease_token: str
    status: Literal["running", "completed", "failed", "cancelled"]
    progress: int | None = Field(default=None, ge=0, le=100)
    message: str = Field(default="", max_length=2000)
    prompt_id: str | None = None
    artifact: dict[str, Any] | None = None


class WorkerHeartbeat(BaseModel):
    state: Literal['ready', 'unhealthy', 'draining']


class TaskRouteUpdate(BaseModel):
    pool: Literal["local", "standard", "any"]
    preferred_worker_id: str | None = Field(default=None, max_length=128)
    min_vram_gb: int | None = Field(default=None, ge=1, le=192)
    architecture: Literal["any", "ampere", "ada", "blackwell"] = "any"
    requeue: bool = False


class TaskPriorityUpdate(BaseModel):
    """Set ``priority`` (higher number = picked up sooner; 100 medium, 1 lowest)
    or ``move`` the task relative to its queued neighbours. Give exactly one."""
    priority: int | None = Field(default=None, ge=MIN_PRIORITY, le=MAX_PRIORITY)
    move: Literal["top", "up", "down", "bottom"] | None = None


ALLOWED_H3_NODES = {
    "VedaSparseAttention",
    "BlockSparseAttention",
    'TMPrepareReference',
    "ApplyMiniMaxH3NegPiP",
    "H3ShadowNegativeAdvanced",
    "MinimaxH3LatentUpscaler3DRefineHandoff",
    "MiniMaxH3NativeAudioLock",
    "VAELoader",
    "VAEDecodeAudio",
    "VAEDecode",
    "BasicScheduler",
    "Ref2VAVSAGatePatch",
    "SamplerCustomAdvanced",
    "BasicGuider",
    "UNETLoader",
    "CLIPLoader",
    "RandomNoise",
    "MiniMaxH3ReferenceToVideo",
    "MiniMaxH3AddGuide",
    "MiniMaxH3ImageToVideo",
    "LoadImage",
    "ImageScale",
    "PromptDirector",
    "ComfyMathExpression",
    "PrimitiveFloat",
    "KSamplerSelect",
    "VHS_VideoCombine",
    "ResolutionSelector",
    "UnloadAllModels",
    "MiniMaxH3SigmaShift",
    "MiniMaxLowVRAMAttention",
    "ModelAttentionBackend",
    "MiniMaxChunkFeedForward",
    "MiniMaxH3ReferencePack",
    "RegexReplace",
    "LoraLoaderModelOnly",
    "ImageFromBatch",
    "SaveImage",
    "SaveAudio",
    "FizgigH3StillLatent",
    "FizgigH3StillDecode",
}

ALLOWED_QWEN21_NODES = {
    'TMPrepareReference',
    "UNETLoader", "CLIPLoader", "VAELoader", "TextEncodeQwenImage21",
    "EmptyLatentImage", "QwenImage21Cache", "VAEDecode", "SaveImage",
    "LoadImage", "KSampler", "LoraLoaderModelOnly", "ViggleTurboLora",
    "RandomNoise", "BasicGuider", "KSamplerSelect", "ViggleTurboSigmas",
    "SamplerCustomAdvanced",
}


def validate_h3_graph(graph: dict[str, dict[str, Any]]) -> None:
    if not 15 <= len(graph) <= 100 or len(json.dumps(graph)) > 1_000_000:
        raise HTTPException(status_code=422, detail="Invalid H3 graph size")
    classes = {node.get("class_type") for node in graph.values()}
    ref_mode = "MiniMaxH3ReferenceToVideo" in classes
    frame_mode = "MiniMaxH3ImageToVideo" in classes
    video_output = "VHS_VideoCombine" in classes
    still_output = ({"ImageFromBatch", "SaveImage"} <= classes
                    or {"FizgigH3StillLatent", "FizgigH3StillDecode", "SaveImage"} <= classes)
    if not classes <= ALLOWED_H3_NODES or not (ref_mode or frame_mode) or video_output == still_output:
        raise HTTPException(
            status_code=422, detail="Only the approved Pocket H3 graph is supported"
        )
    for node in graph.values():
        if node.get("inputs", {}).get("openrouter_api_key"):
            raise HTTPException(status_code=422, detail="API keys must stay in the worker environment")
    try:
        h3_input_paths(graph)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def qwen21_graph(graph: dict[str, dict[str, Any]]) -> bool:
    return any(node.get("class_type") == "TextEncodeQwenImage21" for node in graph.values())


def validate_qwen21_graph(graph: dict[str, dict[str, Any]]) -> None:
    if not 8 <= len(graph) <= 80 or len(json.dumps(graph)) > 1_000_000:
        raise HTTPException(status_code=422, detail="Invalid Qwen Image 2.1 graph size")
    classes = {node.get("class_type") for node in graph.values()}
    required = {"UNETLoader", "CLIPLoader", "VAELoader", "TextEncodeQwenImage21", "QwenImage21Cache", "VAEDecode", "SaveImage"}
    native = "KSampler" in classes
    turbo = {"ViggleTurboLora", "ViggleTurboSigmas", "SamplerCustomAdvanced"} <= classes
    if not classes <= ALLOWED_QWEN21_NODES or not required <= classes or native == turbo:
        raise HTTPException(status_code=422, detail="Only the approved Pocket Qwen Image 2.1 graph is supported")
    unets = {node.get("inputs", {}).get("unet_name") for node in graph.values() if node.get("class_type") == "UNETLoader"}
    clips = {node.get("inputs", {}).get("clip_name") for node in graph.values() if node.get("class_type") == "CLIPLoader"}
    vaes = {node.get("inputs", {}).get("vae_name") for node in graph.values() if node.get("class_type") == "VAELoader"}
    if unets != {"qwen_image_2.1_int8_convrot.safetensors"} or clips != {"qwen3vl_8b_int8_convrot.safetensors"} or vaes != {"qwen_image_2.1_vae_bf16.safetensors"}:
        raise HTTPException(status_code=422, detail="Unsupported Qwen Image 2.1 model set")
    if turbo:
        turbo_nodes = [node for node in graph.values() if node.get("class_type") == "ViggleTurboLora"]
        if len(turbo_nodes) != 1 or turbo_nodes[0].get("inputs", {}).get("lora_name") != "Qwen-Image-2.1-viggle-turbo-v0.2.1-6step-lora-r128.safetensors":
            raise HTTPException(status_code=422, detail="Unsupported Qwen Image 2.1 Turbo LoRA")
    try:
        h3_input_paths(graph)
        if sum(node.get('class_type') == 'LoadImage' for node in graph.values()) > 10:
            raise ValueError("Qwen Image 2.1 supports at most 10 Pocket reference images")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


ALLOWED_FACE_REFINE_NODES = {
    "VHS_LoadVideo", "LoadImage", "UNETLoader", "CLIPLoader", "VAELoader",
    "LoraLoaderModelOnly", "H3FaceTrackCrop", "MiniMaxH3ReferenceToVideo",
    "H3InjectVideoLatent", "MiniMaxH3NativeAudioLock", "H3PerFrameDenoise",
    "BasicGuider", "KSamplerSelect", "BasicScheduler", "RandomNoise", "Ref2VAVSAGatePatch",
    "SamplerCustomAdvanced", "VAEDecode", "H3FaceStitch", "VHS_VideoCombine",
}


def face_refine_graph(graph: dict[str, dict[str, Any]]) -> bool:
    return any(node.get("class_type") == "H3FaceTrackCrop" for node in graph.values())


def validate_face_refine_graph(graph: dict[str, dict[str, Any]]) -> None:
    if not 15 <= len(graph) <= 40 or len(json.dumps(graph)) > 1_000_000:
        raise HTTPException(status_code=422, detail="Invalid H3 Face Refine graph size")
    classes = [node.get("class_type") for node in graph.values()]
    required = {"VHS_LoadVideo", "LoadImage", "H3FaceTrackCrop", "H3FaceStitch", "VHS_VideoCombine"}
    if not set(classes) <= ALLOWED_FACE_REFINE_NODES or not required <= set(classes)             or classes.count("VHS_LoadVideo") != 1 or classes.count("LoadImage") != 1:
        raise HTTPException(status_code=422, detail="Only the approved Pocket H3 Face Refine graph is supported")
    try:
        h3_input_paths(graph)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# LTX-2.5 SDR->HDR IC-LoRA (ltx-hdr-pipeline cloud graph): an RTX-VSR-upscaled SDR clip in, a 24 fps ACEScct
# ProRes 4444 XQ out. RTX VSR has no Linux build, so the upscale runs on the host before submission.
ALLOWED_LTX_HDR_NODES = {
    "UNETLoader", "LoraLoaderModelOnly", "LTXICLoRALoaderModelOnly", "VAELoader", "LTXVVAEForceFloat32",
    "LTXVLoadConditioning", "LTXVConditioning", "VHS_LoadVideo", "LTXVSDRToHDRWorkingSpace", "EmptyLTXVLatentVideo",
    "LTXAddVideoICLoRAGuide", "LTXVEmptyLatentAudio", "LTXVConcatAVLatent", "RandomNoise", "CFGGuider",
    "KSamplerSelect", "ManualSigmas", "SamplerCustomAdvanced", "LTXVSeparateAVLatent", "LTXVCropGuides",
    "VAEDecodeTiled", "ImageScale", "ImageFromBatch", "VHS_VideoCombine",
    "UnloadAllModels",   # frees the transformer before the full-VAE decode (5090 OOM otherwise)
}
LTX_HDR_MODELS = {
    "ltx-2.5-22b-dev-transformer-comfy-int8-convrot.safetensors", "ltx-2.5-22b-distilled-lora-450-bf16.safetensors",
    "ltx-2.5-22b-ic-lora-sdr-to-hdr-1.0.safetensors", "ltx-2.5-video-vae-conv-bf16.safetensors",
    "ltx-2.5-audio-vae-bf16.safetensors", "ltx-2.5-22b-ic-lora-sdr-to-hdr-scene-emb.safetensors",
}
# Official reference set (LTX-2.5_ICLoRA_SDR_to_HDR_Distilled.json): distilled transformer, no distill LoRA, full video VAE.
LTX_HDR_MODELS_REFERENCE = {
    "ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors",
    "ltx-2.5-22b-ic-lora-sdr-to-hdr-1.0.safetensors", "ltx-2.5-video-vae-bf16.safetensors",
    "ltx-2.5-audio-vae-bf16.safetensors", "ltx-2.5-22b-ic-lora-sdr-to-hdr-scene-emb.safetensors",
}

# reference transformer + conv video VAE (the full VAE's untiled fp32 decode OOMs a 32 GB 5090)
LTX_HDR_MODELS_REFERENCE_CONV = (LTX_HDR_MODELS_REFERENCE - {"ltx-2.5-video-vae-bf16.safetensors"}) | {"ltx-2.5-video-vae-conv-bf16.safetensors"}

def ltx_hdr_graph(graph: dict[str, dict[str, Any]]) -> bool:
    return any(node.get("class_type") == "LTXVSDRToHDRWorkingSpace" for node in graph.values())


def validate_ltx_hdr_graph(graph: dict[str, dict[str, Any]]) -> None:
    if not 15 <= len(graph) <= 60 or len(json.dumps(graph)) > 1_000_000:
        raise HTTPException(status_code=422, detail="Invalid LTX HDR graph size")
    classes = [node.get("class_type") for node in graph.values()]
    required = {"LTXVSDRToHDRWorkingSpace", "LTXAddVideoICLoRAGuide", "VHS_LoadVideo", "VHS_VideoCombine"}
    if (not set(classes) <= ALLOWED_LTX_HDR_NODES or not required <= set(classes)
            or classes.count("VHS_LoadVideo") != 1 or classes.count("VHS_VideoCombine") != 1):
        raise HTTPException(status_code=422, detail="Only the approved LTX SDR-to-HDR graph is supported")
    names = {value for node in graph.values() for key, value in node.get("inputs", {}).items()
             if key in {"unet_name", "lora_name", "vae_name", "file_name"} and isinstance(value, str)}
    if names not in (LTX_HDR_MODELS, LTX_HDR_MODELS_REFERENCE, LTX_HDR_MODELS_REFERENCE_CONV):
        raise HTTPException(status_code=422, detail="Unsupported LTX HDR model set")
    try:
        h3_input_paths(graph)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# RIFE x2 frame interpolation of an ACEScct ProRes (ltx-hdr-pipeline interp, farm mode): runs on the ltx-hdr pods.
ALLOWED_RIFE_NODES = {"VHS_LoadVideo", "RIFE VFI", "VHS_VideoCombine"}


def rife_graph(graph: dict[str, dict[str, Any]]) -> bool:
    return any(node.get("class_type") == "RIFE VFI" for node in graph.values())


def validate_rife_graph(graph: dict[str, dict[str, Any]]) -> None:
    classes = [node.get("class_type") for node in graph.values()]
    if (len(graph) != 3 or set(classes) != ALLOWED_RIFE_NODES or len(json.dumps(graph)) > 100_000
            or any(node.get("inputs", {}).get("ckpt_name") not in (None, "rife49.pth") for node in graph.values())):
        raise HTTPException(status_code=422, detail="Only the approved RIFE frame-interpolation graph is supported")
    try:
        h3_input_paths(graph)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def validate_pocket_graph(graph: dict[str, dict[str, Any]]) -> None:
    reference_preprocess.validate_nodes(graph)
    if reference_preprocess.is_graph(graph):
        reference_preprocess.validate_graph(graph)
    elif character_replacement.is_graph(graph):
        character_replacement.validate_graph(graph)
    elif any(node.get('class_type') == 'VoxCPM_TTS' for node in graph.values()):
        allowed = {'VoxCPM_AdvancedParams', 'VoxCPM_TTS', 'SaveAudioMP3', 'LoadAudio', 'SupaVoiceDenoiser', 'VoxCPM_VoiceCloning'}
        classes = [node.get('class_type') for node in graph.values()]
        if (not 3 <= len(graph) <= 6 or len(json.dumps(graph)) > 100000
                or not set(classes) <= allowed or classes.count('VoxCPM_TTS') != 1
                or classes.count('SaveAudioMP3') != 1):
            raise HTTPException(422, 'Only the approved voice workflow is supported')
        for node in graph.values():
            inputs = node.get('inputs', {})
            if node['class_type'] == 'VoxCPM_TTS' and inputs.get('model_name') != 'VoxCPM2':
                raise HTTPException(422, 'Unsupported voice model')
            if node['class_type'] == 'SaveAudioMP3' and inputs.get('filename_prefix') != 'voice/voxcpm2_mobile':
                raise HTTPException(422, 'Unsupported voice output path')
        try:
            h3_input_paths(graph)
        except ValueError as error:
            raise HTTPException(422, str(error))
    elif krea.is_graph(graph):
        krea.validate_graph(graph)
    elif ltx.is_graph(graph):
        ltx.validate_graph(graph)
        try:
            h3_input_paths(graph)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    elif qwen21_graph(graph):
        validate_qwen21_graph(graph)
    elif rife_graph(graph):
        validate_rife_graph(graph)
    elif ltx_hdr_graph(graph):
        validate_ltx_hdr_graph(graph)
    elif face_refine_graph(graph):
        validate_face_refine_graph(graph)
    else:
        validate_h3_graph(graph)


def cloud_profile_for_graph(graph: dict[str, dict[str, Any]]) -> str:
    if character_replacement.is_graph(graph):
        return "ref2v-int8"
    if krea.is_graph(graph):
        requested = {node.get('inputs', {}).get('lora_name') for node in graph.values()
                     if node.get('class_type') == 'LoraLoaderModelOnly'}
        if requested != {krea.IDENTITY}:
            raise HTTPException(422, 'Additional Krea LoRAs are installed only in the local worker pool')
        return 'krea2'
    if ltx.is_graph(graph):
        return 'ltx-hdr' if graph['24']['class_type'] == 'LTXVSaveHLG' else 'ltx-upscale'
    if ltx_hdr_graph(graph) or rife_graph(graph):
        return "ltx-hdr"
    if qwen21_graph(graph):
        profile = "qwen-image-21"
        manifest = json.loads((Path(__file__).resolve().parents[2] / "runtime/h3/models.lock.json").read_text(encoding="utf-8"))
        groups = set(manifest["profiles"][profile])
        available = {Path(item["file"]).name for item in manifest["artifacts"] if item["group"] in groups}
        requested = {node.get("inputs", {}).get("lora_name") for node in graph.values()
                     if node.get("class_type") in {"LoraLoaderModelOnly", "ViggleTurboLora"}}
        if not requested <= available:
            missing = sorted(str(name) for name in requested - available)
            raise HTTPException(status_code=422, detail="LoRA not in the Qwen cloud manifest: " + ", ".join(missing))
        return profile
    if any(node.get("inputs", {}).get("prompt_provider") == "openrouter"
           for node in graph.values()):
        if not prompt_secret_configured():
            raise HTTPException(status_code=422, detail="Configure the controller OpenRouter credential before using cloud prompt enhancement")
    for node in graph.values():
        inputs = node.get("inputs", {})
        if node.get("class_type") in {"PromptDirector", "MiniMaxH3ReferencePack"}:
            if inputs.get("prompt_provider", "none") not in {"none", "openrouter"}:
                raise HTTPException(status_code=422, detail="Cloud prompt enhancement supports OpenRouter only")
    models = {node.get("inputs", {}).get("unet_name") for node in graph.values()
              if node.get("class_type") == "UNETLoader"}
    if models == {"minimax_h3_ref2va_pruned_int8_convrot.safetensors"}:
        profile = "ref2v-int8"
    elif models == {'Minimax-h3_Singularity_ref2va_Pruned_v1.3_int8.safetensors'}:
        profile = 'ref2v-singularity'
    elif models == {'fasth3_omniref_8step_pruned_int8_convrot.safetensors'}:
        profile = 'fasth3-omni'
    else:
        raise HTTPException(status_code=422, detail="Cloud worker does not support this H3 checkpoint")
    manifest = json.loads((Path(__file__).resolve().parents[2] / "runtime/h3/models.lock.json").read_text(encoding="utf-8"))
    groups = set(manifest["profiles"][profile])
    encoders = {
        item['file'].replace('\\', '/').removeprefix('text_encoders/')
        for item in manifest['artifacts']
        if item['group'] in groups and item['file'].replace('\\', '/').startswith('text_encoders/')
    }
    requested_encoders = {
        str(node.get('inputs', {}).get('clip_name')).replace('\\', '/')
        for node in graph.values() if node.get('class_type') == 'CLIPLoader'
    }
    if not requested_encoders <= encoders:
        raise HTTPException(status_code=422, detail='Text encoder not in the H3 cloud profile: '
                            + ', '.join(sorted(requested_encoders - encoders))
                            + '. Use ' + ', '.join(sorted(encoders)))
    available = set()
    for item in manifest["artifacts"]:
        if item["group"] not in groups:
            continue
        installed = (str(item["local_dir"]).rstrip("/.") + "/" + item["file"]).lstrip("/").replace("\\", "/")
        available.add(installed.removeprefix("loras/") if installed.startswith("loras/") else Path(item["file"]).name)
    requested = {str(node.get("inputs", {}).get("gate_file" if node.get("class_type") == "Ref2VAVSAGatePatch" else "lora_name")).replace("\\", "/")
                 for node in graph.values()
                 if node.get("class_type") in {"LoraLoaderModelOnly", "Ref2VAVSAGatePatch"}}
    if not requested <= available:
        missing = sorted(str(name) for name in requested - available)
        raise HTTPException(status_code=422, detail="LoRA not in the cloud model manifest: " + ", ".join(missing))
    predictors = {Path(item['file']).name for item in manifest['artifacts']
                  if item['group'] in groups and item['local_dir'] == 'veda'}
    requested_predictors = {node.get('inputs', {}).get('predictor') for node in graph.values()
                            if node.get('class_type') == 'VedaSparseAttention'}
    if not requested_predictors <= predictors:
        missing = sorted(str(name) for name in requested_predictors - predictors)
        raise HTTPException(422, 'Veda predictor not in the cloud model manifest: ' + ', '.join(missing))
    return profile


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, store: QueueStore = STORE) -> Response:
    # The old path-mounted alias cannot support root-relative browser APIs.
    # Keep bookmarks working by directing them to the complete root app.
    if request.query_params.get("asset") == "app.css":
        return Response(
            (BASE_DIR / "static" / "app.css").read_text(encoding="utf-8"),
            media_type="text/css",
        )
    if not setup_status()["ready"]:
        return RedirectResponse("/setup", status_code=307)
    settings = get_settings()
    state = store.snapshot()
    managed_rentals = managed_rental_snapshots()
    active_rentals = [item for item in managed_rentals
                      if item.get("status") not in TERMINAL_RENTAL_STATES
                      and not item.get("destroyed_at")]
    for managed in active_rentals:
        if not managed.get("instance_id"):
            continue
        worker_id = managed.get("worker_id") or f"vast-{managed['instance_id']}"
        if not any(worker["id"] == worker_id and worker["status"] != "offline"
                   for worker in state["workers"]):
            state["metrics"]["burn_rate"] += float(managed.get("price_per_hour") or 0)
    # Ended rentals fold into a collapsed history so they cannot crowd live capacity.
    rental_history = [item for item in managed_rentals if item not in active_rentals]
    for rental in managed_rentals:
        rental.setdefault("estimated_cost", 0)
        rental["ssh_command"] =(f'ssh -i "{TRIAL_DIR / "h3-vast-smoke"}" -p {rental["ssh_port"]} '
                                 f'root@{rental["ssh_host"]}' if rental.get("ssh_host") else None)
    cloud_trials = cloud_trial_history()
    # Lifetime estimate: every managed rental plus the earlier benchmark trials.
    lifetime_spend = {
        "total": sum(float(item.get("estimated_cost") or 0) for item in [*managed_rentals, *cloud_trials]),
        "rentals": sum(1 for item in managed_rentals if item.get("rented_at")) + len(cloud_trials),
    }
    rental_by_worker = {item.get("worker_id") or f"vast-{item.get('instance_id')}": item
                        for item in active_rentals if item.get("instance_id")}
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "limits": {
                "gpus": settings.max_active_gpus,
                "per_gpu": settings.max_gpu_price_per_hour,
                "fleet": settings.max_total_price_per_hour,
                "lifetime": settings.max_instance_lifetime_hours,
                "idle": settings.idle_destroy_minutes,
            },
            "view_base": "/api/v1/media/file",
            "state": state,
            "cloud_trial": cloud_trial_snapshot(),
            "managed_rentals": managed_rentals,
            "active_rentals": active_rentals,
            "rental_history": rental_history,
            "rental_by_worker": rental_by_worker,
            "cloud_trials": cloud_trials,
            "lifetime_spend": lifetime_spend,
            "trial_videos": {key: (TRIAL_OUTPUT_DIR / name).is_file() for key, name in TRIAL_VIDEOS.items()},
            "trial_logs": {key: (TRIAL_OUTPUT_DIR / name).is_file() for key, name in TRIAL_LOGS.items()},
            "app_version": TENSOR_MASTER_VERSION,
            "prompt_version": PROMPT_VERSION,
        },
        # Revalidate every load so phones pick up new asset versions immediately.
        headers={"Cache-Control": "no-cache"},
    )


def runtime_for_graph(graph: dict, policy: str) -> str:
    if reference_preprocess.is_graph(graph):
        return reference_preprocess.RUNTIME
    if character_replacement.is_graph(graph):
        if policy in {'standard', 'any'}:
            return 'pocket-h3-cloud-ref2v-int8'
        return character_replacement.RUNTIME
    if any(node.get('class_type') == 'VoxCPM_TTS' for node in graph.values()):
        if policy == 'standard':
            raise HTTPException(422, 'Voice generation requires a local voice worker')
        return 'pocket-voice-local'
    if policy in {'standard', 'any'}:
        return 'pocket-h3-cloud-' + cloud_profile_for_graph(graph)
    if qwen21_graph(graph):
        return 'pocket-qwen-image-21-local'
    if krea.is_graph(graph):
        return 'pocket-krea2-local'
    if ltx.is_graph(graph):
        return 'pocket-ltx-local'
    return 'pocket-h3-local'


@app.post("/api/v1/jobs", dependencies=[POCKET_AUTH], status_code=201)
async def submit_job(payload: JobSubmission, store: QueueStore = STORE) -> dict[str, str]:
    """Queue a Pocket graph. Optional ``priority`` (default 100 = medium):
    a higher number is claimed sooner (1000 before 100; 1 is the lowest),
    ties run oldest first."""
    reference_preprocess.prepare_graph(payload.graph, get_settings().comfyui_input_dir)
    validate_pocket_graph(payload.graph)
    job_id = store.submit(
        name=payload.name,
        graph=payload.graph,
        policy=payload.policy,
        min_vram_gb=payload.min_vram_gb,
        architecture=payload.architecture,
        runtime=runtime_for_graph(payload.graph, payload.policy),
        recipe=payload.recipe,
        priority=payload.priority,
    )
    return {"job_id": job_id}


@app.post('/api/v1/ltx/prepare', dependencies=[POCKET_AUTH])
async def prepare_ltx_job(payload: ltx.RecipeRequest) -> dict:
    graph, recipe = await ltx.prepare(payload)
    validate_pocket_graph(graph)
    return {'graph': graph, 'recipe': recipe}


@app.post('/api/v1/ltx/upload')
async def upload_ltx_video(request: Request) -> dict:
    return await ltx.upload(request)


@app.post("/api/v1/creative", dependencies=[POCKET_AUTH])
async def create_creative_batch(payload: creative.Brief, store: QueueStore = STORE) -> dict:
    reference_preprocess.prepare_graph(payload.graph, get_settings().comfyui_input_dir)
    validate_h3_graph(payload.graph)
    if payload.recipe.get("mode") != "h3" or not all(key in payload.graph for key in ("129", "131")):
        raise HTTPException(400, "Creative Engine requires a Pocket H3 workflow")
    if payload.graph["131"].get("class_type") not in {"MiniMaxH3ImageToVideo", "MiniMaxH3ReferenceToVideo"}:
        raise HTTPException(400, "Unsupported creative workflow")
    if payload.graph["131"]["class_type"] == "MiniMaxH3ReferenceToVideo" and "181" not in payload.graph:
        raise HTTPException(400, "Reference workflow needs ReferencePack")
    profile = cloud_profile_for_graph(payload.graph) if payload.policy in {"standard", "any"} else None
    concepts = await creative.develop(payload)
    return creative.save_draft(store, payload, concepts, f"pocket-h3-cloud-{profile}" if profile else "pocket-h3-local")


@app.post("/api/v1/qwen/enhance", dependencies=[POCKET_AUTH])
async def enhance_qwen_prompt(payload: qwen_prompt.EnhanceRequest) -> dict:
    return await qwen_prompt.enhance(payload)


@app.get("/api/v1/creative", dependencies=[POCKET_AUTH])
async def list_creative_batches(store: QueueStore = STORE) -> list:
    return creative.recent_batches(store)


@app.get("/api/v1/creative/{batch_id}", dependencies=[POCKET_AUTH])
async def creative_batch_status(batch_id: str, store: QueueStore = STORE) -> dict:
    return creative.read_batch(store, batch_id)


@app.post("/api/v1/creative/{batch_id}/queue", dependencies=[POCKET_AUTH])
async def queue_creative_batch(batch_id: str, payload: creative.Selection, store: QueueStore = STORE) -> dict:
    return creative.queue_batch(store, batch_id, payload, validate_h3_graph)


@app.get("/api/v1/jobs/{job_id}", dependencies=[POCKET_AUTH])
async def job_status(job_id: str, store: QueueStore = STORE) -> dict:
    job = store.job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    job["edit"] = edit_setup(store, job_id, get_settings().comfyui_input_dir)
    return job


@app.get("/api/v1/outputs")
async def completed_outputs(request: Request, store: QueueStore = STORE,
                            limit: Annotated[int, Query(ge=1, le=1000)] = 1000) -> list[dict]:
    """Authoritative gallery feed; artifacts are already stored by Tensor Master."""
    records = store.output_records(limit=limit)
    missing = {record["job_id"] for record in records if record["recipe"] is None}
    historic = creative.historic_recipe_index(store, missing)
    results = []
    for record in records:
        recipe = normalize_recipe(record["recipe"] or historic.get(record["job_id"]))
        if not recipe:
            recipe = edit_setup(store, record["job_id"], get_settings().comfyui_input_dir)["recipe"] or {}
            if recipe:   # rebuilt from the graph: keep it, so the next gallery load is instant
                store.save_recipe_if_missing(record["job_id"], recipe)
        artifact = record["artifact"]
        if not artifact.get("filename"):
            continue
        if not artifact.get("url"):
            view_url = str(request.url.replace(path='/api/v1/media/file', query=''))
            artifact["url"] = view_url + "?" + urlencode({
                "filename": artifact["filename"],
                "subfolder": artifact.get("subfolder", ""),
                "type": artifact.get("type", "output"),
            })
        try:
            modified = datetime.fromisoformat(record["created_at"]).timestamp()
        except (TypeError, ValueError):
            modified = 0
        results.append({
            **artifact,
            "job_id": record["job_id"],
            "task_id": record["task_id"],
            "prompt_id": record["prompt_id"],
            "comfy_prompt_id": record["comfy_prompt_id"],
            "job_name": record["job_name"],
            "worker_id": record["worker_id"],
            "recipe": recipe,
            "modified": modified,
            "status": "Finished",
        })
    return results


@app.get("/api/v1/tasks/{task_id}/inputs/{asset_path:path}", dependencies=[AUTH])
async def task_input(task_id: str, asset_path: str,
                     lease_token: Annotated[str | None, Header(alias="X-Task-Lease")] = None,
                     store: QueueStore = STORE) -> FileResponse:
    if not store.task_lease_valid(task_id, lease_token):
        raise HTTPException(status_code=403, detail="Task lease required")
    graph = store.task_graph(task_id)
    allowed = h3_input_paths(graph) if graph else set()
    if graph:
        allowed.update('h3_mobile/preprocessed-cache/' + name for name in reference_preprocess.expected_cache_files(graph))
    if graph is None or asset_path not in allowed:
        raise HTTPException(status_code=404, detail="Input is not part of this task")
    root = get_settings().comfyui_input_dir.resolve()
    path = (root / asset_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(status_code=404, detail="Input file is unavailable")
    return FileResponse(path)


@app.put("/api/v1/tasks/{task_id}/output/{filename}", dependencies=[AUTH])
async def upload_task_output(task_id: str, filename: str, request: Request,
                             lease_token: Annotated[str | None, Header(alias="X-Task-Lease")] = None,
                             store: QueueStore = STORE) -> dict[str, str]:
    if not store.task_lease_valid(task_id, lease_token):
        raise HTTPException(status_code=403, detail="Task lease required")
    if store.task_graph(task_id) is None or not re.fullmatch(r"[A-Za-z0-9_.-]{1,180}", filename):
        raise HTTPException(status_code=404, detail="Unknown task or invalid filename")
    root = get_settings().cloud_output_dir / task_id
    root.mkdir(parents=True, exist_ok=True)
    destination = root / filename
    temporary = root / f".{filename}.partial"
    total = 0
    try:
        with temporary.open("wb") as output:
            async for chunk in request.stream():
                total += len(chunk)
                if total > 512 * 1024 * 1024:
                    raise HTTPException(status_code=413, detail="Output exceeds 512 MiB limit")
                output.write(chunk)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {"url": f"/outputs/{task_id}/{filename}", "bytes": str(total)}


@app.put('/api/v1/tasks/{task_id}/reference-cache/{filename}', dependencies=[AUTH])
async def publish_reference_cache(task_id: str, filename: str, request: Request,
                                  lease_token: Annotated[str | None, Header(alias='X-Task-Lease')] = None,
                                  store: QueueStore = STORE) -> dict:
    if not store.task_lease_valid(task_id, lease_token):
        raise HTTPException(403, 'Task lease required')
    graph = store.task_graph(task_id)
    if not graph or filename not in reference_preprocess.expected_cache_files(graph):
        raise HTTPException(403, 'Cache file is not part of this task')
    root = get_settings().comfyui_input_dir
    cache = root / 'h3_mobile' / 'preprocessed-cache'
    cache.mkdir(parents=True, exist_ok=True)
    from uuid import uuid4
    temporary = cache / f'.{uuid4().hex}.upload'
    total = 0
    try:
        with temporary.open('wb') as stream:
            async for chunk in request.stream():
                total += len(chunk)
                if total > 128 * 1024**2:
                    raise HTTPException(413, 'Reference cache image exceeds 128 MiB')
                stream.write(chunk)
        return reference_preprocess.publish_cache(graph, filename, temporary, root)
    finally:
        temporary.unlink(missing_ok=True)


@app.get("/api/v1/tasks/{task_id}/spec")
async def task_spec(task_id: str, store: QueueStore = STORE) -> dict:
    """Readable render settings for the task inspector (same exposure as the outputs feed)."""
    spec = job_spec(store, task_id)
    if spec is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return spec


@app.get("/outputs/{task_id}/{filename}")
async def view_task_output(task_id: str, filename: str, store: QueueStore = STORE) -> FileResponse:
    if store.task_graph(task_id) is None or not re.fullmatch(r"[A-Za-z0-9_.-]{1,180}", filename):
        raise HTTPException(status_code=404)
    path = get_settings().cloud_output_dir / task_id / filename
    if not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path)


@app.get("/api/v1/vast/offers", dependencies=[AUTH])
async def vast_offers(gpu: Literal["rtx5090", "rtxpro6000ws"] = "rtx5090") -> dict:
    """Read-only marketplace search; never returns the API key or raw provider metadata."""
    settings = get_settings()
    key = settings.vast_api_key
    if not key and settings.vast_api_key_file.is_file():
        key = settings.vast_api_key_file.read_text(encoding="utf-8").strip()
    if not key:
        raise HTTPException(status_code=409, detail="Vast API key is not configured")
    gpu_name, min_vram_gb = VAST_GPU_PROFILES[gpu]
    try:
        offers = VastProvider(key).search_offers(
            OfferSearch(
                gpu_names=(gpu_name,),
                min_vram_gb=min_vram_gb,
                max_price_per_hour=settings.max_gpu_price_per_hour,
                min_reliability=0.95,
                disk_gb=150,
            )
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Vast offer search failed") from exc
    return {
        "gpu": gpu,
        "max_price_per_hour": settings.max_gpu_price_per_hour,
        "disk_gb": 150,
        "offers": [
            {
                "id": offer.id,
                "gpu_name": offer.gpu_name,
                "vram_gb": round(offer.vram_gb, 1),
                "price_per_hour": round(offer.price_per_hour, 3),
                "reliability": round(offer.reliability, 3),
            }
            for offer in offers[:20]
        ],
    }


@app.post("/api/v1/workers/register", dependencies=[AUTH])
async def register_worker(payload: WorkerRegistration, request: Request, store: QueueStore = STORE) -> dict[str, str]:
    principal = request.state.principal
    if principal['role'] == 'worker' and principal['subject'] != payload.worker_id:
        raise HTTPException(403, "Worker registration identity does not match")
    store.register_worker(
        worker_id=payload.worker_id,
        name=payload.name,
        provider=payload.provider,
        gpu=payload.gpu,
        architecture=payload.architecture,
        vram_gb=payload.vram_gb,
        runtime=payload.runtime,
        price_per_hour=payload.price_per_hour,
        capabilities=payload.capabilities,
    )
    result = {"status": "registered"}
    if principal['role'] == 'bootstrap':
        result['worker_token'] = authority(get_settings().token_file).enroll_worker(payload.worker_id)
    return result


@app.post("/api/v1/workers/{worker_id}/heartbeat", dependencies=[AUTH])
async def heartbeat(worker_id: str, payload: WorkerHeartbeat | None = None, store: QueueStore = STORE) -> dict[str, bool]:
    if not store.heartbeat(worker_id, payload.state if payload else None):
        raise HTTPException(status_code=404, detail="Worker not registered")
    return {"ok": True}


@app.post("/api/v1/workers/{worker_id}/claim", dependencies=[AUTH])
async def claim(worker_id: str, store: QueueStore = STORE) -> dict:
    task = store.claim(worker_id)
    if task:
        reference_preprocess.hydrate_cache_paths(task['graph'], get_settings().comfyui_input_dir)
    return {"task": task}


class ResumeTask(BaseModel):
    task_id: str
    lease_token: str


@app.post("/api/v1/workers/{worker_id}/resume", dependencies=[AUTH])
async def resume_task(worker_id: str, payload: ResumeTask, store: QueueStore = STORE) -> dict:
    if not store.resume_task(worker_id, payload.task_id, payload.lease_token):
        raise HTTPException(409, "Task was already completed, cancelled, or reassigned")
    return {"ok": True}


@app.post("/api/v1/tasks/{task_id}", dependencies=[AUTH])
async def update_task(
    task_id: str, payload: TaskUpdate, store: QueueStore = STORE
) -> dict[str, bool]:
    success = store.update_task(
        task_id,
        payload.lease_token,
        status=payload.status,
        progress=payload.progress,
        message=payload.message,
        prompt_id=payload.prompt_id,
        artifact=payload.artifact,
    )
    if not success:
        raise HTTPException(status_code=409, detail="Task lease is no longer valid")
    return {"ok": True}


@app.get("/api/v1/tasks/{task_id}/control", dependencies=[AUTH])
async def task_control(
    task_id: str,
    x_task_lease: Annotated[str | None, Header()] = None,
    store: QueueStore = STORE,
) -> dict[str, bool]:
    control = store.task_control(task_id, x_task_lease)
    if control is None:
        raise HTTPException(status_code=409, detail="Task lease is no longer valid")
    return control


@app.post("/api/v1/operator/tasks/{task_id}/requeue")
async def operator_requeue(task_id: str, request: Request, store: QueueStore = STORE) -> dict:
    require_admin(request, mutation=True)
    if not store.requeue_task(task_id):
        raise HTTPException(status_code=409, detail="Only completed, failed, or cancelled tasks can be requeued")
    return {"ok": True, "status": "queued"}


@app.post("/api/v1/operator/tasks/{task_id}/remove")
async def operator_remove(task_id: str, request: Request, store: QueueStore = STORE) -> dict:
    require_admin(request, mutation=True)
    if not store.remove_task(task_id):
        raise HTTPException(409, "Task not found or still running. Stop it before removing its queue entry.")
    return {"ok": True}


def start_local_recovery(gpu: int, arguments: list[str]) -> None:
    root = Path(__file__).resolve().parents[2]
    with (root / 'state' / f'gpu{gpu}-recovery.log').open('a', encoding='utf-8') as output:
        subprocess.Popen([sys.executable, str(root / 'tools/recover_local_gpu.py'), *arguments],
                         cwd=root, stdout=output, stderr=output,
                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


@app.post('/api/v1/operator/tasks/{task_id}/restart-worker', status_code=202)
async def operator_restart_worker(task_id: str, request: Request, store: QueueStore = STORE) -> dict:
    require_admin(request, mutation=True)
    from tools.recover_local_gpu import recovery_target
    try:
        gpu, _ = recovery_target(store, task_id)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    start_local_recovery(gpu, ['--task', task_id])
    return {'ok': True, 'status': 'restarting', 'gpu': gpu}


@app.post('/api/v1/operator/workers/{worker_id}/restart', status_code=202)
async def operator_restart_local_worker(worker_id: str, request: Request, store: QueueStore = STORE) -> dict:
    require_admin(request, mutation=True)
    from tools.recover_local_gpu import worker_recovery_target
    try:
        gpu, _ = worker_recovery_target(store, worker_id)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    start_local_recovery(gpu, ['--gpu', str(gpu)])
    return {'ok': True, 'status': 'restarting', 'gpu': gpu}


@app.post("/api/v1/operator/tasks/{task_id}/route")
async def operator_route_task(
    task_id: str,
    payload: TaskRouteUpdate,
    request: Request,
    store: QueueStore = STORE,
) -> dict:
    require_admin(request, mutation=True)
    graph = store.task_graph(task_id)
    if graph is None:
        raise HTTPException(status_code=404, detail="Task not found")
    runtime = runtime_for_graph(graph, payload.pool)
    default_vram = 24
    try:
        route = store.route_task(
            task_id,
            policy=payload.pool,
            min_vram_gb=payload.min_vram_gb or default_vram,
            architecture=payload.architecture,
            runtime=runtime,
            preferred_worker_id=payload.preferred_worker_id or None,
            requeue=payload.requeue,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if route is None:
        raise HTTPException(status_code=409, detail="Stop a running task before changing its route")
    return {"ok": True, "route": route, "status": "queued" if payload.requeue else None}


@app.post("/api/v1/operator/tasks/{task_id}/priority")
async def operator_task_priority(
    task_id: str,
    payload: TaskPriorityUpdate,
    request: Request,
    store: QueueStore = STORE,
) -> dict:
    """Re-rank a task. Higher priority number = claimed sooner (100 medium,
    1 lowest); ``rank`` in the response is the queue position (1 = next)."""
    require_admin(request, mutation=True)
    if (payload.priority is None) == (payload.move is None):
        raise HTTPException(status_code=422, detail="Give either priority or move")
    if store.task_execution(task_id) is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if payload.move is not None:
        result = store.move_task(task_id, payload.move)
        if result is None:
            raise HTTPException(status_code=409, detail="Only queued tasks can be moved")
    else:
        result = store.set_task_priority(task_id, payload.priority)
        if result is None:
            raise HTTPException(status_code=409, detail="Stop a running task before changing its priority")
    return {"ok": True, **result}


@app.post("/api/v1/operator/tasks/{task_id}/cancel")
async def operator_cancel(task_id: str, request: Request, store: QueueStore = STORE) -> dict:
    require_admin(request, mutation=True)
    status = store.cancel_task(task_id)
    if status is None:
        raise HTTPException(status_code=409, detail="Only queued or running tasks can be cancelled")
    # The leasing worker polls task control and interrupts its own runtime.
    return {"ok": True, "status": status, "warning": None}



@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "version": TENSOR_MASTER_VERSION,
        "prompt_version": PROMPT_VERSION,
    }
