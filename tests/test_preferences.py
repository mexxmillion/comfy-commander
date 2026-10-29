from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jinja2 import Environment, FileSystemLoader

from backend.app import preferences


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(preferences, 'get_settings', lambda: SimpleNamespace(preferences_file=tmp_path / 'preferences.json'))
    app = FastAPI()
    app.include_router(preferences.router)
    return TestClient(app)


def test_preferences_require_admin_and_same_origin(client, monkeypatch):
    assert client.get('/api/v1/settings/preferences').status_code == 401
    assert client.put('/api/v1/settings/preferences', json={}).status_code == 403
    assert client.put('/api/v1/settings/preferences', json={}, headers={'Origin': 'https://other.example'}).status_code == 403


def test_validate_does_not_save_and_updates_survive_reload(client, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(preferences, 'require_admin', lambda request, **kw: calls.append(kw))
    payload = {'aspect_ratio': '16:9', 'duration': 8, 'turbo_steps': 8, 'render_target': 'local'}
    assert client.post('/api/v1/settings/preferences/validate', json=payload).json()['valid']
    assert not (tmp_path / 'preferences.json').exists()
    assert calls[-1] == {'mutation': True}
    assert client.put('/api/v1/settings/preferences', json=payload).json()['saved']
    assert calls[-1] == {'mutation': True}
    reloaded = client.get('/api/v1/settings/preferences').json()
    assert reloaded['aspect_ratio'] == '16:9' and reloaded['render_target'] == 'local'
    assert reloaded['megapixels'] == 0.5


@pytest.mark.parametrize('payload', [{'duration': -1}, {'duration': 1.1}, {'turbo_steps': 10}, {'render_target': 'rent'}, {'tailscale_url': 'https://other.example'}, {'megapixels': 50}])
def test_invalid_preferences_do_not_overwrite(client, monkeypatch, tmp_path, payload):
    monkeypatch.setattr(preferences, 'require_admin', lambda *a, **kw: None)
    client.put('/api/v1/settings/preferences', json={'duration': 6})
    before = (tmp_path / 'preferences.json').read_bytes()
    assert client.put('/api/v1/settings/preferences', json=payload).status_code == 422
    assert (tmp_path / 'preferences.json').read_bytes() == before


def test_unreadable_preferences_are_preserved(client, monkeypatch, tmp_path):
    monkeypatch.setattr(preferences, 'require_admin', lambda *a, **kw: None)
    path = tmp_path / 'preferences.json'
    path.write_text('{broken')
    assert client.put('/api/v1/settings/preferences', json={}).status_code == 503
    assert path.read_text() == '{broken'


def test_settings_and_onboarding_share_management_without_wizard_in_settings():
    environment = Environment(loader=FileSystemLoader(Path(__file__).parents[1] / 'backend/app/templates'))
    template = environment.get_template('setup.html')
    setup = template.render(status={}, profiles={}, settings_mode=False)
    settings = template.render(status={}, profiles={}, settings_mode=True)
    assert 'aria-label="Setup progress"' in setup
    assert 'id="preferences-form"' not in setup
    assert 'aria-label="Settings sections"' in settings
    assert 'aria-label="Setup progress"' not in settings
    for element in ['preferences-form', 'phone-access-url', 'lora-upload-form', 'import-profile-file', 'cloud-ssh-setup']:
        assert f'id="{element}"' in settings
