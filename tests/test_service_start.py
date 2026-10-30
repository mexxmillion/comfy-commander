from backend import service_start as service


def test_start_does_not_duplicate_existing_service(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "ROOT", tmp_path)
    monkeypatch.setattr(service, "healthy", lambda _: True)
    monkeypatch.setattr(service.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("Duplicate service")))
    service.start("http://127.0.0.1:8000", 8000)


def test_start_launches_only_loopback_web_service_and_preserves_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "ROOT", tmp_path)
    checks = iter([False, True])
    monkeypatch.setattr(service, "healthy", lambda _: next(checks))
    calls = []
    monkeypatch.setattr(service.subprocess, "Popen", lambda command, **kwargs: calls.append((command, kwargs)))
    service.start("http://127.0.0.1:8000", 8000)
    command, options = calls[0]
    assert command[1:] == ["-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", "8000"]
    assert "env" not in options  # inherit saved host configuration; never replace credentials
    assert options["cwd"] == tmp_path
