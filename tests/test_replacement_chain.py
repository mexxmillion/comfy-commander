import json
from copy import deepcopy

import pytest
from fastapi import HTTPException

from backend.app import character_replacement as replacement
from backend.app import main
from backend.app.h3_media import h3_input_paths
from backend.app.store import QueueStore
from backend.worker import find_artifact


def h3_graph(kinds=('image', 'video')):
    refs = [{'kind': kind, 'file': f'h3_mobile/source.{"mp4" if kind == "video" else "png"}'} for kind in kinds]
    graph = {str(i): {'class_type': 'RandomNoise', 'inputs': {'noise_seed': i}} for i in range(15)}
    graph.update({
        'pack': {'class_type': 'MiniMaxH3ReferencePack', 'inputs': {'references_json': json.dumps({'references': refs}), 'job_type': 'replacement'}},
        'h3': {'class_type': 'MiniMaxH3ReferenceToVideo', 'inputs': {'ref_videos.ref_video_0': ['pack', 9], 'ref_video_audios.ref_video_audio_0': ['pack', 12]}},
        'out': {'class_type': 'VHS_VideoCombine', 'inputs': {}},
    })
    return graph


def test_one_shot_replacement_preserves_identity_and_audio_and_inverts_target_only():
    base = h3_graph()
    graph = replacement.compose_chain(base, 'the person in red')
    main.validate_pocket_graph(graph)
    assert replacement.validate_chain(graph) == base
    assert graph['h3']['inputs']['ref_videos.ref_video_0'] == ['replacement_9', 0]
    assert graph['h3']['inputs']['ref_video_audios.ref_video_audio_0'] == ['pack', 12]
    assert graph['replacement_9']['inputs']['destination'] == ['pack', 9]
    assert graph['replacement_9']['inputs']['source'] == ['replacement_8', 0]
    assert graph['replacement_9']['inputs']['mask'] == ['replacement_7', 0]
    assert graph['replacement_4']['inputs']['text'] == 'the person in red'
    assert graph['replacement_12']['inputs']['audio'] == ['pack', 12]
    assert h3_input_paths(graph) == {'h3_mobile/source.mp4', 'h3_mobile/source.png'}
    assert not replacement.is_graph(graph)  # Route by the actual H3 model, not standalone preparation.


@pytest.mark.parametrize('kinds', [(), ('image',), ('video',)])
def test_replacement_requires_image_and_video(kinds):
    with pytest.raises(HTTPException, match='at least one source video and one replacement image'):
        replacement.compose_chain(h3_graph(kinds), 'person')


def test_combined_graph_cannot_change_tracking_or_invert_background():
    graph = replacement.compose_chain(h3_graph(), 'person')
    for node, field, value in [('replacement_9', 'mask', ['pack', 9]), ('replacement_6', 'max_objects', 4), ('replacement_2', 'ckpt_name', 'other.safetensors')]:
        changed = deepcopy(graph)
        changed[node]['inputs'][field] = value
        with pytest.raises(HTTPException):
            main.validate_pocket_graph(changed)


@pytest.mark.asyncio
async def test_single_submission_builds_chain_and_retains_one_job(tmp_path):
    store = QueueStore(f"sqlite:///{tmp_path / 'jobs.sqlite3'}")
    result = await main.submit_job(main.JobSubmission(name='Replace', graph=h3_graph(), policy='local', recipe={'mode': 'h3', 'replacementTarget': 'person in blue'}), store)
    graph = store.task_graph(result['job_id'] + '-t001')
    assert graph['replacement_4']['inputs']['text'] == 'person in blue'
    assert len(store.snapshot()['tasks']) == 1
    assert replacement.is_chain(graph)


def test_worker_primary_output_is_h3_even_when_mask_finishes_first():
    history = {'outputs': {'replacement_11': {'gifs': [{'filename': 'mask.mkv'}]}, 'out': {'gifs': [{'filename': 'final.mp4'}]}, 'replacement_12': {'gifs': [{'filename': 'guide.mkv'}]}}}
    assert find_artifact(history, 'out')['filename'] == 'final.mp4'
    assert find_artifact(history, 'replacement_11')['filename'] == 'mask.mkv'
    assert find_artifact(history, 'replacement_12')['filename'] == 'guide.mkv'
