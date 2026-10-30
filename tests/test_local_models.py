import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import local_models as models


@pytest.fixture
def local_setup(tmp_path, monkeypatch):
    root = tmp_path / "ComfyUI"
    root.mkdir()
    (root / "main.py").write_text("# test runtime", encoding="utf-8")
    (root / "custom_nodes").mkdir()
    settings = SimpleNamespace(preferences_file=tmp_path / "preferences.json", comfyui_root=root,
                               huggingface_token_file=tmp_path / "hf", comfyui_url="http://127.0.0.1:8188")
    monkeypatch.setattr(models, "get_settings", lambda: settings)
    monkeypatch.setattr(models, "node_checks", lambda *_: ([], {"status": "unavailable"}))
    item = {"group": "ref2v-int8", "repo": "public/model", "revision": "a"*40, "file": "weights.safetensors",
            "local_dir": "diffusion_models", "size": 4, "sha256": hashlib.sha256(b"test").hexdigest()}
    lock = tmp_path / "models.lock.json"
    lock.write_text(json.dumps({"artifacts": [item]}), encoding="utf-8")
    monkeypatch.setattr(models, "LOCK_FILE", lock)
    monkeypatch.setattr(models, "STATE", {"status": "idle"})
    return root, item


def test_fresh_local_setup_lists_missing_models_and_exact_destinations(local_setup):
    root, item = local_setup
    result = models.inventory(models.Selection(root=str(root)))
    assert result["missing_bytes"] == 4
    assert result["files"][0]["status"] == "missing"
    assert result["files"][0]["destination"] == str(root / "models/diffusion_models/weights.safetensors")
    assert item["revision"] in result["files"][0]["url"]
    assert not (root / "models").exists()  # checking never installs anything


def test_portable_parent_is_resolved_and_missing_comfyui_rejected(local_setup):
    root, _ = local_setup
    assert models.comfy_root(root.parent) == root
    with pytest.raises(ValueError):
        models.comfy_root(root / "not-installed")
    with pytest.raises(ValueError):
        models.comfy_root("relative/path")


def test_existing_size_conflict_is_not_considered_installed(local_setup):
    root, _ = local_setup
    path = root / "models/diffusion_models/weights.safetensors"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"wrong-size")
    assert models.inventory(models.Selection(root=str(root)))["files"][0]["status"] == "conflict"


def test_profile_selection_does_not_pull_other_profiles(local_setup):
    assert len(models.artifacts("h3")) == 1
    assert models.artifacts("qwen") == []
    with pytest.raises(ValueError):
        models.artifacts("arbitrary-url-or-profile")


def test_manifest_paths_cannot_escape_models_directory(local_setup, monkeypatch):
    root, item = local_setup
    item["local_dir"] = "../../outside"
    monkeypatch.setattr(models, "artifacts", lambda _: [item])
    with pytest.raises(ValueError, match="leaves"):
        models.inventory(models.Selection(root=str(root)))


def test_download_errors_never_expose_library_tokens_or_signed_urls(local_setup, monkeypatch):
    import huggingface_hub
    def fail(**kwargs):
        raise RuntimeError("sensitive-library-response-fixture")
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", fail)
    monkeypatch.setattr(models, "provider_secret", lambda *_: None)
    selection = models.Selection(root=str(local_setup[0]))
    models.STATE.update(status="downloading", completed_files=0)
    models.download_missing(selection, models.inventory(selection)["files"])
    assert models.STATE["status"] == "failed"
    assert "sensitive-library-response-fixture" not in json.dumps(models.STATE)


def test_node_check_does_not_probe_remote_comfyui_urls(tmp_path, monkeypatch):
    (tmp_path / "custom_nodes").mkdir()
    monkeypatch.setattr(models, "get_settings", lambda: SimpleNamespace(comfyui_url="http://public.example"))
    monkeypatch.setattr(models.httpx, "get", lambda *a, **k: pytest.fail("Remote probe forbidden"))
    nodes, runtime = models.node_checks(tmp_path, "h3")
    assert nodes and all(not node["found"] for node in nodes)
    assert runtime["status"] == "unavailable"


@pytest.mark.parametrize("content,expected", [(b"test", "completed"), (b"evil", "failed")])
def test_download_verifies_before_publishing(local_setup, monkeypatch, content, expected):
    import huggingface_hub
    root, _ = local_setup
    def download(**kwargs):
        assert kwargs["repo_id"] == "public/model"
        assert kwargs["revision"] == "a"*40
        target = Path(kwargs["local_dir"]) / kwargs["filename"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return str(target)
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", download)
    monkeypatch.setattr(models, "provider_secret", lambda *_: None)
    selection = models.Selection(root=str(root))
    rows = models.inventory(selection)["files"]
    models.STATE.update(status="downloading", completed_files=0)
    models.download_missing(selection, rows)
    assert models.STATE["status"] == expected
    target = root / "models/diffusion_models/weights.safetensors"
    assert target.exists() == (expected == "completed")
    if target.exists():
        assert target.read_bytes() == b"test"


def test_concurrent_existing_model_is_never_overwritten(local_setup, monkeypatch):
    import huggingface_hub
    root, _ = local_setup
    selection = models.Selection(root=str(root))
    rows = models.inventory(selection)["files"]
    target = root / "models/diffusion_models/weights.safetensors"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"mine")
    staged = root / "downloaded"
    staged.write_bytes(b"test")
    def download(**kwargs):
        path = Path(kwargs["local_dir"]) / kwargs["filename"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test")
        return str(path)
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", download)
    monkeypatch.setattr(models, "provider_secret", lambda *_: None)
    models.STATE.update(status="downloading", completed_files=0)
    models.download_missing(selection, rows)
    assert models.STATE["status"] == "failed"
    assert target.read_bytes() == b"mine"


def test_api_requires_admin_for_paths_and_same_origin_for_download(local_setup, monkeypatch):
    from fastapi import HTTPException
    calls = []
    def guard(request, mutation=False):
        calls.append(mutation)
        raise HTTPException(403 if mutation else 401, "Sign in")
    monkeypatch.setattr(models, "require_admin", guard)
    app = FastAPI()
    app.include_router(models.router)
    client = TestClient(app)
    assert client.get('/api/v1/setup/local-models').status_code == 401
    assert client.post('/api/v1/setup/local-models/download', json={"root": str(local_setup[0])}).status_code == 403
    assert calls == [False, True]


def test_download_conflicts_and_duplicate_requests_are_rejected(local_setup, monkeypatch):
    monkeypatch.setattr(models, "require_admin", lambda *a, **k: None)
    app = FastAPI()
    app.include_router(models.router)
    client = TestClient(app)
    root, _ = local_setup
    models.STATE["status"] = "downloading"
    assert client.post('/api/v1/setup/local-models/download', json={"root": str(root)}).status_code == 409
    models.STATE["status"] = "idle"
    path = root / "models/diffusion_models/weights.safetensors"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"wrong-size")
    assert client.post('/api/v1/setup/local-models/download', json={"root": str(root)}).status_code == 409
