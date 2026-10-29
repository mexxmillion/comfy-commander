"""Package only pinned public runtime sources, without a local ComfyUI install."""
import hashlib
import json
import logging
import re
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / 'runtime/h3'
EXCLUDED = {'ComfyUI-LTXVideo', 'comfyui-frame-interpolation'}
log = logging.getLogger(__name__)


def source_archive(item: dict) -> tuple[str, str | None, bool]:
    if item.get('type') == 'registry':
        url, digest = item.get('archive_url', ''), item.get('archive_sha256', '')
        if urlsplit(url).hostname != 'cdn.comfy.org' or not re.fullmatch(r'[a-f0-9]{64}', digest):
            raise RuntimeError('Registry node source needs a reviewed URL and SHA-256')
        return url, digest, False
    revision = item.get('revision', '')
    remote = urlsplit(item.get('url', ''))
    repo = remote.path.removeprefix('/').removesuffix('.git')
    if remote.hostname != 'github.com' or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo) or not re.fullmatch(r'[a-f0-9]{40}', revision):
        raise RuntimeError('Runtime node source must be a pinned public GitHub revision')
    return f'https://codeload.github.com/{repo}/zip/{revision}', None, True


def extract_source(archive: Path, destination: Path, strip_root: bool) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as package:
        if sum(item.file_size for item in package.infolist()) > 1024**3:
            raise RuntimeError('Runtime source archive exceeds 1 GiB')
        for item in package.infolist():
            path = PurePosixPath(item.filename)
            if (path.is_absolute() or '..' in path.parts or '\\' in item.filename or ':' in item.filename
                    or ((item.external_attr >> 16) & 0o170000) == 0o120000):
                raise RuntimeError('Unsafe runtime source archive entry')
            parts = path.parts[1:] if strip_root else path.parts
            if not parts or '.git' in parts or '__pycache__' in parts or item.is_dir():
                continue
            target = destination.joinpath(*parts)
            if not target.resolve().is_relative_to(destination.resolve()):
                raise RuntimeError('Runtime source entry leaves its staging directory')
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.open(item) as reader, target.open('wb') as writer:
                shutil.copyfileobj(reader, writer)


def fetch_source(item: dict, destination: Path, cache: Path) -> None:
    url, expected, strip_root = source_archive(item)
    cache.mkdir(parents=True, exist_ok=True)
    cached = cache / (hashlib.sha256(url.encode()).hexdigest() + '.zip')
    if not cached.exists() or (expected and hashlib.sha256(cached.read_bytes()).hexdigest() != expected):
        temporary = cached.with_suffix('.partial')
        try:
            with httpx.stream('GET', url, follow_redirects=True, timeout=60) as response:
                response.raise_for_status()
                total = 0
                with temporary.open('wb') as output:
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > 512 * 1024**2:
                            raise RuntimeError('Runtime source download exceeds 512 MiB')
                        output.write(chunk)
            if expected and hashlib.sha256(temporary.read_bytes()).hexdigest() != expected:
                raise RuntimeError('Runtime node archive checksum mismatch')
            temporary.replace(cached)
        finally:
            temporary.unlink(missing_ok=True)
    try:
        extract_source(cached, destination, strip_root)
    except zipfile.BadZipFile as exc:
        raise RuntimeError('Pinned runtime source archive is not a valid ZIP') from exc


def build_bundle(destination: Path, community_manifest: Path | None = None) -> None:
    sources = json.loads((RUNTIME / 'sources.lock.json').read_text(encoding='utf-8'))
    python = json.loads((RUNTIME / 'python-runtime.lock.json').read_text(encoding='utf-8'))
    if sources['comfyui']['revision'] != python['comfyui_revision']:
        raise RuntimeError('Pinned ComfyUI and Python runtime revisions disagree')
    destination.parent.mkdir(parents=True, exist_ok=True)
    cache = destination.parent / 'runtime-source-cache'
    # Only this dedicated, verified temporary directory is recursively cleaned up.
    with tempfile.TemporaryDirectory(prefix='community-runtime-', dir=destination.parent) as temporary:
        stage = Path(temporary).resolve()
        if stage.parent != destination.parent.resolve():
            raise RuntimeError('Invalid runtime staging directory')
        for name, item in sources['custom_nodes'].items():
            if name in EXCLUDED:
                continue
            log.info('Packaging pinned public node: %s', name)
            target = stage / 'custom_nodes' / name
            if item.get('type') == 'snapshot':
                for relative, expected in item.get('files', {}).items():
                    path = RUNTIME / item['overlay'] / relative
                    normalized = path.read_bytes().replace(b'\r\n', b'\n')
                    if hashlib.sha256(normalized).hexdigest() != expected:
                        raise RuntimeError(f'Pinned source snapshot checksum mismatch: {name}')
                shutil.copytree(RUNTIME / item['overlay'], target)
            else:
                fetch_source(item, target, cache)
            if item.get('patch'):
                # A private staging Git index prevents discovery of the controller's repository.
                flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                for args in (['git', 'init', '-q', str(target)],
                             ['git', '-C', str(target), 'apply', '--ignore-whitespace', str(RUNTIME / item['patch'])]):
                    result = subprocess.run(args, capture_output=True, timeout=30, check=False, creationflags=flags)
                    if result.returncode:
                        raise RuntimeError(f'Pinned runtime patch failed for {name}')
        shutil.copytree(RUNTIME / 'source-overlays/comfy', stage / 'comfy-source/comfy')
        for overlay in (RUNTIME / 'source-overlays/custom_nodes').iterdir():
            shutil.copytree(overlay, stage / 'custom_nodes' / overlay.name, dirs_exist_ok=True)
        for name in ('viggle_turbo.py', 'h3_beta57.py'):
            shutil.copy2(RUNTIME / name, stage / 'custom_nodes' / name)
        runtime = stage / 'h3-runtime'
        runtime.mkdir()
        (runtime / 'local-runtime.json').write_text(json.dumps(python), encoding='utf-8')
        (runtime / 'local-runtime-constraints.txt').write_text(
            ''.join(f'{name}=={version}\n' for name, version in python['packages'].items()), encoding='utf-8')
        for name in ('models.lock.json', 'sources.lock.json', 'sync_models.py', 'remote_prepare.sh', 'remote_sync.sh', 'remote_start.sh'):
            shutil.copy2(RUNTIME / name, runtime / name)
        if community_manifest and community_manifest.is_file():
            shutil.copy2(community_manifest, runtime / 'community-loras.json')
        for relative in ('backend/__init__.py', 'backend/worker.py', 'backend/worker_recovery.py',
                         'backend/app/__init__.py', 'backend/app/auth.py', 'backend/app/config.py',
                         'backend/app/credential_paths.py', 'backend/app/h3_media.py'):
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
        partial = destination.with_suffix('.partial')
        try:
            with tarfile.open(partial, 'w:gz') as bundle:
                for path in sorted(stage.rglob('*')):
                    if path.is_file() and '.git' not in path.relative_to(stage).parts:
                        bundle.add(path, arcname=path.relative_to(stage).as_posix(), recursive=False)
            partial.replace(destination)
        finally:
            partial.unlink(missing_ok=True)
