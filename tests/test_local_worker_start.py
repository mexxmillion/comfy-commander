from types import SimpleNamespace

import pytest

from backend import local_worker_start as worker


def process(root, controller, identity):
    return SimpleNamespace(info={"cmdline": ["python", "-m", "backend.worker"]}, pid=123,
                           cwd=lambda: str(root), environ=lambda: {"TENSOR_MASTER_URL": controller,
                           "TENSOR_WORKER_ID": identity, "COMFYUI_URL": "http://localhost:8188"})


def test_existing_matching_worker_is_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "ROOT", tmp_path)
    monkeypatch.setattr(worker.psutil, "process_iter", lambda *_: [process(tmp_path, 'http://127.0.0.1:8000', 'local-gpu-1')])
    assert worker.worker_owner('local-gpu-1', 'http://127.0.0.1:8188', 'http://127.0.0.1:8000') == 123


def test_other_dispatcher_worker_blocks_competing_connection(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "ROOT", tmp_path)
    monkeypatch.setattr(worker.psutil, "process_iter", lambda *_: [process(tmp_path / 'other', 'https://other.example', 'other-worker')])
    with pytest.raises(RuntimeError, match='Another worker'):
        worker.worker_owner('local-gpu-1', 'http://127.0.0.1:8188', 'http://127.0.0.1:8000')


def test_local_launcher_rejects_nonlocal_comfyui():
    with pytest.raises(RuntimeError, match='localhost'):
        worker.local_endpoint('http://public.example:8188')
