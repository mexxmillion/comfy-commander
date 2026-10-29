"""Admin-managed LoRA catalogue for future community cloud workers."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .config import get_settings

SUPPORTED_PROFILES = ("qwen-image-21", "ref2v-int8", "ref2v-singularity", "fasth3-omni", "flf2v-int8")
MODEL_SUFFIXES = {".safetensors", ".pt", ".ckpt"}
COMMIT_RE = re.compile(r"^[0-9a-fA-F]{40,64}$")
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")


def _safe_repo_path(value: str) -> str:
    normalized = value.strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts or path.suffix.lower() not in MODEL_SUFFIXES:
        raise ValueError("LoRA path must be a relative .safetensors, .pt, or .ckpt path")
    return str(path)


class LoraProfiles(BaseModel):
    profiles: list[Literal["qwen-image-21", "ref2v-int8", "ref2v-singularity", "fasth3-omni", "flf2v-int8"]]

    @field_validator("profiles")
    @classmethod
    def profiles_are_present(cls, value: list[str]) -> list[str]:
        result = list(dict.fromkeys(value))
        if not result:
            raise ValueError("Choose at least one cloud profile")
        return result


class RegisterLora(LoraProfiles):
    repo_id: str = Field(min_length=3, max_length=200)
    revision: str = Field(min_length=40, max_length=64)
    file: str = Field(min_length=1, max_length=500)
    display_name: str | None = Field(default=None, max_length=120)

    @field_validator("repo_id")
    @classmethod
    def valid_repo(cls, value: str) -> str:
        value = value.strip()
        if not REPO_RE.fullmatch(value):
            raise ValueError("Repository must look like owner/name")
        return value

    @field_validator("revision")
    @classmethod
    def immutable_revision(cls, value: str) -> str:
        value = value.strip().lower()
        if not COMMIT_RE.fullmatch(value):
            raise ValueError("Revision must be an immutable Hugging Face commit SHA")
        return value

    @field_validator("file")
    @classmethod
    def valid_file(cls, value: str) -> str:
        return _safe_repo_path(value)


class UploadLora(LoraProfiles):
    local_id: str = Field(min_length=64, max_length=64)
    repo_id: str = Field(min_length=3, max_length=200)
    repo_path: str = Field(min_length=1, max_length=500)
    private: bool = True
    commit_message: str = Field(default="Add LoRA from Tensor Master Community", min_length=3, max_length=200)
    display_name: str | None = Field(default=None, max_length=120)

    @field_validator("local_id")
    @classmethod
    def valid_local_id(cls, value: str) -> str:
        if not SHA256_RE.fullmatch(value):
            raise ValueError("Invalid local LoRA selection")
        return value.lower()

    @field_validator("repo_id")
    @classmethod
    def valid_repo(cls, value: str) -> str:
        return RegisterLora.valid_repo(value)

    @field_validator("repo_path")
    @classmethod
    def valid_file(cls, value: str) -> str:
        return _safe_repo_path(value)


def _candidate_id(root_index: int, relative: str) -> str:
    return hashlib.sha256(f"{root_index}:{relative}".encode()).hexdigest()


def local_candidates() -> list[dict]:
    candidates: list[dict] = []
    seen: set[str] = set()
    for root_index, models_root in enumerate(get_settings().model_directories):
        lora_root = (models_root / "loras").resolve()
        if not lora_root.is_dir():
            continue
        for path in lora_root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in MODEL_SUFFIXES:
                continue
            relative = path.relative_to(lora_root).as_posix()
            candidate_id = _candidate_id(root_index, relative)
            if candidate_id in seen:
                continue
            seen.add(candidate_id)
            candidates.append({"id": candidate_id, "name": relative, "size": path.stat().st_size})
    return sorted(candidates, key=lambda item: item["name"].lower())


def resolve_local_candidate(candidate_id: str) -> Path:
    for root_index, models_root in enumerate(get_settings().model_directories):
        lora_root = (models_root / "loras").resolve()
        if not lora_root.is_dir():
            continue
        for path in lora_root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in MODEL_SUFFIXES:
                continue
            relative = path.relative_to(lora_root).as_posix()
            if _candidate_id(root_index, relative) == candidate_id:
                resolved = path.resolve()
                if not resolved.is_relative_to(lora_root):
                    break
                return resolved
    raise ValueError("The selected local LoRA no longer exists")


def read_manifest(path: Path | None = None) -> dict:
    path = path or get_settings().community_lora_manifest
    if not path.is_file():
        return {"version": 1, "loras": []}
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("Community LoRA manifest is unexpectedly large")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != 1 or not isinstance(data.get("loras"), list):
        raise ValueError("Unsupported community LoRA manifest")
    for item in data["loras"]:
        validate_entry(item)
    return data


def validate_entry(item: dict) -> None:
    if not REPO_RE.fullmatch(str(item.get("repo", ""))):
        raise ValueError("Invalid repository in community LoRA manifest")
    if not COMMIT_RE.fullmatch(str(item.get("revision", ""))):
        raise ValueError("Community LoRAs must use immutable revisions")
    _safe_repo_path(str(item.get("file", "")))
    if item.get("local_dir") != "loras":
        raise ValueError("Community LoRAs must install in the LoRA directory")
    if not SHA256_RE.fullmatch(str(item.get("sha256", ""))):
        raise ValueError("Community LoRA has no valid SHA-256")
    if not isinstance(item.get("size"), int) or item["size"] <= 0:
        raise ValueError("Community LoRA has no valid size")
    profiles = item.get("profiles")
    if not isinstance(profiles, list) or not profiles or not set(profiles) <= set(SUPPORTED_PROFILES):
        raise ValueError("Community LoRA has invalid profiles")


def save_manifest(data: dict, path: Path | None = None) -> None:
    path = path or get_settings().community_lora_manifest
    for item in data.get("loras", []):
        validate_entry(item)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _metadata(repo_id: str, revision: str, filename: str, token: str) -> tuple[str, int, str]:
    try:
        from huggingface_hub import HfApi
    except ImportError as exc:  # pragma: no cover - dependency failure is operational
        raise RuntimeError("Install the huggingface-hub dependency to manage LoRAs") from exc
    info = HfApi(token=token).model_info(repo_id=repo_id, revision=revision, files_metadata=True)
    commit = str(info.sha or "").lower()
    if not COMMIT_RE.fullmatch(commit):
        raise RuntimeError("Hugging Face did not return an immutable commit SHA")
    sibling = next((item for item in (info.siblings or []) if item.rfilename == filename), None)
    if sibling is None:
        raise ValueError(f"{filename} was not found in {repo_id}")
    lfs = getattr(sibling, "lfs", None)
    sha256 = str(getattr(lfs, "sha256", "") or "").lower()
    size = int(getattr(sibling, "size", 0) or getattr(lfs, "size", 0) or 0)
    if not SHA256_RE.fullmatch(sha256) or size <= 0:
        raise ValueError("The selected Hugging Face file must expose LFS SHA-256 and size metadata")
    return commit, size, sha256


def huggingface_token() -> str:
    from .credential_paths import provider_secret
    token = provider_secret("huggingface", get_settings().huggingface_token_file)
    if not token:
        raise ValueError("Connect Hugging Face before managing cloud LoRAs")
    return token


def _entry(*, repo: str, revision: str, filename: str, size: int, sha256: str,
           profiles: list[str], source: str, display_name: str | None) -> dict:
    identity = hashlib.sha256(f"{repo}@{revision}:{filename}".encode()).hexdigest()[:20]
    item = {
        "id": identity,
        "name": (display_name or PurePosixPath(filename).name).strip(),
        "repo": repo,
        "revision": revision,
        "file": filename,
        "local_dir": "loras",
        "size": size,
        "sha256": sha256,
        "profiles": profiles,
        "source": source,
        "created_at": datetime.now(UTC).isoformat(),
    }
    validate_entry(item)
    return item


def upsert_entry(item: dict) -> dict:
    manifest = read_manifest()
    # A ComfyUI-relative LoRA path is a deployment target. Re-registering the
    # same target replaces its old immutable revision instead of downloading
    # two different files onto one worker path.
    manifest["loras"] = [existing for existing in manifest["loras"]
                         if existing["id"] != item["id"] and existing["file"] != item["file"]]
    manifest["loras"].append(item)
    save_manifest(manifest)
    return item


def register_lora(payload: RegisterLora) -> dict:
    commit, size, sha256 = _metadata(payload.repo_id, payload.revision, payload.file, huggingface_token())
    if commit != payload.revision:
        raise ValueError("The requested revision did not resolve to the same immutable commit")
    return upsert_entry(_entry(repo=payload.repo_id, revision=commit, filename=payload.file,
                               size=size, sha256=sha256, profiles=payload.profiles,
                               source="registered", display_name=payload.display_name))


def upload_lora(payload: UploadLora) -> dict:
    local_file = resolve_local_candidate(payload.local_id)
    token = huggingface_token()
    try:
        from huggingface_hub import HfApi
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Install the huggingface-hub dependency to manage LoRAs") from exc
    api = HfApi(token=token)
    api.create_repo(repo_id=payload.repo_id, repo_type="model", private=payload.private, exist_ok=True)
    commit_info = api.upload_file(path_or_fileobj=local_file, path_in_repo=payload.repo_path,
                                  repo_id=payload.repo_id, repo_type="model",
                                  commit_message=payload.commit_message)
    revision = str(getattr(commit_info, "oid", "") or "").lower()
    if not COMMIT_RE.fullmatch(revision):
        revision, _, _ = _metadata(payload.repo_id, "main", payload.repo_path, token)
    local_size = local_file.stat().st_size
    local_hash = file_sha256(local_file)
    remote_revision, remote_size, remote_hash = _metadata(payload.repo_id, revision, payload.repo_path, token)
    if remote_size != local_size or remote_hash != local_hash:
        raise RuntimeError("Hugging Face upload metadata did not match the selected local LoRA")
    return upsert_entry(_entry(repo=payload.repo_id, revision=remote_revision,
                               filename=payload.repo_path, size=local_size, sha256=local_hash,
                               profiles=payload.profiles, source="uploaded",
                               display_name=payload.display_name))


def remove_entry(entry_id: str) -> bool:
    manifest = read_manifest()
    remaining = [item for item in manifest["loras"] if item["id"] != entry_id]
    if len(remaining) == len(manifest["loras"]):
        return False
    manifest["loras"] = remaining
    save_manifest(manifest)
    return True


def entries_for_profile(profile: str) -> list[dict]:
    return [item for item in read_manifest()["loras"] if profile in item["profiles"]]
