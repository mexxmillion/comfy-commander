import json
from contextlib import contextmanager

import httpx
import pytest

from backend import worker


@pytest.mark.parametrize('response', [httpx.Response(404,content=b''), httpx.Response(200,text='<html>no extension</html>'), httpx.Response(200,json=[])])
def test_missing_optional_boot_route_does_not_break_public_worker(response):
    with httpx.Client(base_url='http://comfy.test',transport=httpx.MockTransport(lambda request:response)) as comfy:
        assert worker.optional_runtime_boot_id(comfy) is None


def test_public_worker_completes_using_standard_comfy_api(monkeypatch):
    prompts, reports = [], []
    @contextmanager
    def no_socket(url):
        yield None
    monkeypatch.setattr(worker,'optional_websocket',no_socket)
    monkeypatch.setenv('TENSOR_WORKER_PROVIDER','Local')
    def comfy_response(request):
        path=request.url.path
        if path=='/h3-mobile/status':return httpx.Response(404,content=b'')
        if path=='/queue':return httpx.Response(200,json=dict(queue_running=[],queue_pending=[]))
        if path=='/prompt':
            payload=json.loads(request.content);prompts.append(payload)
            return httpx.Response(200,json=dict(prompt_id=payload['prompt_id']))
        if path.startswith('/history/'):
            if not prompts:return httpx.Response(200,json={})
            pid=prompts[0]['prompt_id']
            return httpx.Response(200,json={pid:dict(status=dict(status_str='success'),outputs={'9':{'images':[dict(filename='fixture.png',subfolder='',type='output')]}})})
        raise AssertionError(path)
    def api_response(request):
        if request.method=='POST':reports.append(json.loads(request.content))
        return httpx.Response(200,json=dict(cancel_requested=False))
    task=dict(task_id='fixture-task',lease_token='fixture-lease',graph={'9':dict(class_type='SaveImage',inputs={})})
    with httpx.Client(base_url='http://controller.test',transport=httpx.MockTransport(api_response)) as api,httpx.Client(base_url='http://comfy.test',transport=httpx.MockTransport(comfy_response)) as comfy:
        worker.run_task(api,comfy,task)
    assert len(prompts)==1
    assert reports[-1]['status']=='completed'
