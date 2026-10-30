from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from backend.app import local_models


def test_lists_only_folders_without_reading_files(tmp_path):
    (tmp_path / 'ComfyUI').mkdir()
    (tmp_path / '.private').mkdir()
    (tmp_path / 'credential.txt').write_text('never return file contents')
    result = local_models.local_folder_listing(str(tmp_path))
    assert result['folders'] == [{'name': 'ComfyUI', 'path': str(tmp_path / 'ComfyUI')}]
    assert result['path'] == str(tmp_path.resolve())
    assert result['parent'] == str(tmp_path.parent)
    assert 'never return' not in str(result)


@pytest.mark.parametrize('value', ['relative/path', '//untrusted-server/share', '\\\\untrusted-server\\share'])
def test_refuses_relative_and_network_paths(value):
    with pytest.raises(ValueError):
        local_models.local_folder_listing(value)


def test_browser_requires_admin_before_any_listing(monkeypatch):
    def deny(*args, **kwargs):
        raise HTTPException(403, 'Sign in first')
    monkeypatch.setattr(local_models, 'require_admin', deny)
    monkeypatch.setattr(local_models, 'local_folder_listing', lambda *_: pytest.fail('Unauthenticated listing'))
    app = FastAPI()
    app.include_router(local_models.router)
    assert TestClient(app).get('/api/v1/setup/local-models/folders').status_code == 403


def test_bad_folder_error_does_not_echo_sensitive_path(monkeypatch):
    monkeypatch.setattr(local_models, 'require_admin', lambda *_: None)
    def failed(*args):
        raise OSError('private-path-detail')
    monkeypatch.setattr(local_models, 'local_folder_listing', failed)
    app = FastAPI()
    app.include_router(local_models.router)
    response = TestClient(app).get('/api/v1/setup/local-models/folders')
    assert response.status_code == 400
    assert 'private-path-detail' not in response.text
