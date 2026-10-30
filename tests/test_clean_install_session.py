import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location('clean_install_session', Path(__file__).parents[1] / 'tools/clean_install_session.py')
session = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(session)


def test_recording_environment_does_not_inherit_working_credentials_or_data(tmp_path):
    inherited = dict(PATH='system-path', APPDATA='user-settings', TENSOR_MASTER_CREDENTIAL_DIR='old',
                     DATABASE_URL='old-db', OPENROUTER_API_KEY='old-llm', HF_TOKEN='old-hf',
                     VAST_API_KEY='old-vast', RUNPOD_API_KEY='old-runpod', COMFYUI_ROOT='old-runtime',
                     MODEL_DIRECTORIES='old-models', CLOUD_OUTPUT_DIR='old-outputs', PYTHONPATH='old-code')
    app = tmp_path / 'app'
    env = session.isolated_environment(app, inherited)
    assert env['PATH'] == inherited['PATH'] and env['APPDATA'] == inherited['APPDATA']
    assert all(env.get(name) != value for name, value in inherited.items() if name not in {'PATH', 'APPDATA'})
    assert env['TENSOR_MASTER_CREDENTIAL_DIR'] == str(app / 'state/credentials')
    assert env['OPENROUTER_SECRET_FILE'] == str(app / 'state/credentials/openrouter_api_key')
    assert inherited['HF_TOKEN'] == 'old-hf'
    assert not app.exists()


def test_clone_refuses_existing_folder_without_replacing_it(tmp_path, monkeypatch):
    app = tmp_path / 'app'
    app.mkdir()
    marker = app / 'keep.txt'
    marker.write_text('keep')
    monkeypatch.setattr(session.subprocess, 'run', lambda *a, **kw: pytest.fail('No command should run'))
    with pytest.raises(RuntimeError, match='already exists'):
        session.clone(tmp_path, {})
    assert marker.read_text() == 'keep'


def test_recording_start_refuses_another_service(tmp_path, monkeypatch):
    app = tmp_path / 'app'
    python = app / '.venv' / ('Scripts/python.exe' if session.os.name == 'nt' else 'bin/python')
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(session, 'require_checkout', lambda app: None)
    class BusyPort:
        def __enter__(self): return self
        def __exit__(self, *args): return False
    monkeypatch.setattr(session.socket, 'create_connection', lambda *a, **kw: BusyPort())
    monkeypatch.setattr(session.subprocess, 'run', lambda *a, **kw: pytest.fail('Existing server must not be reused or stopped'))
    monkeypatch.setattr(session.sys, 'argv', ['session', 'start', '--workspace', str(tmp_path)])
    assert session.main() == 1


def test_start_passes_isolated_environment_without_opening_old_browser(tmp_path,monkeypatch):
    app=tmp_path/'app'
    python=app/'.venv'/('Scripts/python.exe' if session.os.name=='nt' else 'bin/python')
    python.parent.mkdir(parents=True);python.touch()
    monkeypatch.setattr(session,'require_checkout',lambda app:None)
    def free_port(*a,**kw):raise ConnectionRefusedError
    monkeypatch.setattr(session.socket,'create_connection',free_port)
    monkeypatch.setenv('TENSOR_MASTER_CREDENTIAL_DIR','old-installation')
    monkeypatch.setenv('OPENROUTER_API_KEY','old-secret')
    calls=[]
    monkeypatch.setattr(session.subprocess,'run',lambda command,**kw:calls.append((command,kw)))
    monkeypatch.setattr(session.sys,'argv',['session','start','--workspace',str(tmp_path)])
    assert session.main()==0
    command,options=calls[0]
    assert '--no-browser' in command
    assert 'OPENROUTER_API_KEY' not in options['env']
    assert options['env']['TENSOR_MASTER_CREDENTIAL_DIR']==str(app/'state/credentials')
