from types import SimpleNamespace

import pytest


def test_vast_bootstrap_reaches_model_token_transfer_with_settings_object(monkeypatch, tmp_path):
    from backend.app import config, credential_paths, rental_cost, rental_preferences
    from backend.app.providers import vast
    from runtime.h3 import managed_worker as worker

    class StopBeforeRemoteInstall(Exception):
        pass

    configured = SimpleNamespace(max_gpu_price_per_hour=1, max_download_cost_per_gb=.01,
                                 max_active_gpus=1, max_total_price_per_hour=1,
                                 huggingface_token_file=tmp_path / "hf-token")
    offer = SimpleNamespace(id=1, gpu_name="RTX 4090", vram_gb=24, price_per_hour=.5, raw={})
    state = {"profile": "ref2v-int8", "offer_id": 1, "max_minutes": 30, "max_price_per_hour": 1}
    monkeypatch.setattr(config, "get_settings", lambda: configured)
    monkeypatch.setattr(worker, "preflight", lambda: None)
    monkeypatch.setattr(worker, "vast_api_key", lambda: "provider-fixture")
    monkeypatch.setattr(vast, "VastProvider", lambda _: SimpleNamespace(search_offers=lambda _: [offer]))
    monkeypatch.setattr(worker, "ranked_rental_offers", lambda *a, **k: [offer])
    monkeypatch.setattr(rental_cost, "vast_estimate", lambda *a: {})
    monkeypatch.setattr(rental_preferences, "record_preferences", lambda _: None)
    monkeypatch.setattr(rental_preferences, "offer_allowed", lambda *a: True)
    monkeypatch.setattr(worker, "check_credit", lambda *a: None)
    provider = SimpleNamespace(show_instances=lambda: [], create_instance=lambda **k: {"success": True, "new_contract": 123},
                               show_instance=lambda **k: {"ssh_host": "localhost", "ssh_port": 1234})
    monkeypatch.setattr(worker, "vast", lambda: provider)
    monkeypatch.setattr(worker, "ssh_bootstrap_command", lambda: "fixture")
    def update(**values):
        state.update(values)
        return dict(state)
    monkeypatch.setattr(worker, "update", update)
    monkeypatch.setattr(worker, "read_state", lambda: state)
    monkeypatch.setattr(worker, "log", lambda *a: None)
    monkeypatch.setattr(worker, "LOG", tmp_path / "bootstrap.log")
    monkeypatch.setattr(worker.subprocess, "Popen", lambda *a, **k: None)
    monkeypatch.setattr(worker, "run_ssh", lambda *a, **k: None)
    monkeypatch.setattr(worker, "credential_files", lambda _: [])
    monkeypatch.setattr(worker, "transfer", lambda *a: None)
    monkeypatch.setattr(worker, "capture_remote_logs", lambda *a: None)
    reads, transfers = [], []
    def secret(provider, path):
        reads.append((provider, path))
        return "isolated-token-fixture"
    monkeypatch.setattr(credential_paths, "provider_secret", secret)
    def transfer(record, token, destination):
        transfers.append((token, destination))
        raise StopBeforeRemoteInstall
    monkeypatch.setattr(worker, "transfer_secret", transfer)
    monkeypatch.setattr(worker, "destroy_instance", lambda *a: state.update(status="destroyed"))
    worker.launch(state)
    assert reads == [("huggingface", configured.huggingface_token_file)]
    assert transfers == [("isolated-token-fixture", "hf_read.txt")]
    assert state["status"] == "needs_attention"
    assert not state.get("destroyed_at")


def test_script_transfer_normalizes_windows_line_endings(monkeypatch, tmp_path):
    from runtime.h3 import managed_worker as worker
    script = tmp_path / "remote_start.sh"
    original = b"\xef\xbb\xbf#!/usr/bin/env bash\r\nset -euo pipefail\r\n"
    script.write_bytes(original)
    sent = []
    monkeypatch.setattr(worker, "_transfer_file",
                        lambda record, path, name: sent.append((path.read_bytes(), name, path)))
    worker.transfer({}, script, "remote_start.sh")
    assert sent[0][:2] == (b"#!/usr/bin/env bash\nset -euo pipefail\n", "remote_start.sh")
    assert not sent[0][2].exists()
    assert script.read_bytes() == original


def test_binary_transfer_is_unchanged(monkeypatch, tmp_path):
    from runtime.h3 import managed_worker as worker
    bundle = tmp_path / "runtime.tar.gz"
    bundle.write_bytes(b"\x00\r\n\xff")
    sent = []
    monkeypatch.setattr(worker, "_transfer_file", lambda record, path, name: sent.append(path))
    worker.transfer({}, bundle, "runtime.tar.gz")
    assert sent == [bundle]


def test_failed_rental_explains_commander_cleanup_and_actual_script_error(tmp_path, monkeypatch):
    from backend.app import rental_ui
    monkeypatch.setattr(rental_ui, "redact_log", lambda text: text)
    path = tmp_path / "rental-example.json"
    path.with_suffix(".log").write_text("set: pipefail\n: invalid option name\n", encoding="utf-8")
    result = rental_ui._decorate(dict(status="destroyed", destroyed_at="2026-10-30T03:50:00+00:00",
        destroy_reason="Setup failed: remote script failed", models_ready_at="2026-10-30T03:49:00+00:00"), path)
    assert result["failure"]
    assert "Commander terminated" in result["cleanup_explanation"]
    assert "Windows line endings" in result["failure_message"]
    assert "model download completed" in result["failed_stage"]


def test_linux_startup_scripts_parse_before_allocation(tmp_path):
    from runtime.h3.managed_worker import validate_startup_scripts
    validate_startup_scripts()
    for name in ("remote_prepare.sh", "remote_sync.sh", "remote_start.sh"):
        (tmp_path / name).write_bytes(b"#!/usr/bin/env bash\r\nif then\r\n")
    with pytest.raises(RuntimeError, match="syntax validation failed"):
        validate_startup_scripts(tmp_path)
