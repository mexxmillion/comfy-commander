import json

import httpx

from backend.app.providers.runpod import RunpodProvider, PLACEMENT


def test_runpod_quote_and_launch_use_same_cuda_floor_and_host_filters():
    requests = []
    def respond(request):
        body=json.loads(request.content)
        requests.append(body)
        if body['query'].lstrip().startswith('mutation'):
            return httpx.Response(200,json={'data':{'podFindAndDeployOnDemand':{'id':'fixture-pod','costPerHr':1.24}}})
        return httpx.Response(200,json={'data':{'gpuTypes':[dict(id='NVIDIA GeForce RTX 5090',displayName='RTX 5090',memoryInGb=32,secureCloud=True,lowestPrice=dict(stockStatus='High',uninterruptablePrice=1.19,availableGpuCounts=[1]))]}})
    provider=RunpodProvider('fixture-token',transport=httpx.MockTransport(respond))
    assert provider.offers(3)[0]['gpu']=='RTX 5090'
    assert provider.create_instance('NVIDIA GeForce RTX 5090',name='fixture',public_key='ssh-ed25519 fixture-public')['id']=='fixture-pod'
    quote=requests[0]['variables']['placement']
    launch=requests[1]['variables']['input']
    for key,value in PLACEMENT.items():
        assert quote[key]==launch[key]==value
    assert launch['minCudaVersion']=='13.0' and 'allowedCudaVersions' not in launch
    assert json.loads(launch['dockerArgs'])['entrypoint']==['/bin/bash','-lc']
    assert 'PasswordAuthentication=no' in json.loads(launch['dockerArgs'])['cmd'][0]
    assert len(requests)==2


def test_saved_hourly_ceiling_loads_on_restart_and_respects_fleet_cap(monkeypatch,tmp_path):
    from backend.app.config import get_settings
    path=tmp_path/'rental-preferences.json'
    monkeypatch.setenv('RENTAL_PREFERENCES_FILE',str(path))
    monkeypatch.setenv('MAX_GPU_PRICE_PER_HOUR','1.7')
    monkeypatch.setenv('MAX_TOTAL_PRICE_PER_HOUR','4')
    try:
        path.write_text(json.dumps(dict(max_price_per_hour=3)),encoding='utf-8')
        get_settings.cache_clear()
        assert get_settings().max_gpu_price_per_hour==3
        path.write_text(json.dumps(dict(max_price_per_hour=5)),encoding='utf-8')
        get_settings.cache_clear()
        assert get_settings().max_gpu_price_per_hour==1.7
    finally:
        get_settings.cache_clear()
