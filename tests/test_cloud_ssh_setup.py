from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import cloud_ssh, community


def fake_keygen(monkeypatch, key, *, race=False):
    protected = []
    monkeypatch.setattr(cloud_ssh, '_protect', lambda path, flags: protected.append(path))
    monkeypatch.setattr(cloud_ssh.shutil, 'which', lambda name: 'ssh-keygen')
    def run(command, **kwargs):
        assert protected and protected[0].is_dir()
        staged = Path(command[command.index('-f') + 1])
        staged.write_bytes(b'isolated test key material')
        Path(str(staged) + '.pub').write_text('isolated test public key')
        if race:
            Path(str(key) + '.pub').write_text('external existing public key')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(cloud_ssh.subprocess, 'run', run)
    return protected


def test_human_key_creation_protects_staging_and_refuses_overwrite(monkeypatch, tmp_path):
    key = tmp_path / 'worker-key'
    protected = fake_keygen(monkeypatch, key)
    cloud_ssh.create_cloud_key(key)
    assert key.is_file() and Path(str(key) + '.pub').is_file()
    assert len(protected) == 2
    before = key.read_bytes()
    with pytest.raises(ValueError, match='never overwritten'):
        cloud_ssh.create_cloud_key(key)
    assert key.read_bytes() == before


def test_concurrent_public_file_is_retained_and_partial_pair_rolled_back(monkeypatch, tmp_path):
    key = tmp_path / 'worker-key'
    fake_keygen(monkeypatch, key, race=True)
    with pytest.raises(FileExistsError):
        cloud_ssh.create_cloud_key(key)
    assert not key.exists()
    assert Path(str(key) + '.pub').read_text() == 'external existing public key'


def test_cloud_key_creation_requires_host_origin_admin_and_confirmation(monkeypatch, tmp_path):
    key = tmp_path / 'worker-key'
    monkeypatch.setattr(community, 'get_settings', lambda: SimpleNamespace(cloud_ssh_key_file=key))
    app = FastAPI()
    app.include_router(community.router)
    client = TestClient(app, base_url='http://127.0.0.1:8000', client=('127.0.0.1', 12345))
    assert client.post('/api/v1/setup/cloud-ssh', json={'confirm': True}).status_code == 403
    checks = []
    monkeypatch.setattr(community, 'require_admin', lambda request, **kw: checks.append(kw))
    calls = []
    monkeypatch.setattr(cloud_ssh, 'create_cloud_key', lambda path: calls.append(path))
    assert client.post('/api/v1/setup/cloud-ssh', json={'confirm': False}).status_code == 422
    assert client.post('/api/v1/setup/cloud-ssh', json={'confirm': True}, headers={'X-Forwarded-For': '100.64.0.2'}).status_code == 403
    assert not calls
    assert client.post('/api/v1/setup/cloud-ssh', json={'confirm': True}).status_code == 200
    assert calls == [key] and {'mutation': True} in checks

@pytest.mark.parametrize('first', ['vast', 'runpod'])
def test_connecting_provider_automatically_prepares_shared_ssh_once(monkeypatch, tmp_path, first):
    key = tmp_path / 'worker-key'
    monkeypatch.setattr(community, 'get_settings', lambda: SimpleNamespace(cloud_ssh_key_file=key))
    monkeypatch.setattr(community, 'require_admin', lambda *a, **kw: None)
    monkeypatch.setattr(community, '_test_secret', lambda *a: 'Provider verified.')
    monkeypatch.setattr(community, '_secret_path', lambda service: tmp_path / (service + '-api-key'))
    monkeypatch.setattr(cloud_ssh, 'register_vast_public_key', lambda *a: None)
    created = []
    def create(path):
        created.append(path)
        path.write_text('isolated test key material')
        Path(str(path) + '.pub').write_text('isolated test public key')
    monkeypatch.setattr(cloud_ssh, 'create_cloud_key', create)
    app = FastAPI(); app.include_router(community.router)
    client = TestClient(app)
    for provider in [first, 'runpod' if first == 'vast' else 'vast']:
        result = client.post('/api/v1/setup/secret', json={'service': provider, 'key': 'isolated-provider-test-key'})
        assert result.status_code == 200 and result.json()['cloud_ssh_ready']
        assert 'shared worker SSH key is ready' in result.json()['message']
    assert created == [key]


def test_provider_key_is_saved_with_actionable_warning_if_ssh_preparation_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(community, 'require_admin', lambda *a, **kw: None)
    monkeypatch.setattr(community, '_test_secret', lambda *a: 'Provider verified.')
    monkeypatch.setattr(community, '_secret_path', lambda service: tmp_path / 'provider-key')
    def fail():
        raise ValueError('OpenSSH unavailable')
    monkeypatch.setattr(community, 'prepare_cloud_ssh', fail)
    app = FastAPI(); app.include_router(community.router)
    result = TestClient(app).post('/api/v1/setup/secret', json={'service': 'vast', 'key': 'isolated-provider-test-key'})
    assert result.status_code == 200 and result.json()['cloud_ssh_ready'] is False
    assert (tmp_path / 'provider-key').is_file()
    assert 'OpenSSH Client' in result.json()['message']


def test_existing_provider_verify_requires_confirmation_and_never_returns_key(monkeypatch):
    calls = []
    monkeypatch.setattr(community, 'require_admin', lambda *a, **kw: calls.append(kw))
    monkeypatch.setattr(community, '_secret_path', lambda service: Path('fixture-provider'))
    monkeypatch.setattr(community, 'provider_secret', lambda *a: 'isolated-provider-test-key')
    monkeypatch.setattr(community, '_test_secret', lambda *a: 'Provider verified.')
    monkeypatch.setattr(community, 'prepare_provider_ssh', lambda *a: True)
    app = FastAPI()
    app.include_router(community.router)
    client = TestClient(app)
    assert client.post('/api/v1/setup/secret/vast/verify', json={'confirm':False}).status_code == 422
    response = client.post('/api/v1/setup/secret/vast/verify', json={'confirm':True})
    assert response.status_code == 200 and response.json()['cloud_ssh_ready']
    assert 'isolated-provider-test-key' not in response.text
    assert calls[-1] == {'mutation':True}


def test_vast_registration_sends_only_public_half_and_reuses_existing_key(monkeypatch, tmp_path):
    import vastai
    private = tmp_path / 'worker-key'
    private.write_text('private fixture must never be sent')
    public = 'ssh-ed25519 AAAATEST controller-fixture'
    Path(str(private)+'.pub').write_text(public)
    rows = []
    sent = []
    def create(*, ssh_key):
        assert ssh_key == public
        assert 'private fixture' not in ssh_key
        sent.append(ssh_key)
        rows.append({'public_key':ssh_key,'private_key':'unrelated account data'})
    provider = SimpleNamespace(show_ssh_keys=lambda:rows,create_ssh_key=create)
    monkeypatch.setattr(vastai,'VastAI',lambda **kw:provider)
    cloud_ssh.register_vast_public_key('isolated-provider-test-key',private)
    cloud_ssh.register_vast_public_key('isolated-provider-test-key',private)
    assert sent == [public]
    assert private.read_text() == 'private fixture must never be sent'
    assert cloud_ssh.vast_public_key_registered(provider,public+' comment-ignored')


def test_unregistered_vast_key_fails_preflight_before_paid_capacity(monkeypatch,tmp_path):
    from backend.app import credential_paths
    from runtime.h3 import managed_worker
    public=tmp_path/'worker.pub';public.write_text('ssh-ed25519 AAAATEST controller-fixture')
    monkeypatch.setattr(managed_worker,'settings',lambda:SimpleNamespace(vast_api_key_file=tmp_path/'vast',huggingface_token_file=tmp_path/'hf'))
    monkeypatch.setattr(managed_worker,'validate_ssh_key_files',lambda:None)
    monkeypatch.setattr(managed_worker,'SSH_PUBLIC_KEY',public)
    monkeypatch.setattr(credential_paths,'provider_secret',lambda *a:'isolated-provider-test-key')
    monkeypatch.setattr(managed_worker,'vast',lambda:SimpleNamespace(show_ssh_keys=lambda:[]))
    import pytest
    with pytest.raises(RuntimeError,match='not registered with Vast'):
        managed_worker.preflight()
