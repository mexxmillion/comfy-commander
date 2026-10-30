"""Experimental, controller-owned local QwenVL graph. No provider keys or arbitrary graphs."""
import base64
import io
import json
import time
import uuid
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException
from PIL import Image

from backend.process_lock import process_lock
from .config import get_settings

NODE = 'AILab_QwenVL_Advanced'


def endpoint():
    value = get_settings().comfyui_url.rstrip('/')
    parsed = urlsplit(value)
    if (parsed.scheme != 'http' or parsed.hostname not in {'localhost', '127.0.0.1', '::1'}
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise HTTPException(409, 'Experimental QwenVL requires the configured localhost ComfyUI. No remote fallback.')
    return value


def inspect(client, config):
    response = client.get(endpoint() + '/object_info')
    response.raise_for_status()
    nodes = response.json()
    if NODE not in nodes or 'PreviewAny' not in nodes:
        raise HTTPException(409, 'Install ComfyUI-QwenVL (1038lab) and a ComfyUI version with PreviewAny, then restart ComfyUI. Nothing was installed or run.')
    required = nodes[NODE]['input']['required']
    if config.model not in required['model_name'][0]:
        raise HTTPException(409, 'The selected QwenVL model is not listed by this installed node. Check the model ID against the node dropdown.')
    if config.local_quantization not in required['quantization'][0]:
        raise HTTPException(409, 'This installed QwenVL node does not support the selected quantization.')
    cap = required['max_tokens'][1].get('max', 4096)
    if config.max_output_tokens > cap:
        raise HTTPException(409, f'This installed QwenVL node allows at most {cap} output tokens. Lower the configured output limit.')
    return required


def messages(payload):
    lines, images = [], []
    for message in payload.get('messages', []):
        lines.append(message.get('role', 'user').upper() + ':')
        content = message.get('content', '')
        if isinstance(content, str):
            lines.append(content)
            continue
        for part in content:
            if part.get('type') == 'text':
                lines.append(part.get('text', ''))
            elif part.get('type') == 'image_url':
                images.append(part['image_url']['url'])
            else:
                raise HTTPException(409, 'Experimental QwenVL supports text and one image only. Disable enhancement for video/audio or use a capable endpoint.')
    if len(images) > 1:
        raise HTTPException(409, 'This QwenVL adapter accepts one image, not multiple references. Nothing was submitted.')
    return '\n\n'.join(lines), images


def image_bytes(url):
    if not url.startswith(('data:image/jpeg;base64,', 'data:image/png;base64,')) or len(url) > 16 * 1024 * 1024:
        raise HTTPException(422, 'QwenVL accepts only a bounded inline PNG/JPEG, not external URLs.')
    try:
        data = base64.b64decode(url.split(',', 1)[1], validate=True)
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > 16_000_000:
                raise ValueError('Oversized image')
            image.verify()
        return data
    except (ValueError, OSError, Image.DecompressionBombError):
        raise HTTPException(422, 'Invalid local QwenVL image.') from None


def graph(required, config, payload, image=None):
    text, _ = messages(payload)
    inputs = {}
    overrides = dict(model_name=config.model, quantization=config.local_quantization,
                     custom_prompt=text, max_tokens=payload['max_tokens'], keep_model_loaded=False,
                     attention_mode='auto', use_torch_compile=False, device='auto', seed=1,
                     temperature=max(.1, min(1., payload.get('temperature', .6))))
    # Known interface only: an upstream node API change must fail visibly, not add arbitrary inputs.
    defaults = dict(top_p=.9, num_beams=1, repetition_penalty=1.2, frame_count=1)
    for name, spec in required.items():
        if name in overrides:
            inputs[name] = overrides[name]
        elif name in defaults:
            inputs[name] = defaults[name]
        elif name == 'preset_prompt' and isinstance(spec[0], list) and spec[0]:
            inputs[name] = spec[0][0]
        else:
            raise HTTPException(409, 'Unsupported QwenVL node interface; use a compatible endpoint or supported node version.')
    result = {'1': {'class_type': NODE, 'inputs': inputs},
              '2': {'class_type': 'PreviewAny', 'inputs': {'source': ['1', 0]}}}
    if image:
        result['3'] = {'class_type': 'LoadImage', 'inputs': {'image': image}}
        inputs['image'] = ['3', 0]
    return result


def complete(config, payload):
    text, images = messages(payload)
    data = image_bytes(images[0]) if images else None
    state = get_settings().preferences_file.with_name('local-qwenvl-pending.json')
    with process_lock(state.with_suffix('.lock')) as owned:
        if not owned:
            raise HTTPException(409, 'A local QwenVL request is already in progress.')
        with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
            base = endpoint()
            required = inspect(client, config)
            if state.is_file():
                previous = json.loads(state.read_text(encoding='utf-8'))
                pid = previous.get('prompt_id')
                history = client.get(base + '/history/' + pid) if pid else None
                if history is not None:
                    history.raise_for_status()
                if not pid or not history.json().get(pid, {}).get('status', {}).get('completed'):
                    raise HTTPException(409, f'A previous QwenVL submission needs reconciliation (request {previous["request_id"]}). Inspect ComfyUI queue/history before retrying; no duplicate submitted.')
                state.unlink()
            queue = client.get(base + '/queue')
            queue.raise_for_status()
            if any(queue.json().get(key) for key in ('queue_running', 'queue_pending')):
                raise HTTPException(409, 'Local ComfyUI is busy. Retry AI assistance when its queue is idle; no request submitted.')
            image_path = None
            if data:
                uploaded = client.post(base + '/upload/image', files={'image': ('qwenvl-' + uuid.uuid4().hex + '.png', data, 'image/png')}, data={'type': 'input', 'subfolder': 'commander-qwenvl'})
                uploaded.raise_for_status()
                info = uploaded.json()
                image_path = (info.get('subfolder', '') + '/' + info['name']).lstrip('/')
                if '..' in image_path or image_path.startswith('/') or '\\' in image_path:
                    raise HTTPException(502, 'ComfyUI returned an invalid image path.')
            approved = graph(required, config, payload, image_path)
            pending = {'request_id': uuid.uuid4().hex}
            state.write_text(json.dumps(pending), encoding='utf-8')
            # Submit exactly once. Unknown transport outcome remains recorded and blocks blind resubmission.
            response = client.post(base + '/prompt', json={'prompt': approved, 'client_id': pending['request_id']})
            if response.status_code == 400:
                state.unlink()
                raise HTTPException(409, 'ComfyUI rejected the QwenVL graph. Check installed node compatibility; no retry was submitted.')
            response.raise_for_status()
            pid = str(response.json()['prompt_id'])
            pending['prompt_id'] = pid
            state.write_text(json.dumps(pending), encoding='utf-8')
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                response = client.get(base + '/history/' + pid)
                response.raise_for_status()
                row = response.json().get(pid, {})
                status = row.get('status', {})
                if status.get('status_str') == 'error':
                    state.unlink()
                    raise HTTPException(502, f'Local QwenVL failed (ComfyUI prompt {pid}). Inspect ComfyUI logs; no cloud fallback.')
                if status.get('completed'):
                    state.unlink()
                    output = row.get('outputs', {}).get('2', {}).get('text', [])
                    if not output or not isinstance(output[0], str) or not output[0].strip():
                        raise HTTPException(502, 'Local QwenVL returned no text. Inspect its ComfyUI history.')
                    return httpx.Response(200, json={'choices': [{'message': {'content': output[0]}}]},
                                          request=httpx.Request('POST', 'http://localhost/local-qwenvl'))
                time.sleep(.5)
            raise HTTPException(504, f'Local QwenVL is still queued/running (ComfyUI prompt {pid}). Request retained; inspect history before retrying.')
