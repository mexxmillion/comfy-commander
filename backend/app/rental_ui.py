"""Authenticated, operator-confirmed control of one disposable H3 rental."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
import re
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Literal
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from runtime.h3.managed_worker import eligible_offer, preflight, validate_startup_scripts

from .auth import MIN_PASSWORD_LENGTH, ensure_token, password_record, session_signature
from .config import get_settings
from .identity import authority
from .providers import runpod
from .providers.base import OfferSearch
from .providers.vast import VastProvider
from .rental_cost import MIN_CLOUD_VRAM_GB, VAST_GPUS, estimate, vast_estimate
from .rental_preferences import GPUClass, filter_offers, read_rental_preferences, selected_gpu_preferences

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "state" / "managed-rental.json"
DEFAULT_STATE = STATE
RENTALS = ROOT / "state" / "rentals"
MANAGER = ROOT / "runtime" / "h3" / "managed_worker.py"
COOKIE = "tensor_master_admin"
LOCK = Lock()
BUILD_LOCK = Lock()
LOGIN_LOCK = Lock()
LOGIN_ATTEMPTS: dict[str, list[float]] = {}
router = APIRouter(prefix="/api/v1/cloud")


class Unlock(BaseModel):
    token: str


class PasswordLogin(BaseModel):
    password: str = Field(min_length=1, max_length=256)
    remember: bool = True


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256, repr=False)
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=256, repr=False)


class PasswordRecovery(BaseModel):
    recovery_key: str = Field(min_length=1, max_length=256, repr=False)
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=256, repr=False)


def _password_attempt(request: Request) -> None:
    with LOGIN_LOCK:
        now = time.monotonic()
        peer = request.client.host if request.client else 'unknown'
        attempts = [stamp for stamp in LOGIN_ATTEMPTS.get(peer, []) if now - stamp < 60]
        LOGIN_ATTEMPTS[peer] = attempts
        if len(attempts) >= 5:
            raise HTTPException(429, "Too many attempts. Try again in one minute.")
        attempts.append(now)


def _password_matches(password: str, saved: str) -> bool:
    record = json.loads(saved)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(record["salt"]), n=16384, r=8, p=1).hex()
    return hmac.compare_digest(digest, record["hash"])


def _save_changed_password(password: str) -> JSONResponse:
    try:
        identity = authority(get_settings().token_file)
        identity.set_password(password_record(password))
        identity.export()
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        raise HTTPException(503, "Password update could not be completed. Try signing in before retrying.") from exc
    response = JSONResponse({"ok": True, "message": "Password updated. All devices are signed out. Sign in with your new password."})
    response.delete_cookie(COOKIE)
    return response


def _password_path() -> Path:
    return get_settings().token_file.with_name("admin_password.json")


def _local_setup(request: Request) -> bool:
    return (request.client is not None and request.client.host in {"127.0.0.1", "::1"}
            and request.url.hostname in {"127.0.0.1", "localhost", "::1"}
            and not request.headers.get("x-forwarded-for"))


def _password_cookie(remember: bool, request: Request) -> JSONResponse:
    lifetime = (365 * 24 if remember else 12) * 3600
    try:
        value = authority(get_settings().token_file).new_session(lifetime)
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        raise HTTPException(503, "Saved sign-in state is unavailable; no password reset is needed.") from exc
    response = JSONResponse({"ok": True})
    response.set_cookie(COOKIE, value, httponly=True, samesite="strict",
                        secure=request.url.scheme == "https", max_age=lifetime if remember else None)
    return response


@router.get("/auth-status")
async def password_status(request: Request) -> dict:
    if not get_settings().token_file.with_name('identity.sqlite3').exists():
        configured = _password_path().exists()
    else:
        try:
            configured = bool(authority(get_settings().token_file).setting('password'))
        except (OSError, RuntimeError, sqlite3.Error) as exc:
            raise HTTPException(503, "Sign-in store is unavailable") from exc
    return {"configured": configured, "can_setup": _local_setup(request)}


@router.post("/password-setup")
async def password_setup(payload: PasswordLogin, request: Request) -> JSONResponse:
    if not _origin_ok(request) or not _local_setup(request):
        raise HTTPException(403, "Set your password once on the host PC at http://127.0.0.1:8000")
    if len(payload.password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(400, f"Use at least {MIN_PASSWORD_LENGTH} characters")
    record = password_record(payload.password)
    # Create the first token before saving the first password; later startups
    # must never manufacture a new token beside an existing password.
    try:
        ensure_token(get_settings().token_file)
        identity = authority(get_settings().token_file)
        if identity.setting('password'):
            raise HTTPException(409, "Password already configured; sign in instead")
    except (OSError, RuntimeError) as exc:
        raise HTTPException(503, "Saved Tensor Master credential is unavailable; restore it on the host PC.") from exc
    try:
        identity.set_password(record, initial=True)
        identity.export()
    except ValueError as exc:
        raise HTTPException(409, "Password already configured; sign in instead") from exc
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        raise HTTPException(503, "Could not persist sign-in state; retry signing in") from exc
    return _password_cookie(payload.remember, request)


@router.post("/login")
async def password_login(payload: PasswordLogin, request: Request) -> JSONResponse:
    if not _origin_ok(request):
        raise HTTPException(403, "Same-origin request required")
    _password_attempt(request)
    try:
        saved = authority(get_settings().token_file).setting('password')
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        raise HTTPException(503, "Sign-in store is unavailable; no password reset is needed") from exc
    if not saved:
        raise HTTPException(409, "Set your password on the host PC first")
    record = json.loads(saved)
    digest = hashlib.scrypt(payload.password.encode(), salt=bytes.fromhex(record["salt"]), n=16384, r=8, p=1).hex()
    if not hmac.compare_digest(digest, record["hash"]):
        raise HTTPException(401, "Incorrect password")
    return _password_cookie(payload.remember, request)


@router.post("/password-change")
async def change_password(payload: PasswordChange, request: Request) -> JSONResponse:
    require_admin(request, mutation=True)
    _password_attempt(request)
    try:
        saved = authority(get_settings().token_file).setting('password')
        valid = bool(saved) and _password_matches(payload.current_password, saved)
    except (OSError, RuntimeError, sqlite3.Error, ValueError, KeyError) as exc:
        raise HTTPException(503, "Sign-in store is unavailable; restore it before changing your password.") from exc
    if not valid:
        raise HTTPException(401, "Current password is incorrect")
    return _save_changed_password(payload.new_password)


@router.get("/password-recovery")
async def password_recovery_info(request: Request) -> dict:
    local = _local_setup(request)
    return {"host_only": True, "available": local,
            "recovery_key_file": str(get_settings().token_file) if local else None}


@router.post("/password-recover")
async def recover_password(payload: PasswordRecovery, request: Request) -> JSONResponse:
    if not _origin_ok(request) or not _local_setup(request):
        raise HTTPException(403, "Recover your password on the host PC at http://127.0.0.1:8000/settings#access")
    _password_attempt(request)
    try:
        principal = authority(get_settings().token_file).principal(payload.recovery_key)
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        raise HTTPException(503, "Saved identity is unavailable; restore it before recovering your password.") from exc
    if not principal or principal['role'] != 'bootstrap':
        raise HTTPException(401, "Recovery key is incorrect")
    return _save_changed_password(payload.new_password)


@router.post("/logout")
async def logout(request: Request) -> JSONResponse:
    if not _origin_ok(request):
        raise HTTPException(403, "Same-origin request required")
    value = request.cookies.get(COOKIE, '')
    if value:
        try:
            authority(get_settings().token_file).revoke_session(value)
        except (OSError, RuntimeError, sqlite3.Error) as exc:
            raise HTTPException(503, "Sign-out could not be saved; retry shortly") from exc
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE)
    return response


# Vast hosts must download at least this fast (user rule: slow hosts waste the rental on the model sync)
VAST_MIN_INET_DOWN = 1000


class Launch(BaseModel):
    offer_id: int | str
    provider: Literal["vast", "runpod"] = "runpod"
    profile: str = "ref2v-int8"
    max_minutes: int = Field(default=90, ge=15, le=180)
    max_price_per_hour: float = Field(gt=0)
    count: int = Field(default=1, ge=1, le=10)
    max_budget_usd: float | None = Field(default=None, gt=0, le=10000, allow_inf_nan=False)
    gpu_class: GPUClass | None = None
    gpu_name: str | None = Field(default=None, max_length=100)


def _origin_ok(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return False
    from urllib.parse import urlsplit
    return urlsplit(origin).netloc.lower() == request.headers.get("host", "").lower()


def _signature(timestamp: str) -> str:
    if timestamp.startswith("s."):
        try:
            return session_signature(get_settings().token_file, timestamp, authority(get_settings().token_file).setting('password') or 'unset')
        except (OSError, RuntimeError) as exc:
            raise HTTPException(503, "Saved sign-in state is unavailable; no password reset is needed.") from exc
    try:
        token = authority(get_settings().token_file).setting('bootstrap')
    except (OSError, RuntimeError) as exc:
        raise HTTPException(503, "Saved Tensor Master credential is unavailable; restore it on the host PC.") from exc
    if timestamp.startswith("p."):
        token += authority(get_settings().token_file).setting('password') or 'unset'
    return hmac.new(token.encode(), timestamp.encode(), hashlib.sha256).hexdigest()


def _admin(request: Request, *, mutation: bool = False) -> None:
    if mutation and not _origin_ok(request):
        raise HTTPException(status_code=403, detail="Same-origin request required")
    value = request.cookies.get(COOKIE, "")
    if value.startswith('d.'):
        try:
            valid = authority(get_settings().token_file).session_valid(value)
        except (OSError, RuntimeError, sqlite3.Error) as exc:
            raise HTTPException(503, "Sign-in store is unavailable; no password reset is needed") from exc
        if valid:
            return
        raise HTTPException(401, "Sign in to GPU controls")
    if value:
        try:
            if authority(get_settings().token_file).legacy_revoked(value):
                raise HTTPException(401, "This session has been replaced or signed out")
        except (OSError, RuntimeError, sqlite3.Error) as exc:
            raise HTTPException(503, "Sign-in store is unavailable") from exc
    if value.startswith(("p.", "s.")):
        try:
            prefix, expiry, signature = value.split(".")
            lifetime = 365 if prefix == "s" else 30
            valid = 0 < int(expiry) - time.time() <= lifetime * 24 * 3600
        except ValueError:
            valid = False
            expiry = signature = ""
        if valid and hmac.compare_digest(_signature(prefix + "." + expiry), signature):
            request.state.legacy_session = (value, max(1, int(expiry) - int(time.time())))
            return
        raise HTTPException(401, "Sign in to GPU controls")
    try:
        timestamp, signature = value.split(".", 1)
        valid_time = 0 <= time.time() - int(timestamp) <= 12 * 3600
    except ValueError:
        valid_time = False
        timestamp = signature = ""
    if not valid_time or not hmac.compare_digest(_signature(timestamp), signature):
        raise HTTPException(status_code=401, detail="Sign in to admin controls first")
    request.state.legacy_session = (value, max(1, int(timestamp) + 12 * 3600 - int(time.time())))


def require_admin(request: Request, *, mutation: bool = False) -> None:
    """Reuse the rental-admin session for operator queue mutations."""
    _admin(request, mutation=mutation)


# These states do not represent usable or intentionally running capacity.
# ``creation_unknown`` is retained in rental history for diagnosis, but there
# is no provider instance id that Tensor Master can monitor or terminate, so it
# must not inflate the active-rental counter.
TERMINAL = {"destroyed", "failed", "creation_unknown"}


def model_sync_progress(path: Path) -> dict | None:
    logfile = path.with_name(path.stem + "-models.log")
    if not logfile.is_file():
        return None
    with logfile.open('rb') as stream:
        stream.seek(max(0, logfile.stat().st_size - 65536))
        lines = stream.read().decode('utf-8', errors='replace').splitlines()
    for line in reversed(lines):
        try:
            event = json.loads(line)
            if event.get('event') != 'sync_progress':
                continue
            total, done = int(event['total_bytes']), int(event['bytes'])
            if total <= 0 or not 0 <= done <= total:
                continue
            rate = float(event.get('bytes_per_second') or 0)
            eta = event.get('eta_seconds')
            return dict(percent=round(done / total * 100, 1),
                        downloaded_gib=round(done / 1024**3, 2), total_gib=round(total / 1024**3, 2),
                        speed_mbps=round(rate / 1e6, 1) if math.isfinite(rate) and rate > 0 else None,
                        eta_minutes=math.ceil(float(eta) / 60) if eta is not None and math.isfinite(float(eta)) and float(eta) >= 0 else None,
                        file=str(event.get('file', '')).replace('\\', '/').split('/')[-1][:160],
                        stage=str(event.get('stage', 'downloading'))[:30],
                        files_ready=max(0, int(event.get('files_ready', 0))),
                        files_total=max(0, int(event.get('files_total', 0))))
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
    return None


def _decorate(record: dict, path: Path) -> dict:
    record = dict(record)
    record.setdefault("rental_id", path.stem)
    record['sync_progress'] = model_sync_progress(path) if record.get('status') == 'syncing_models' else None
    record["failure"] = bool(record.get("error") or record.get("status") in {"failed", "destroy_failed", "creation_unknown"}
                             or "failed" in str(record.get("destroy_reason", "")).lower())
    record["failure_message"] = record.get("error") or record.get("destroy_reason") or ""
    record["failure_message"] = redact_log(record["failure_message"])
    if record.get("destroyed_at") and record.get("destroy_reason"):
        record["cleanup_explanation"] = "Commander terminated this instance: " + redact_log(str(record["destroy_reason"]))
    else:
        record["cleanup_explanation"] = ""
    lifecycle = path.with_suffix(".log")
    if record["failure"] and lifecycle.is_file():
        tail = lifecycle.read_text(encoding="utf-8", errors="replace")[-8000:]
        if "pipefail" in tail and "invalid option name" in tail:
            record["failure_message"] = "Commander deployment bug: the startup script had Windows line endings; Linux rejected pipefail."
            record["failed_stage"] = "worker startup (model download completed)" if record.get("models_ready_at") else "startup"
    if record.get("error"):
        record["error"] = redact_log(record["error"])
    record["cleanup_status"] = ("Instance terminated" if record.get("destroyed_at") else "No GPU allocated" if not record.get("instance_id") and record.get("status") != "creation_unknown" else "Check provider; instance may still be billable")
    try:
        if "rented_at" in record:
            from datetime import UTC, datetime
            end = datetime.fromisoformat(record.get("destroyed_at", datetime.now(UTC).isoformat()))
            elapsed = max(0, (end - datetime.fromisoformat(record["rented_at"])).total_seconds())
            record["elapsed_minutes"] = round(elapsed / 60, 1)
            record["estimated_cost"] = round(elapsed / 3600 * float(record.get("price_per_hour") or 0), 3)
            for label, start, finish in (
                ("boot_seconds", "rented_at", "ssh_ready_at"),
                ("runtime_seconds", "ssh_ready_at", "runtime_ready_at"),
                ("model_seconds", "runtime_ready_at", "models_ready_at"),
                ("worker_start_seconds", "models_ready_at", "worker_ready_at"),
                ("ready_seconds", "rented_at", "worker_ready_at"),
            ):
                record[label] = (round((datetime.fromisoformat(record[finish]) -
                                        datetime.fromisoformat(record[start])).total_seconds(), 1)
                                 if start in record and finish in record else None)
        for key in ("boot_seconds", "runtime_seconds", "model_seconds",
                    "worker_start_seconds", "ready_seconds"):
            record.setdefault(key, None)
        return record
    except (ValueError, KeyError, TypeError):
        return None


def all_current() -> list[dict]:
    paths = ([STATE] if STATE != DEFAULT_STATE else
             sorted(RENTALS.glob("rental-*.json"), key=lambda p: p.stat().st_mtime, reverse=True))
    records = []
    for path in paths:
        try:
            decorated = _decorate(json.loads(path.read_text(encoding="utf-8")), path)
            if decorated:
                records.append(decorated)
        except (FileNotFoundError, ValueError, TypeError):
            continue
    return records


def current() -> dict | None:
    records = all_current()
    if records:
        return records[0]
    if STATE == DEFAULT_STATE and STATE.is_file():
        try:
            return _decorate(json.loads(STATE.read_text(encoding="utf-8")), STATE)
        except (ValueError, TypeError):
            pass
    return None


def active_rentals() -> list[dict]:
    return [item for item in all_current()
            if item.get("status") not in TERMINAL and not item.get("destroyed_at")]


def worker_health(record: dict, workers: list[dict]) -> dict:
    record = dict(record, worker_warning="")
    if record.get("destroyed_at") or record.get("status") in TERMINAL or not record.get("instance_id"):
        return record
    worker_id = record.get("worker_id") or f"{record.get('provider', 'vast')}-{record['instance_id']}"
    worker = next((item for item in workers if item["id"] == worker_id), None)
    if worker and worker.get("runtime") != f"pocket-h3-cloud-{record['profile']}":
        record["worker_warning"] = f"Worker {worker_id} registered an incompatible runtime. Expected pocket-h3-cloud-{record['profile']}. It cannot serve this profile."
    elif worker and worker.get("status") in {"offline", "unhealthy"}:
        age = round(float(worker.get("heartbeat_age_seconds") or 0))
        record["worker_warning"] = f"Worker {worker_id} is {worker['status']} (last heartbeat {age}s ago). Check Worker and ComfyUI logs; the instance may still be billable."
    elif worker is None and (record.get("worker_ready_at") or record.get("status") == "ready"):
        record["worker_warning"] = f"Worker {worker_id} disappeared after becoming ready. Check logs and provider state; the instance may still be billable."
    return record


def _state_path(rental_id: str) -> Path:
    if STATE != DEFAULT_STATE:
        return STATE
    if not rental_id.startswith("rental-") or not rental_id[7:].isalnum():
        raise HTTPException(404, "Rental not found")
    path = (RENTALS / f"{rental_id}.json").resolve()
    if path.parent != RENTALS.resolve() or not path.is_file():
        raise HTTPException(404, "Rental not found")
    return path


def build_bundle() -> None:
    # Fail before invoking the builder (and before any provider action). Do not
    # relay captured subprocess output: it can contain private paths or secrets.
    settings = get_settings()
    if settings.cloud_runtime_source == "community":
        from runtime.h3.build_community import build_bundle as build_community_bundle
        try:
            build_community_bundle(ROOT / "state/h3-cloud-runtime.tar.gz", settings.community_lora_manifest)
        except (OSError, ValueError, RuntimeError, httpx.HTTPError) as exc:
            raise RuntimeError("Public H3 runtime packaging failed. Check network access and the pinned runtime sources. No GPU rental was started.") from exc
        return
    sources = {"COMFYUI_ROOT": settings.comfyui_root, "COMFYUI_PYTHON": settings.comfyui_python}
    for name, value in sources.items():
        if not value:
            raise RuntimeError(
                f"Cloud runtime packaging requires {name}. Configure it on the controller "
                "host and restart the web server. No GPU rental was started."
            )
        path = Path(value)
        valid = (path / "custom_nodes").is_dir() if name == "COMFYUI_ROOT" else path.is_file()
        if not valid:
            raise RuntimeError(
                f"Cloud runtime packaging cannot use {name}. Check the configured "
                "ComfyUI installation and Python executable on the controller host. "
                "No GPU rental was started."
            )
    script = ROOT / "runtime" / "h3" / "build_local.ps1"
    manifest = str(get_settings().community_lora_manifest)
    result = subprocess.run(["pwsh", "-NoProfile", "-File", str(script), "-BundleOnly",
                             "-ComfyRoot", str(settings.comfyui_root),
                             "-LocalPython", str(settings.comfyui_python),
                             "-CommunityLoraManifest", manifest],
                            cwd=ROOT, capture_output=True, text=True, timeout=180, check=False,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise RuntimeError("Pocket H3 runtime packaging failed; inspect the local node sources")


@router.post("/unlock")
async def unlock(payload: Unlock, request: Request) -> JSONResponse:
    if not _origin_ok(request):
        raise HTTPException(status_code=403, detail="Same-origin request required")
    try:
        principal = authority(get_settings().token_file).principal(payload.token)
        valid = principal and principal['role'] == 'bootstrap'
    except (OSError, RuntimeError) as exc:
        raise HTTPException(503, "Saved Tensor Master credential is unavailable; restore it on the host PC.") from exc
    if not valid:
        raise HTTPException(status_code=401, detail="Incorrect control key")
    return _password_cookie(False, request)


@router.get("/status")
async def status(request: Request) -> dict:
    _admin(request)
    rentals = all_current()
    return {"rental": rentals[0] if rentals else None, "rentals": rentals}


@router.get("/logs/{kind}")
async def logs(kind: str, request: Request, rental_id: str | None = None) -> PlainTextResponse:
    _admin(request)
    names = {"manager": "managed-rental.log", "setup": "managed-prepare.log",
             "models": "managed-models.log", "worker": "managed-worker.log",
             "comfy": "managed-comfy.log"}
    if kind not in names:
        raise HTTPException(status_code=404)
    if rental_id:
        state_path = _state_path(rental_id)
        suffix = {"manager": "", "setup": "-prepare", "models": "-models",
                  "worker": "-worker", "comfy": "-comfy"}[kind]
        path = state_path.with_name(state_path.stem + suffix + ".log")
    else:
        path = ROOT / "state" / names[kind]
    if not path.is_file():
        raise HTTPException(status_code=404)
    content = path.read_text(encoding="utf-8", errors="replace")[-250_000:]
    return PlainTextResponse(redact_log(content))


def redact_log(content: str) -> str:
    from .credential_paths import provider_secret
    for provider in ("vast", "runpod", "huggingface"):
        secret = provider_secret(provider)
        if secret:
            content = content.replace(secret, "[REDACTED]")
    for secret_path in (get_settings().vast_api_key_file, get_settings().huggingface_token_file,
                        get_settings().runpod_api_key_file, get_settings().token_file):
        if secret_path.is_file():
            secret = secret_path.read_text(encoding="utf-8").strip()
            if secret:
                content = content.replace(secret, "[REDACTED]")
    content = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", "[REDACTED PRIVATE KEY]", content, flags=re.S)
    content = re.sub(r"\b(?:hf_|sk-|ghp_|github_pat_)[A-Za-z0-9_-]{16,}", "[REDACTED]", content)
    content = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._~-]+", r"\1[REDACTED]", content)
    return re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?[REDACTED QUERY]", content)


@router.get("/diagnostics/{rental_id}")
async def diagnostics(rental_id: str, request: Request) -> dict:
    _admin(request)
    path = _state_path(rental_id)
    record = _decorate(json.loads(path.read_text(encoding="utf-8")), path)
    result = {key: record.get(key) for key in ("rental_id", "provider", "profile", "status", "instance_id", "failure_message", "failed_stage", "cleanup_status", "requested_at", "failed_at", "destroyed_at")}
    logs = []
    for label, suffix in (("Lifecycle", ""), ("Runtime setup", "-prepare"), ("Models", "-models"), ("Worker", "-worker"), ("ComfyUI", "-comfy")):
        logfile = path.with_name(path.stem + suffix + ".log")
        if logfile.is_file():
            lines = logfile.read_text(encoding="utf-8", errors="replace").splitlines()[-100:]
            logs.append(f"=== {label} ===\n" + "\n".join(lines)[-30000:])
    result["log"] = redact_log("\n\n".join(logs)) or "No log output yet. The rental record is retained."
    result["failure_message"] = redact_log(str(result.get("failure_message") or ""))
    return result


@router.get("/offers")
async def offers(request: Request, provider: Literal["vast", "runpod"] = "runpod", profile: str = "ref2v-int8", gpu_class: GPUClass | None = None) -> dict:
    _admin(request)
    from .profile_import import rental_base
    profile = rental_base(profile)
    settings = get_settings()
    preferences = selected_gpu_preferences(read_rental_preferences(), gpu_class)
    if profile not in {"ref2v-int8", "ref2v-singularity", "fasth3-omni", "flf2v-int8", "qwen-image-21"}:
        raise HTTPException(status_code=422, detail="Unsupported Community Edition runtime profile")
    if provider == "runpod":
        if not runpod.configured():
            return {"offers": [], "configured": False,
                    "message": "Connect RunPod in Setup first. No Pod is rented when you connect."}
        try:
            available = runpod.client().offers(preferences.max_price_per_hour)
            for item in available:   # Runpod does not bill data transfer: the model sync is free
                item.update(estimate(float(item["price_per_hour"]), 0.0, profile))
            available = filter_offers(available, preferences)
            return {"offers": available, "configured": True,
                    "message": None if available else
                    f"No supported Runpod Secure Cloud GPU is in stock under the ${settings.max_gpu_price_per_hour:.2f}/hr safety cap. Refresh later or choose Vast.ai."}
        except (RuntimeError, OSError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    from .credential_paths import provider_secret
    key = provider_secret("vast", settings.vast_api_key_file)
    results = VastProvider(key).search_offers(OfferSearch(
        VAST_GPUS, max(MIN_CLOUD_VRAM_GB, preferences.minimum_vram_gb), preferences.max_price_per_hour, 0.98, 150,
        extra_query=f"inet_down>={VAST_MIN_INET_DOWN} direct_port_count>=1 cuda_max_good>=13"))
    # Datacenter and verified non-datacenter hosts alike, as long as the download is fast enough for the model sync
    available = [offer for offer in results if eligible_offer(
        offer.raw, offer_id=offer.id, max_price=settings.max_gpu_price_per_hour,
        max_download_cost=settings.max_download_cost_per_gb, require_datacenter=False,
        min_inet_down=VAST_MIN_INET_DOWN)]
    # Rank by what the worker actually bills: model-sync download + first hour of GPU, not the hourly rate alone.
    rows = sorted(({"id": item.id, "gpu": item.gpu_name,
                    "vram_gb": round(item.vram_gb, 1),
                    "price_per_hour": round(item.price_per_hour, 3),
                    "reliability": round(item.reliability, 3),
                    "download_mbps": round(float(item.raw.get("inet_down") or 0)),
                    "datacenter": item.raw.get("datacenter") is True,
                    "location": item.raw.get("geolocation") or "", **vast_estimate(item.raw, profile)} for item in available),
                  key=lambda row: row["first_hour_cost"])
    rows = filter_offers(rows, preferences)
    return {"offers": rows[:20],
            "message": None if rows else
            f"No Vast offer meets the safety filters (>= {VAST_MIN_INET_DOWN} Mbps download, model download <= ${settings.max_download_cost_per_gb:g}/GB). Refresh later or choose Runpod.",
            "limits": {"max_hourly": settings.max_gpu_price_per_hour,
                       "max_download_cost_per_gb": settings.max_download_cost_per_gb,
                       "max_lifetime_hours": settings.max_instance_lifetime_hours}}


@router.post("/launch", status_code=202)
async def launch(payload: Launch, request: Request) -> dict:
    _admin(request, mutation=True)
    from .profile_import import rental_base
    imported_profile = payload.profile if payload.profile.startswith("profile-") else None
    payload.profile = rental_base(payload.profile)
    if payload.profile not in {"ref2v-int8", "ref2v-singularity", "fasth3-omni", "flf2v-int8", "qwen-image-21"}:
        raise HTTPException(status_code=422, detail="Unsupported Community Edition runtime profile")
    settings = get_settings()
    preferences = selected_gpu_preferences(read_rental_preferences(), payload.gpu_class)
    if payload.max_minutes > preferences.max_minutes or payload.max_price_per_hour > preferences.max_price_per_hour:
        raise HTTPException(422, "Requested rental exceeds saved duration or hourly price limits; update Settings first")
    budget = min(preferences.max_budget_usd, payload.max_budget_usd or preferences.max_budget_usd)
    if payload.count * payload.max_price_per_hour * payload.max_minutes / 60 > budget:
        raise HTTPException(422, "Requested GPU time exceeds the total launch budget")
    if payload.max_price_per_hour > settings.max_gpu_price_per_hour:
        raise HTTPException(status_code=422, detail="Price ceiling exceeds the configured GPU limit")
    if payload.provider == "vast" and (not isinstance(payload.offer_id, int) or payload.offer_id <= 0):
        raise HTTPException(status_code=422, detail="Invalid Vast offer ID")
    if payload.provider == "runpod" and (not isinstance(payload.offer_id, str) or len(payload.offer_id) > 150):
        raise HTTPException(status_code=422, detail="Invalid Runpod GPU type")
    if payload.provider == "vast" and payload.count != 1:
        raise HTTPException(status_code=422, detail="Choose one Vast offer at a time; Runpod supports quantity 1–10")
    if payload.max_minutes > min(180, settings.max_instance_lifetime_hours * 60):
        raise HTTPException(status_code=422, detail="Requested lifetime exceeds safety limit")
    def prepare_runtime() -> None:
        with BUILD_LOCK:
            build_bundle()
            validate_startup_scripts()
            if payload.provider == "runpod":
                from runtime.h3.runpod_worker import preflight as runpod_preflight
                runpod_preflight()
            else:
                preflight()
    try:
        # Vast preflight checks this server's /health endpoint. Keep that blocking
        # request off the ASGI event loop so it can answer its own health check.
        await asyncio.to_thread(prepare_runtime)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    with LOCK:
        active = active_rentals()
        if len(active) + payload.count > settings.max_active_gpus:
            raise HTTPException(status_code=409, detail=f"Fleet limit is {settings.max_active_gpus} GPUs; {len(active)} already starting or active")
        reserved = sum(float(item.get("price_per_hour") or item.get("max_price_per_hour") or 0) for item in active)
        if reserved + payload.count * payload.max_price_per_hour > settings.max_total_price_per_hour:
            raise HTTPException(status_code=409, detail=f"Fleet hourly budget is ${settings.max_total_price_per_hour:.2f}; lower quantity or price")
        if STATE == DEFAULT_STATE:
            RENTALS.mkdir(parents=True, exist_ok=True)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        (ROOT / "state").mkdir(parents=True, exist_ok=True)
        log = (ROOT / "state" / "managed-process.log").open("a", encoding="utf-8")
        requests = []
        try:
            for _ in range(payload.count):
                rental_id = f"rental-{uuid4().hex[:12]}"
                state_path = STATE if STATE != DEFAULT_STATE else RENTALS / f"{rental_id}.json"
                record = {"rental_id": rental_id, "status": "requested", "offer_id": payload.offer_id,
                          "provider": payload.provider, "profile": payload.profile,
                          "max_minutes": payload.max_minutes,
                          "max_price_per_hour": payload.max_price_per_hour,
                          "max_budget_usd": budget / payload.count,
                          "gpu_class": preferences.gpu_class,
                          "minimum_vram_gb": preferences.minimum_vram_gb,
                          "requested_at": datetime.now(UTC).isoformat()}
                if payload.gpu_name:
                    record["gpu_name"] = payload.gpu_name
                if imported_profile:
                    record["imported_profile"] = imported_profile
                if payload.provider == "vast":     # same host rules as the offer list
                    record.update(allow_non_datacenter=True, min_inet_down=VAST_MIN_INET_DOWN)
                state_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
                manager = ROOT / "runtime/h3/runpod_worker.py" if payload.provider == "runpod" else MANAGER
                try:
                    subprocess.Popen([sys.executable, str(manager), "launch", "--state-file", str(state_path)], cwd=ROOT,
                                     stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                     creationflags=flags, close_fds=True)
                except OSError as exc:
                    record["status"] = "failed"
                    record["error"] = "Could not start the rental manager"
                    state_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
                    raise HTTPException(status_code=500, detail=record["error"]) from exc
                requests.append(rental_id)
        finally:
            log.close()
    return {"status": "requested", "offer_id": payload.offer_id, "rentals": requests}


@router.post("/destroy", status_code=202)
async def destroy(request: Request) -> dict:
    _admin(request, mutation=True)
    record = current()
    if not record or not record.get("instance_id") or record.get("destroyed_at"):
        raise HTTPException(status_code=409, detail="No active managed GPU")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    (ROOT / "state").mkdir(parents=True, exist_ok=True)
    log = (ROOT / "state" / "managed-process.log").open("a", encoding="utf-8")
    try:
        manager = ROOT / "runtime/h3/runpod_worker.py" if record.get("provider") == "runpod" else MANAGER
        state_path = _state_path(record["rental_id"])
        subprocess.Popen([sys.executable, str(manager), "destroy", "--state-file", str(state_path)], cwd=ROOT,
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         creationflags=flags, close_fds=True)
    finally:
        log.close()
    return {"status": "destroy_requested"}


@router.post("/destroy/{rental_id}", status_code=202)
async def destroy_one(rental_id: str, request: Request) -> dict:
    _admin(request, mutation=True)
    state_path = _state_path(rental_id)
    record = _decorate(json.loads(state_path.read_text(encoding="utf-8")), state_path)
    if not record.get("instance_id") or record.get("destroyed_at"):
        raise HTTPException(status_code=409, detail="This rental has no active provider instance")
    manager = ROOT / "runtime/h3/runpod_worker.py" if record.get("provider") == "runpod" else MANAGER
    (ROOT / "state").mkdir(parents=True, exist_ok=True)
    log = (ROOT / "state" / "managed-process.log").open("a", encoding="utf-8")
    try:
        subprocess.Popen([sys.executable, str(manager), "destroy", "--state-file", str(state_path)], cwd=ROOT,
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), close_fds=True)
    finally:
        log.close()
    return {"status": "destroy_requested", "rental_id": rental_id}


@router.post("/destroy-all", status_code=202)
async def destroy_all(request: Request) -> dict:
    _admin(request, mutation=True)
    requested = []
    for record in active_rentals():
        if not record.get("instance_id"):
            continue
        await destroy_one(record["rental_id"], request)
        requested.append(record["rental_id"])
    return {"status": "destroy_requested", "rentals": requested}


def upgrade_legacy_response(request, response):
    legacy = getattr(request.state, 'legacy_session', None)
    if legacy and response.status_code < 400:
        value, lifetime = legacy
        replacement = authority(get_settings().token_file).upgrade_session(value, lifetime)
        response.set_cookie(COOKIE, replacement, httponly=True, samesite='strict',
                            secure=request.url.scheme == 'https', max_age=lifetime)
    return response
