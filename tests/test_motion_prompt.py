import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from backend.app import generator_prompt as prompts


def request(**overrides):
    data = dict(prompt='stale unrelated text', motion_mode='person_scene', references=[
        {'kind':'image','file':'h3_mobile/character.jpg'},
        {'kind':'image','file':'h3_mobile/location.jpg'},
        {'kind':'video','file':'h3_mobile/motion.mp4'}])
    data.update(overrides)
    return prompts.EnhanceRequest(**data)


def test_canned_motion_ignores_unrelated_hidden_prompt_and_binds_roles():
    text = prompts.motion_direction(request())
    assert 'stale unrelated text' not in text
    assert all(tag in text for tag in ['<Picture 1>', '<Picture 2>', '<Video 1>'])
    assert 'not identity, clothing or background' in text


def test_optional_motion_direction_requires_explicit_flag():
    text = prompts.motion_direction(request(prompt='Add warm lighting', custom_motion_prompt=True))
    assert 'Additional operator direction:\nAdd warm lighting' in text
    assert '<Picture 2>' in text


def test_motion_validates_required_references_before_enhancement():
    with pytest.raises(HTTPException):
        prompts.motion_direction(request(references=[]))
    with pytest.raises(HTTPException):
        prompts.motion_direction(request(frame_mode=True))


def test_scene_enhancement_uses_dedicated_system_and_canned_direction(monkeypatch):
    calls=[]
    provider=SimpleNamespace(url='https://provider.invalid/chat/completions', headers={},
                             body=lambda value:value)
    monkeypatch.setattr(prompts.llm, 'connection', lambda *_:provider)
    async def references(*_):return [], ['Picture 1: character', 'Picture 2: location', 'Video 1: motion']
    monkeypatch.setattr(prompts, 'reference_content', references)
    class Client:
        def __init__(self, **kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self, *args):pass
        async def post(self, url, **kwargs):
            calls.append(kwargs['json'])
            return httpx.Response(200, json={'choices':[{'message':{'content':'Enhanced character and location instructions.'}}]})
    monkeypatch.setattr(prompts.httpx, 'AsyncClient', Client)
    result=asyncio.run(prompts.enhance(request()))
    assert result['prompt']=='Enhanced character and location instructions.'
    assert len(calls)==1
    assert 'Picture 2 supplies the replacement location' in calls[0]['messages'][0]['content']
    assert 'stale unrelated text' not in str(calls[0])
