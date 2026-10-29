import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.app import character_replacement, community_loras, credential_paths
from backend.app.character_replacement import PrepareRequest
from backend.app.community import COMMUNITY_PROFILES, character_replacement_recipe
from backend.app.community_loras import RegisterLora, read_manifest, save_manifest
from backend.app.studio_agent import analyze_workflow


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
        "fasth3-omni", "flf2v-int8", "h3-character-replacement",
    }
    assert all(profile["minimum_vram_gb"] == 16 for profile in COMMUNITY_PROFILES.values())
    assert all(profile["recommended_vram_gb"] >= 24 for profile in COMMUNITY_PROFILES.values())


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
        "qwen-image-21", "ref2v-int8", "ref2v-singularity", "fasth3-omni", "flf2v-int8"
    }
    sam3 = [item for item in manifest["artifacts"] if item["group"] == "sam3"]
    assert len(sam3) == 1
    assert sam3[0]["sha256"] == "9ba99c92703c2e8b4f47de2d34a539bb8e18923049e238b780d70dbe6368eb03"


def test_h3_profiles_do_not_download_acc():
    root = Path(__file__).parents[1]
    manifest = json.loads((root / "runtime/h3/models.lock.json").read_text(encoding="utf-8"))
    assert not any("-acc-" in item["file"].lower() for item in manifest["artifacts"])
    for groups in manifest["profiles"].values():
        assert "ref2v-turbo" not in groups
    template = (root / "backend/app/templates/generator.html").read_text(encoding="utf-8")
    assert 'name="turbo" value="10"' not in template


def test_ref2v_defaults_and_pinned_lightx2v_downloads():
    root = Path(__file__).parents[1]
    manifest = json.loads((root / "runtime/h3/models.lock.json").read_text(encoding="utf-8"))
    artifacts = [a for a in manifest["artifacts"] if a["group"] == "lightx2v-ref2v"]
    assert len(artifacts) == 2
    assert all(a["repo"] == "lightx2v/Minimax-h3-Turbo" for a in artifacts)
    assert all(len(a["revision"]) == 40 and len(a["sha256"]) == 64 for a in artifacts)
    assert all(a["local_dir"] == "loras" and a["size"] > 0 for a in artifacts)
    for profile in ("ref2v-int8", "ref2v-singularity"):
        assert "lightx2v-ref2v" in manifest["profiles"][profile]
    template = (root / "backend/app/templates/generator.html").read_text(encoding="utf-8")
    assert '<input id="turbo-enabled" type="checkbox" checked>' in template
    assert '<input id="sparse-enabled" type="checkbox">' in template
    assert 'id="megapixels"' in template and 'step="any" value="0.5" required' in template
    assert 'step="0.25" value="5" required' in template
    assert template.index('value="9:16 (Portrait Widescreen)"') < template.index(
        'value="16:9 (Widescreen)"'
    )


def test_community_lora_manifest_round_trip(tmp_path):
    path = tmp_path / "community-loras.json"
    entry = {
        "id": "demo", "name": "Demo", "repo": "owner/model", "revision": "a" * 40,
        "file": "styles/demo.safetensors", "local_dir": "loras", "size": 123,
        "sha256": "b" * 64, "profiles": ["ref2v-int8"], "source": "registered",
        "created_at": "2026-01-01T00:00:00Z",
    }
    save_manifest({"version": 1, "loras": [entry]}, path)
    assert read_manifest(path)["loras"] == [entry]


def test_community_lora_registration_requires_immutable_safe_reference():
    with pytest.raises(ValidationError, match="at least 40"):
        RegisterLora(repo_id="owner/model", revision="main", file="demo.safetensors",
                     profiles=["ref2v-int8"])
    with pytest.raises(ValidationError, match="relative"):
        RegisterLora(repo_id="owner/model", revision="a" * 40, file="../demo.safetensors",
                     profiles=["ref2v-int8"])


def test_local_lora_candidates_do_not_expose_absolute_paths(tmp_path, monkeypatch):
    models = tmp_path / "models"
    (models / "loras").mkdir(parents=True)
    (models / "loras" / "safe.safetensors").write_bytes(b"safe")
    (models / "loras" / "ignore.txt").write_text("no", encoding="utf-8")
    settings = type("Settings", (), {"model_directories": [models]})()
    monkeypatch.setattr(community_loras, "get_settings", lambda: settings)
    candidates = community_loras.local_candidates()
    assert [item["name"] for item in candidates] == ["safe.safetensors"]
    assert str(tmp_path) not in json.dumps(candidates)


def test_agent_workflow_analysis_builds_unresolved_safe_profile():
    analysis = analyze_workflow({"1": {"class_type": "LoraLoaderModelOnly", "inputs": {
        "lora_name": "personal/style.safetensors"}}})
    assert analysis["node_types"] == ["LoraLoaderModelOnly"]
    assert analysis["model_references"] == ["personal/style.safetensors"]
    assert analysis["profile_draft"]["models"][0]["source"] == "unresolved"


def test_environment_provider_secret_precedes_private_file(tmp_path, monkeypatch):
    saved = tmp_path / "runpod"
    saved.write_text("file-value", encoding="utf-8")
    monkeypatch.setenv("RUNPOD_API_KEY", "environment-value")
    assert credential_paths.provider_secret("runpod", saved) == "environment-value"
    assert credential_paths.provider_secret_source("runpod", saved) == "environment"


def test_huggingface_accepts_standard_environment_alias(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setenv("HUGGINGFACE_TOKEN", "alias-value")
    assert credential_paths.provider_secret("huggingface", tmp_path / "missing") == "alias-value"


def test_fresh_install_navigation_does_not_require_task_rows():
    root = Path(__file__).parents[1]
    console = (root / "backend/app/static/console.js").read_text(encoding="utf-8")
    early_navigation = "addEventListener('hashchange',nav);nav();"
    optional_queue_control = "if(selectVisibleTasks)selectVisibleTasks.onchange"
    assert early_navigation in console
    assert optional_queue_control in console
    assert console.index(early_navigation) < console.index(optional_queue_control)


def test_homepage_defaults_to_generate():
    console = (Path(__file__).parents[1] / "backend/app/static/console.js").read_text(encoding="utf-8")
    assert "const route=location.hash||'#generator'" in console
    assert "a.hash===(location.hash||'#generator')" in console
    assert "location.hash||'#monitor'" not in console


def test_flf2v_has_public_rental_models_and_enabled_controls():
    root = Path(__file__).parents[1]
    manifest = json.loads((root / "runtime/h3/models.lock.json").read_text(encoding="utf-8"))
    groups = manifest["profiles"]["flf2v-int8"]
    files = [a for a in manifest["artifacts"] if a["group"] in groups]
    assert any(a["file"].endswith("minimax_h3_fl2va_pruned_int8_convrot.safetensors") for a in files)
    assert sum(a["group"] == "lightx2v-fl2v" for a in files) == 2
    assert all(len(a["revision"]) == 40 and len(a["sha256"]) == 64 for a in files)
    template = (root / "backend/app/templates/generator.html").read_text(encoding="utf-8")
    assert '<label><input type="radio" name="h3-type" value="frames">' in template


def test_flf2v_cloud_routing_accepts_matching_turbo_and_rejects_ref2v_lora():
    from backend.app.main import cloud_profile_for_graph

    graph = {
        "model": {"class_type": "UNETLoader", "inputs": {
            "unet_name": "minimax_h3_fl2va_pruned_int8_convrot.safetensors"}},
        "turbo": {"class_type": "LoraLoaderModelOnly", "inputs": {
            "lora_name": "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors"}},
    }
    assert cloud_profile_for_graph(graph) == "flf2v-int8"
    graph["turbo"]["inputs"]["lora_name"] = "minimax_h3_ref2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors"
    with pytest.raises(HTTPException, match="LoRA not in the cloud model manifest"):
        cloud_profile_for_graph(graph)
