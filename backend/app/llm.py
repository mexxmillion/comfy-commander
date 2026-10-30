"""Controller-owned LLM routing. Endpoint changes and keys are human Setup actions."""
import json
import math
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from .config import get_settings
from .rental_ui import require_admin

router = APIRouter(prefix="/api/v1/settings/llm")
OPENROUTER = "https://openrouter.ai/api/v1"
OPENROUTER_MODELS = {"google/gemini-3-flash-preview", "google/gemini-2.5-flash-lite", "x-ai/grok-4.3",
                     "google/gemini-3.8-flash", "deepseek/deepseek-v4.1-flash-1017"}


class LLMSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["openrouter", "compatible"] = "openrouter"
    base_url: str = "http://127.0.0.1:1234/v1"
    model: str = Field(default="", max_length=200)
    context_window: int = Field(default=131072, ge=32768, le=2097152)
    max_output_tokens: int = Field(default=24000, ge=1200, le=65536)
    vision: bool = False
    audio: bool = False
    video: bool = False
    json_format: Literal["json_schema", "json_object", "prompt"] = "json_object"
    token_parameter: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    temperature: bool = True

    @field_validator("base_url")
    @classmethod
    def endpoint(cls, value):
        value = value.strip().rstrip("/")
        parsed = urlsplit(value)
        _ = parsed.port
        if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
                or any(ord(c) <= 32 for c in value)
                or parsed.scheme not in {"http", "https"}
                or (parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"})
                or parsed.path.endswith(("/chat/completions", "/models"))):
            raise ValueError("Use an API base URL ending in /v1 (not /chat/completions), HTTPS or localhost HTTP, with no credentials/query/fragment.")
        return value

    @field_validator("model")
    @classmethod
    def model_id(cls, value):
        if any(ord(c) < 32 for c in value):
            raise ValueError("Use a model ID without control characters.")
        return value.strip()

    @model_validator(mode="after")
    def usable(self):
        if self.provider == "compatible" and not self.model:
            raise ValueError("Enter the model ID served by your endpoint.")
        if self.max_output_tokens >= self.context_window:
            raise ValueError("Output token limit must leave room for input in the context window.")
        return self


class SaveSettings(BaseModel):
    settings: LLMSettings
    api_key: SecretStr | None = None


def settings_path():
    return get_settings().preferences_file.with_name("llm-settings.json")


def key_path():
    return get_settings().token_file.parent / "compatible-llm-key.json"


def read_settings():
    path = settings_path()
    if not path.is_file():
        return LLMSettings()
    try:
        return LLMSettings.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        raise HTTPException(409, "LLM configuration is invalid. Review Settings > Connections.") from None


def custom_key(endpoint):
    path = key_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("key") if data.get("endpoint") == endpoint else None
    except (OSError, ValueError):
        return None


@dataclass
class Connection:
    settings: LLMSettings
    key: str | None

    @property
    def base_url(self):
        return OPENROUTER if self.settings.provider == "openrouter" else self.settings.base_url

    @property
    def url(self):
        return self.base_url + "/chat/completions"

    @property
    def headers(self):
        return {"Authorization": f"Bearer {self.key}"} if self.key else {}

    def body(self, payload):
        body = dict(payload)
        config = self.settings
        custom = config.provider == "compatible"
        if custom:
            body["model"] = config.model
            body.pop("reasoning", None)  # OpenRouter extension, not a portable parameter
            if not config.temperature:
                body.pop("temperature", None)
            if "response_format" in body:
                schema = body["response_format"].get("json_schema", {}).get("schema")
                if config.json_format != "json_schema" and schema:
                    body["messages"] = [{"role": "system", "content": "Return only one valid JSON object matching this schema. No Markdown or extra text.\n" + json.dumps(schema)}, *body.get("messages", [])]
                if config.json_format == "prompt":
                    body.pop("response_format")
                elif config.json_format == "json_object":
                    body["response_format"] = {"type": "json_object"}
        elif body.get("model") not in OPENROUTER_MODELS:
            raise HTTPException(400, "Choose a supported OpenRouter model or configure a compatible endpoint in Settings.")
        text_bytes, media_budget = 0, 0
        for message in body.get("messages", []):
            content = message.get("content", "")
            if isinstance(content, str):
                text_bytes += len(content.encode("utf-8"))
                continue
            for part in content:
                kind = part.get("type")
                if kind == "text":
                    text_bytes += len(part.get("text", "").encode("utf-8"))
                elif kind in {"image_url", "video_url", "input_audio"}:
                    capability, budget = {"image_url": ("vision", 4096), "video_url": ("video", 16384), "input_audio": ("audio", 8192)}[kind]
                    if custom and not getattr(config, capability):
                        raise HTTPException(409, f"The configured LLM is not declared to support {capability} input. Choose a capable model in Settings > Connections, use supported references, or turn prompt enhancement off. Nothing was sent.")
                    media_budget += budget
        output = body.get("max_tokens", body.get("max_completion_tokens", 1200))
        if output > config.max_output_tokens:
            raise HTTPException(409, "This request needs a larger LLM output limit. Reduce the Creative Engine variation count or increase the limit in Settings > Connections. Nothing was sent.")
        if math.ceil(text_bytes / 3) + media_budget + output > config.context_window:
            raise HTTPException(413, "Estimated input/media plus output exceeds the configured LLM context window. Shorten the workflow/conversation/references or choose a larger context window. Nothing was sent.")
        if custom and config.token_parameter == "max_completion_tokens" and "max_tokens" in body:
            body["max_completion_tokens"] = body.pop("max_tokens")
        return body


def connection(reader=None):
    config = read_settings()
    if config.provider == "openrouter":
        if reader is None:
            from .prompt_secret import read_key
            reader = read_key
        try:
            key = reader()
        except RuntimeError:
            raise HTTPException(409, "The saved OpenRouter key could not be loaded. Review Settings > Connections.") from None
        if not key:
            raise HTTPException(409, "Connect OpenRouter or configure a compatible LLM endpoint in Settings > Connections.")
    else:
        key = custom_key(config.base_url)
        if not key and urlsplit(config.base_url).scheme != "http":
            raise HTTPException(409, "Save a key for this compatible endpoint in Settings > Connections. OpenRouter keys are never reused for other endpoints.")
    return Connection(config, key)


def check_connection(payload):
    config = payload.settings
    if config.provider == "openrouter":
        from .prompt_secret import read_key
        try:
            provider = Connection(config, read_key())
        except RuntimeError:
            raise HTTPException(409, "The OpenRouter key could not be loaded. Review its service card in Connections.") from None
        if not provider.key:
            raise HTTPException(409, "Connect the OpenRouter key using its service card first.")
    else:
        key = payload.api_key.get_secret_value().strip() if payload.api_key else custom_key(config.base_url)
        if key and (len(key) > 8192 or "\n" in key or "\r" in key):
            raise HTTPException(400, "Invalid API key format.")
        if not key and urlsplit(config.base_url).scheme != "http":
            raise HTTPException(400, "Enter a key for this HTTPS endpoint. Keys saved for other endpoints are never reused.")
        provider = Connection(config, key)
    try:
        with httpx.Client(timeout=15, follow_redirects=False) as client:
            response = client.get(provider.base_url + "/models", headers=provider.headers)
        if response.status_code != 200:
            raise HTTPException(400, f"LLM models check returned HTTP {response.status_code}. Check the base URL and key. Redirects are not followed.")
        rows = response.json()["data"]
        if not isinstance(rows, list):
            raise ValueError("Invalid model catalogue")
        if config.provider == "compatible" and not any(row.get("id") == config.model for row in rows):
            raise HTTPException(400, "The configured model ID was not listed by this endpoint's /models response.")
        return provider, {"message": "Models endpoint reached; selected model listed. Media, context and JSON capabilities are declared settings, not an inference test.", "model_count": len(rows)}
    except (httpx.HTTPError, KeyError, ValueError, TypeError):
        raise HTTPException(400, "LLM connection check failed. Check network, API base URL, TLS certificate and model ID. No inference was requested.") from None


@router.get("")
def get_config(request: Request):
    require_admin(request)
    config = read_settings()
    return {"settings": config.model_dump(), "custom_key_saved": bool(custom_key(config.base_url))}


@router.post("/validate")
def validate(payload: SaveSettings, request: Request):
    require_admin(request, mutation=True)
    _, report = check_connection(payload)
    return report


@router.put("")
def save(payload: SaveSettings, request: Request):
    require_admin(request, mutation=True)
    provider, report = check_connection(payload)
    from .community import _write_secret
    if payload.settings.provider == "compatible" and payload.api_key:
        _write_secret(key_path(), json.dumps({"endpoint": provider.base_url, "key": provider.key}))
    _write_secret(settings_path(), payload.settings.model_dump_json())
    return {**report, "saved": True}


@router.delete("/key")
def remove_key(request: Request):
    require_admin(request, mutation=True)
    key_path().unlink(missing_ok=True)
    return {"removed": True}
