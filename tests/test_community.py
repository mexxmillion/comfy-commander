import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.app import character_replacement
from backend.app.character_replacement import PrepareRequest
from backend.app.community import COMMUNITY_PROFILES, character_replacement_recipe


def request(**overrides):
    values = {
        "source_video": "h3_mobile/source-shot.mkv",
        "target_description": "the performer wearing the red jacket",
        "output_slug": "demo-shot",
    }
    values.update(overrides)
    return PrepareRequest(**values)


def test_community_profiles_are_deliberately_small():
    assert set(COMMUNITY_PROFILES) == {
        "qwen-image-21", "ref2v-int8", "ref2v-singularity",
        "fasth3-omni", "h3-character-replacement",
    }


def test_sam3_builder_emits_review_outputs_and_no_image_guides():
    graph = character_replacement.build_graph(request())
    assert len(graph) == 12
    assert graph["11"]["inputs"]["filename_prefix"].endswith("/mask")
    assert graph["12"]["inputs"]["filename_prefix"].endswith("/target-guide")
    classes = {node["class_type"] for node in graph.values()}
    assert "SAM3_VideoTrack" in classes
    assert "MiniMaxH3AddGuide" not in classes
    character_replacement.validate_graph(graph)


def test_sam3_graph_rejects_target_switching_configuration():
    graph = character_replacement.build_graph(request())
    graph["6"]["inputs"]["max_objects"] = 2
    with pytest.raises(HTTPException, match="exactly one"):
        character_replacement.validate_graph(graph)


def test_sam3_graph_rejects_untrusted_output_path():
    graph = character_replacement.build_graph(request())
    graph["12"]["inputs"]["filename_prefix"] = "../../escape"
    with pytest.raises(HTTPException, match="output path"):
        character_replacement.validate_graph(graph)


def test_prepare_request_accepts_only_pocket_uploads():
    with pytest.raises(ValidationError):
        request(source_video="C:/private/source.mkv")


def test_character_recipe_requires_human_review():
    text = json.dumps(character_replacement_recipe())
    assert "reviewed SAM3" in text
    assert "No MiniMaxH3AddGuide" in text


def test_model_manifest_has_only_public_profiles_and_pinned_sam3():
    manifest = json.loads((Path(__file__).parents[1] / "runtime/h3/models.lock.json")
                          .read_text(encoding="utf-8"))
    assert set(manifest["profiles"]) == {
        "qwen-image-21", "ref2v-int8", "ref2v-singularity", "fasth3-omni"
    }
    sam3 = [item for item in manifest["artifacts"] if item["group"] == "sam3"]
    assert len(sam3) == 1
    assert sam3[0]["sha256"] == "9ba99c92703c2e8b4f47de2d34a539bb8e18923049e238b780d70dbe6368eb03"
