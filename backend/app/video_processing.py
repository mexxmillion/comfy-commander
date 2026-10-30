"""Human-triggered CPU preview of the same bounded video stage used by H3 graphs."""

import asyncio
import importlib.util
import logging
import subprocess
from functools import lru_cache
from pathlib import Path
from threading import Lock
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import get_settings
from .h3_media import h3_input_paths
from .job_spec import _h3_frames, _resolution
from .media import VIDEOS, ffmpeg_executable, media_path
from .rental_ui import require_admin

router = APIRouter()
PREVIEW_LOCK = Lock()
logger = logging.getLogger(__name__)


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

    @field_validator('crop', 'trim', mode='before')
    @classmethod
    def validate_window(cls, value, info):
        if value is None:
            return value
        from .generator_prompts.refs import validate_crop, validate_trim
        return validate_crop(value) if info.field_name == 'crop' else validate_trim(value)


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


def prepare_preview(settings):
    # The CPU operation owns the lock. Cancelling an HTTP request cannot release
    # it while its worker thread/FFmpeg process is still running.
    if not PREVIEW_LOCK.acquire(blocking=False):
        raise HTTPException(409, "Another video preview is processing; wait for it to finish")
    try:
        root = get_settings().comfyui_input_dir
        file = video_module().prepare_video(root, **settings)
        preview = browser_preview(root, file)
        return file, preview
    finally:
        PREVIEW_LOCK.release()


def prepared_timing(root, file):
    """Count stored video packets without decoding or depending on ffprobe."""
    result = subprocess.run(
        [ffmpeg_executable(), '-v', 'error', '-nostdin', '-i', str(Path(root) / file),
         '-map', '0:v:0', '-c:v', 'copy', '-progress', 'pipe:1', '-nostats', '-f', 'null', '-'],
        capture_output=True, text=True, timeout=120,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    counts = [int(line.split('=', 1)[1]) for line in result.stdout.splitlines() if line.startswith('frame=')]
    if result.returncode or not counts or counts[-1] <= 0:
        raise ValueError('Selected window contains no video frames')
    return counts[-1], counts[-1] / 24


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
    try:
        settings = graph["video_prepare"]["inputs"]
        file, preview = await asyncio.to_thread(prepare_preview, settings)
        actual_frames, actual_duration = await asyncio.to_thread(prepared_timing, get_settings().comfyui_input_dir, file)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        diagnostic_id = uuid4().hex[:12]
        logger.exception('Video preparation failed; diagnostic ID %s', diagnostic_id)
        if isinstance(exc, subprocess.TimeoutExpired):
            status, message = 504, 'Video processing timed out. Select a shorter interval or lower the resolution.'
        elif isinstance(exc, FileNotFoundError):
            status, message = 503, 'Video processing dependencies or source are unavailable. Check FFmpeg installation and that the source still exists.'
        elif str(exc) == 'Selected window contains no video frames':
            status, message = 422, 'The selected interval contains no video frames. Move the start earlier or choose another source.'
        else:
            status, message = 422, 'Video preparation failed. Check that the source plays and that the selected interval is valid.'
        raise HTTPException(status, f'{message} Original preserved. Controller log diagnostic ID: {diagnostic_id}.') from exc
    return {
        "reference": {
            "kind": "video",
            "file": file,
            "use_soundtrack": True,
            "preparation": {
                "source": payload.file,
                "preview": preview,
                "actual_frames": actual_frames,
                "actual_duration": actual_duration,
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
        "actual_frames": actual_frames,
        "actual_duration": actual_duration,
    }
