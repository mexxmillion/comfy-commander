"""First-run onboarding and the public Community Edition capability catalogue."""

from __future__ import annotations

import os
import platform
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from .character_replacement import PrepareRequest, build_graph
from .community_loras import (
    RegisterLora,
    UploadLora,
    local_candidates,
    read_manifest,
    register_lora,
    remove_entry,
    upload_lora,
)
from .config import get_settings
from .credential_paths import openrouter_secret_file, provider_secret, provider_secret_source
from .identity import authority
from .providers.runpod import RunpodProvider
from .rental_ui import require_admin

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")
router = APIRouter()

COMMUNITY_PROFILES = {
    "qwen-image-21": {
        "name": "Qwen Image 2.1",
        "kind": "image",
        "summary": "Text-to-image and reference editing, with optional Viggle Turbo.",
        "minimum_vram_gb": 16,
        "recommended_vram_gb": 24,
        "gated": False,
    },
    "ref2v-int8": {
        "name": "MiniMax H3 Ref2V Community",
        "kind": "video",
        "summary": "Public INT8 Ref2VA with LightX2V 4/8-step Ref2V LoRAs by default, with optional sparse attention.",
        "minimum_vram_gb": 16,
        "recommended_vram_gb": 24,
        "gated": False,
    },
    "ref2v-singularity": {
        "name": "MiniMax H3 Singularity",
        "kind": "video",
        "summary": "Singularity Ref2VA with supported 4-step and 8-step Turbo recipes.",
        "minimum_vram_gb": 16,
        "recommended_vram_gb": 24,
        "gated": True,
    },
    "fasth3-omni": {
        "name": "FastH3 Omni",
        "kind": "video",
        "summary": "Fast H3 Omni INT8, native eight-step profile with VSA sparse attention by default.",
        "minimum_vram_gb": 16,
        "recommended_vram_gb": 24,
        "gated": True,
    },
    "flf2v-int8": {
        "name": "H3 First / Last Frame",
        "kind": "video",
        "summary": "Public FL2VA INT8 for first-frame, last-frame, both-keyframe or text-only video with matching LightX2V FL2V LoRAs.",
        "minimum_vram_gb": 16, "recommended_vram_gb": 24, "gated": True,
    },
    "h3-character-replacement": {
        "name": "H3 Character Replacement",
        "kind": "guided-workflow",
        "summary": "Qwen reference preparation, reviewed SAM3 target-video tracking, and H3 Ref2VA.",
        "minimum_vram_gb": 16,
        "recommended_vram_gb": 24,
        "gated": True,
        "execution_profile": "ref2v-int8",
    },
}


def _configured(path: Path) -> bool:
    try:
        return path.is_file() and bool(path.read_text(encoding="utf-8").strip())
    except OSError:
        return False


def setup_status() -> dict:
    settings = get_settings()
    try:
        password = bool(authority(settings.token_file).setting("password"))
    except (OSError, RuntimeError, sqlite3.Error):
        password = False
    return {
        "password": password,
        "runpod": bool(provider_secret("runpod", settings.runpod_api_key_file)),
        "vast": bool(provider_secret("vast", settings.vast_api_key_file)),
        "huggingface": bool(provider_secret("huggingface", settings.huggingface_token_file)),
        "openrouter": _configured(openrouter_secret_file()) or bool(os.getenv("OPENROUTER_API_KEY", "").strip()),
        "ready": password,
    }


class SecretSetup(BaseModel):
    service: Literal["runpod", "vast", "huggingface", "openrouter"]
    key: str = Field(min_length=8, max_length=4096)


def _write_secret(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(value.strip() + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _test_secret(service: str, key: str) -> str:
    settings = get_settings()
    if service == "runpod":
        RunpodProvider(key).offers(settings.max_gpu_price_per_hour)
        return "RunPod connected"
    if service == "vast":
        with httpx.Client(timeout=20, headers={"Authorization": f"Bearer {key}"}) as api:
            response = api.get("https://console.vast.ai/api/v0/users/current/")
        if response.is_error:
            raise RuntimeError(f"Vast.ai rejected the credential (HTTP {response.status_code})")
        return "Vast.ai connected"
    if service == "huggingface":
        with httpx.Client(timeout=20, headers={"Authorization": f"Bearer {key}"}) as api:
            response = api.get("https://huggingface.co/api/whoami-v2")
        if response.is_error:
            raise RuntimeError(f"Hugging Face rejected the token (HTTP {response.status_code})")
        return "Hugging Face connected"
    with httpx.Client(timeout=20, headers={"Authorization": f"Bearer {key}"}) as api:
        response = api.get("https://openrouter.ai/api/v1/models")
    if response.is_error:
        raise RuntimeError(f"OpenRouter rejected the key (HTTP {response.status_code})")
    return "OpenRouter connected"


def _secret_path(service: str) -> Path:
    settings = get_settings()
    return {
        "runpod": settings.runpod_api_key_file,
        "vast": settings.vast_api_key_file,
        "huggingface": settings.huggingface_token_file,
        "openrouter": openrouter_secret_file(),
    }[service]


@router.get("/setup", response_class=HTMLResponse)
@router.get("/settings", response_class=HTMLResponse)
async def setup_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="setup.html",
        context={"status": setup_status(), "profiles": COMMUNITY_PROFILES,
                 "settings_mode": request.url.path == "/settings"},
        headers={"Cache-Control": "no-store"},
    )


@router.get("/api/v1/setup/status")
async def status() -> dict:
    settings = get_settings()
    sources = {
        "runpod": provider_secret_source("runpod", settings.runpod_api_key_file),
        "vast": provider_secret_source("vast", settings.vast_api_key_file),
        "huggingface": provider_secret_source("huggingface", settings.huggingface_token_file),
        "openrouter": "environment" if os.getenv("OPENROUTER_API_KEY", "").strip() else
                      ("private file" if setup_status()["openrouter"] else None),
    }
    return {"services": setup_status(), "credential_sources": sources, "profiles": COMMUNITY_PROFILES}


def _local_diagnostics() -> dict:
    """Return sanitized, read-only first-run checks. Never starts a worker."""
    settings = get_settings()
    gpus: list[dict] = []
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi:
        try:
            result = subprocess.run(
                [nvidia_smi, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=4, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            for line in result.stdout.splitlines():
                name, _, memory = line.rpartition(",")
                if name.strip() and memory.strip().isdigit():
                    gpus.append({"name": name.strip(), "vram_gb": round(int(memory.strip()) / 1024, 1)})
        except (OSError, subprocess.SubprocessError):
            pass
    comfy_reachable = False
    if settings.comfyui_url.startswith(("http://127.0.0.1", "http://localhost")):
        try:
            comfy_reachable = not httpx.get(f"{settings.comfyui_url.rstrip('/')}/system_stats", timeout=1.5).is_error
        except httpx.HTTPError:
            pass
    return {
        "platform": platform.system(),
        "gpus": gpus,
        "comfyui_reachable": comfy_reachable,
        "docker_available": bool(shutil.which("docker")),
        "tailscale_available": bool(shutil.which("tailscale") or Path("C:/Program Files/Tailscale/tailscale.exe").is_file()),
        "models_detected": any(path.is_dir() and next(path.rglob("*.safetensors"), None) for path in settings.model_directories),
        "starts_nothing": True,
    }


@router.get("/api/v1/setup/diagnostics")
async def diagnostics() -> dict:
    return _local_diagnostics()


@router.get("/api/v1/setup/phone-access")
def setup_phone_access(request: Request) -> dict:
    require_admin(request)
    from .phone_access import phone_access
    port = (request.scope.get("server") or (None, 8000))[1] or 8000
    return phone_access(port)


@router.get("/api/v1/setup/cloud-ssh")
def cloud_ssh_status(request: Request) -> dict:
    require_admin(request)
    key = get_settings().cloud_ssh_key_file
    # Paths and public readiness only. Private key contents never leave the host.
    command = "ssh-keygen -t ed25519 -f '" + str(key).replace("'", "''") + "' -C comfy-commander"
    return {"ready": key.is_file() and Path(str(key) + ".pub").is_file(),
            "command": command, "directory": str(key.parent)}


@router.post("/api/v1/setup/phone-access")
def enable_setup_phone_access(request: Request) -> dict:
    require_admin(request, mutation=True)
    from .phone_access import enable_phone_access
    port = (request.scope.get("server") or (None, 8000))[1] or 8000
    try:
        return enable_phone_access(port)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise HTTPException(409, "Private phone access could not be enabled. Sign into Tailscale and check existing Serve routes on this computer.") from exc


@router.post("/api/v1/setup/secret")
async def save_secret(payload: SecretSetup, request: Request) -> dict:
    require_admin(request, mutation=True)
    key = payload.key.strip()
    try:
        message = _test_secret(payload.service, key)
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    path = _secret_path(payload.service)
    if payload.service == "openrouter" and path.suffix == ".dpapi":
        # Community setup is portable; use its private credential directory instead
        # of overwriting an existing Windows DPAPI credential managed by ComfyUI.
        path = get_settings().token_file.parent / "openrouter_api_key"
    _write_secret(path, key)
    return {"ok": True, "service": payload.service, "message": message}


@router.delete("/api/v1/setup/secret/{service}")
async def delete_secret(service: Literal["runpod", "vast", "huggingface", "openrouter"], request: Request) -> dict:
    require_admin(request, mutation=True)
    settings = get_settings()
    paths = [_secret_path(service)]
    if service == "openrouter":
        # Remove the portable community credential, but never delete a DPAPI file
        # that another ComfyUI installation may own.
        paths = [settings.token_file.parent / "openrouter_api_key"]
    removed = False
    for path in paths:
        try:
            if path.is_file():
                path.unlink()
                removed = True
        except OSError as exc:
            raise HTTPException(500, "Could not remove the local credential") from exc
    still_connected = bool(setup_status()[service])
    message = ("Local credential removed. An environment-provided credential is still active."
               if still_connected else
               "Local credential removed. Provider-side revocation is separate.")
    return {"ok": True, "service": service, "removed": removed,
            "connected": still_connected, "message": message}


@router.get("/api/v1/setup/loras")
async def list_loras(request: Request) -> dict:
    require_admin(request)
    return {
        "local": local_candidates(),
        "registered": read_manifest()["loras"],
        "profiles": [key for key in COMMUNITY_PROFILES if key != "h3-character-replacement"],
        "huggingface_connected": setup_status()["huggingface"],
    }


@router.post("/api/v1/setup/loras/register")
async def add_registered_lora(payload: RegisterLora, request: Request) -> dict:
    require_admin(request, mutation=True)
    try:
        item = register_lora(payload)
    except (OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, "Hugging Face could not verify that LoRA") from exc
    return {"ok": True, "lora": item}


@router.post("/api/v1/setup/loras/upload")
async def add_uploaded_lora(payload: UploadLora, request: Request) -> dict:
    require_admin(request, mutation=True)
    try:
        item = upload_lora(payload)
    except (OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, "Hugging Face could not upload that LoRA") from exc
    return {"ok": True, "lora": item}


@router.delete("/api/v1/setup/loras/{entry_id}")
async def unregister_lora(entry_id: str, request: Request) -> dict:
    require_admin(request, mutation=True)
    if not remove_entry(entry_id):
        raise HTTPException(404, "LoRA registration not found")
    return {"ok": True, "message": "Removed from future workers; no local or Hugging Face file was deleted."}


@router.get("/api/v1/community/profiles")
async def profiles() -> dict:
    return {"profiles": COMMUNITY_PROFILES}


@router.get("/api/v1/community/agent-manifest")
async def agent_manifest() -> dict:
    return {
        "name": "Comfy Commander by Tensor Master",
        "description": "Agent-drivable ComfyUI studio and GPU scheduler",
        "read_only": ["GET /health", "GET /api/v1/community/profiles", "GET /api/v1/jobs/{job_id}"],
        "builders": ["POST /api/v1/community/character-replacement/prepare"],
        "mutations": ["POST /api/v1/jobs"],
        "human_admin_only": [
            "POST/DELETE /api/v1/setup/secret/*",
            "POST/DELETE /api/v1/setup/loras/*",
        ],
        "rules": [
            "Never place credentials in prompts, graphs, recipes, logs, or command arguments.",
            "Validate a selected profile and inputs before submission.",
            "A timed-out submission may have succeeded; reconcile before retrying.",
            "Rentals require an authenticated human session and explicit cost limits.",
            "Agents must not upload LoRAs or change credentials unless a human explicitly requests that exact action.",
        ],
    }


def character_replacement_recipe() -> dict:
    return {
        "id": "h3-character-replacement-v1",
        "status": "one-shot-or-guided",
        "stages": [
            "Prepare scene-matched identity and wardrobe references with Qwen Image 2.1",
            "Normalize the source once and plan exclusive frame intervals",
            "Track the intended performer from the target description using SAM3_Detect and SAM3_VideoTrack",
            "Invert only the tracked target pixels and retain normal partner/background colors",
            "Use that aligned video as the H3 Ref2VA video reference",
            "Render H3 in the same job, preserve source audio with audio lock, then review motion, identity and seams",
        ],
        "default_graph_rule": "No MiniMaxH3AddGuide image nodes; the tracked target-only inverted VIDEO is the motion guide.",
        "limitations": [
            "SAM3 weights are gated and require the user to accept their upstream license.",
            "Tracking and output require visual review; completion is not creative acceptance.",
            "The public profile does not include private identity, Eros, Dolores, or personal LoRAs.",
        ],
    }


@router.get("/api/v1/community/workflows/character-replacement")
async def replacement_workflow() -> dict:
    return character_replacement_recipe()


@router.post("/api/v1/community/character-replacement/prepare")
async def prepare_replacement(payload: PrepareRequest) -> dict:
    return {
        "graph": build_graph(payload),
        "recipe": character_replacement_recipe(),
        "next": "Submit the graph, then visually review both the mask and target-guide videos before H3 generation.",
    }
