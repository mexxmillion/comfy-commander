from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import rental_ui
from backend.app.auth import ensure_token, password_record
from backend.app.identity import authority

OLD = 'old-test-password-123'
NEW = 'new-test-password-456'


@pytest.fixture
def auth(tmp_path, monkeypatch):
    token_file = tmp_path / 'api_token'
    key = ensure_token(token_file)
    identity = authority(token_file)
    identity.set_password(password_record(OLD))
    identity.export()
    monkeypatch.setattr(rental_ui, 'get_settings', lambda: SimpleNamespace(token_file=token_file))
    monkeypatch.setattr(rental_ui, 'LOGIN_ATTEMPTS', {})
    app = FastAPI()
    app.include_router(rental_ui.router)
    client = TestClient(app, base_url='http://127.0.0.1:8000', client=('127.0.0.1', 12345))
    client.headers['Origin'] = 'http://127.0.0.1:8000'
    return client, identity, key


def login(client, password=OLD):
    response = client.post('/api/v1/cloud/login', json={'password': password})
    assert response.status_code == 200
    return client.cookies.get(rental_ui.COOKIE)


def test_password_change_requires_signin_current_password_and_same_origin(auth):
    client, identity, _ = auth
    payload = {'current_password': OLD, 'new_password': NEW}
    assert client.post('/api/v1/cloud/password-change', json=payload).status_code == 401
    session = login(client)
    assert client.post('/api/v1/cloud/password-change', json=payload, headers={'Origin': 'https://other.example'}).status_code == 403
    assert client.post('/api/v1/cloud/password-change', json={**payload, 'current_password': 'incorrect'}).status_code == 401
    assert identity.session_valid(session)


def test_password_change_revokes_every_device_and_keeps_identity(auth):
    client, identity, key = auth
    session = login(client)
    other = identity.new_session(3600)
    result = client.post('/api/v1/cloud/password-change', json={'current_password': OLD, 'new_password': NEW})
    assert result.status_code == 200
    assert not identity.session_valid(session) and not identity.session_valid(other)
    assert identity.legacy_revoked('old-unregistered-cookie')
    assert identity.principal(key)['role'] == 'bootstrap'
    assert client.post('/api/v1/cloud/login', json={'password': OLD}).status_code == 401
    login(client, NEW)
    assert OLD not in result.text and NEW not in result.text and key not in result.text


def test_forgot_password_requires_host_and_recovery_key(auth):
    client, identity, key = auth
    assert client.get('/api/v1/cloud/password-recovery').json()['available']
    payload = {'recovery_key': key, 'new_password': NEW}
    assert client.post('/api/v1/cloud/password-recover', json=payload, headers={'X-Forwarded-For': '100.64.0.2'}).status_code == 403
    assert client.post('/api/v1/cloud/password-recover', json={**payload, 'recovery_key': 'incorrect'}).status_code == 401
    worker_key = identity.enroll_worker('test')
    assert client.post('/api/v1/cloud/password-recover', json={**payload, 'recovery_key': worker_key}).status_code == 401
    result = client.post('/api/v1/cloud/password-recover', json=payload)
    assert result.status_code == 200
    assert key not in result.text
    login(client, NEW)


def test_phone_cannot_read_recovery_path_or_reset(auth):
    client, _, key = auth
    client.base_url = 'https://commander.example.ts.net'
    client.headers['Origin'] = str(client.base_url).rstrip('/')
    info = client.get('/api/v1/cloud/password-recovery').json()
    assert not info['available'] and info['recovery_key_file'] is None
    assert client.post('/api/v1/cloud/password-recover', json={'recovery_key': key, 'new_password': NEW}).status_code == 403


def test_recovery_is_rate_limited_and_short_password_rejected(auth):
    client, _, key = auth
    assert client.post('/api/v1/cloud/password-recover', json={'recovery_key': key, 'new_password': 'short'}).status_code == 422
    for _ in range(5):
        assert client.post('/api/v1/cloud/password-recover', json={'recovery_key': 'bad', 'new_password': NEW}).status_code == 401
    assert client.post('/api/v1/cloud/password-recover', json={'recovery_key': key, 'new_password': NEW}).status_code == 429
