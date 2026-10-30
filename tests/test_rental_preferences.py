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
        {"max_price_per_hour": 4.1},
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


def test_manager_uses_connected_vast_credential_instead_of_legacy_file(monkeypatch, tmp_path):
    from backend.app import credential_paths
    from runtime.h3 import managed_worker
    configured = tmp_path / 'configured-vast-key'
    monkeypatch.setattr(managed_worker, 'settings', lambda: SimpleNamespace(vast_api_key_file=configured))
    calls = []
    monkeypatch.setattr(credential_paths, 'provider_secret', lambda service, path: calls.append((service,path)) or 'isolated-provider-test-key')
    assert managed_worker.vast_api_key() == 'isolated-provider-test-key'
    assert calls == [('vast', configured)]
    assert not configured.exists()


@pytest.mark.parametrize("reported,expected", [(23.98828125,24), (15.92,16), (23.4,23.4)])
def test_driver_reserved_vram_compares_consistently(reported, expected):
    assert rentals.nominal_vram_gb(reported) == expected


def test_gpu_defaults_and_explicit_5080_preserve_cost_limits():
    base = rentals.RentalPreferences(minimum_vram_gb=24, max_budget_usd=2, max_minutes=30)
    assert base.gpu_class == "5090"
    selected = rentals.selected_gpu_preferences(base, "5080")
    assert selected.minimum_vram_gb == 16
    assert selected.max_budget_usd == 2 and selected.max_minutes == 30
    assert rentals.offer_allowed(dict(gpu="RTX 5080", vram_gb=15.92, price_per_hour=.5), selected)
    assert not rentals.offer_allowed(dict(gpu="RTX 5080", vram_gb=15.92, price_per_hour=.5), base)


@pytest.mark.parametrize("workers,fragment", [
    ([], "disappeared"),
    ([dict(id="vast-123", runtime="wrong", status="ready")], "incompatible"),
    ([dict(id="vast-123", runtime="pocket-h3-cloud-ref2v-int8", status="offline")], "offline"),
])
def test_worker_problems_remain_visible(workers, fragment):
    record = dict(provider="vast", instance_id=123, profile="ref2v-int8", status="ready")
    assert fragment in rental_ui.worker_health(record, workers)["worker_warning"]


def test_incompatible_or_unhealthy_worker_cannot_pass_readiness():
    from runtime.h3.managed_worker import worker_ready
    good = dict(id="vast-123", runtime="pocket-h3-cloud-ref2v-int8", status="ready")
    assert worker_ready(good, "vast-123", "ref2v-int8")
    assert not worker_ready(good | dict(status="unhealthy"), "vast-123", "ref2v-int8")
    assert not worker_ready(good | dict(runtime="other"), "vast-123", "ref2v-int8")


def test_diagnostics_require_admin(client):
    assert client.get("/api/v1/cloud/diagnostics/rental-example").status_code == 401


def test_diagnostic_logs_redact_credentials(monkeypatch, tmp_path):
    from backend.app import credential_paths
    secret = "fixture-provider-private-value"
    keyfile = tmp_path / "key"
    keyfile.write_text(secret, encoding="utf-8")
    settings = SimpleNamespace(**{name: keyfile for name in
        ("vast_api_key_file", "huggingface_token_file", "runpod_api_key_file", "token_file")})
    monkeypatch.setattr(rental_ui, "get_settings", lambda: settings)
    monkeypatch.setattr(credential_paths, "provider_secret", lambda *args: secret)
    text = rental_ui.redact_log(secret + " Bearer abc123 https://example.test/file?signature=private")
    assert secret not in text and "abc123" not in text and "signature=private" not in text


def test_admin_can_raise_saved_hourly_ceiling_above_initial_default(client,monkeypatch):
    monkeypatch.setattr(rental_ui,'require_admin',lambda *a,**kw:None)
    payload=dict(gpu_class='5090',minimum_vram_gb=16,max_minutes=30,max_budget_usd=2,max_price_per_hour=3)
    assert client.put('/api/v1/settings/rentals',json=payload).status_code==200
    assert client.get('/api/v1/settings/rentals').json()['preferences']['max_price_per_hour']==3

@pytest.mark.parametrize('fail_on',[1,2])
def test_partial_quantity_launch_preserves_receipts_and_stops_remaining(monkeypatch,tmp_path,fail_on):
    from runtime.h3 import runpod_worker
    from types import SimpleNamespace
    import json
    app=FastAPI();app.include_router(rental_ui.router)
    monkeypatch.setattr(rental_ui,'_admin',lambda *a,**kw:None)
    monkeypatch.setattr(rental_ui,'get_settings',lambda:SimpleNamespace(max_gpu_price_per_hour=2,max_instance_lifetime_hours=2,max_active_gpus=10,max_total_price_per_hour=10))
    monkeypatch.setattr(rental_ui,'read_rental_preferences',lambda:rentals.RentalPreferences(max_minutes=30,max_price_per_hour=1,max_budget_usd=10))
    for name in ['build_bundle','validate_startup_scripts']:
        monkeypatch.setattr(rental_ui,name,lambda:None)
    monkeypatch.setattr(runpod_worker,'preflight',lambda:None)
    monkeypatch.setattr(rental_ui,'active_rentals',lambda:[])
    monkeypatch.setattr(rental_ui,'ROOT',tmp_path)
    monkeypatch.setattr(rental_ui,'RENTALS',tmp_path/'state/rentals')
    calls=[]
    def spawn(*args,**kwargs):
        calls.append(args)
        if len(calls)==fail_on:raise OSError('fixture manager failure')
        return SimpleNamespace(pid=123)
    monkeypatch.setattr(rental_ui.subprocess,'Popen',spawn)
    response=TestClient(app).post('/api/v1/cloud/launch',json=dict(provider='runpod',offer_id='fixture-gpu',profile='ref2v-int8',count=3,max_minutes=30,max_price_per_hour=.1,max_budget_usd=10))
    assert len(calls)==fail_on
    records={p.stem:json.loads(p.read_text()) for p in (tmp_path/'state/rentals').glob('*.json')}
    assert len(records)==fail_on
    assert sum(r['status']=='failed' for r in records.values())==1
    if fail_on==1:
        assert response.status_code==503 and 'No GPU request was submitted' in response.json()['detail']
        assert response.json()['allocation_started'] is False
    else:
        assert response.status_code==202
        data=response.json()
        assert data['status']=='partial' and len(data['rentals'])==1
        assert records[data['rentals'][0]]['status']=='requested'
        assert records[data['failed_rentals'][0]]['status']=='failed'
        assert 'Do not repeat the full launch' in data['warning']


def test_tunnel_log_is_available_in_diagnostics_and_stage(client,monkeypatch,tmp_path):
    import json
    path=tmp_path/'rental-fixture.json';path.write_text(json.dumps(dict(rental_id='rental-fixture',status='ready',provider='runpod',profile='ref2v-int8')))
    path.with_name('rental-fixture-tunnel.log').write_text('remote port forwarding failed private-fixture')
    monkeypatch.setattr(rental_ui,'_admin',lambda *a,**kw:None)
    monkeypatch.setattr(rental_ui,'_state_path',lambda rid:path)
    monkeypatch.setattr(rental_ui,'_decorate',lambda record,path:record)
    monkeypatch.setattr(rental_ui,'redact_log',lambda text:text.replace('private-fixture','[REDACTED]'))
    stage=client.get('/api/v1/cloud/logs/tunnel?rental_id=rental-fixture')
    assert stage.status_code==200 and '[REDACTED]' in stage.text
    report=client.get('/api/v1/cloud/diagnostics/rental-fixture')
    assert report.status_code==200 and 'SSH tunnel' in report.json()['log']
    assert 'private-fixture' not in report.text
