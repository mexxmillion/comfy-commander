import pytest

from backend.app import creative
from backend.app.store import QueueStore


@pytest.mark.parametrize('policy,provider,runtime', [
    ('local', 'Local', 'pocket-h3-local'),
    ('any', 'Local', 'pocket-h3-cloud-ref2v-int8'),
    ('standard', 'Vast.ai', 'pocket-h3-cloud-ref2v-int8'),
])
def test_creative_jobs_can_be_claimed_by_16gb_workers(tmp_path, policy, provider, runtime):
    store = QueueStore(f"sqlite:///{tmp_path / 'queue.sqlite3'}")
    graph = {'129': {'inputs': {'noise_seed': 1}}, '131': {
        'class_type': 'MiniMaxH3ImageToVideo', 'inputs': {'prompt': 'test'}}}
    payload = creative.Brief(brief='Two variations', recipe={}, graph=graph, policy=policy)
    draft = creative.save_draft(store, payload, [{'title': 'One', 'prompt': 'A person walking'}], runtime)
    selection = creative.Selection(concepts=[creative.Concept(index=0, title='One', prompt='A person walking')])
    queued = creative.queue_batch(store, draft['id'], selection, lambda graph: None)
    job_id = queued['concepts'][0]['job_id']
    store.register_worker(worker_id='test16', name='16GB', provider=provider, gpu='Test', architecture='any',
                          vram_gb=16, runtime=runtime)
    lease = store.claim('test16')
    assert lease is not None and lease['job_id'] == job_id
    # Repeating dispatch keeps the existing job rather than duplicating it.
    repeated = creative.queue_batch(store, draft['id'], selection, lambda graph: None)
    assert repeated['concepts'][0]['job_id'] == job_id


def test_store_default_uses_16gb_but_explicit_requirements_are_preserved(tmp_path):
    store = QueueStore(f"sqlite:///{tmp_path / 'queue.sqlite3'}")
    normal = store.submit(name='Default', graph={})
    store.submit(name='Heavy', graph={}, min_vram_gb=24)
    store.register_worker(worker_id='test16', name='16GB', provider='Local', gpu='Test', architecture='any',
                          vram_gb=16, runtime='pocket-h3-local')
    assert store.claim('test16')['job_id'] == normal
    assert store.claim('test16') is None
