"""QwenVL-style describe/caption node backed by Prompt Director's local llama-server profiles.

Same surface as AILab QwenVL Advanced (image, video, preset_prompt, custom_prompt, frame_count,
max_tokens, temperature, seed, keep_model_loaded), but the model is any GGUF profile from
config.json / the E:/LLM launcher catalog, run through your own llama.cpp build.
"""
from __future__ import annotations

import atexit
import json
import logging
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from threading import Thread
from urllib import error, request

from comfy_api.latest import io

try:
    from .config import load_config, profile_options, resolve_profile
    from .prompts import clean_output
    from .runtime import (
        OPENROUTER_CHAT_URL,
        REQUEST_TIMEOUT,
        _check_interrupt,
        _drain_output,
        _image_data_url,
        _wait_for_server,
        build_command,
        endpoint_chat_url,
        find_free_port,
        free_comfy_memory,
        random_seed,
        resolve_executable,
        resolve_openrouter_key,
        stop_server,
    )
    from .video import evenly_spaced_indices
except ImportError:
    from config import load_config, profile_options, resolve_profile
    from prompts import clean_output
    from runtime import (
        OPENROUTER_CHAT_URL,
        REQUEST_TIMEOUT,
        _check_interrupt,
        _drain_output,
        _image_data_url,
        _wait_for_server,
        build_command,
        endpoint_chat_url,
        find_free_port,
        free_comfy_memory,
        random_seed,
        resolve_executable,
        resolve_openrouter_key,
        stop_server,
    )
    from video import evenly_spaced_indices

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent
DEFAULT_CONTEXT_SIZE = 81920
MIN_IMAGE_TOKENS = 1024
MAX_IMAGE_TOKENS = 4096
PROVIDERS = ("local llama", "custom endpoint", "openrouter")
DEFAULT_SYSTEM = (
    "You are a precise vision-language assistant. Follow the user's instruction exactly and "
    "describe only what is visible. Output only the requested text, with no preamble."
)

# ---------------------------------------------------------------------------
# Presets: VLMPrompt's list (a superset of QwenVL's), then QwenVL's, then vlm_presets.json here.
# Later sources add/override entries; the dropdown keeps the first source's order.
# ---------------------------------------------------------------------------

_BUILTIN = {
    "🖼️ Tags": "List concise comma-separated tags for the visible subjects, attributes, setting, lighting, composition, and style.",
    "🖼️ Simple Description": "Describe this image in one or two sentences.",
    "🖼️ Detailed Description": "Describe this image in detail: subject, clothing, pose, environment, lighting and composition.",
    "🖼️ Ultra Detailed Description": "Describe every clearly visible detail, including subjects, appearance, pose, objects, setting, materials, lighting, color, camera angle, composition, and style.",
    "🎬 Cinematic Description": "Describe the scene as a cinematic shot, covering subject, action, setting, lighting, lens and camera perspective, composition, mood, and color grade.",
    "🖼️ Detailed Analysis": "Analyze the visible content, composition, lighting, color, perspective, visual style, mood, and notable details without inventing unseen facts.",
    "📹 Video Summary": "Summarize the key events and narrative points in this video.",
    "📖 Short Story": "Write a short story inspired by the visible scene while preserving its subjects, setting, and mood.",
    "🪄 Prompt Refine & Expand": "Rewrite the supplied visual idea as a clear, detailed generation prompt, preserving its intent while adding coherent subject, setting, lighting, composition, and style details.",
}


def _custom_nodes_dirs() -> list[Path]:
    # This package is often a junction into another checkout, so don't derive siblings from
    # the resolved __file__; ask ComfyUI where custom_nodes lives.
    dirs = [Path(__file__).parent.parent]
    try:
        import folder_paths
        dirs = [Path(p) for p in folder_paths.get_folder_paths("custom_nodes")] + dirs
    except Exception:
        pass
    return dirs


def _load_presets() -> tuple[list[str], dict[str, str]]:
    sources = []
    for pack, filename, key in (("ComfyUI-VLMPrompt", "system_prompts.json", "prompts"),
                                ("ComfyUI-QwenVL", "AILab_System_Prompts.json", "qwenvl")):
        found = next((d / pack / filename for d in _custom_nodes_dirs() if (d / pack / filename).is_file()), None)
        if found:
            sources.append((found, key))
    sources.append((ROOT / "vlm_presets.json", "prompts"))
    names = list(_BUILTIN)
    prompts = dict(_BUILTIN)
    for path, key in sources:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        entries = data.get(key) if isinstance(data, dict) else None
        if not isinstance(entries, dict):
            continue
        order = [n for n in data.get("_preset_prompts", []) if n in entries] or list(entries)
        for name in order + [n for n in entries if n not in order]:
            text = entries[name]
            if isinstance(text, dict):  # tolerate {"prompt": "..."} style entries
                text = text.get("prompt") or text.get("system") or ""
            if isinstance(text, str) and text.strip():
                prompts[name] = text
                if name not in names:
                    names.append(name)
    return names + ["Custom"], prompts


# ---------------------------------------------------------------------------
# Optional keep-alive llama-server (one at a time).
# ---------------------------------------------------------------------------

_server: dict = {"key": None, "process": None, "url": None, "tail": None}


def _shutdown_server() -> None:
    stop_server(_server["process"])
    _server.update(key=None, process=None, url=None, tail=None)


atexit.register(_shutdown_server)


def _ensure_server(config, profile, context_size: int, image_max_tokens: int) -> str:
    key = (profile.profile_id, str(profile.model_path), context_size, image_max_tokens)
    proc = _server["process"]
    if proc is not None and proc.poll() is None and _server["key"] == key:
        return _server["url"]
    _shutdown_server()
    if not profile.model_path.is_file():
        raise FileNotFoundError(f"GGUF model was not found: {profile.model_path}")
    if profile.mmproj_path is None:
        logger.warning("Profile %s has no mmproj; images/video will be ignored by the model.", profile.label)
    elif not profile.mmproj_path.is_file():
        raise FileNotFoundError(f"Vision projector was not found: {profile.mmproj_path}")

    free_comfy_memory()
    port = find_free_port()
    extra_args = profile.extra_server_args + ("--image-max-tokens", str(image_max_tokens))
    command = build_command(resolve_executable(config.server_path), profile.model_path.resolve(), profile.alias,
                            port, context_size, profile.kv_type, profile.mmproj_path, extra_args)
    tail: deque[str] = deque(maxlen=60)
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                               encoding="utf-8", errors="replace", creationflags=flags)
    Thread(target=_drain_output, args=(process.stdout, tail), daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_for_server(base, process)
    except BaseException:
        logger.error("llama-server output before failure:\n%s", "\n".join(tail))
        stop_server(process)
        raise
    _server.update(key=key, process=process, url=f"{base}/v1/chat/completions", tail=tail)
    return _server["url"]


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------

def _content(prompt: str, image, video, frame_count: int, extra_context: str,
             max_visuals: int | None = None) -> list[dict] | str:
    parts: list[dict] = []
    image_count = min(image.shape[0], frame_count) if image is not None else 0
    video_count = min(len(video), frame_count) if video is not None else 0
    if max_visuals is not None and image_count + video_count > max_visuals:
        if image_count and video_count:
            image_limit = max(1, round(max_visuals * image_count / (image_count + video_count)))
            image_count = min(image_count, image_limit)
            video_count = min(video_count, max_visuals - image_count)
            if not video_count:
                video_count = 1
                image_count = max_visuals - 1
        elif image_count:
            image_count = max_visuals
        else:
            video_count = max_visuals
    if image is not None:
        image_indices = evenly_spaced_indices(image.shape[0], image_count)
        for number, i in enumerate(image_indices, 1):
            if len(image_indices) > 1:
                parts.append({"type": "text", "text": f"Image {number}:"})
            parts.append({"type": "image_url", "image_url": {"url": _image_data_url(image[i])}})
    if video is not None and len(video):
        idx = evenly_spaced_indices(len(video), video_count)
        parts.append({"type": "text", "text": (
            f"The following {len(idx)} images are chronological frames sampled evenly from one video "
            f"({len(video)} frames). Treat them as a single continuous clip, not separate pictures.")})
        for i in idx:
            parts.append({"type": "image_url", "image_url": {"url": _image_data_url(video[i], max_side=768)}})
    text = prompt if not extra_context.strip() else f"{prompt}\n\n{extra_context.strip()}"
    if not parts:
        return text
    return parts + [{"type": "text", "text": text}]


def _post(url: str, payload: dict, api_key: str) -> str:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    body = json.dumps(payload).encode("utf-8")
    result: dict = {}

    def run() -> None:
        try:
            req = request.Request(url, data=body, headers=headers, method="POST")
            with request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                result["data"] = json.loads(resp.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            result["error"] = RuntimeError(f"endpoint returned HTTP {exc.code}: {detail.replace(api_key, '***') if api_key else detail}"[:800])
        except BaseException as exc:
            result["error"] = exc

    thread = Thread(target=run, daemon=True)
    thread.start()
    while thread.is_alive():
        thread.join(timeout=0.25)
        _check_interrupt()
    if "error" in result:
        raise result["error"]
    try:
        return str(result["data"]["choices"][0]["message"]["content"] or "")
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("endpoint returned an unexpected chat response") from exc


class LocalVLMDescribe(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        config = load_config()
        labels, _ = profile_options(config)
        default_label = next((p.label for p in config.profiles if p.profile_id == config.default_profile), labels[0])
        names, _ = _load_presets()
        default_preset = "🖼️ Detailed Description" if "🖼️ Detailed Description" in names else names[0]
        return io.Schema(
            node_id="LocalVLMDescribe",
            display_name="VLM Describe (Local LLM)",
            category="prompt/director",
            description="QwenVL-style image/video describe with presets, using your local GGUF profiles (llama-server).",
            inputs=[
                io.Image.Input("image", optional=True),
                io.Image.Input("video", optional=True, tooltip="Frame batch (e.g. from a video loader)."),
                io.Combo.Input("model", options=labels, default=default_label,
                               tooltip="GGUF profile from Prompt Director config.json / the E:/LLM launcher catalog."),
                io.Combo.Input("preset_prompt", options=names, default=default_preset,
                               tooltip="Presets from VLMPrompt / QwenVL (and vlm_presets.json here)."),
                io.String.Input("custom_prompt", multiline=True, default="",
                                tooltip="When filled it completely replaces the preset (same as QwenVL)."),
                io.Int.Input("frame_count", default=8, min=1, max=32, tooltip="Video frames sent to the model."),
                io.Int.Input("max_tokens", default=1024, min=16, max=8192),
                io.Float.Input("temperature", default=0.6, min=0.0, max=2.0, step=0.05),
                io.Int.Input("seed", default=1, min=-1, max=2**31 - 1, control_after_generate=True),
                io.Boolean.Input("keep_model_loaded", default=False,
                                 tooltip="Keep llama-server running for the next VLM Describe call (faster), "
                                         "at the cost of its VRAM staying in use. Off = stop right after."),
                io.Boolean.Input("thinking", default=False, advanced=True,
                                 tooltip="Allow the model's reasoning mode (slower; stripped from the output)."),
                io.Int.Input("context_size", default=DEFAULT_CONTEXT_SIZE, min=16384, max=131072, step=16384,
                             advanced=True, tooltip="80K minimum for local VLM requests; up to 128K when needed."),
                io.String.Input("extra_context", multiline=True, default="", optional=True, advanced=True,
                                tooltip="Appended after the prompt, e.g. reference_context from Video Frames for VL."),
                io.String.Input("system_prompt", multiline=True, default="", optional=True, advanced=True,
                                tooltip="Override the default system message."),
                io.Combo.Input("provider", options=list(PROVIDERS), default="local llama", advanced=True),
                io.String.Input("api_base", default="http://127.0.0.1:8080/v1", optional=True, advanced=True,
                                tooltip="custom endpoint only."),
                io.String.Input("remote_model", default="", optional=True, advanced=True,
                                tooltip="Model id for custom endpoint / openrouter."),
                io.String.Input("api_key", default="", optional=True, advanced=True,
                                tooltip="custom endpoint key, or OpenRouter key (else OPENROUTER_API_KEY / LLM_KEY)."),
            ],
            outputs=[io.String.Output("RESPONSE")],
        )

    @classmethod
    def execute(cls, model, preset_prompt, custom_prompt, frame_count, max_tokens, temperature, seed,
                keep_model_loaded, thinking=False, context_size=DEFAULT_CONTEXT_SIZE, image=None, video=None, extra_context="", system_prompt="",
                provider="local llama", api_base="", remote_model="", api_key="") -> io.NodeOutput:
        _, presets = _load_presets()
        prompt = custom_prompt.strip() or presets.get(preset_prompt, "")
        if not prompt:
            raise ValueError("Preset is 'Custom' but custom_prompt is empty.")
        payload = {
            "messages": [
                {"role": "system", "content": system_prompt.strip() or DEFAULT_SYSTEM},
                {"role": "user", "content": _content(prompt, image, video, frame_count, extra_context or "")},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": 0.9,
            "seed": random_seed(seed),
        }
        started = time.monotonic()
        if provider == "local llama":
            config = load_config()
            profile = resolve_profile(model, config)
            if context_size < DEFAULT_CONTEXT_SIZE:
                logger.info("VLM Describe raised context from %d to %d tokens", context_size, DEFAULT_CONTEXT_SIZE)
                context_size = DEFAULT_CONTEXT_SIZE
            image_count = min(image.shape[0], frame_count) if image is not None else 0
            video_count = min(len(video), frame_count) if video is not None else 0
            requested_visuals = image_count + video_count
            text_size = len(prompt) + len(extra_context or "") + len(system_prompt or DEFAULT_SYSTEM)
            vision_budget = max(MIN_IMAGE_TOKENS, context_size - max_tokens - max(1024, text_size // 3))
            max_visuals = max(1, min(requested_visuals, vision_budget // MIN_IMAGE_TOKENS)) if requested_visuals else 0
            image_max_tokens = min(MAX_IMAGE_TOKENS, vision_budget // max_visuals) if max_visuals else MAX_IMAGE_TOKENS
            if max_visuals < requested_visuals:
                logger.info("VLM Describe sampled %d of %d requested visuals to fit %d tokens",
                            max_visuals, requested_visuals, context_size)
            payload["messages"][1]["content"] = _content(
                prompt, image, video, frame_count, extra_context or "", max_visuals=max_visuals or None)
            try:
                url = _ensure_server(config, profile, context_size, image_max_tokens)
                payload["model"] = profile.alias
                if not thinking:
                    payload["chat_template_kwargs"] = {"enable_thinking": False}
                text = _post(url, payload, "local")
            finally:
                if not keep_model_loaded:
                    _shutdown_server()
        else:
            if provider == "openrouter":
                url, key = OPENROUTER_CHAT_URL, resolve_openrouter_key(api_key)
            else:
                url, key = endpoint_chat_url(api_base), api_key.strip()
                if not thinking:
                    payload["chat_template_kwargs"] = {"enable_thinking": False}
            if not remote_model.strip():
                raise ValueError(f"{provider} needs remote_model.")
            payload["model"] = remote_model.strip()
            text = _post(url, payload, key)
        logger.info("VLM Describe (%s) finished in %.1fs", provider, time.monotonic() - started)
        return io.NodeOutput(clean_output(text))
