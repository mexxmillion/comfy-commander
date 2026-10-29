from types import SimpleNamespace

import pytest
from starlette.requests import Request

from backend.app import community
from runtime.h3 import managed_worker


def test_missing_key_pair_has_setup_guidance_and_creates_nothing(monkeypatch, tmp_path):
    key = tmp_path / "cloud-worker-ed25519"
    monkeypatch.setattr(managed_worker, "SSH_KEY", key)
    monkeypatch.setattr(managed_worker, "SSH_PUBLIC_KEY", key.with_suffix(".pub"))
    with pytest.raises(RuntimeError, match="Cloud SSH access") as error:
        managed_worker.validate_ssh_key_files()
    assert "No GPU rental was started" in str(error.value)
    assert not key.exists()
    key.touch()
    with pytest.raises(RuntimeError):
        managed_worker.validate_ssh_key_files()
    key.with_suffix(".pub").touch()
    managed_worker.validate_ssh_key_files()


def test_ssh_setup_returns_command_not_private_contents(monkeypatch, tmp_path):
    key = tmp_path / "cloud worker"
    key.write_text("never-return-private-content", encoding="utf-8")
    monkeypatch.setattr(community, "get_settings", lambda: SimpleNamespace(cloud_ssh_key_file=key))
    monkeypatch.setattr(community, "require_admin", lambda request: None)
    result = community.cloud_ssh_status(Request({"type": "http"}))
    assert result["ready"] is False
    assert "ssh-keygen -t ed25519" in result["command"]
    assert "never-return-private-content" not in str(result)
