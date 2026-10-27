"""What a cloud worker really costs before it renders anything.

Every rental downloads its whole model profile on startup. Vast hosts may bill that download
per GB at very different rates, so the cheapest $/hour listing is often not the cheapest worker. Runpod does not
charge for data transfer.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

# Cloud GPUs Tensor Master rents for H3. 24 GB (RTX 4090) runs the 768x1344 Eros graph like the local 3090 does.
VAST_GPUS = ("RTX_5090", "RTX_4090", "RTX_6000Ada")
RUNPOD_GPU_NAMES = ("5090", "4090", "RTX PRO 6000", "RTX 6000 Ada")
MIN_CLOUD_VRAM_GB = 24

LOCK = Path(__file__).resolve().parents[2] / "runtime" / "h3" / "models.lock.json"


@lru_cache
def profile_sync_gb(profile: str) -> float:
    """Gigabytes a fresh worker downloads for this runtime profile (models.lock.json)."""
    manifest = json.loads(LOCK.read_text(encoding="utf-8"))
    groups = set(manifest["profiles"].get(profile, ()))
    size = sum(int(item.get("size") or 0) for item in manifest["artifacts"] if item.get("group") in groups)
    return round(size / 1e9, 1)


def download_cost_per_gb(offer: dict) -> float:
    try:
        return max(0.0, float(offer.get("inet_down_cost") or 0))
    except (TypeError, ValueError):
        return 0.0


def estimate(price_per_hour: float, download_per_gb: float, profile: str, hours: float = 1.0) -> dict:
    """Sync cost + GPU time for the first `hours`, so offers can be compared on what they actually bill."""
    sync_gb = profile_sync_gb(profile)
    sync = round(sync_gb * download_per_gb, 3)
    return {"sync_gb": sync_gb, "sync_cost": sync, "download_cost_per_gb": round(download_per_gb, 4),
            "first_hour_cost": round(sync + price_per_hour * hours, 3)}


def vast_estimate(offer: dict, profile: str) -> dict:
    return estimate(float(offer.get("dph_total") or 0), download_cost_per_gb(offer), profile)
