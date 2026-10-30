import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.app import main
from backend.app.store import QueueStore


@pytest.mark.asyncio
async def test_reroute_local_lora_to_16gb_without_cloud_registration(monkeypatch, tmp_path):
    monkeypatch.setattr(main, 'require_admin', lambda *a, **kw: None)
    store = QueueStore(f"sqlite:///{tmp_path / 'queue.sqlite3'}")
    graph = {'1': {'class_type': 'UNETLoader', 'inputs': {'unet_name': 'minimax_h3_ref2va_pruned_int8_convrot.safetensors'}},
             '2': {'class_type': 'LoraLoaderModelOnly', 'inputs': {'lora_name': 'local-style.safetensors'}}}
    job = store.submit(name='Local LoRA', graph=graph, min_vram_gb=24)
    task = job + '-t001'
    request = Request({'type': 'http', 'method': 'POST', 'path': '/'})
    # A change to the mixed pool must still reject unresolved cloud dependencies.
    with pytest.raises(HTTPException, match='LoRA not in the cloud model manifest'):
        await main.operator_route_task(task, main.TaskRouteUpdate(pool='any', min_vram_gb=16), request, store)
    saved = await main.operator_route_task(task, main.TaskRouteUpdate(pool='local', min_vram_gb=16), request, store)
    assert saved['route']['min_vram_gb'] == 16
    assert saved['route']['policy'] == 'local'
    snapshot = next(row for row in store.snapshot()['tasks'] if row['id'] == task)
    assert snapshot['min_vram_gb'] == 16
    assert snapshot['requirement'].startswith('16 GB+')
