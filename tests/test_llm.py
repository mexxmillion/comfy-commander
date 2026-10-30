import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.app import llm


def config(**values):
    return llm.LLMSettings(provider='compatible', base_url='http://127.0.0.1:1234/v1', model='local-vision', **values)


def body(parts=None, **extra):
    return {'model':'google/gemini-3-flash-preview','max_tokens':6000,'temperature':.4,
            'messages':[{'role':'user','content':parts or 'Write a prompt'}], **extra}


@pytest.mark.parametrize('url',['http://public.example/v1','https://user:password@example.com/v1','https://example.com/v1?key=secret','https://example.com/v1#key','https://example.com/v1/chat/completions','https://example.com:bad/v1'])
def test_endpoint_validation_rejects_unsafe_or_wrong_urls(url):
    with pytest.raises(ValidationError):
        llm.LLMSettings(base_url=url)


def test_custom_provider_uses_own_model_and_portable_parameters():
    provider=llm.Connection(config(token_parameter='max_completion_tokens',temperature=False),None)
    result=provider.body(body(reasoning={'effort':'none'},response_format={'type':'json_schema','json_schema':{}}))
    assert result['model']=='local-vision'
    assert result['max_completion_tokens']==6000 and 'max_tokens' not in result
    assert 'reasoning' not in result and 'temperature' not in result
    assert result['response_format']=={'type':'json_object'}
    assert provider.body(result)==result  # retries preserve output/context accounting


@pytest.mark.parametrize('kind,capability',[('image_url','vision'),('video_url','video'),('input_audio','audio')])
def test_unsupported_media_is_blocked_before_transport(kind,capability):
    provider=llm.Connection(config(),None)
    with pytest.raises(HTTPException) as error:
        provider.body(body([{'type':kind}]))
    assert error.value.status_code==409 and capability in error.value.detail
    assert llm.Connection(config(**{capability:True}),None).body(body([{'type':kind}]))


def test_context_and_output_budget_are_checked():
    provider=llm.Connection(config(context_window=32768),None)
    with pytest.raises(HTTPException) as error:
        provider.body(body([{'type':'text','text':'x'*100000}]))
    assert error.value.status_code==413
    with pytest.raises(HTTPException):
        llm.Connection(config(max_output_tokens=1200),None).body(body())


def test_key_is_bound_to_endpoint_and_openrouter_reader_is_never_used(tmp_path,monkeypatch):
    settings=SimpleNamespace(token_file=tmp_path/'api_token',preferences_file=tmp_path/'preferences.json')
    monkeypatch.setattr(llm,'get_settings',lambda:settings)
    llm.key_path().write_text(json.dumps({'endpoint':'https://first.example/v1','key':'isolated-key-fixture'}),encoding='utf-8')
    selected=llm.LLMSettings(provider='compatible',base_url='https://second.example/v1',model='model')
    monkeypatch.setattr(llm,'read_settings',lambda:selected)
    def forbidden():
        pytest.fail('OpenRouter key must not be forwarded')
    with pytest.raises(HTTPException) as error:
        llm.connection(forbidden)
    assert error.value.status_code==409
    monkeypatch.setattr(llm,'read_settings',lambda:config())
    assert llm.connection(forbidden).key is None  # keyless local endpoint


def test_openrouter_default_routing_is_preserved(monkeypatch):
    monkeypatch.setattr(llm,'read_settings',llm.LLMSettings)
    provider=llm.connection(lambda:'isolated-key-fixture')
    assert provider.url=='https://openrouter.ai/api/v1/chat/completions'
    assert provider.body(body())['model']=='google/gemini-3-flash-preview'
    with pytest.raises(HTTPException):
        provider.body({**body(),'model':'arbitrary-model'})


def test_validate_models_does_not_call_completion_or_follow_redirects(monkeypatch):
    calls=[]
    class Client:
        def __init__(self,**kwargs):
            assert kwargs['follow_redirects'] is False
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get(self,url,**kwargs):
            calls.append(url)
            return httpx.Response(200,json={'data':[{'id':'local-vision'}]})
    monkeypatch.setattr(llm.httpx,'Client',Client)
    provider,result=llm.check_connection(llm.SaveSettings(settings=config()))
    assert calls==['http://127.0.0.1:1234/v1/models']
    assert result['model_count']==1 and provider.key is None


def test_qwen_enhancement_uses_configured_endpoint_without_openrouter_key(monkeypatch):
    from backend.app import qwen_prompt
    monkeypatch.setattr(llm,'read_settings',lambda:config())
    monkeypatch.setattr(llm,'custom_key',lambda _:None)
    calls=[]
    class Client:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,**kwargs):
            calls.append((url,kwargs))
            return httpx.Response(200,json={'choices':[{'message':{'content':'A clear image prompt.'}}]})
    monkeypatch.setattr(qwen_prompt.httpx,'AsyncClient',Client)
    result=asyncio.run(qwen_prompt.enhance(qwen_prompt.EnhanceRequest(prompt='A mountain',mode='text-to-image')))
    assert result['model']=='local-vision'
    assert calls[0][0]=='http://127.0.0.1:1234/v1/chat/completions'
    assert calls[0][1]['headers']=={}


def test_settings_routes_require_admin_and_mutation_guard(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    calls=[]
    def deny(request,mutation=False):
        calls.append(mutation);raise HTTPException(403,'Sign in')
    monkeypatch.setattr(llm,'require_admin',deny)
    app=FastAPI();app.include_router(llm.router)
    client=TestClient(app)
    assert client.get('/api/v1/settings/llm').status_code==403
    assert client.put('/api/v1/settings/llm',json={'settings':config().model_dump()}).status_code==403
    assert client.delete('/api/v1/settings/llm/key').status_code==403
    assert calls==[False,True,True]


def test_h3_and_creative_engine_use_custom_model(monkeypatch):
    from backend.app import generator_prompt, creative
    monkeypatch.setattr(llm,'read_settings',lambda:config(json_format='prompt'))
    monkeypatch.setattr(llm,'custom_key',lambda _:None)
    calls=[]
    class Client:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,**kwargs):
            request=kwargs['json'];calls.append(request)
            assert url=='http://127.0.0.1:1234/v1/chat/completions'
            assert request['model']=='local-vision'
            content='A clear video prompt.'
            if 'JSON object' in request['messages'][0]['content']:
                assert 'response_format' not in request
                assert 'JSON object' in request['messages'][0]['content']
                content=json.dumps({'concepts':[{'title':'One','prompt':'The first landscape.'},{'title':'Two','prompt':'The second landscape.'}]})
            return httpx.Response(200,json={'choices':[{'message':{'content':content}}]})
    monkeypatch.setattr(generator_prompt.httpx,'AsyncClient',Client)
    result=asyncio.run(generator_prompt.enhance(generator_prompt.EnhanceRequest(prompt='A mountain',job_type='standard')))
    assert result['model']=='local-vision'
    result=asyncio.run(creative._develop_ideas(creative.Brief(brief='Two landscapes',count=2,recipe={'singleFrame':True},graph={})))
    assert len(result)==2


def test_profile_review_uses_custom_json_endpoint(monkeypatch):
    from backend.app import studio_agent
    monkeypatch.setattr(llm,'read_settings',lambda:config(json_format='prompt'))
    monkeypatch.setattr(llm,'custom_key',lambda _:None)
    monkeypatch.setattr(studio_agent,'require_admin',lambda *a,**k:None)
    monkeypatch.setattr(studio_agent,'setup_status',lambda:{'ready':False})
    class Client:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,url,**kwargs):
            assert url=='http://127.0.0.1:1234/v1/chat/completions'
            assert kwargs['json']['model']=='local-vision'
            assert 'response_format' not in kwargs['json']
            assert 'JSON object' in kwargs['json']['messages'][0]['content']
            return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'message':'Validate the profile first.','action':'none'})}}]})
    monkeypatch.setattr(studio_agent.httpx,'Client',Client)
    result=asyncio.run(studio_agent.agent_chat(studio_agent.AgentChat(context='profile_import',model='local-vision',messages=[studio_agent.ChatMessage(role='user',content='Review my workflow')]),None))
    assert result['model']=='local-vision' and result['action'] is None
