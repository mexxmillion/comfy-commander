import subprocess

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import rental_ui


@pytest.fixture
def runtime_env(monkeypatch, tmp_path):
    root = tmp_path / "comfy"
    (root / "custom_nodes").mkdir(parents=True)
    python = tmp_path / "python.exe"
    python.touch()
    monkeypatch.setenv("COMFYUI_ROOT", str(root))
    monkeypatch.setenv("COMFYUI_PYTHON", str(python))


@pytest.mark.parametrize("name", ["COMFYUI_ROOT", "COMFYUI_PYTHON"])
@pytest.mark.parametrize("value", [None, "   ", "missing-private-installation"])
def test_bundle_reports_configuration_without_running_builder(
    monkeypatch, runtime_env, name, value
):
    if value is None:
        monkeypatch.delenv(name)
    else:
        monkeypatch.setenv(name, value)

    def unexpected_run(*args, **kwargs):
        pytest.fail("Builder must not run with invalid configuration")

    monkeypatch.setattr(rental_ui.subprocess, "run", unexpected_run)
    with pytest.raises(RuntimeError) as error:
        rental_ui.build_bundle()
    assert name in str(error.value)
    assert "No GPU rental was started" in str(error.value)
    assert "missing-private-installation" not in str(error.value)


def test_bundle_keeps_subprocess_diagnostics_private(monkeypatch, runtime_env):
    monkeypatch.setattr(
        rental_ui.subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 1, stdout="private-path", stderr="secret-value"
        ),
    )
    with pytest.raises(RuntimeError) as error:
        rental_ui.build_bundle()
    assert "packaging failed" in str(error.value)
    assert "private-path" not in str(error.value)
    assert "secret-value" not in str(error.value)


def test_launch_returns_actionable_conflict_before_provider_actions(monkeypatch, runtime_env):
    monkeypatch.delenv("COMFYUI_PYTHON")
    monkeypatch.setattr(rental_ui, "_admin", lambda *args, **kwargs: None)

    def unexpected_process(*args, **kwargs):
        pytest.fail("No builder or rental process may start")

    monkeypatch.setattr(rental_ui.subprocess, "run", unexpected_process)
    monkeypatch.setattr(rental_ui.subprocess, "Popen", unexpected_process)
    app = FastAPI()
    app.include_router(rental_ui.router)
    with TestClient(app) as client:
        response = client.post("/api/v1/cloud/launch", json={
            "offer_id": "test-gpu", "max_price_per_hour": 0.01,
        })
    assert response.status_code == 409
    assert "COMFYUI_PYTHON" in response.json()["detail"]
    assert "restart the web server" in response.json()["detail"]
