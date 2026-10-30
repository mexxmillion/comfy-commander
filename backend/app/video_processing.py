"""Human-triggered CPU preview of the same bounded video stage used by H3 graphs."""

import asyncio
import importlib.util
import subprocess
from functools import lru_cache
from pathlib import Path
from threading import Lock
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .config import get_settings
from .h3_media import h3_input_paths
from .job_spec import _h3_frames, _resolution
from .media import VIDEOS, ffmpeg_executable, media_path
from .rental_ui import require_admin

router = APIRouter()
PREVIEW_LOCK = Lock()


@lru_cache
def video_module():
    path = (
        Path(__file__).resolve().parents[2]
        / "runtime/h3/source-overlays/custom_nodes/ComfyUI-TensorMaster-Preprocessors/video.py"
    )
    spec = importlib.util.spec_from_file_location("commander_video_preparation", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VideoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    file: str = Field(max_length=1024)
    aspect_ratio: str = Field(
        default="9:16", pattern=r"^(9:16|16:9|1:1|2:3|3:4|3:2|4:3)(?: \([^\n]+\))?$"
    )
    megapixels: float = Field(default=0.5, ge=0.1, le=16)
    duration: float = Field(default=5, ge=0.25, le=60)
    crop: list[float] | None = None
    trim: list[float] | None = None


def processing_graph(payload):
    import json

    width, height = _resolution(payload.aspect_ratio, payload.megapixels, 32)
    return {
        "video_prepare": {
            "class_type": "TMPrepareVideo",
            "inputs": {
                "source_file": payload.file,
                "width": width,
                "height": height,
                "frame_count": _h3_frames(payload.duration),
                "crop_json": json.dumps(payload.crop),
                "trim_json": json.dumps(payload.trim),
            },
        }
    }


def browser_preview(root, file):
    source = Path(root) / file
    target = source.with_suffix(".mp4")
    if not target.is_file():
        temporary = target.with_name("." + uuid4().hex + ".mp4")
        try:
            result = subprocess.run(
                [
                    ffmpeg_executable(),
                    "-v",
                    "error",
                    "-nostdin",
                    "-i",
                    str(source),
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a:0?",
                    "-c:v",
                    "copy",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    "-map_metadata",
                    "-1",
                    "-movflags",
                    "+faststart",
                    "-y",
                    str(temporary),
                ],
                capture_output=True,
                check=False,
                timeout=120,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode:
                raise RuntimeError("Browser preview encoding failed")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return target.relative_to(root).as_posix()


@router.post("/api/v1/generator/process-video")
async def process_video(payload: VideoRequest, request: Request):
    require_admin(request, mutation=True)
    graph = processing_graph(payload)
    try:
        h3_input_paths(graph)
        source = media_path(Path(payload.file).name, Path(payload.file).parent.as_posix(), "input")
        if source.suffix.lower() not in VIDEOS:
            raise ValueError("Choose a managed video reference")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not PREVIEW_LOCK.acquire(blocking=False):
        raise HTTPException(409, "Another video preview is processing; wait for it to finish")
    try:
        settings = graph["video_prepare"]["inputs"]
        file = await asyncio.to_thread(
            video_module().prepare_video, get_settings().comfyui_input_dir, **settings
        )
        preview = await asyncio.to_thread(browser_preview, get_settings().comfyui_input_dir, file)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(
            422,
            "Video preparation failed; check the source and processing window. Original preserved.",
        ) from exc
    finally:
        PREVIEW_LOCK.release()
    return {
        "reference": {
            "kind": "video",
            "file": file,
            "use_soundtrack": True,
            "preparation": {
                "source": payload.file,
                "preview": preview,
                **({"source_trim": payload.trim} if payload.trim else {}),
                **({"source_crop": payload.crop} if payload.crop else {}),
                **{k: settings[k] for k in ("width", "height", "frame_count")},
            },
        },
        "graph": graph,
        "preview_file": preview,
        "fps": 24,
        "requested_duration": payload.duration,
        "maximum_frames": settings["frame_count"],
    }
