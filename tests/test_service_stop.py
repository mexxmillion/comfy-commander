from types import SimpleNamespace

import pytest

from backend import service_stop


@pytest.mark.parametrize('module,args,owned,expected', [
    ('uvicorn', ['backend.app.main:app'], True, 'controller'),
    ('backend.worker', ['--local-gpu', '1'], True, 'local worker'),
    ('backend.worker', [], True, None),
    ('uvicorn', ['other.app:app'], True, None),
    ('uvicorn', ['backend.app.main:app'], False, None),
    ('runtime.h3.managed_worker', [], True, None),
    ('main.py', [], True, None),
])
def test_targets_only_owned_controller_and_local_workers(tmp_path, monkeypatch, module, args, owned, expected):
    monkeypatch.setattr(service_stop, 'ROOT', tmp_path)
    process = SimpleNamespace(cmdline=lambda: ['python', '-m', module, *args],
                              cwd=lambda: str(tmp_path if owned else tmp_path / 'other'))
    assert service_stop.owned_role(process) == expected


def test_stop_preserves_unrelated_processes_and_never_kills_children(monkeypatch, tmp_path):
    monkeypatch.setattr(service_stop, 'ROOT', tmp_path)
    calls = []
    def process(pid, module, args):
        return SimpleNamespace(pid=pid, cmdline=lambda: ['python', '-m', module, *args],
                               cwd=lambda: str(tmp_path), terminate=lambda: calls.append(pid))
    controller = process(1, 'uvicorn', ['backend.app.main:app'])
    worker = process(2, 'backend.worker', ['--local-gpu', '1'])
    watchdog = process(3, 'runtime.h3.managed_worker', ['watchdog'])
    monkeypatch.setattr(service_stop.psutil, 'process_iter', lambda: [controller, watchdog, worker])
    monkeypatch.setattr(service_stop.psutil, 'wait_procs', lambda targets, timeout: (targets, []))
    assert service_stop.stop() == 0
    assert calls == [2, 1]
