from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import rental_preferences as rentals
from backend.app import rental_ui


@pytest.fixture
def client(monkeypatch, tmp_path):
    settings = SimpleNamespace(
        rental_preferences_file=tmp_path / "rentals.json",
        max_gpu_price_per_hour=1.7,
        max_instance_lifetime_hours=2,
        max_active_gpus=3,
        max_total_price_per_hour=4,
    )
    monkeypatch.setattr(rentals, "get_settings", lambda: settings)
    app = FastAPI()
    app.include_router(rentals.router)
    app.include_router(rental_ui.router)
    return TestClient(app)


def test_requires_admin_and_same_origin(client):
    assert client.get("/api/v1/settings/rentals").status_code == 401
    assert client.put("/api/v1/settings/rentals", json={}).status_code == 403
    assert (
        client.put(
            "/api/v1/settings/rentals", json={}, headers={"Origin": "https://other.example"}
        ).status_code
        == 403
    )


def test_validation_save_reload_and_hard_caps(client, monkeypatch, tmp_path):
    monkeypatch.setattr(rental_ui, "require_admin", lambda *a, **kw: None)
    payload = {
        "gpu_class": "5090",
        "minimum_vram_gb": 32,
        "max_minutes": 30,
        "max_budget_usd": 2,
        "max_price_per_hour": 1,
    }
    assert client.post("/api/v1/settings/rentals/validate", json=payload).json()["valid"]
    assert not (tmp_path / "rentals.json").exists()
    assert client.put("/api/v1/settings/rentals", json=payload).json()["saved"]
    assert client.get("/api/v1/settings/rentals").json()["preferences"] == payload
    before = (tmp_path / "rentals.json").read_bytes()
    for invalid in [
        {"max_minutes": 150},
        {"max_price_per_hour": 1.8},
        {"gpu_class": "unsupported"},
        {"max_budget_usd": -1},
    ]:
        assert client.put("/api/v1/settings/rentals", json=payload | invalid).status_code == 422
        assert (tmp_path / "rentals.json").read_bytes() == before
    (tmp_path / "rentals.json").write_text("{broken")
    assert client.put("/api/v1/settings/rentals", json=payload).status_code == 503
    assert (tmp_path / "rentals.json").read_text() == "{broken"


def test_filters_download_budget_vram_and_class():
    prefs = rentals.RentalPreferences(
        gpu_class="consumer",
        minimum_vram_gb=32,
        max_minutes=60,
        max_price_per_hour=1,
        max_budget_usd=1.2,
    )
    good = {"gpu": "RTX 5090", "vram_gb": 32, "price_per_hour": 0.8, "sync_cost": 0.3}
    assert rentals.offer_allowed(good, prefs)
    for change in [
        {"gpu": "RTX 6000 Ada"},
        {"vram_gb": 24},
        {"price_per_hour": 1.1},
        {"sync_cost": 0.5},
        {"price_per_hour": float("nan")},
    ]:
        assert not rentals.offer_allowed(good | change, prefs)
    filtered = rentals.filter_offers([good, good | {"sync_cost": 0.5}], prefs)
    assert len(filtered) == 1 and filtered[0]["maximum_duration_cost"] == 1.1
    snapshot = rentals.record_preferences(prefs.model_dump())
    assert rentals.offer_allowed(good, snapshot)


def test_launch_rejects_overrides_before_runtime_or_provider_actions(client, monkeypatch):
    monkeypatch.setattr(rental_ui, "_admin", lambda *a, **kw: None)
    monkeypatch.setattr(
        rental_ui,
        "read_rental_preferences",
        lambda: rentals.RentalPreferences(max_minutes=30, max_price_per_hour=1, max_budget_usd=1),
    )
    actions = []
    monkeypatch.setattr(rental_ui, "build_bundle", lambda: actions.append("build"))
    base = {
        "provider": "runpod",
        "offer_id": "RTX 5090",
        "max_minutes": 30,
        "max_price_per_hour": 0.8,
    }
    for change in [
        {"max_minutes": 90},
        {"max_price_per_hour": 1.1},
        {"count": 3},
        {"max_budget_usd": 0.1},
    ]:
        assert client.post("/api/v1/cloud/launch", json=base | change).status_code == 422
    assert not actions


def test_runpod_rechecks_offer_before_paid_create(monkeypatch):
    from runtime.h3 import runpod_worker

    calls = []
    provider = SimpleNamespace(
        offers=lambda maximum: [
            {"id": "gpu", "gpu": "RTX 5090", "vram_gb": 32, "price_per_hour": 1}
        ],
        create_instance=lambda *a, **kw: calls.append("create"),
    )
    monkeypatch.setattr(runpod_worker, "client", lambda: provider)
    monkeypatch.setattr(runpod_worker, "preflight", lambda: None)
    monkeypatch.setattr(runpod_worker.shared, "update", lambda **kw: calls.append(kw))
    monkeypatch.setattr(runpod_worker.shared, "log", lambda *a: None)
    record = {
        "profile": "ref2v-int8",
        "offer_id": "gpu",
        "max_minutes": 30,
        "max_price_per_hour": 1.2,
        "max_budget_usd": 0.1,
    }
    with pytest.raises(RuntimeError, match="budget limits"):
        runpod_worker.launch(record)
    assert "create" not in calls
    assert any("budget limits" in str(item) for item in calls)


def test_compact_settings_include_rental_controls():
    from jinja2 import Environment, FileSystemLoader

    templates = Path(__file__).parents[1] / "backend/app/templates"
    html = (
        Environment(loader=FileSystemLoader(templates))
        .get_template("setup.html")
        .render(status={}, profiles={}, settings_mode=True)
    )
    assert 'data-step-target="9"' in html and 'id="rental-preferences-form"' in html
    for field in rentals.RentalPreferences.model_fields:
        assert f'name="{field}"' in html


def test_vast_rechecks_download_budget_before_paid_create(monkeypatch, tmp_path):
    from backend.app.providers import vast
    from runtime.h3 import managed_worker

    key = tmp_path / "provider-fixture"
    key.write_text("isolated-provider-test-key")
    offer = SimpleNamespace(id=1, gpu_name="RTX 5090", vram_gb=32, price_per_hour=0.8, raw={})
    monkeypatch.setattr(managed_worker, "KEY", key)
    monkeypatch.setattr(managed_worker, "preflight", lambda: None)
    monkeypatch.setattr(managed_worker, "ranked_rental_offers", lambda *a, **kw: [offer])
    monkeypatch.setattr(
        vast, "VastProvider", lambda key: SimpleNamespace(search_offers=lambda query: [offer])
    )
    from backend.app import rental_cost

    monkeypatch.setattr(rental_cost, "vast_estimate", lambda *a: {"sync_cost": 0.5})
    calls = []
    monkeypatch.setattr(
        managed_worker, "vast", lambda: pytest.fail("Provider mutation must not run")
    )
    monkeypatch.setattr(managed_worker, "log", lambda *a: None)
    monkeypatch.setattr(managed_worker, "update", lambda **kw: calls.append(kw))
    with pytest.raises(RuntimeError, match="No equivalent"):
        managed_worker.launch(
            {
                "profile": "ref2v-int8",
                "offer_id": 1,
                "max_minutes": 60,
                "max_price_per_hour": 1,
                "max_budget_usd": 1,
            }
        )
    assert calls[-1]["status"] == "failed"
