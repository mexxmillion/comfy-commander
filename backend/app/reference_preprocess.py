"""Controller-only contracts for queued, offline reference isolation."""
import hashlib
import json
import re
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException
from PIL import Image
from pydantic import BaseModel, Field

from .h3_media import h3_input_paths
from .media import IMAGES, media_path

CAPABILITY = 'reference-preprocess-v1'
RUNTIME = 'pocket-reference-preprocess-v1'


class IsolationRequest(BaseModel):
    file: str = Field(max_length=1024)
    text: str = Field(default='person', min_length=1, max_length=200)
    isolate: bool = True
    depth: bool = False
    invert_depth: bool = False
    use_cache: bool = False
    confidence: float = Field(default=0.25, ge=0.05, le=0.95)
    selection: str = Field(default='best', pattern=r'^(all|best|[1-9]|1[0-9]|20)$')
    crop: bool = False
    background: Literal['white', 'gray', 'black'] = 'gray'
    policy: Literal['local', 'standard', 'any'] = 'local'


def input_path(file):
    h3_input_paths({'1': {'class_type': 'LoadImage', 'inputs': {'image': file}}})
    path = media_path(Path(file).name, Path(file).parent.as_posix(), 'input')
    if path.suffix.lower() not in IMAGES:
        raise HTTPException(422, 'Subject isolation currently accepts still images only')
    return path


def cache_keys(source, payload):
    plan = cache_plan(source, payload)
    inference = hashlib.sha256((plan['isolation_key'] + plan['depth_key'] + str(payload.isolate) + str(payload.depth)).encode()).hexdigest()
    return inference, plan['cache_key']


def cache_plan(source, payload):
    with source.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    manifest = json.loads((Path(__file__).resolve().parents[2] / 'runtime/h3/models.lock.json').read_text())
    artifacts = [item for item in manifest['artifacts'] if item['group'] == 'reference-isolate']
    def key(parameters, files):
        return hashlib.sha256(json.dumps({'source': digest, 'version': CAPABILITY, 'parameters': parameters,
            'models': sorted((item['repo'], item['revision'], item['file'], item['sha256']) for item in files)}, sort_keys=True).encode()).hexdigest()
    isolation_key = key({'text': payload.text.strip().lower(), 'confidence': payload.confidence, 'selection': payload.selection},
        [item for item in artifacts if 'depth-anything' not in item['repo'].lower()])
    depth_key = key({'invert': payload.invert_depth}, [item for item in artifacts if 'depth-anything' in item['repo'].lower()])
    final = key({'isolate': isolation_key if payload.isolate else False,
                 'depth': depth_key if payload.depth else False, 'background': payload.background, 'crop': payload.crop}, [])
    return {'isolation_key': isolation_key, 'depth_key': depth_key, 'cache_key': final, 'source_sha256': digest}


def node_inputs(payload):
    return {'source_file': payload.file, 'isolate': payload.isolate, 'text': payload.text.strip(),
            'confidence': payload.confidence, 'selection': payload.selection, 'depth': payload.depth,
            'invert_depth': payload.invert_depth, 'background': payload.background, 'crop': payload.crop,
            'use_cache': payload.use_cache, 'cache_key': '', 'isolation_key': '', 'depth_key': '',
            'cached_isolation': '', 'cached_depth': '', 'source_sha256': ''}


def graph_for(payload):
    return {'1': {'class_type': 'LoadImage', 'inputs': {'image': payload.file}},
            '2': {'class_type': 'TMPrepareReference', 'inputs': node_inputs(payload)},
            '3': {'class_type': 'SaveImage', 'inputs': {'images': ['2', 1], 'filename_prefix': 'reference-isolation/cutout'}}}


def is_graph(graph):
    classes = {node.get('class_type') for node in graph.values()}
    return 'TMPrepareReference' in classes and classes <= {'LoadImage', 'TMPrepareReference', 'SaveImage'}


def payload_for_node(inputs):
    return IsolationRequest(file=inputs['source_file'], **{key: inputs[key] for key in
        ('isolate', 'text', 'confidence', 'selection', 'depth', 'invert_depth', 'background', 'crop', 'use_cache')})


def validate_nodes(graph):
    try:
        count = 0
        for node in graph.values():
            if node.get('class_type') != 'TMPrepareReference':
                continue
            count += 1
            inputs = node['inputs']
            payload = payload_for_node(inputs)
            if set(inputs) - {'source_sha256'} != set(node_inputs(payload)) - {'source_sha256'} or (payload.isolate and not payload.text.strip()) or not (payload.isolate or payload.depth):
                raise ValueError('Invalid reference preprocessing settings')
            for name in ('cache_key', 'isolation_key', 'depth_key', 'source_sha256'):
                if inputs.get(name) and not re.fullmatch(r'[a-f0-9]{64}', inputs[name]):
                    raise ValueError('Invalid preprocessing cache key')
            for name, key, suffix in (('cached_isolation', 'isolation_key', 'rgba'), ('cached_depth', 'depth_key', 'depth')):
                if inputs[name] and inputs[name] != f"h3_mobile/preprocessed-cache/{inputs[key]}-{suffix}.png":
                    raise ValueError('Invalid cached reference path')
        if count > 10:
            raise ValueError('At most 10 images can be preprocessed')
        h3_input_paths(graph)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


def prepare_graph(graph, root):
    validate_nodes(graph)
    for node in graph.values():
        if node.get('class_type') != 'TMPrepareReference':
            continue
        inputs = node['inputs']
        payload = payload_for_node(inputs)
        source = input_path(payload.file)
        inputs.update(cache_plan(source, payload))
    hydrate_cache_paths(graph, root)
    return graph


def hydrate_cache_paths(graph, root):
    for node in graph.values():
        if node.get('class_type') != 'TMPrepareReference':
            continue
        inputs = node['inputs']
        payload = payload_for_node(inputs)
        for name, key, suffix in (('cached_isolation', 'isolation_key', 'rgba'), ('cached_depth', 'depth_key', 'depth')):
            file = f"h3_mobile/preprocessed-cache/{inputs[key]}-{suffix}.png"
            enabled = payload.isolate if name == 'cached_isolation' else payload.depth
            inputs[name] = file if enabled and payload.use_cache and (root / file).is_file() else ''
    return graph


def validate_graph(graph):
    validate_nodes(graph)
    try:
        if set(graph) != {'1', '2', '3'} or graph['1']['class_type'] != 'LoadImage' or graph['2']['class_type'] != 'TMPrepareReference':
            raise ValueError('Only the approved reference preprocessing graph is supported')
        if graph['1']['inputs'] != {'image': graph['2']['inputs']['source_file']} or graph['3'] != {
            'class_type': 'SaveImage', 'inputs': {'images': ['2', 1], 'filename_prefix': 'reference-isolation/cutout'}}:
            raise ValueError('Invalid preprocessing output graph')
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


def expected_cache_files(graph):
    names = set()
    for node in graph.values():
        if node.get('class_type') != 'TMPrepareReference' or not node['inputs'].get('use_cache'):
            continue
        inputs = node['inputs']
        for key, suffix, enabled in (('isolation_key', 'rgba', inputs['isolate']),
                                    ('depth_key', 'depth', inputs['depth']), ('cache_key', 'rgb', True)):
            if enabled and re.fullmatch(r'[a-f0-9]{64}', inputs.get(key, '')):
                names.add(f'{inputs[key]}-{suffix}.png')
    return names


def publish_cache(graph, filename, uploaded, root):
    if filename not in expected_cache_files(graph):
        raise HTTPException(403, 'Cache file is not part of this task')
    destination = root / 'h3_mobile' / 'preprocessed-cache' / filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f'.{uuid4().hex}.png')
    try:
        with Image.open(uploaded) as image:
            expected_mode = 'RGBA' if filename.endswith('-rgba.png') else 'RGB'
            if image.mode != expected_mode or image.width * image.height > 20_000_000:
                raise HTTPException(422, 'Invalid cached reference image')
            image.save(temporary)
        temporary.replace(destination)
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise HTTPException(422, 'Cannot decode cached reference image') from exc
    finally:
        temporary.unlink(missing_ok=True)
    return {'file': destination.relative_to(root).as_posix()}


def role_notes(references):
    notes = []
    for index, ref in enumerate(ref for ref in references if ref.get('kind') == 'image'):
        tag = f'<Picture {index + 1}>'
        if ref.get('isolation'):
            notes.append(f"{tag} is scheduled for isolation of {ref['isolation'].get('text', 'the selected subject')!r}. "
                'The attached image is the ORIGINAL, not the processed result. The renderer receives only the selected '
                'content on a solid background. Do not borrow the original background, other people, or unrelated clothing/identity from this asset.')
        if ref.get('depth'):
            notes.append(f'{tag} will be converted to a grayscale relative depth map. Use it for spatial structure, not source colors or facial identity.')
    return notes


def cached_reference(root, recipe):
    key = recipe['cache_key']
    if not re.fullmatch(r'[a-f0-9]{64}', key):
        raise HTTPException(422, 'Invalid isolation cache key')
    base = root / 'h3_mobile' / 'preprocessed'
    if not all((base / f'{key}{suffix}.png').is_file() for suffix in ('', '-cutout', '-mask')):
        return None
    return {'kind': 'image', 'file': f'h3_mobile/preprocessed/{key}.png',
            'preprocess': {'version': CAPABILITY, 'source': recipe['source'], 'text': recipe['text'],
                'confidence': recipe['confidence'], 'selection': recipe['selection'],
                'crop': recipe.get('crop', False),
                'background': recipe.get('background', 'gray'),
                'isolate': recipe.get('isolate', True), 'depth': recipe.get('depth', False),
                'invert_depth': recipe.get('invert_depth', False), 'use_cache': recipe.get('use_cache', False),
                'cutout': f'h3_mobile/preprocessed/{key}-cutout.png',
                'mask': f'h3_mobile/preprocessed/{key}-mask.png'}}


def raw_path(root, recipe):
    key = recipe.get('inference_key', recipe['cache_key'])
    if not re.fullmatch(r'[a-f0-9]{64}', key):
        raise HTTPException(422, 'Invalid isolation cache key')
    return root / 'h3_mobile' / 'preprocessed' / f'{key}-raw.png'


def materialize_cached(root, recipe):
    cached = cached_reference(root, recipe)
    if cached:
        return cached
    path = raw_path(root, recipe)
    return render_reference(root, recipe, path) if path.is_file() else None


def promote_artifact(root, recipe, artifact, task_id):
    cached = materialize_cached(root, recipe) if recipe.get('use_cache', False) else None
    if cached:
        return cached
    path = media_path(artifact['filename'], artifact.get('subfolder', ''), artifact.get('type', 'output'),
                      task_id if artifact.get('url', '').startswith('/outputs/') else '')
    return render_reference(root, recipe, path)


def render_reference(root, recipe, path):
    # Retain a full-resolution raw mask; background/crop changes need no GPU work.
    base = root / 'h3_mobile' / 'preprocessed'
    base.mkdir(parents=True, exist_ok=True)
    key = recipe['cache_key']
    with Image.open(path) as image:
        if image.mode != 'RGBA':
            raise HTTPException(422, 'Isolation worker returned an image without a foreground mask')
        rgba = image.copy()
    mask = rgba.getchannel('A')
    if mask.getbbox() is None:
        raise HTTPException(422, 'Isolation produced an empty foreground mask')
    raw = raw_path(root, recipe)
    if not raw.exists():
        temporary = base / f'.{uuid4().hex}.png'
        try:
            rgba.save(temporary)
            temporary.replace(raw)
        finally:
            temporary.unlink(missing_ok=True)
    if recipe.get('crop'):
        left, top, right, bottom = mask.getbbox()
        padding = max(4, round(max(right - left, bottom - top) * 0.05))
        bounds = (max(0, left - padding), max(0, top - padding),
                  min(rgba.width, right + padding), min(rgba.height, bottom + padding))
        rgba = rgba.crop(bounds)
        mask = rgba.getchannel('A')
    colors = {'white': (255, 255, 255), 'gray': (127, 127, 127), 'black': (0, 0, 0)}
    composite = Image.new('RGB', rgba.size, colors[recipe.get('background', 'gray')])
    composite.paste(rgba.convert('RGB'), mask=mask)
    for suffix, image in (('-cutout', rgba), ('-mask', mask), ('', composite)):
        temporary = base / f'.{uuid4().hex}.png'
        try:
            image.save(temporary)
            temporary.replace(base / f'{key}{suffix}.png')
        finally:
            temporary.unlink(missing_ok=True)
    return cached_reference(root, recipe)
