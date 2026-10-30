import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from backend.app import profile_import as profiles
from backend.app.studio_agent import AgentChat, _proposal


def workflow(model=None):
    graph = {str(i): {"class_type": "PrimitiveFloat", "inputs": {"value": 1}} for i in range(1, 16)}
    graph["1"] = {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {}}
    graph["2"] = {"class_type": "VHS_VideoCombine", "inputs": {}}
    if model:
        graph["3"] = {"class_type": "UNETLoader", "inputs": {"unet_name": model}}
    return graph


def pinned_model():
    manifest = json.loads((profiles.ROOT / "runtime/h3/models.lock.json").read_text(encoding="utf-8"))
    return next(a["file"].split("/", 1)[-1] for a in manifest["artifacts"] if a["group"] == "ref2v-int8")


@pytest.fixture(autouse=True)
def local_only(monkeypatch, tmp_path):
    monkeypatch.setattr(profiles, "PROFILES", tmp_path / "profiles")
    monkeypatch.setattr(profiles, "entries_for_profile", lambda base: [])
    monkeypatch.setattr(profiles, "require_admin", lambda *args, **kwargs: None)


def test_known_pinned_model_resolves():
    manifest = json.loads((profiles.ROOT / "runtime/h3/models.lock.json").read_text(encoding="utf-8"))
    artifact = next(a for a in manifest["artifacts"] if a["group"] == "ref2v-int8")
    name = artifact["file"].split("/", 1)[-1]
    result = profiles.inspect_profile(profiles.ProfileImport(name="Known", workflow=workflow(name)))
    assert result["valid"] and result["ready"]


def test_unknown_model_is_saved_as_draft_and_cannot_rent():
    payload = profiles.ProfileImport(name="Missing", workflow=workflow("unknown.safetensors"))
    result = profiles.save_import(payload, Request({"type": "http"}))
    assert result["valid"] and not result["ready"]
    assert result["unresolved_models"] == ["unknown.safetensors"]
    assert "Hugging Face" in result["upload_question"]
    with pytest.raises(HTTPException, match="unresolved"):
        profiles.rental_base(result["id"])


def test_invalid_canvas_unknown_nodes_and_broken_links_are_rejected():
    for graph in [{"nodes": []}, {**workflow(), "3": {"class_type": "UnapprovedNode", "inputs": {}}},
                  {**workflow(), "3": {"class_type": "PrimitiveFloat", "inputs": {"value": ["missing", 0]}}}]:
        result = profiles.inspect_profile(profiles.ProfileImport(name="Invalid", workflow=graph))
        assert not result["valid"] and not result["ready"]


def test_credentials_are_rejected_without_echo_or_save():
    graph = workflow()
    graph["3"]["inputs"]["api_key"] = "do-not-echo-this"
    payload = profiles.ProfileImport(name="Secret", workflow=graph)
    result = profiles.inspect_profile(payload)
    assert not result["valid"] and "do-not-echo-this" not in json.dumps(result)
    with pytest.raises(HTTPException):
        profiles.save_import(payload, Request({"type": "http"}))
    assert not profiles.PROFILES.exists()


def test_saved_profile_is_revalidated_before_rental(monkeypatch):
    payload = profiles.ProfileImport(name="Reviewed", workflow=workflow(pinned_model()))
    saved = profiles.save_import(payload, Request({"type": "http"}))
    assert profiles.rental_base(saved["id"]) == "ref2v-int8"
    monkeypatch.setattr(profiles, "inspect_profile", lambda payload: {"valid": False, "ready": False})
    with pytest.raises(HTTPException):
        profiles.rental_base(saved["id"])


def test_assistant_cannot_propose_execution_or_rentals():
    assert _proposal({"action": "submit_workflow"}, workflow()) is None
    assert _proposal({"action": "open_rental"}, workflow()) is None
    with pytest.raises(ValidationError):
        AgentChat(model="google/gemini-3.8-flash", messages=[{"role": "user", "content": "help"}])


def test_companion_removed_and_import_is_onboarding_only():
    root = Path(__file__).parents[1]
    dashboard = (root / "backend/app/templates/dashboard.html").read_text(encoding="utf-8")
    setup = (root / "backend/app/templates/setup.html").read_text(encoding="utf-8")
    assert "agent-widget" not in dashboard
    assert 'id="profile-agent"' in setup
    assert 'id="profile-import" class="wizard-panel" data-wizard-step="5"' in setup
