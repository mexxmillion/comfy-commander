"""Use the existing controller-owned OpenRouter credential, never a workflow key."""
import os
import subprocess

from .credential_paths import openrouter_secret_file


def configured() -> bool:
    return bool(os.getenv("OPENROUTER_API_KEY", "").strip()) or (
        openrouter_secret_file().is_file())


def read_key() -> str | None:
    if os.getenv("OPENROUTER_API_KEY", "").strip():
        return os.environ["OPENROUTER_API_KEY"].strip()
    if not configured():
        return None
    path = openrouter_secret_file()
    if path.suffix != ".dpapi":
        return path.read_text(encoding="utf-8").strip() or None
    # Captured in memory only. Never sent to a log, command argument, or local plaintext file.
    script = r'''$ErrorActionPreference='Stop'
$p=$env:OPENROUTER_SECRET_FILE
$s=(Get-Content -LiteralPath $p -Raw).Trim() | ConvertTo-SecureString
$c=[System.Management.Automation.PSCredential]::new('openrouter',$s)
[Console]::Out.Write($c.GetNetworkCredential().Password)
'''
    environment = dict(os.environ, OPENROUTER_SECRET_FILE=str(path))
    try:
        result = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-Command", script],
                                capture_output=True, text=True, timeout=20, env=environment, check=False,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Cannot load the saved OpenRouter credential") from exc
    if result.returncode or not result.stdout.strip():
        raise RuntimeError("Cannot unlock the existing OpenRouter credential for this Windows account")
    return result.stdout.strip()
