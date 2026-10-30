from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import local_runtime as runtime


def test_personal_comfyui_cannot_be_replaced(tmp_path):
    (tmp_path / 'main.py').write_text('personal install', encoding='utf-8')
    with pytest.raises(ValueError, match='never overwritten'):
        runtime.plan(runtime.RuntimeSelection(destination=str(tmp_path)))
    assert (tmp_path / 'main.py').read_text() == 'personal install'


def test_runtime_plan_is_profile_specific_and_confirmation_changes(tmp_path):
    root = tmp_path / 'managed'
    h3 = runtime.plan(runtime.RuntimeSelection(destination=str(root), model_set='h3'))
    qwen = runtime.plan(runtime.RuntimeSelection(destination=str(root), model_set='qwen'))
    assert h3['confirmation'] != qwen['confirmation']
    assert 'comfyui-minimaxrefpack' in {item['name'] for item in h3['components']}
    assert {item['name'] for item in qwen['components']} == {'ComfyUI', 'viggle_turbo.py'}
    assert not root.exists()  # review never installs code


def test_apply_rejects_unreviewed_plan_before_thread_or_download(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, 'require_admin', lambda *a, **k: None)
    monkeypatch.setattr(runtime, 'STATE', {'status': 'idle'})
    app = FastAPI()
    app.include_router(runtime.router)
    client = TestClient(app)
    result = client.post('/api/v1/setup/local-runtime/apply', json={'destination': str(tmp_path / 'managed'), 'confirmation': 'stale-plan'})
    assert result.status_code in {400, 409}  # platform unsupported or review rejected
    assert not (tmp_path / 'managed').exists()


def test_managed_install_stages_sources_and_uses_isolated_environment(tmp_path, monkeypatch):
    root = tmp_path / 'managed'
    monkeypatch.setattr(runtime, 'STATE', {'status': 'preparing'})
    monkeypatch.setattr(runtime, 'get_settings', lambda: SimpleNamespace(preferences_file=tmp_path / 'preferences.json'))
    calls, fetched = [], []
    def fetch(item, destination, cache):
        fetched.append(item)
        destination.mkdir(parents=True)
        (destination / '__init__.py').write_text('# source', encoding='utf-8')
        if destination.name == 'ComfyUI':
            (destination / 'main.py').write_text('# core', encoding='utf-8')
            (destination / 'requirements.txt').write_text('comfyui-workflow-templates==1.0\nnumpy\n', encoding='utf-8')
            (destination / 'custom_nodes').mkdir()
    def run(command, cwd):
        calls.append(command)
        if command[1:3] == ['-m', 'venv']:
            python = Path(command[3]) / 'Scripts/python.exe'
            python.parent.mkdir(parents=True)
            python.write_text('# interpreter fixture', encoding='utf-8')
    monkeypatch.setattr(runtime, 'fetch_source', fetch)
    monkeypatch.setattr(runtime, 'run', run)
    selection = runtime.RuntimeSelection(destination=str(root), model_set='qwen')
    runtime.prepare(selection)
    assert runtime.STATE['status'] == 'prepared'
    assert len(fetched) == 1  # Qwen only needs core plus bundled Turbo node
    assert (root / 'custom_nodes/viggle_turbo.py').is_file()
    assert not (root / 'custom_nodes/comfyui-minimaxrefpack').exists()
    assert (root / 'Start ComfyUI.cmd').is_file()
    assert 'workflow-templates' not in (root / 'commander-requirements.txt').read_text()
    assert all(command[0] == str(root / '.venv/Scripts/python.exe') for command in calls if 'pip' in command)
    assert not any('backend.worker' in command or 'uvicorn' in command for command in calls)
    # A repair preserves model/input/output data and backs up changed managed sources.
    for directory in ('models', 'input', 'output'):
        (root / directory / 'keep.txt').write_text('user data', encoding='utf-8')
    (root / 'main.py').write_text('# local change', encoding='utf-8')
    runtime.prepare(selection)
    assert runtime.STATE['status'] == 'prepared'
    assert any(path.read_text() == '# local change' for path in (root / 'source-backups').rglob('main.py'))
    for directory in ('models', 'input', 'output'):
        assert (root / directory / 'keep.txt').read_text() == 'user data'


def test_runtime_install_failure_does_not_claim_ready_or_expose_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, 'STATE', {'status': 'preparing'})
    monkeypatch.setattr(runtime, 'get_settings', lambda: SimpleNamespace(preferences_file=tmp_path / 'preferences.json'))
    def fail(*args):
        raise RuntimeError('sensitive-library-response-fixture')
    monkeypatch.setattr(runtime, 'fetch_source', fail)
    runtime.prepare(runtime.RuntimeSelection(destination=str(tmp_path / 'managed')))
    assert runtime.STATE['status'] == 'failed'
    assert 'sensitive-library-response-fixture' not in str(runtime.STATE)


def test_runtime_operation_reports_reboot_interruption(tmp_path, monkeypatch):
    import json
    import psutil
    monkeypatch.setattr(runtime, 'get_settings', lambda: SimpleNamespace(preferences_file=tmp_path / 'preferences.json'))
    runtime.operation_file().write_text(json.dumps({'status':'preparing','pid':99999999}), encoding='utf-8')
    def stopped(pid):
        raise psutil.NoSuchProcess(pid)
    monkeypatch.setattr(psutil, 'Process', stopped)
    assert runtime.read_operation()['status'] == 'failed'
    assert 'interrupted' in runtime.read_operation()['phase']
