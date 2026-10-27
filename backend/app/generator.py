"""Controller-owned Generator APIs. No worker HTTP calls or runtime imports."""
import asyncio
import json
import math
import time
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
from threading import Lock
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from PIL import Image, UnidentifiedImageError

from .config import get_settings
from .generator_prompt import EnhanceRequest, enhance
from .media import AUDIO, IMAGES, VIDEOS, media_path
from .rental_ui import require_admin
from .store import QueueStore
from . import reference_preprocess as isolation

router = APIRouter()
LIBRARY_LOCK = Lock()
BOOT_ID = uuid4().hex


@lru_cache
def queue_store():
    return QueueStore(get_settings().database_url)


STORE = Depends(queue_store)


def library_path():
    return get_settings().cloud_output_dir.parent / 'generator-library.json'


def read_library():
    path = library_path()
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}


@router.get('/h3-mobile', include_in_schema=False)
@router.get('/h3-mobile/', include_in_schema=False)
def legacy_page():
    return RedirectResponse('/#generator', status_code=307)


@router.get('/api/v1/generator/status')
def status(request: Request, store: QueueStore = STORE):
    require_admin(request)
    snapshot = store.snapshot()
    return {'boot_id': BOOT_ID, 'version': '1', 'recent_jobs': 500,
            'running': snapshot['metrics']['running'], 'queued': snapshot['metrics']['queued'],
            'workers': snapshot['metrics']['active_workers'],
            'hidden_ids': [key for key, value in read_library().items() if value.get('hidden')]}


@router.get('/api/v1/generator/catalog')
def catalog(request: Request):
    require_admin(request)
    loras = set()
    for directory in get_settings().model_directories:
        root = directory / 'loras'
        if root.is_dir():
            loras.update(path.relative_to(root).as_posix() for path in root.rglob('*')
                         if path.is_file() and path.suffix.lower() in {'.safetensors', '.pt', '.ckpt'})
    return {'loras': sorted(loras), 'modes': ['h3', 'qwen']}


@router.post('/api/v1/generator/upload')
async def upload(request: Request):
    require_admin(request, mutation=True)
    form = await request.form()
    file = form.get('image')
    if file is None or not getattr(file, 'filename', None):
        raise HTTPException(400, 'Choose a reference file')
    name = file.filename
    if Path(name).name != name or '\\' in name:
        raise HTTPException(400, 'Unsupported reference filename')
    if Path(name).suffix.lower() == '.jfif':
        name = str(Path(name).with_suffix('.jpg'))
    if Path(name).suffix.lower() not in IMAGES | VIDEOS | AUDIO:
        raise HTTPException(400, f'Unsupported reference file type: {Path(name).suffix or "no extension"}. Use JPEG, PNG, WebP, or a supported video/audio file.')
    root = get_settings().comfyui_input_dir / 'h3_mobile'
    root.mkdir(parents=True, exist_ok=True)
    destination = root / name
    temporary = root / f'.{uuid4().hex}.upload'
    size = 0
    try:
        with temporary.open('wb') as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > 2 * 1024**3:
                    raise HTTPException(413, 'Reference exceeds 2 GiB')
                output.write(chunk)
        if not size:
            raise HTTPException(400, 'Reference file is empty')
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
        await file.close()
    return {'name': name, 'subfolder': 'h3_mobile', 'type': 'input'}


@router.get('/api/v1/generator/probe')
async def probe(request: Request, file: str):
    require_admin(request)
    if not file.startswith('h3_mobile/') or '\\' in file or '..' in Path(file).parts:
        raise HTTPException(400, 'Invalid reference path')
    path = media_path(Path(file).name, Path(file).parent.as_posix(), 'input')
    if path.suffix.lower() in IMAGES:
        try:
            with Image.open(path) as image:
                return {'kind': 'image', 'width': image.width, 'height': image.height,
                        'duration': None, 'fps': None, 'has_audio': False}
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
            raise HTTPException(422, 'Cannot decode reference image')
    process = await asyncio.create_subprocess_exec('ffprobe', '-v', 'error', '-show_streams',
        '-show_format', '-of', 'json', str(path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        output, _ = await asyncio.wait_for(process.communicate(), 30)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise HTTPException(422, 'Reference inspection timed out')
    if process.returncode:
        raise HTTPException(422, 'Cannot decode reference media')
    data = json.loads(output)
    video = next((item for item in data.get('streams', []) if item['codec_type'] == 'video'), None)
    return {'kind': 'video' if video else 'audio', 'width': video.get('width') if video else None,
            'height': video.get('height') if video else None,
            'fps': float(Fraction(video.get('r_frame_rate', '0/1'))) if video else None,
            'duration': float(data.get('format', {}).get('duration', 0)),
            'has_audio': any(item['codec_type'] == 'audio' for item in data.get('streams', []))}


@router.post('/api/v1/generator/isolate')
def isolate_reference(payload: isolation.IsolationRequest, request: Request, store: QueueStore = STORE):
    require_admin(request, mutation=True)
    if payload.isolate and not payload.text.strip():
        raise HTTPException(422, 'Describe the subject to isolate')
    try:
        source = isolation.input_path(payload.file)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    inference_key, cache_key = isolation.cache_keys(source, payload)
    recipe = {'mode': 'reference-isolate', 'source': payload.file, 'text': payload.text.strip(), 'direction': payload.text.strip(),
              'confidence': payload.confidence, 'selection': payload.selection,
              'crop': payload.crop, 'background': payload.background,
              'isolate': payload.isolate, 'depth': payload.depth, 'invert_depth': payload.invert_depth,
              'use_cache': payload.use_cache,
              'cache_key': cache_key, 'inference_key': inference_key}
    cached = isolation.materialize_cached(get_settings().comfyui_input_dir, recipe) if payload.use_cache else None
    if cached:
        return {'status': 'completed', 'reference': cached}
    graph = isolation.graph_for(payload)
    isolation.prepare_graph(graph, get_settings().comfyui_input_dir)
    isolation.validate_graph(graph)
    job_id = store.submit(name=f'Isolate reference · {payload.text.strip()[:90]}', graph=graph,
        workflow='reference-isolate', policy=payload.policy, min_vram_gb=4,
        architecture='any', runtime=isolation.RUNTIME, recipe=recipe)
    return {'status': 'queued', 'job_id': job_id}


@router.get('/api/v1/generator/isolate/{job_id}')
def isolation_status(job_id: str, request: Request, store: QueueStore = STORE):
    require_admin(request)
    job = store.job(job_id)
    recipe = store.job_recipe(job_id)
    if not job or not recipe or recipe.get('mode') != 'reference-isolate':
        raise HTTPException(404, 'Isolation job not found')
    task = job['tasks'][0]
    result = {'status': task['status'], 'message': task['message'], 'progress': task['progress']}
    if task['status'] == 'completed' and task.get('artifact'):
        result['reference'] = isolation.promote_artifact(get_settings().comfyui_input_dir, recipe,
                                                       task['artifact'], task['id'])
    return result


@router.get('/api/v1/generator/library')
def library(request: Request):
    require_admin(request)
    files = []
    for key, record in reversed(list(read_library().items())):
        if record.get('hidden') or not isinstance(record.get('recipe'), dict):
            continue
        output = dict(record.get('output', {}))
        if output.get('url', '').startswith('/h3-mobile/dispatch-output/'):
            output['url'] = output['url'].replace('/h3-mobile/dispatch-output/', '/outputs/', 1)
        files.append({**output, 'prompt_id': key, 'recipe': record['recipe'],
                      'modified': record.get('created', 0), 'saved': record.get('saved', False),
                      'preset_name': record.get('preset_name', ''), 'timings': record.get('timings', {})})
    return files


@router.post('/api/v1/generator/presets')
def save_preset(payload: dict, request: Request):
    require_admin(request, mutation=True)
    if len(json.dumps(payload)) > 262144 or not isinstance(payload.get('prompt_id'), str):
        raise HTTPException(400, 'Invalid preset')
    key = payload['prompt_id'][:128]
    with LIBRARY_LOCK:
        records = read_library()
        record = dict(records.get(key, {'created': time.time()}))
        for field in ('recipe', 'output', 'timings'):
            if field in payload:
                if not isinstance(payload[field], dict):
                    raise HTTPException(400, f'{field} must be an object')
                record[field] = payload[field]
        if 'output' in payload:
            url = record['output'].get('url')
            if url and not url.startswith(('/outputs/', '/api/v1/media/file?')):
                raise HTTPException(400, 'Invalid output URL')
        for field in ('saved', 'hidden'):
            if field in payload:
                record[field] = bool(payload[field])
        if 'preset_name' in payload:
            record['preset_name'] = str(payload['preset_name']).strip()[:120]
        if 'timings' in record:
            record['timings'] = {name: value for name, value in record['timings'].items()
                if name in {'total_seconds', 'generation_seconds', 'queue_seconds'}
                and isinstance(value, (int, float)) and math.isfinite(value) and value >= 0}
        records[key] = record
        saved = [(key, value) for key, value in records.items() if value.get('saved')][-500:]
        unsaved = [(key, value) for key, value in records.items() if not value.get('saved')]
        records = dict(saved + (unsaved[-(500-len(saved)):] if len(saved) < 500 else []))
        path = library_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(records, ensure_ascii=False), encoding='utf-8')
        temporary.replace(path)
    return {'saved': True}


@router.post('/api/v1/generator/h3-enhance')
async def h3_enhance(payload: EnhanceRequest, request: Request):
    require_admin(request, mutation=True)
    return await enhance(payload)
