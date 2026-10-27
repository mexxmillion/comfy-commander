"""Small cached gallery previews and the host's reusable source library."""
import asyncio
import hashlib
import os
import subprocess
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from PIL import Image, ImageOps

from .config import get_settings
from .rental_ui import require_admin

router = APIRouter(prefix="/api/v1/media")
IMAGES = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.tif', '.tiff'}
VIDEOS = {'.mp4', '.mov', '.webm', '.mkv', '.avi', '.m4v'}
AUDIO = {'.wav', '.mp3', '.m4a', '.aac', '.flac', '.ogg'}
PREVIEW_LIMIT = asyncio.Semaphore(2)


def media_path(filename: str, subfolder: str = '', type: str = 'output', task_id: str = '') -> Path:
    settings = get_settings()
    if type not in {'input', 'output', 'temp'}:
        raise HTTPException(400, 'Invalid media type')
    root = (settings.cloud_output_dir if task_id else
            settings.comfyui_input_dir if type == 'input' else
            settings.comfyui_input_dir.parent / type).resolve()
    # Cloud outputs are stored flat as cloud-outputs/<task>/<filename>, while the artifact keeps the job's ComfyUI
    # subfolder (e.g. "myanmar_fashion/cast3"); accept both layouts so subfoldered farm renders still preview.
    candidates = ([root / task_id / filename, root / task_id / subfolder / filename] if task_id
                  else [root / subfolder / filename])
    for candidate in candidates:
        path = candidate.resolve()
        if path.is_relative_to(root) and path.is_file():
            return path
    raise HTTPException(404, 'Media is unavailable')


def make_preview(source: Path, destination: Path) -> None:
    temporary = destination.with_name(f'{uuid4().hex}.jpg')
    try:
        if source.suffix.lower() in IMAGES:
            with Image.open(source) as image:
                image = ImageOps.exif_transpose(image)
                image.thumbnail((480, 480))
                image.convert('RGB').save(temporary, quality=78)
        else:
            subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-threads', '1',
                            '-i', str(source), '-frames:v', '1', '-vf',
                            'scale=480:480:force_original_aspect_ratio=decrease',
                            '-q:v', '5', '-y', str(temporary)],
                           check=True, timeout=30, capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


@router.get('/file')
def file(request: Request, filename: str, subfolder: str = '', type: str = 'output', task_id: str = ''):
    if type == 'input':
        require_admin(request)
    return FileResponse(media_path(filename, subfolder, type, task_id),
                        headers={'Cache-Control': 'private, max-age=3600'})


@router.get('/preview')
async def preview(request: Request, filename: str, subfolder: str = '', type: str = 'output', task_id: str = ''):
    if type == 'input':
        require_admin(request)
    source = media_path(filename, subfolder, type, task_id)
    if source.suffix.lower() not in IMAGES | VIDEOS:
        return Response(status_code=204)
    stat = source.stat()
    key = hashlib.sha256(f'{source}:{stat.st_size}:{stat.st_mtime_ns}'.encode()).hexdigest()
    cache = get_settings().cloud_output_dir.parent / 'media-previews'
    cache.mkdir(parents=True, exist_ok=True)
    destination = cache / f'{key}.jpg'
    async with PREVIEW_LIMIT:
        if not destination.exists():
            try:
                await asyncio.to_thread(make_preview, source, destination)
            except (OSError, subprocess.SubprocessError):
                return Response(status_code=204)
    return FileResponse(destination, media_type='image/jpeg',
                        headers={'Cache-Control': 'private, max-age=86400'})


@router.get('/sources')
def sources(request: Request, kind: str = 'image', search: str = '', page: int = 0):
    require_admin(request)
    extensions = {'image': IMAGES, 'video': VIDEOS, 'audio': AUDIO}.get(kind)
    if extensions is None or page < 0:
        raise HTTPException(400, 'Invalid source filter')
    root = get_settings().comfyui_input_dir.resolve()
    files = []
    for path in root.rglob('*'):
        if path.suffix.lower() not in extensions or not path.is_file() or not path.resolve().is_relative_to(root):
            continue
        relative = path.relative_to(root)
        if search.lower() not in relative.as_posix().lower():
            continue
        stat = path.stat()
        files.append({'filename': path.name, 'subfolder': relative.parent.as_posix() if relative.parent != Path('.') else '',
                      'file': relative.as_posix(), 'kind': kind, 'type': 'input',
                      'bytes': stat.st_size, 'modified': stat.st_mtime})
    files.sort(key=lambda item: item['modified'], reverse=True)
    return {'total': len(files), 'page': page, 'items': files[page * 24:(page + 1) * 24]}
