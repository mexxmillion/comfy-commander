"""Human-triggered creation of a dedicated cloud worker SSH identity."""
import csv
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from threading import Lock

KEY_LOCK = Lock()


def _protect(path: Path, flags: int) -> None:
    if os.name != 'nt':
        path.chmod(0o700 if path.is_dir() else 0o600)
        return
    owner = subprocess.run(['whoami', '/user', '/fo', 'csv', '/nh'], capture_output=True,
                           text=True, timeout=10, creationflags=flags, check=True)
    sid = next(csv.reader(owner.stdout.strip().splitlines()))[-1].strip()
    if not sid.startswith('S-1-'):
        raise ValueError('Could not identify the local account for SSH key protection.')
    permission = '(OI)(CI)F' if path.is_dir() else 'F'
    result = subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r', '*' + sid + ':' + permission],
                            capture_output=True, timeout=10, creationflags=flags, check=False)
    if result.returncode:
        raise ValueError('Could not protect the SSH key files for this local account.')


def create_cloud_key(key: Path) -> None:
    public = Path(str(key) + '.pub')
    with KEY_LOCK:
        if key.exists() or public.exists():
            raise ValueError('Existing cloud SSH keys are never overwritten.')
        executable = shutil.which('ssh-keygen')
        if not executable:
            raise ValueError('Install OpenSSH Client on this computer, then try again.')
        key.parent.mkdir(parents=True, exist_ok=True)
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        with tempfile.TemporaryDirectory(prefix='.cloud-ssh-', dir=key.parent) as temporary:
            stage = Path(temporary).resolve()
            if stage.parent != key.parent.resolve():
                raise ValueError('Invalid SSH staging location')
            _protect(stage, flags)
            staged_key = stage / 'key'
            staged_public = stage / 'key.pub'
            result = subprocess.run([executable, '-q', '-t', 'ed25519', '-N', '', '-f', str(staged_key), '-C', 'comfy-commander'],
                                    capture_output=True, stdin=subprocess.DEVNULL, timeout=30, creationflags=flags, check=False)
            if result.returncode or not staged_key.is_file() or not staged_public.is_file():
                raise ValueError('SSH key creation failed. Check OpenSSH and file permissions.')
            _protect(staged_key, flags)
            published = []
            try:
                # Exclusive hard links preserve protection and refuse concurrent replacement.
                for source, destination in ((staged_key, key), (staged_public, public)):
                    os.link(source, destination)
                    published.append(destination)
            except OSError:
                for destination in published:
                    destination.unlink()
                raise


def public_key_identity(public_key: str) -> tuple[str, str]:
    parts = public_key.strip().split()
    if len(parts) < 2 or parts[0] not in {'ssh-ed25519','ssh-rsa','ecdsa-sha2-nistp256','ecdsa-sha2-nistp384','ecdsa-sha2-nistp521'}:
        raise ValueError('Invalid worker SSH public key')
    return parts[0], parts[1]


def vast_public_key_registered(provider, public_key: str) -> bool:
    identity = public_key_identity(public_key)
    rows = provider.show_ssh_keys()
    if isinstance(rows, dict):
        rows = rows.get('ssh_keys', rows.get('keys'))
    if not isinstance(rows, list):
        raise RuntimeError('Could not verify Vast SSH registration')
    for row in rows:
        if not isinstance(row,dict) or row.get('deleted_at'):
            continue
        value = row.get('public_key') or row.get('ssh_key') or row.get('key') or ''
        try:
            if public_key_identity(value) == identity:
                return True
        except ValueError:
            continue
    return False


def register_vast_public_key(api_key: str, key_file: Path) -> None:
    """Human setup only. Register the public half; never read/send the private half."""
    from vastai import VastAI
    public = Path(str(key_file) + '.pub').read_text(encoding='utf-8').strip()
    public_key_identity(public)
    with KEY_LOCK:
        provider = VastAI(api_key=api_key, raw=True)
        if vast_public_key_registered(provider, public):
            return
        provider.create_ssh_key(ssh_key=public)
        if not vast_public_key_registered(provider, public):
            raise RuntimeError('Vast did not confirm worker SSH registration')
