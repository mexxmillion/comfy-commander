import json
import subprocess

import pytest

from backend.app import phone_access as phone

DNS = "test.example.ts.net"


def mock_status(monkeypatch, config):
    monkeypatch.setattr(phone, "tailscale_executable", lambda: "tailscale")
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        data = {"BackendState": "Running", "Self": {"DNSName": DNS + "."}} if args[1] == "status" else config
        return subprocess.CompletedProcess(args, 0, json.dumps(data), "")

    monkeypatch.setattr(phone.subprocess, "run", run)
    return calls


def config(proxy="http://127.0.0.1:8000", port=443):
    return {"Web": {f"{DNS}:{port}": {"Handlers": {"/": {"Proxy": proxy}}}}}


def test_private_controller_url_and_no_peer_data(monkeypatch):
    calls = mock_status(monkeypatch, config(port=8443))
    result = phone.phone_access()
    assert result["url"] == f"https://{DNS}:8443/"
    assert result["state"] == "ready"
    assert all("--json" in args for args in calls)
    assert "Self" not in result and "Peer" not in result


def test_standard_https_port_is_explicit_in_copyable_url(monkeypatch):
    mock_status(monkeypatch, config())
    result = phone.phone_access()
    assert result["url"] == f"https://{DNS}:443/"
    assert result["https_port"] == 443


def test_phone_is_a_complete_onboarding_step():
    import re
    from pathlib import Path

    template = (Path(__file__).parents[1] / "backend/app/templates/setup.html").read_text(encoding="utf-8")
    assert re.findall(r'data-wizard-step="(\d+)"', template) == ["0", "1", "2", "3", "4", "5"]
    assert 'id="phone-access" class="wizard-panel" data-wizard-step="4"' in template
    assert 'href="https://tailscale.com/download"' in template
    assert 'data-step-target="4">Phone access' in template


@pytest.mark.parametrize("proxy", ["http://127.0.0.1:8188", "http://remote:8000", "http://127.0.0.1:8000/other"])
def test_unrelated_service_is_never_shown_as_commander(monkeypatch, proxy):
    mock_status(monkeypatch, config(proxy))
    result = phone.phone_access()
    assert result["url"] is None
    assert not result["can_enable"]
    with pytest.raises(ValueError):
        phone.enable_phone_access()


def test_public_funnel_is_not_labeled_private(monkeypatch):
    settings = config()
    settings["AllowFunnel"] = {f"{DNS}:443": True}
    mock_status(monkeypatch, settings)
    assert phone.phone_access()["url"] is None


def test_enable_requires_explicit_call_and_free_https_port(monkeypatch):
    calls = mock_status(monkeypatch, {})
    assert phone.phone_access()["can_enable"]
    assert not any("--bg" in args for args in calls)
    phone.enable_phone_access()
    assert sum("--bg" in args for args in calls) == 1
    assert ["tailscale", "serve", "--bg", "--yes", "http://127.0.0.1:8000"] in calls


def test_no_tailscale_hides_section(monkeypatch):
    monkeypatch.setattr(phone, "tailscale_executable", lambda: None)
    assert phone.phone_access()["available"] is False
