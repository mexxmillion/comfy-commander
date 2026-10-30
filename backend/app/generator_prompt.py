"""H3 prompt writing on the controller, independent of the GPU worker runtime."""
import asyncio
import base64
import io
import math
import mimetypes
import re
import tempfile
from pathlib import Path
from typing import Literal

import httpx
from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field

from .config import get_settings
from .generator_prompts import director
from .generator_prompts.refs import ReferenceError, ReferenceSet
from .prompt_secret import read_key
from . import llm
from .reference_preprocess import role_notes
from .media import ffmpeg_executable

PROMPTS = Path(__file__).with_name('generator_prompts')


class EnhanceRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=40000)
    references: list[dict] = Field(default_factory=list, max_length=15)
    frame_mode: bool = False
    job_type: Literal['auto', 'standard', 'replacement'] = 'auto'
    aspect_ratio: str = Field(default='16:9 (Landscape)', max_length=64)
    megapixels: float = Field(default=0.7, ge=0.1, le=16)
    duration: float = Field(default=5, ge=0.25, le=60)
    model: str = Field(default='google/gemini-3-flash-preview', min_length=1, max_length=200)
    motion_mode: Literal['person', 'person_scene'] | None = None
    custom_motion_prompt: bool = False
    target_description: str = Field(default='person', min_length=3, max_length=300)


def motion_direction(payload):
    """Canonical motion contract; custom direction is an explicit optional UI choice."""
    from .character_replacement import DEFAULT_REPLACEMENT_PROMPT
    if not payload.motion_mode:
        return payload.prompt
    if payload.frame_mode:
        raise HTTPException(422, 'Motion control uses a source video, not Frames mode.')
    counts = {kind: sum(ref.get('kind') == kind for ref in payload.references)
              for kind in ('image', 'video', 'audio')}
    expected = 2 if payload.motion_mode == 'person_scene' else 1
    if counts != {'image': expected, 'video': 1, 'audio': 0} or len(payload.references) != expected + 1:
        raise HTTPException(422, 'Motion control needs one source video, one character image and an optional location image.')
    if payload.motion_mode == 'person':
        base = DEFAULT_REPLACEMENT_PROMPT + '\nThe selected source target is: ' + payload.target_description
    else:
        base = ('Replace the performer in <Video 1> with the character in <Picture 1>. '
                'Preserve the character identity, face, hairstyle and clothing. Replace the original '
                'background entirely with the location in <Picture 2>. The video supplies motion, '
                'poses, timing and camera framing, not identity, clothing or background. Match scene '
                'lighting, shadows and foot contact. Preserve source audio when present. One continuous shot.')
    return base + ('\nAdditional operator direction:\n' + payload.prompt if payload.custom_motion_prompt else '')


def reference_path(name: str) -> Path:
    root = get_settings().comfyui_input_dir.resolve()
    path = (root / name).resolve()
    if (not name.startswith('h3_mobile/') or '\\' in name or '..' in Path(name).parts
            or not path.is_relative_to(root) or not path.is_file()):
        raise HTTPException(400, 'Reference is unavailable')
    return path


async def convert_media(path: Path, kind: str, crop=None, trim=None):
    """Preserve the reference crop/trim and its audio when sending media to the writer."""
    if not crop and not trim and kind == 'video':
        if path.stat().st_size > 64 * 1024**2:
            raise HTTPException(413, 'Prompt reference video exceeds 64 MiB; trim the reference first')
        return path.read_bytes(), mimetypes.guess_type(path.name)[0] or 'video/mp4'
    suffix = '.mp4' if kind == 'video' else '.wav'
    with tempfile.TemporaryDirectory(prefix='tensor-prompt-') as directory:
        output = Path(directory) / ('reference' + suffix)
        command = [ffmpeg_executable(), '-nostdin', '-v', 'error', '-threads', '1']
        if trim:
            command += ['-ss', str(trim[0])]
        command += ['-i', str(path)]
        if trim:
            command += ['-t', str(trim[1] - trim[0])]
        if kind == 'video':
            filters = []
            if crop:
                x, y, w, h = crop
                filters.append(f'crop=trunc(iw*{w}/2)*2:trunc(ih*{h}/2)*2:iw*{x}:ih*{y}')
            filters.append('scale=trunc(iw/2)*2:trunc(ih/2)*2')
            command += ['-vf', ','.join(filters), '-c:v', 'libx264', '-preset', 'fast', '-crf', '23', '-c:a', 'aac']
        else:
            command += ['-vn', '-ac', '1', '-ar', '24000', '-c:a', 'pcm_s16le']
        command += ['-y', str(output)]
        process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        try:
            await asyncio.wait_for(process.communicate(), 120)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise HTTPException(422, 'Prompt reference conversion timed out')
        if process.returncode or not output.is_file():
            raise HTTPException(422, 'Cannot prepare prompt reference')
        if output.stat().st_size > 64 * 1024**2:
            raise HTTPException(413, 'Prompt reference exceeds 64 MiB; trim the reference first')
        return output.read_bytes(), 'video/mp4' if kind == 'video' else 'audio/wav'


async def reference_content(references: ReferenceSet):
    parts, manifest = [], []
    total = 0
    for tagged in references.assign_tags():
        path = reference_path(tagged.file)
        label = tagged.tag + (f' {tagged.audio_tag}' if tagged.audio_tag else '')
        manifest.append(f'{label}: {tagged.file}')
        parts.append({'type': 'text', 'text': f'{tagged.kind}_reference {label} — the next media:'})
        if tagged.kind == 'image':
            try:
                with Image.open(path) as source:
                    image = ImageOps.exif_transpose(source).convert('RGB')
                    if tagged.ref.crop:
                        x, y, w, h = tagged.ref.crop
                        image = image.crop((round(x*image.width), round(y*image.height), round((x+w)*image.width), round((y+h)*image.height)))
                    image.thumbnail((1280, 1280))
                    buffer = io.BytesIO()
                    image.save(buffer, format='JPEG', quality=90)
                    data = buffer.getvalue()
            except (UnidentifiedImageError, OSError, Image.DecompressionBombError, ValueError):
                raise HTTPException(422, 'Cannot prepare reference image')
            parts.append({'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,'+base64.b64encode(data).decode()}})
        else:
            data, mime = await convert_media(path, tagged.kind, tagged.ref.crop, tagged.ref.trim)
            encoded = base64.b64encode(data).decode()
            if tagged.kind == 'video':
                parts.append({'type': 'video_url', 'video_url': {'url': f'data:{mime};base64,{encoded}'}})
            else:
                parts.append({'type': 'input_audio', 'input_audio': {'data': encoded, 'format': 'wav'}})
        total += len(data)
        if total > 96 * 1024**2:
            raise HTTPException(413, 'Prompt references exceed 96 MiB in total')
    return parts, manifest


async def enhance(payload: EnhanceRequest):
    direction = motion_direction(payload)
    provider = await asyncio.to_thread(llm.connection, read_key)
    normalized = [{**item, 'kind': 'image' if item.get('kind') in {'first_frame', 'last_frame'} else item.get('kind')}
                  for item in payload.references]
    try:
        references = ReferenceSet.from_obj(normalized)
        references.validate()
    except ReferenceError as error:
        raise HTTPException(400, str(error))
    parts, manifest = await reference_content(references)
    provider.body({'model': payload.model, 'max_tokens': 6000,
                   'messages': [{'role': 'user', 'content': parts}]})
    counts = references.counts()
    aspect = payload.aspect_ratio.split()[0]
    try:
        a, b = (float(value) for value in aspect.split(':'))
        width = round(math.sqrt(payload.megapixels * 1_000_000 * a / b) / 16) * 16
        height = round(width * b / a / 16) * 16
    except (ValueError, ZeroDivisionError):
        raise HTTPException(400, 'Invalid output aspect ratio')
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            headers = provider.headers
            mode = ('replacement' if payload.motion_mode == 'person' else 'standard') if payload.motion_mode else payload.job_type
            if not payload.frame_mode and mode == 'auto':
                mode = 'standard'
                if counts['video'] and counts['image']:
                    classification = await client.post(provider.url, headers=headers,
                        json=provider.body({'model': 'google/gemini-2.5-flash-lite', 'max_tokens': 6, 'temperature': 0,
                              'messages': [{'role': 'system', 'content': (PROMPTS/'classifier.txt').read_text(encoding='utf-8')},
                                           {'role': 'user', 'content': payload.prompt}]}))
                    if classification.status_code == 200:
                        answer = classification.json().get('choices', [{}])[0].get('message', {}).get('content') or ''
                        mode = 'replacement' if 'REPLACEMENT' in answer.upper() else 'standard'
            if payload.frame_mode:
                target = director.H3_FL2V if counts['image'] == 2 else director.H3_I2V if counts['image'] else director.H3_T2V
                system = director.build_system_prompt(target, 'Balanced')
                user = director.build_user_prompt(target, payload.prompt, payload.duration, counts['image'], width=width, height=height)
            else:
                filename = 'system_prompt_motion_scene.md' if payload.motion_mode == 'person_scene' else 'system_prompt_replacement.md' if mode == 'replacement' else 'system_prompt.md'
                system = (PROMPTS/filename).read_text(encoding='utf-8')
                user = f'USER DIRECTION:\n{direction}\n\nTARGET FORMAT:\n{width} x {height}; {payload.duration:g} seconds'
            if manifest:
                user += '\n\nReference manifest:\n' + '\n'.join(manifest)
            if notes := role_notes(payload.references):
                user += '\n\nReference preprocessing contract:\n' + '\n'.join(notes)
            request_body = provider.body({'model': payload.model, 'max_tokens': 6000, 'temperature': 0.4,
                      'reasoning': {'effort': 'none'},
                      'messages': [{'role': 'system', 'content': system},
                                   {'role': 'user', 'content': [{'type': 'text', 'text': user}, *parts]}]})
            result = await client.post(provider.url, headers=headers, json=request_body)
        if result.status_code != 200:
            raise HTTPException(502, f'Prompt provider returned HTTP {result.status_code}')
        prompt = director.clean_output(result.json()['choices'][0]['message']['content'])
        if not payload.frame_mode:
            prompt = re.sub(r'\bflush\b', '', prompt, flags=re.IGNORECASE).strip()
        if not prompt or len(prompt) > 24000:
            raise ValueError('Empty or oversized prompt')
        return {'prompt': prompt, 'model': request_body['model'], 'mode': mode}
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        raise HTTPException(502, 'Prompt provider returned an invalid response or timed out')
