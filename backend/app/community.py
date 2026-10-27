"""First-run onboarding and the public Community Edition capability catalogue."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from .character_replacement import PrepareRequest, build_graph
from .config import get_settings
from .credential_paths import openrouter_secret_file
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
        "minimum_vram_gb": 24,
        "gated": False,
    },
    "ref2v-int8": {
        "name": "MiniMax H3 Ref2V Community",
        "kind": "video",
        "summary": "Public INT8 Ref2VA with native, Turbo and ACC recipe choices.",
        "minimum_vram_gb": 30,
        "gated": False,
    },
    "ref2v-singularity": {
        "name": "MiniMax H3 Singularity",
        "kind": "video",
        "summary": "Singularity Ref2VA with supported 4-step and 8-step Turbo recipes.",
        "minimum_vram_gb": 20,
        "gated": True,
    },
    "fasth3-omni": {
        "name": "FastH3 Omni",
        "kind": "video",
        "summary": "Fast H3 Omni INT8, native eight-step profile.",
        "minimum_vram_gb": 24,
        "gated": True,
    },
    "h3-character-replacement": {
        "name": "H3 Character Replacement",
        "kind": "guided-workflow",
        "summary": "Qwen reference preparation, reviewed SAM3 target-video tracking, and H3 Ref2VA.",
        "minimum_vram_gb": 30,
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
        "runpod": _configured(settings.runpod_api_key_file),
        "vast": bool(settings.vast_api_key) or _configured(settings.vast_api_key_file),
        "huggingface": _configured(settings.huggingface_token_file),
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
async def setup_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="setup.html",
        context={"status": setup_status(), "profiles": COMMUNITY_PROFILES},
        headers={"Cache-Control": "no-store"},
    )


@router.get("/api/v1/setup/status")
async def status() -> dict:
    return {"services": setup_status(), "profiles": COMMUNITY_PROFILES}


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


@router.get("/api/v1/community/profiles")
async def profiles() -> dict:
    return {"profiles": COMMUNITY_PROFILES}


@router.get("/api/v1/community/agent-manifest")
async def agent_manifest() -> dict:
    return {
        "name": "Tensor Master Community",
        "description": "Agent-drivable ComfyUI studio and GPU scheduler",
        "read_only": ["GET /health", "GET /api/v1/community/profiles", "GET /api/v1/jobs/{job_id}"],
        "builders": ["POST /api/v1/community/character-replacement/prepare"],
        "mutations": ["POST /api/v1/jobs"],
        "rules": [
            "Never place credentials in prompts, graphs, recipes, logs, or command arguments.",
            "Validate a selected profile and inputs before submission.",
            "A timed-out submission may have succeeded; reconcile before retrying.",
            "Rentals require an authenticated human session and explicit cost limits.",
        ],
    }


def character_replacement_recipe() -> dict:
    return {
        "id": "h3-character-replacement-v1",
        "status": "guided",
        "stages": [
            "Prepare scene-matched identity and wardrobe references with Qwen Image 2.1",
            "Normalize the source once and plan exclusive frame intervals",
            "Track the intended performer with a reviewed SAM3 initial mask and SAM3_VideoTrack",
            "Invert only the tracked target pixels and retain normal partner/background colors",
            "Use that aligned video as the H3 Ref2VA video reference",
            "Render exact intervals, trim model padding, review motion and seams, then mux original continuous audio",
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
