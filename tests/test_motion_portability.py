import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from backend.app import generator, media


@pytest.mark.asyncio
@pytest.mark.parametrize('sound', [False, True])
async def test_probe_works_without_ffprobe(monkeypatch, tmp_path, sound):
    target = tmp_path / 'h3_mobile' / 'sample.mp4'
    target.parent.mkdir()
    command = [media.ffmpeg_executable(), '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=32x32:r=10:d=0.5']
    if sound:
        command += ['-f', 'lavfi', '-i', 'sine=frequency=440:duration=0.5', '-c:a', 'aac', '-shortest']
    command += ['-c:v', 'libx264', '-y', str(target)]
    await asyncio.to_thread(subprocess.run, command, check=True, capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    monkeypatch.setattr(generator, 'require_admin', lambda *a, **kw: None)
    monkeypatch.setattr(generator.shutil, 'which', lambda name: None)
    monkeypatch.setattr(media, 'get_settings', lambda: SimpleNamespace(comfyui_input_dir=tmp_path))
    result = await generator.probe(Request({'type': 'http'}), 'h3_mobile/sample.mp4')
    assert result['kind'] == 'video' and result['width'] == 32 and result['height'] == 32
    assert result['has_audio'] is sound
    assert result['duration'] > 0 and result['fps'] == 10


def test_model_and_profile_pin_character_swap_without_enabling_by_default():
    import json

    from backend.app.character_replacement import CHARACTER_SWAP_LORA, character_swap_profile
    root = Path(__file__).parents[1]
    manifest = json.loads((root / 'runtime/h3/models.lock.json').read_text())
    artifacts = [a for a in manifest['artifacts'] if a['group'] == 'character-swap-v15']
    assert len(artifacts) == 1
    artifact = artifacts[0]
    assert artifact['file'] == CHARACTER_SWAP_LORA
    assert artifact['repo'] == 'akatz-ai/MiniMax-H3-Character-Swap-LoRA'
    assert len(artifact['revision']) == 40 and len(artifact['sha256']) == 64
    assert 'character-swap-v15' in manifest['profiles']['ref2v-int8']
    profile = character_swap_profile()
    assert profile['characterSwapEnabled'] and profile['characterSwapStrength'] == 1
    template = (root / 'backend/app/templates/generator.html').read_text(encoding='utf-8')
    assert '<input id="character-swap-enabled" type="checkbox">' in template
