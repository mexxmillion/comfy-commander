"""Bounded, cached CPU video preparation before reference tensor decoding."""

import hashlib
import json
import math
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4


def prepare_video(
    input_root, source_file, width, height, frame_count, crop_json="null", trim_json="null"
):
    root = Path(input_root).resolve()
    source = (root / source_file).resolve()
    if (
        not source_file.startswith("h3_mobile/")
        or not source.is_relative_to(root)
        or not source.is_file()
    ):
        raise ValueError("Video source must be a managed input file")
    if not all(
        isinstance(n, int) and not isinstance(n, bool) for n in (width, height, frame_count)
    ):
        raise ValueError("Video preparation dimensions and frame count must be integers")
    if not (32 <= width <= 8192 and 32 <= height <= 8192 and 5 <= frame_count <= 1450):
        raise ValueError("Video preparation exceeds supported dimensions or duration")
    crop, trim = json.loads(crop_json), json.loads(trim_json)
    filters = []
    if crop is not None:
        if (
            not isinstance(crop, list)
            or len(crop) != 4
            or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in crop)
            or not 0 <= crop[0] < crop[2] <= 1
            or not 0 <= crop[1] < crop[3] <= 1
        ):
            raise ValueError("Invalid normalized crop")
        left, top, right, bottom = crop
        filters.append(f"crop=iw*{right - left}:ih*{bottom - top}:iw*{left}:ih*{top}")
    if trim is not None and (
        not isinstance(trim, list)
        or len(trim) != 2
        or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in trim)
        or not 0 <= trim[0] < trim[1]
    ):
        raise ValueError("Invalid exclusive trim interval")
    start = trim[0] if trim else 0
    duration = min(frame_count / 24, trim[1] - trim[0]) if trim else frame_count / 24
    # Fit without stretching or cropping away the performer. Decode only the needed window.
    filters += [
        f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos",
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
        "setsar=1",
        "fps=24",
        f"trim=end_frame={frame_count}",
    ]
    with source.open("rb") as stream:
        source_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    digest = hashlib.sha256(
        json.dumps([2, source_hash, width, height, frame_count, crop, trim]).encode()
    ).hexdigest()
    relative = f"h3_mobile/video-prepared/{digest}.mkv"
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        return relative
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        from imageio_ffmpeg import get_ffmpeg_exe

        ffmpeg = get_ffmpeg_exe()
    temporary = target.with_name("." + uuid4().hex + ".mkv")
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-ss",
        str(start),
        "-i",
        str(source),
        "-t",
        str(duration),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-vf",
        ",".join(filters),
        "-c:v",
        "libx264",
        "-threads",
        "2",
        "-preset",
        "fast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-map_metadata",
        "-1",
        "-c:a",
        "pcm_s16le",
        "-ar",
        "48000",
        "-y",
        str(temporary),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            check=False,
            timeout=600,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode or not temporary.is_file():
            raise RuntimeError("Video resize failed; original reference has been preserved")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return relative


class TMPrepareVideo:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "source_file": ("STRING", {"default": ""}),
                "width": ("INT", {"default": 544, "min": 32, "max": 8192}),
                "height": ("INT", {"default": 960, "min": 32, "max": 8192}),
                "frame_count": ("INT", {"default": 124, "min": 5, "max": 1450}),
                "crop_json": ("STRING", {"default": "null"}),
                "trim_json": ("STRING", {"default": "null"}),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("prepared_video",)
    FUNCTION = "prepare"
    CATEGORY = "Tensor Master/Reference"

    @classmethod
    def IS_CHANGED(cls, source_file, **kwargs):
        import folder_paths

        source = Path(folder_paths.get_input_directory()) / source_file
        with source.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    def prepare(self, **kwargs):
        import folder_paths

        return (prepare_video(folder_paths.get_input_directory(), **kwargs),)
