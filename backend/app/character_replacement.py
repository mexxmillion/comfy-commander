"""Guarded SAM3 preparation graph for the public character-replacement workflow."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator

from .h3_media import h3_input_paths

SAM3_CHECKPOINT = "sam3.1_multiplex_fp16.safetensors"
RUNTIME = "pocket-h3-local"
ALLOWED_NODES = {
    "VHS_LoadVideo",
    "CheckpointLoaderSimple",
    "ImageFromBatch",
    "CLIPTextEncode",
    "SAM3_Detect",
    "SAM3_VideoTrack",
    "SAM3_TrackToMask",
    "ImageInvert",
    "ImageCompositeMasked",
    "MaskToImage",
    "VHS_VideoCombine",
}
REQUIRED_COUNTS = {
    "VHS_LoadVideo": 1,
    "CheckpointLoaderSimple": 1,
    "ImageFromBatch": 1,
    "CLIPTextEncode": 1,
    "SAM3_Detect": 1,
    "SAM3_VideoTrack": 1,
    "SAM3_TrackToMask": 1,
    "ImageInvert": 1,
    "ImageCompositeMasked": 1,
    "MaskToImage": 1,
    "VHS_VideoCombine": 2,
}


class PrepareRequest(BaseModel):
    source_video: str = Field(min_length=11, max_length=255)
    target_description: str = Field(min_length=3, max_length=300)
    output_slug: str = Field(default="replacement", pattern=r"^[a-z0-9][a-z0-9_-]{0,47}$")
    width: int = Field(default=640, ge=256, le=1536, multiple_of=32)
    height: int = Field(default=1120, ge=256, le=1536, multiple_of=32)
    frame_count: int = Field(default=77, ge=1, le=721)
    frame_rate: int = Field(default=24, ge=1, le=60)

    @field_validator("source_video")
    @classmethod
    def validate_source(cls, value: str) -> str:
        if (not value.startswith("h3_mobile/") or "\\" in value
                or ".." in Path(value).parts or not Path(value).name):
            raise ValueError("source_video must be a Pocket-uploaded h3_mobile file")
        return value


def is_graph(graph: dict[str, dict[str, Any]]) -> bool:
    classes = {node.get("class_type") for node in graph.values()}
    return "SAM3_VideoTrack" in classes or "SAM3_TrackToMask" in classes


def validate_graph(graph: dict[str, dict[str, Any]]) -> None:
    classes = [node.get("class_type") for node in graph.values()]
    if len(graph) != 12 or len(json.dumps(graph)) > 100_000 or not set(classes) <= ALLOWED_NODES:
        raise HTTPException(422, "Only the approved SAM3 character-preparation graph is supported")
    if any(classes.count(name) != count for name, count in REQUIRED_COUNTS.items()):
        raise HTTPException(422, "The SAM3 character-preparation graph has invalid node counts")

    checkpoint = next(node for node in graph.values() if node.get("class_type") == "CheckpointLoaderSimple")
    if checkpoint.get("inputs", {}).get("ckpt_name") != SAM3_CHECKPOINT:
        raise HTTPException(422, "Unsupported SAM3 checkpoint")

    loader = next(node for node in graph.values() if node.get("class_type") == "VHS_LoadVideo")
    load_inputs = loader.get("inputs", {})
    numeric_limits = {
        "custom_width": (256, 1536),
        "custom_height": (256, 1536),
        "frame_load_cap": (1, 721),
        "skip_first_frames": (0, 100_000),
        "select_every_nth": (1, 30),
    }
    for key, (lower, upper) in numeric_limits.items():
        value = load_inputs.get(key)
        if not isinstance(value, int) or not lower <= value <= upper:
            raise HTTPException(422, f"Invalid SAM3 video input: {key}")
    if load_inputs["custom_width"] % 32 or load_inputs["custom_height"] % 32:
        raise HTTPException(422, "SAM3 dimensions must be divisible by 32")

    text = next(node for node in graph.values() if node.get("class_type") == "CLIPTextEncode").get("inputs", {}).get("text")
    if not isinstance(text, str) or not 3 <= len(text) <= 300:
        raise HTTPException(422, "Describe the performer SAM3 should track")

    track = next(node for node in graph.values() if node.get("class_type") == "SAM3_VideoTrack").get("inputs", {})
    if track.get("max_objects") != 1 or track.get("detect_interval") != 100_000:
        raise HTTPException(422, "Community replacement tracks exactly one reviewed performer")
    selection = next(node for node in graph.values() if node.get("class_type") == "SAM3_TrackToMask")
    if selection.get("inputs", {}).get("object_indices") != "0":
        raise HTTPException(422, "Community replacement supports only the first tracked object")

    prefixes = sorted(
        node.get("inputs", {}).get("filename_prefix", "")
        for node in graph.values() if node.get("class_type") == "VHS_VideoCombine"
    )
    if (len(prefixes) != 2 or any(not re.fullmatch(r"community/character-replacement/[a-z0-9_-]+/(mask|target-guide)", p)
                                  for p in prefixes)):
        raise HTTPException(422, "Unsupported character-replacement output path")
    try:
        h3_input_paths(graph)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def build_graph(request: PrepareRequest) -> dict[str, dict[str, Any]]:
    prefix = f"community/character-replacement/{request.output_slug}"
    graph: dict[str, dict[str, Any]] = {
        "1": {"class_type": "VHS_LoadVideo", "inputs": {
            "video": request.source_video, "force_rate": 0,
            "custom_width": request.width, "custom_height": request.height,
            "frame_load_cap": request.frame_count, "skip_first_frames": 0,
            "select_every_nth": 1, "format": "None"}},
        "2": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": SAM3_CHECKPOINT}},
        "3": {"class_type": "ImageFromBatch", "inputs": {"image": ["1", 0], "batch_index": 0, "length": 1}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 1], "text": request.target_description}},
        "5": {"class_type": "SAM3_Detect", "inputs": {"model": ["2", 0], "image": ["3", 0],
            "conditioning": ["4", 0], "threshold": 0.35, "refine_iterations": 2, "individual_masks": False}},
        "6": {"class_type": "SAM3_VideoTrack", "inputs": {"images": ["1", 0], "model": ["2", 0],
            "initial_mask": ["5", 0], "detection_threshold": 0.5, "max_objects": 1, "detect_interval": 100_000}},
        "7": {"class_type": "SAM3_TrackToMask", "inputs": {"track_data": ["6", 0], "object_indices": "0"}},
        "8": {"class_type": "ImageInvert", "inputs": {"image": ["1", 0]}},
        "9": {"class_type": "ImageCompositeMasked", "inputs": {"destination": ["1", 0], "source": ["8", 0],
            "mask": ["7", 0], "x": 0, "y": 0, "resize_source": False}},
        "10": {"class_type": "MaskToImage", "inputs": {"mask": ["7", 0]}},
    }
    combine = {"frame_rate": request.frame_rate, "loop_count": 0, "format": "video/ffv1-mkv",
               "level": "3", "coder": "1", "context": "1", "gop_size": 1, "slices": "16",
               "slicecrc": "1", "save_metadata": True, "trim_to_audio": False, "pingpong": False,
               "save_output": True}
    graph["11"] = {"class_type": "VHS_VideoCombine", "inputs": {
        "images": ["10", 0], "filename_prefix": f"{prefix}/mask", "pix_fmt": "gray16le", **combine}}
    graph["12"] = {"class_type": "VHS_VideoCombine", "inputs": {
        "images": ["9", 0], "audio": ["1", 2], "filename_prefix": f"{prefix}/target-guide",
        "pix_fmt": "bgra", **combine}}
    validate_graph(graph)
    return graph
