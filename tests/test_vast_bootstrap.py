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
    with pytest.raises(StopBeforeRemoteInstall):
        worker.launch(state)
    assert reads == [("huggingface", configured.huggingface_token_file)]
    assert transfers == [("isolated-token-fixture", "hf_read.txt")]
    assert state["status"] == "destroyed"
