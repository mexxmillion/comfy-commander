"""Small cached gallery previews and the host's reusable source library."""
import asyncio
import hashlib
import os
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from PIL import Image, ImageOps
from pydantic import BaseModel, Field

from .config import get_settings
from .rental_ui import require_admin

router = APIRouter(prefix="/api/v1/media")
IMAGES = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.tif', '.tiff'}
VIDEOS = {'.mp4', '.mov', '.webm', '.mkv', '.avi', '.m4v'}
AUDIO = {'.wav', '.mp3', '.m4a', '.aac', '.flac', '.ogg'}
PREVIEW_LIMIT = asyncio.Semaphore(2)


class SourceImport(BaseModel):
    file: str = Field(min_length=1, max_length=1024)


@router.post('/sources/import')
def import_source(payload: SourceImport, request: Request):
    require_admin(request, mutation=True)
    relative = Path(payload.file)
    if (relative.is_absolute() or '\\' in payload.file or ':' in payload.file
            or '..' in relative.parts):
        raise HTTPException(400, 'Choose a file from the source library')
    source = media_path(relative.name, relative.parent.as_posix(), 'input')
    suffix = source.suffix.lower()
    if suffix not in IMAGES | VIDEOS | AUDIO:
        raise HTTPException(400, 'Unsupported reference file type')
    if not 0 < source.stat().st_size <= 2 * 1024**3:
        raise HTTPException(413, 'Reference must be nonempty and no larger than 2 GiB')
    root = get_settings().comfyui_input_dir.resolve()
    if relative.parts[0] == 'h3_mobile' and source.relative_to(root).parts[0] == 'h3_mobile':
        destination = source
    else:
        folder = (root / 'h3_mobile' / 'imported').resolve()
        if not folder.is_relative_to(root) or folder.relative_to(root).parts[0] != 'h3_mobile':
            raise HTTPException(400, 'Managed reference directory is unavailable')
        folder.mkdir(parents=True, exist_ok=True)
        temporary = folder / f'.{uuid4().hex}.import'
        digest = hashlib.sha256()
        size = 0
        try:
            with source.open('rb') as reader, temporary.open('xb') as writer:
                while chunk := reader.read(1024 * 1024):
                    size += len(chunk)
                    if size > 2 * 1024**3:
                        raise HTTPException(413, 'Reference exceeds 2 GiB')
                    digest.update(chunk)
                    writer.write(chunk)
            destination = (folder / f'{digest.hexdigest()}{suffix}').resolve()
            if not destination.is_relative_to(root):
                raise HTTPException(400, 'Managed reference path is unavailable')
            if not destination.exists():
                temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    path = destination.relative_to(root)
    return {'file': path.as_posix(), 'filename': destination.name,
            'subfolder': path.parent.as_posix(), 'type': 'input',
            'kind': 'image' if suffix in IMAGES else 'video' if suffix in VIDEOS else 'audio'}


def ffmpeg_executable() -> str:
    executable = shutil.which('ffmpeg')
    if executable:
        return executable
    from imageio_ffmpeg import get_ffmpeg_exe
    return get_ffmpeg_exe()


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
            subprocess.run([ffmpeg_executable(), '-nostdin', '-v', 'error', '-threads', '1',
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
