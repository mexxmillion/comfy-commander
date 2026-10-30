"""Operator rental defaults constrained by the installation's safety caps."""

import json
import math
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .config import get_settings

router = APIRouter()
GPUClass = Literal["any", "consumer", "professional", "4090", "5080", "5090", "6000"]


class RentalPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    gpu_class: GPUClass = "5090"
    minimum_vram_gb: int = Field(default=16, ge=16, le=192)
    max_minutes: int = Field(default=90, ge=15, le=180)
    max_price_per_hour: float = Field(default=1.7, ge=0.01, le=100)
    max_budget_usd: float = Field(default=5, ge=0.01, le=10000)


def caps() -> dict:
    settings = get_settings()
    return {
        "max_price_per_hour": min(100, settings.max_total_price_per_hour),
        "max_minutes": min(180, settings.max_instance_lifetime_hours * 60),
        "max_active_gpus": settings.max_active_gpus,
        "fleet_hourly_cap": settings.max_total_price_per_hour,
    }


def validate_limits(preferences: RentalPreferences) -> None:
    limits = caps()
    if (
        preferences.max_price_per_hour > limits["max_price_per_hour"]
        or preferences.max_minutes > limits["max_minutes"]
    ):
        raise HTTPException(
            422,
            "Rental defaults cannot exceed this installation’s hourly price or lifetime safety caps",
        )


def read_rental_preferences() -> RentalPreferences:
    limits = caps()
    path = get_settings().rental_preferences_file
    try:
        preferences = (
            RentalPreferences.model_validate_json(path.read_text(encoding="utf-8"))
            if path.exists()
            else RentalPreferences(
                max_price_per_hour=min(1.7, limits["max_price_per_hour"]),
                max_minutes=min(90, limits["max_minutes"]),
            )
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(
            503, "Saved rental settings are unreadable; restore them before renting"
        ) from exc
    # Lower installation caps always win, including after an environment change.
    preferences.max_price_per_hour = min(
        preferences.max_price_per_hour, limits["max_price_per_hour"]
    )
    preferences.max_minutes = min(preferences.max_minutes, limits["max_minutes"])
    return preferences


def offer_allowed(offer: dict, preferences: RentalPreferences) -> bool:
    name = str(offer.get("gpu", "")).lower()
    gpu = preferences.gpu_class
    matches = (
        gpu == "any"
        or gpu == "consumer"
        and any(item in name for item in ("4090", "5080", "5090"))
        or gpu == "professional"
        and "6000" in name
        or gpu in {"4090", "5080", "5090", "6000"}
        and gpu in name
    )
    price = float(offer.get("price_per_hour") or 0)
    sync = float(offer.get("sync_cost") or 0)
    vram = float(offer.get("vram_gb") or 0)
    return bool(
        matches
        and all(math.isfinite(value) for value in (price, sync, vram))
        and 0 < price <= preferences.max_price_per_hour
        and sync >= 0
        and nominal_vram_gb(vram) >= preferences.minimum_vram_gb
        and sync + price * preferences.max_minutes / 60 <= preferences.max_budget_usd + 1e-9
    )


def nominal_vram_gb(reported: float) -> float:
    """Account for <=128 MiB reserved by the driver, consistently in picker/manager.

    A 24 GB 4090 commonly reports 23.988 GiB; a 16 GB 5080 reports about
    15.92 GiB. Never round a substantially smaller GPU up to the next class.
    """
    if math.isfinite(reported) and 0 <= round(reported) - reported <= 0.125:
        return float(round(reported))
    return reported


def selected_gpu_preferences(preferences, gpu_class):
    if gpu_class is None:
        return preferences
    return preferences.model_copy(update={"gpu_class": gpu_class,
        "minimum_vram_gb": 16 if gpu_class == "5080" else preferences.minimum_vram_gb})


def filter_offers(offers: list[dict], preferences: RentalPreferences) -> list[dict]:
    return [
        dict(
            offer,
            maximum_duration_cost=round(
                float(offer.get("sync_cost") or 0)
                + float(offer["price_per_hour"]) * preferences.max_minutes / 60,
                3,
            ),
        )
        for offer in offers
        if offer_allowed(offer, preferences)
    ]


def record_preferences(record: dict) -> RentalPreferences:
    return RentalPreferences(
        gpu_class=record.get("gpu_class", "any"),
        minimum_vram_gb=record.get("minimum_vram_gb", 24),
        max_minutes=record["max_minutes"],
        max_price_per_hour=record["max_price_per_hour"],
        max_budget_usd=record.get("max_budget_usd", 5),
    )


@router.get("/api/v1/settings/rentals")
def rental_preferences(request: Request) -> dict:
    from .rental_ui import require_admin

    require_admin(request)
    return {"preferences": read_rental_preferences().model_dump(), "caps": caps()}


@router.post("/api/v1/settings/rentals/validate")
def validate_rental_preferences(request: Request, payload: RentalPreferences) -> dict:
    from .rental_ui import require_admin

    require_admin(request, mutation=True)
    validate_limits(payload)
    return {"valid": True, "preferences": payload.model_dump()}


@router.put("/api/v1/settings/rentals")
def save_rental_preferences(request: Request, payload: RentalPreferences) -> dict:
    from .rental_ui import require_admin

    require_admin(request, mutation=True)
    validate_limits(payload)
    read_rental_preferences()
    path = get_settings().rental_preferences_file
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(payload.model_dump(), indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    getattr(get_settings, 'cache_clear', lambda: None)()
    return {"saved": True, "preferences": payload.model_dump()}
