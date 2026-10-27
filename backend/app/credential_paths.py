"""Stable desktop credential paths, independent of launcher APPDATA virtualization."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DESKTOP_CREDENTIALS = ROOT / "state" / "credentials"
PROVIDER_ENVIRONMENTS = {
    "runpod": ("RUNPOD_API_KEY",),
    "vast": ("VAST_API_KEY",),
    "huggingface": ("HF_TOKEN", "HUGGINGFACE_TOKEN"),
}


def credential_directory() -> Path:
    """Return the installation-owned secret directory without creating it."""
    configured = os.getenv("TENSOR_MASTER_CREDENTIAL_DIR")
    return Path(configured) if configured else DESKTOP_CREDENTIALS


def default_token_file() -> Path:
    # Missing files must not silently switch this installation's identity.
    return credential_directory() / "api_token"


def provider_secret_file(provider: str) -> Path:
    if provider not in {"runpod", "vast", "huggingface"}:
        raise ValueError("Unsupported secret provider")
    return credential_directory() / f"{provider}_api_key"


def provider_secret(provider: str, path: Path | None = None) -> str:
    """Read a provider key without exposing its source or value to callers."""
    if provider not in PROVIDER_ENVIRONMENTS:
        raise ValueError("Unsupported secret provider")
    for name in PROVIDER_ENVIRONMENTS[provider]:
        value = os.getenv(name, "").strip()
        if value:
            return value
    candidate = path or provider_secret_file(provider)
    try:
        return candidate.read_text(encoding="utf-8").strip() if candidate.is_file() else ""
    except OSError:
        return ""


def provider_secret_source(provider: str, path: Path | None = None) -> str | None:
    if any(os.getenv(name, "").strip() for name in PROVIDER_ENVIRONMENTS[provider]):
        return "environment"
    return "private file" if provider_secret(provider, path) else None


def openrouter_secret_file() -> Path:
    explicit = os.getenv("OPENROUTER_SECRET_FILE")
    if explicit:
        return Path(explicit)
    stable = credential_directory() / "openrouter_api_key.dpapi"
    try:
        stable_exists = stable.is_file()
    except OSError:
        stable_exists = False
    if os.name == "nt" and stable_exists:
        return stable
    portable = credential_directory() / "openrouter_api_key"
    try:
        portable_exists = portable.is_file()
    except OSError:
        portable_exists = False
    if portable_exists or os.name != "nt":
        return portable
    return Path(os.getenv("APPDATA", "")) / "ComfyUI" / "openrouter_api_key.dpapi"
