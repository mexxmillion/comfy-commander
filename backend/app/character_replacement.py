"""Guarded SAM3 preparation graph for the public character-replacement workflow."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator

from .h3_media import h3_input_paths

SAM3_CHECKPOINT = "sam3.1_multiplex_fp16.safetensors"
CHARACTER_SWAP_LORA = "h3_character_swap_v1.5.safetensors"
DEFAULT_REPLACEMENT_PROMPT = ("Replace the person in <Video 1> with the character in <Picture 1>. "
    "Preserve the character's face, hairstyle, clothing and proportions consistently throughout. "
    "Copy the source movement, poses, timing and gestures. Preserve the camera, framing, background, "
    "lighting and original soundtrack. Keep the character fully clothed and naturally integrated into the scene.")


def character_swap_profile() -> dict:
    """Copyable starter recipe; enabling it is an explicit UI choice."""
    return {"id": "h3-character-swap-v15", "name": "Motion control · Character Swap v1.5",
            "base_profile": "ref2v-int8", "mode": "h3", "h3Type": "reference",
            "jobType": "replacement", "qualityPreset": "normal", "turbo": 8,
            "turboEnabled": True, "steps": 8, "sampler": "euler", "audioLock": True,
            "characterSwapEnabled": True, "characterSwapStrength": 1.0,
            "megapixels": 0.5, "duration": 5, "aspect": "9:16 (Portrait Widescreen)",
            "lora": CHARACTER_SWAP_LORA,
            "source": "https://huggingface.co/akatz-ai/MiniMax-H3-Character-Swap-LoRA",
            "note": "Optional identity adapter. Review motion and identity; longer shots can still drift."}


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
    return ("SAM3_VideoTrack" in classes or "SAM3_TrackToMask" in classes) and "MiniMaxH3ReferenceToVideo" not in classes


def is_chain(graph: dict[str, dict[str, Any]]) -> bool:
    return any(key.startswith("replacement_") for key in graph)


def _reference_nodes(graph: dict) -> tuple[str, str, list]:
    packs = [key for key, node in graph.items() if node.get("class_type") == "MiniMaxH3ReferencePack"]
    h3 = [key for key, node in graph.items() if node.get("class_type") == "MiniMaxH3ReferenceToVideo"]
    if len(packs) != 1 or len(h3) != 1:
        raise HTTPException(422, "Replacement requires the H3 Reference video workflow")
    if sum(node.get("class_type") == "VHS_VideoCombine" for node in graph.values()) != 1:
        raise HTTPException(422, "Replacement requires one final H3 video output")
    try:
        refs = json.loads(graph[packs[0]]["inputs"]["references_json"])["references"]
        if (len(refs) != 2 or sum(ref.get("kind") == "video" for ref in refs) != 1
                or sum(ref.get("kind") == "image" for ref in refs) != 1):
            raise ValueError()
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(422, "Replacement requires exactly one source video and one replacement image") from exc
    return packs[0], h3[0], refs


def _chain_nodes(pack: str, refs: list, target: str, graph: dict | None = None) -> dict:
    source = next(ref["file"] for ref in refs if ref.get("kind") == "video")
    try:
        request = PrepareRequest(source_video=source, target_description=target, output_slug="one-shot")
    except ValueError as exc:
        raise HTTPException(422, "Describe the person to replace (3–300 characters)") from exc
    nodes = build_graph(request)
    del nodes["1"]
    for node in nodes.values():
        for key, value in node["inputs"].items():
            if isinstance(value, list) and len(value) == 2:
                node["inputs"][key] = ([pack, 9 if value[1] == 0 else 12] if value[0] == "1"
                                       else ["replacement_" + value[0], value[1]])
    result = {"replacement_" + key: node for key, node in nodes.items()}
    if graph is not None:
        video = next(ref for ref in refs if ref.get('kind') == 'video')
        target_inputs = graph[_reference_nodes(graph)[1]]['inputs']
        result['replacement_video_prepare'] = {'class_type':'TMPrepareVideo','inputs':{
            'source_file':source,'width':target_inputs.get('width', graph[pack]['inputs'].get('width')), 'height':target_inputs.get('height', graph[pack]['inputs'].get('height')),
            'frame_count':graph[_reference_nodes(graph)[1]]['inputs']['length'],
            'crop_json':json.dumps(video.get('crop')), 'trim_json':json.dumps(video.get('trim'))}}
    return result


def compose_chain(graph: dict, target: str, prepare_video: bool = True) -> dict:
    """Wire tracked target inversion straight into H3 in the same prompt."""
    if is_chain(graph):
        raise HTTPException(422, "Replacement stages are already present")
    pack, h3, refs = _reference_nodes(graph)
    if graph[h3]["inputs"].get("ref_videos.ref_video_0") != [pack, 9]:
        raise HTTPException(422, "Replacement must use the first reference video")
    if any(node.get("class_type") == "MiniMaxH3AddGuide" for node in graph.values()):
        raise HTTPException(422, "Replacement uses a video reference, without image guide nodes")
    result = deepcopy(graph)
    result.update(_chain_nodes(pack, refs, target, graph if prepare_video else None))
    if prepare_video:
        result[pack]['inputs']['prepared_video_0']=['replacement_video_prepare',0]
    result[h3]["inputs"]["ref_videos.ref_video_0"] = ["replacement_9", 0]
    return result


def validate_chain(graph: dict) -> dict:
    """Validate the exact canonical SAM3 branch; return the original H3 graph."""
    base = {key: deepcopy(node) for key, node in graph.items() if not key.startswith("replacement_")}
    pack, h3, refs = _reference_nodes(base)
    target = graph.get("replacement_4", {}).get("inputs", {}).get("text", "")
    prepared = 'replacement_video_prepare' in graph
    expected = _chain_nodes(pack, refs, target, base if prepared else None)
    if prepared:
        if base[pack]['inputs'].pop('prepared_video_0', None) != ['replacement_video_prepare',0]:
            raise HTTPException(422, "Replacement must use the canonical prepared video")
    elif 'prepared_video_0' in base[pack]['inputs']:
        raise HTTPException(422, "Prepared video is missing its canonical stage")
    actual = {key: node for key, node in graph.items() if key.startswith("replacement_")}
    if actual != expected or base[h3]["inputs"].get("ref_videos.ref_video_0") != ["replacement_9", 0]:
        raise HTTPException(422, "Replacement must retain the approved SAM3 tracking and target-only inversion wiring")
    if any(node.get("class_type") == "MiniMaxH3AddGuide" for node in base.values()):
        raise HTTPException(422, "Replacement uses a video reference, without image guide nodes")
    base[h3]["inputs"]["ref_videos.ref_video_0"] = [pack, 9]
    return base


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
