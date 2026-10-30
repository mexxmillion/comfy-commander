"""Validated, portable generation defaults shared by desktop and phone."""
import json
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .config import get_settings
from .rental_ui import require_admin

router = APIRouter()


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    aspect_ratio: Literal["9:16", "16:9", "1:1", "2:3", "3:4", "3:2", "4:3"] = "9:16"
    megapixels: float = Field(default=0.5, ge=0.1, le=16)
    duration: float = Field(default=5, ge=0.25, le=60, multiple_of=0.25)
    render_target: Literal["any", "local", "cloud"] = "any"
    h3_type: Literal["reference", "frames"] = "reference"
    h3_model: Literal["ref2v-int8", "ref2v-singularity", "fasth3-omni"] = "ref2v-int8"
    turbo_steps: Literal[4, 8] = 4
    quality_preset: Literal["preview", "normal", "hq"] | None = None
    video_encoding: Literal["standard", "high"] = "high"
    sparse_attention: Literal["auto", "off", "SLA", "VSA", "SOL"] = "auto"
    ai_enhance: bool = True
    audio_lock: bool = False


def read_preferences() -> Preferences:
    path = get_settings().preferences_file
    if not path.exists():
        return Preferences()
    try:
        return Preferences.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(503, "Saved preferences could not be read; restore the preferences file before updating.") from exc


@router.get("/api/v1/settings/preferences")
def preferences(request: Request) -> dict:
    require_admin(request)
    return read_preferences().model_dump()


@router.post("/api/v1/settings/preferences/validate")
def validate_preferences(request: Request, payload: Preferences) -> dict:
    require_admin(request, mutation=True)
    return {"valid": True, "preferences": payload.model_dump()}


@router.put("/api/v1/settings/preferences")
def update_preferences(request: Request, payload: Preferences) -> dict:
    require_admin(request, mutation=True)
    read_preferences()  # Preserve unreadable state rather than silently replacing it.
    path = get_settings().preferences_file
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload.model_dump(), indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return {"saved": True, "preferences": payload.model_dump()}
