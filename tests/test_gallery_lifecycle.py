import json

import pytest
from sqlalchemy.orm import Session
from starlette.requests import Request

from backend.app.store import QueueStore, TaskRow


@pytest.mark.asyncio
async def test_gallery_tracks_one_job_from_queue_to_media(tmp_path):
    from backend.app.main import completed_outputs

    store = QueueStore(f"sqlite:///{tmp_path / 'gallery.sqlite3'}")
    recipe = {"mode": "h3", "direction": "A public test scene", "seed": 42}
    job_id = store.submit(name="Test generation", graph={}, recipe=recipe)
    request = Request({"type": "http", "scheme": "http", "server": ("testserver", 80),
                       "path": "/api/v1/outputs", "query_string": b"", "headers": []})
    queued = await completed_outputs(request, store=store, limit=100)
    assert len(queued) == 1
    assert queued[0]["job_id"] == job_id
    assert queued[0]["status"] == "Queued"
    assert not queued[0].get("filename")
    assert queued[0]["recipe"] == recipe
    task_id = queued[0]["task_id"]
    with Session(store.engine) as db, db.begin():
        task = db.get(TaskRow, task_id)
        task.status, task.progress = "running", 35
        task.message = "Sampling"
    running = await completed_outputs(request, store=store, limit=100)
    assert running[0]["status"] == "Running"
    assert running[0]["progress"] == 35
    assert running[0]["task_id"] == task_id
    with Session(store.engine) as db, db.begin():
        task = db.get(TaskRow, task_id)
        task.status = "completed"
        task.artifact_json = json.dumps({"filename": "result.mp4", "url": "/outputs/test/result.mp4"})
    finished = await completed_outputs(request, store=store, limit=100)
    assert len(finished) == 1
    assert finished[0]["status"] == "Finished"
    assert finished[0]["filename"] == "result.mp4"
    assert finished[0]["job_id"] == job_id
    assert finished[0]["recipe"] == recipe


def test_requeued_job_does_not_show_stale_output(tmp_path):
    store = QueueStore(f"sqlite:///{tmp_path / 'gallery.sqlite3'}")
    store.submit(name="Retry", graph={})
    task_id = store.output_records()[0]["task_id"]
    with Session(store.engine) as db, db.begin():
        task = db.get(TaskRow, task_id)
        task.artifact_json = json.dumps({"filename": "previous.mp4"})
    record = store.output_records()[0]
    assert record["status"] == "queued"
    assert record["artifact"] == {}
