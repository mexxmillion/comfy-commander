import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.app import main, media
from backend.app.config import Settings
from backend.app.store import QueueStore


@pytest.mark.asyncio
async def test_cloud_media_round_trip_without_local_comfyui(monkeypatch, tmp_path):
    settings = Settings(_env_file=None, comfyui_root=None, comfyui_python=None,
                        comfyui_input_dir=tmp_path / 'uploads', cloud_output_dir=tmp_path / 'outputs')
    monkeypatch.setattr(main, 'get_settings', lambda: settings)
    monkeypatch.setattr(media, 'get_settings', lambda: settings)
    store = QueueStore(f"sqlite:///{tmp_path / 'gallery.sqlite3'}")
    source = settings.comfyui_input_dir / 'h3_mobile' / 'reference.png'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'uploaded-reference')
    graph = {'1': {'class_type': 'MiniMaxH3ReferencePack', 'inputs': {
        'references_json': json.dumps({'references': [{'file': 'h3_mobile/reference.png'}]})}}}
    job = store.submit(name='Cloud media', graph=graph, min_vram_gb=16,
                       runtime='pocket-h3-cloud-ref2v-int8', policy='standard', recipe={'mode': 'h3'})
    store.register_worker(worker_id='test-cloud', name='Test', provider='Vast.ai', gpu='Test',
                          architecture='ampere', vram_gb=24, runtime='pocket-h3-cloud-ref2v-int8')
    lease = store.claim('test-cloud')
    task, token = lease['task_id'], lease['lease_token']
    response = await main.task_input(task, 'h3_mobile/reference.png', lease_token=token, store=store)
    assert response.path.read_bytes() == source.read_bytes()
    with pytest.raises(HTTPException):
        await main.task_input(task, 'h3_mobile/reference.png', lease_token='wrong', store=store)
    with pytest.raises(HTTPException):
        await main.task_input(task, 'h3_mobile/other.png', lease_token=token, store=store)

    async def receive():
        return {'type': 'http.request', 'body': b'completed-cloud-video', 'more_body': False}

    request = Request({'type': 'http', 'method': 'PUT', 'path': '/', 'headers': []}, receive)
    uploaded = await main.upload_task_output(task, 'result.mp4', request, lease_token=token, store=store)
    assert store.update_task(task, token, status='completed', progress=100,
                             artifact={'filename': 'result.mp4', 'subfolder': 'H3', 'url': uploaded['url']})
    store.engine.dispose()
    # Restart the controller without the rented worker or a ComfyUI directory.
    reopened = QueueStore(f"sqlite:///{tmp_path / 'gallery.sqlite3'}")
    records = reopened.output_records()
    assert records[0]['job_id'] == job and records[0]['status'] == 'completed'
    output = await main.view_task_output(task, 'result.mp4', store=reopened)
    assert output.path.read_bytes() == b'completed-cloud-video'
    assert media.media_path('result.mp4', subfolder='H3', task_id=task).read_bytes() == b'completed-cloud-video'
