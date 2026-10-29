"""Read-only discovery of this controller's private Tailscale Serve URL."""
import json
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit


def tailscale_executable() -> str | None:
    executable = shutil.which("tailscale")
    installed = Path("C:/Program Files/Tailscale/tailscale.exe")
    if not executable and installed.is_file():
        executable = str(installed)
    return executable


def phone_access(port: int = 8000) -> dict:
    executable = tailscale_executable()
    result = {"available": bool(executable), "connected": False, "url": None,
              "suggested_url": None, "state": "unavailable", "can_enable": False}
    if not executable:
        return result

    def read(*args):
        response = subprocess.run(
            [executable, *args], capture_output=True, text=True, timeout=5, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if response.returncode:
            raise ValueError("Tailscale status unavailable")
        value = json.loads(response.stdout)
        if not isinstance(value, dict):
            raise TypeError("Invalid Tailscale status")
        return value

    try:
        status = read("status", "--json")
        result["connected"] = status.get("BackendState") == "Running"
        if not result["connected"]:
            result["state"] = "disconnected"
            return result
        dns = str((status.get("Self") or {}).get("DNSName", "")).rstrip(".")
        if not re.fullmatch(r"[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*\.ts\.net", dns):
            result["state"] = "dns_unavailable"
            return result
        result.update(suggested_url=f"https://{dns}", state="not_shared")
        serve = read("serve", "status", "--json")
        configs = [serve, *(serve.get("Foreground") or {}).values()]
        result["can_enable"] = not any(
            "443" in (config.get("TCP") or {}) or
            any(address.endswith(":443") for address in (config.get("Web") or {}))
            for config in configs
        )
        for config in configs:
            for address, web in (config.get("Web") or {}).items():
                host, _, tls_port = address.rpartition(":")
                if host != dns or not tls_port.isdigit() or not 1 <= int(tls_port) <= 65535:
                    continue
                if (config.get("AllowFunnel") or {}).get(address) or (serve.get("AllowFunnel") or {}).get(address):
                    continue
                proxy = ((web.get("Handlers") or {}).get("/") or {}).get("Proxy", "")
                target = urlsplit(proxy)
                if (target.scheme != "http" or target.hostname not in {"127.0.0.1", "localhost", "::1"}
                        or target.port != port or target.path not in {"", "/"}
                        or target.username or target.password or target.query or target.fragment):
                    continue
                result.update(url=f"https://{dns}" + (f":{tls_port}" if tls_port != "443" else ""), state="ready")
                return result
        return result
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, AttributeError):
        result["state"] = "status_unavailable"
        result["can_enable"] = False
        return result


def enable_phone_access(port: int = 8000) -> dict:
    status = phone_access(port)
    if status["state"] == "ready":
        return status
    if not status["can_enable"]:
        raise ValueError("Sign into Tailscale first. If HTTPS port 443 is already shared, review its existing Serve routes on the host.")
    response = subprocess.run(
        [tailscale_executable(), "serve", "--bg", "--yes", f"http://127.0.0.1:{port}"],
        capture_output=True, text=True, timeout=20, check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if response.returncode:
        raise ValueError("Tailscale Serve could not enable private HTTPS. Check Tailscale on the controller computer.")
    return phone_access(port)
