"""Shared local API credential for Pocket AI Generator and worker agents."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from pathlib import Path

POCKET_BRIDGE_CONTEXT = b"tensor-master-pocket-bridge-v1"
SERVICE_BRIDGE_CONTEXT = b"tensor-master-pocket-service-v2"


def pocket_bridge_token(openrouter_key: str) -> str:
    """Derive a scoped service credential without exposing the provider key."""
    return hmac.new(openrouter_key.encode("utf-8"), POCKET_BRIDGE_CONTEXT, hashlib.sha256).hexdigest()


def ensure_token(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        value = path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        if path.name != "session_secret" and path.with_name("admin_password.json").exists():
            raise RuntimeError("Saved API token is missing; restore it instead of resetting existing sessions")
        value = secrets.token_urlsafe(48)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            value = path.read_text(encoding="ascii").strip()
        else:
            with os.fdopen(descriptor, "w", encoding="ascii") as stream:
                stream.write(value)
    if len(value) < 32:
        raise RuntimeError("Tensor Master API token is missing or too short")
    return value


def token_matches(path: Path, supplied: str | None) -> bool:
    # A request must never create a replacement token and sign out every device.
    if not supplied:
        return False
    value = path.read_text(encoding="ascii").strip()
    if len(value) < 32:
        raise RuntimeError("Tensor Master API token is missing or too short")
    return hmac.compare_digest(value, supplied)


MIN_PASSWORD_LENGTH = 12


def service_bridge_token(api_token: str) -> str:
    """Provider credentials never determine the farm's service identity."""
    return hmac.new(api_token.encode("ascii"), SERVICE_BRIDGE_CONTEXT, hashlib.sha256).hexdigest()


def session_signature(token_path: Path, value: str, password: str) -> str:
    """Sign browser sessions with their own durable key; password reset revokes them."""
    secret = token_path.with_name("session_secret").read_text(encoding="ascii").strip()
    if len(secret) < 32:
        raise RuntimeError("Legacy session key is unavailable")
    password_identity = hashlib.sha256(password.encode()).hexdigest()
    return hmac.new(secret.encode(), (value + "." + password_identity).encode(), hashlib.sha256).hexdigest()


def password_record(password: str) -> dict:
    """Salted scrypt record stored as admin_password.json beside the API token."""
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
    return {"salt": salt, "hash": digest.hex()}
