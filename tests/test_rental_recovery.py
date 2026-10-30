from datetime import timedelta
from types import SimpleNamespace

import pytest

from runtime.h3 import managed_worker as worker


def phase_fixture(monkeypatch, tmp_path, results):
    clock = [100.0]
    record = dict(status="syncing_models", deadline=(worker.now()+timedelta(minutes=20)).isoformat())
    starts, logs = [], []
    monkeypatch.setattr(worker.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(worker.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0]+seconds))
    monkeypatch.setattr(worker, "read_state", lambda: record)
    monkeypatch.setattr(worker, "log", logs.append)
    monkeypatch.setattr(worker, "ssh_arguments", lambda r: [])
    monkeypatch.setattr(worker, "run_ssh", lambda r, command, **kw: starts.append(command))
    monkeypatch.setattr(worker, "phase_log", lambda name: tmp_path / (name+'.log'))
    monkeypatch.setattr(worker.subprocess, "run", lambda *a, **kw: next(results))
    return record, starts, logs


def test_connection_reset_reconnects_without_restarting_setup(monkeypatch, tmp_path):
    results = iter([SimpleNamespace(returncode=255), SimpleNamespace(returncode=0, stdout=b'model progress\n\n__COMMANDER_EXIT__0\n')])
    record, starts, logs = phase_fixture(monkeypatch, tmp_path, results)
    path = tmp_path/'models.log'
    worker.run_remote_phase(record, 'bash /workspace/remote_sync.sh', timeout=300, logfile=path)
    assert len(starts) == 1 and 'nohup flock -n' in starts[0]
    assert 'reconnected' in ' '.join(logs)
    assert path.read_text() == 'model progress\n'


def test_continuous_connection_loss_retries_for_two_minutes(monkeypatch, tmp_path):
    results = iter([SimpleNamespace(returncode=255)] * 40)
    record, starts, logs = phase_fixture(monkeypatch, tmp_path, results)
    with pytest.raises(TimeoutError, match='two minutes'):
        worker.run_remote_phase(record, 'fixture', timeout=300, logfile=tmp_path/'models.log')
    assert len(starts) == 1
    assert 'remote setup continues' in ' '.join(logs)


def test_transient_download_exit_retries_same_instance(monkeypatch, tmp_path):
    results = iter([SimpleNamespace(returncode=0, stdout=b'temporary connection error\n\n__COMMANDER_EXIT__1\n'),
                    SimpleNamespace(returncode=0, stdout=b'MODELS_READY\n\n__COMMANDER_EXIT__0\n')])
    record, starts, logs = phase_fixture(monkeypatch, tmp_path, results)
    worker.run_remote_phase(record, 'fixture', timeout=300, logfile=tmp_path/'models.log')
    assert len([s for s in starts if 'nohup' in s]) == 2
    assert 'cached partial files' in ' '.join(logs)


def test_script_failure_is_retained_with_original_deadline(monkeypatch):
    record = dict(status='preparing', deadline=(worker.now()+timedelta(minutes=20)).isoformat())
    updates = []
    monkeypatch.setattr(worker, 'update', lambda **kw: updates.append(kw))
    monkeypatch.setattr(worker, 'log', lambda message: None)
    assert worker.retain_for_diagnosis(record, RuntimeError('script failed'))
    assert updates[0]['status'] == 'needs_attention'
    assert 'deadline' not in updates[0]
    assert not worker.retain_for_diagnosis(record, TimeoutError('two minutes'))


def test_permission_failure_does_not_blindly_redownload(monkeypatch, tmp_path):
    results = iter([SimpleNamespace(returncode=0, stdout=b'403 access denied\n\n__COMMANDER_EXIT__1\n')])
    record, starts, logs = phase_fixture(monkeypatch, tmp_path, results)
    with pytest.raises(RuntimeError, match='exited with code 1'):
        worker.run_remote_phase(record, 'fixture', timeout=300, logfile=tmp_path/'models.log')
    assert len(starts) == 1
