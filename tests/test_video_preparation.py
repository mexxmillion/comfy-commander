import subprocess
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import video_processing
from backend.app.media import ffmpeg_executable, ffmpeg_metadata
from backend.worker import execution_phase, progress_message


def test_video_stage_resizes_resamples_trims_and_caches_without_touching_original(
    tmp_path, monkeypatch
):
    source = tmp_path / "h3_mobile/source.mp4"
    source.parent.mkdir()
    subprocess.run(
        [
            ffmpeg_executable(),
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=96x160:rate=30:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            "-y",
            str(source),
        ],
        check=True,
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    before = source.read_bytes()
    module = video_processing.video_module()
    relative = module.prepare_video(
        tmp_path, "h3_mobile/source.mp4", 64, 96, 22, trim_json="[0.5, 1.5]"
    )
    result = tmp_path / relative
    probe = subprocess.run(
        [ffmpeg_executable(), "-hide_banner", "-i", str(result), "-t", "0", "-f", "null", "-"],
        capture_output=True,
        text=True,
        check=False,
    )
    metadata = ffmpeg_metadata(probe.stderr)
    assert (metadata["width"], metadata["height"], metadata["fps"], metadata["has_audio"]) == (
        64,
        96,
        24,
        True,
    )
    raw = subprocess.run(
        [
            ffmpeg_executable(),
            "-v",
            "error",
            "-i",
            str(result),
            "-map",
            "0:v:0",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        capture_output=True,
        check=True,
    )
    assert len(raw.stdout) // (64 * 96 * 3) == 22
    assert video_processing.prepared_timing(tmp_path, relative) == (22, 22 / 24)
    preview = video_processing.browser_preview(tmp_path, relative)
    assert preview.endswith(".mp4") and (tmp_path / preview).is_file()
    assert source.read_bytes() == before
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *a, **kw: pytest.fail("Cached preview must not be re-encoded"),
    )
    assert (
        module.prepare_video(tmp_path, "h3_mobile/source.mp4", 64, 96, 22, trim_json="[0.5, 1.5]")
        == relative
    )
    with pytest.raises(ValueError):
        module.prepare_video(tmp_path, "../outside.mp4", 64, 96, 22)
    with pytest.raises(ValueError):
        module.prepare_video(tmp_path, "h3_mobile/source.mp4", 64, 96, 22, crop_json="[0,0,2,1]")


def test_process_api_is_guarded_and_returns_reviewable_reference(monkeypatch, tmp_path):
    app = FastAPI()
    app.include_router(video_processing.router)
    client = TestClient(app)
    assert (
        client.post(
            "/api/v1/generator/process-video", json={"file": "h3_mobile/source.mp4"}
        ).status_code
        == 403
    )
    calls = []
    monkeypatch.setattr(video_processing, "require_admin", lambda *a, **kw: calls.append(kw))
    source = tmp_path / "source.mp4"
    source.touch()
    monkeypatch.setattr(video_processing, "media_path", lambda *a: source)
    monkeypatch.setattr(
        video_processing, "get_settings", lambda: SimpleNamespace(comfyui_input_dir=tmp_path)
    )
    monkeypatch.setattr(
        video_processing,
        "video_module",
        lambda: SimpleNamespace(
            prepare_video=lambda *a, **kw: "h3_mobile/video-prepared/fixture.mkv"
        ),
    )
    monkeypatch.setattr(
        video_processing, "browser_preview", lambda *a: "h3_mobile/video-prepared/fixture.mp4"
    )
    monkeypatch.setattr(video_processing, 'prepared_timing', lambda *a: (72, 3.0))
    result = client.post(
        "/api/v1/generator/process-video", json={"file": "h3_mobile/source.mp4"}
    ).json()
    assert result["reference"]["preparation"] == {
        "source": "h3_mobile/source.mp4",
        "preview": "h3_mobile/video-prepared/fixture.mp4",
        "actual_frames": 72,
        "actual_duration": 3.0,
        "width": 544,
        "height": 960,
        "frame_count": 124,
    }
    assert result["graph"]["video_prepare"]["class_type"] == "TMPrepareVideo"
    assert calls[-1] == {"mutation": True}
    assert result['actual_frames'] == 72 and result['actual_duration'] == 3


def test_tracking_frames_are_not_sampling_steps():
    assert progress_message("SAM3_VideoTrack", 90, 142) == "SAM3 tracking · source frame 90/142"
    assert progress_message("SamplerCustomAdvanced", 4, 8) == "Sampling 4/8"
    assert "encoding" in execution_phase("MiniMaxH3ReferenceToVideo")


def test_inspector_uses_output_graph_dimensions_and_frame_grid(tmp_path):
    from backend.app.job_spec import job_spec
    from backend.app.store import QueueStore

    store = QueueStore(f"sqlite:///{tmp_path / 'jobs.sqlite3'}")
    graph = {
        "131": {
            "class_type": "MiniMaxH3ReferenceToVideo",
            "inputs": {"width": 640, "height": 640, "length": 39},
        },
        "162": {
            "class_type": "ResolutionSelector",
            "inputs": {"aspect_ratio": "9:16", "megapixels": 0.5, "multiple": 32},
        },
        "133": {"class_type": "PrimitiveFloat", "inputs": {"value": 5}},
        "181": {
            "class_type": "MiniMaxH3ReferencePack",
            "inputs": {"references_json": '{"references":[]}', "job_type": "replacement"},
        },
    }
    job = store.submit(
        name="Inspector regression",
        graph=graph,
        policy="local",
        min_vram_gb=16,
        architecture="any",
        runtime="pocket-h3-local",
    )
    spec = job_spec(store, job + "-t001")
    assert (spec["width"], spec["height"], spec["frames"], spec["fps"]) == (640, 640, 39, 24)
    assert spec["encoded_aspect"] == "1:1" and spec["duration_seconds"] == 1.62
    assert spec["type"] == "H3 Motion control · SAM3 → H3"
    assert spec["aspect_label"] == "Square"


@pytest.mark.parametrize("width,height,orientation", [(544, 960, "Portrait"), (960, 544, "Landscape")])
def test_inspector_orientation_uses_output_canvas_not_legacy_selector_title(tmp_path, width, height, orientation):
    from backend.app.job_spec import job_spec
    from backend.app.store import QueueStore

    store = QueueStore(f"sqlite:///{tmp_path / 'jobs.sqlite3'}")
    job = store.submit(
        name="Orientation regression", policy="local", min_vram_gb=16,
        architecture="any", runtime="pocket-h3-local",
        graph={
            "131": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {"width": width, "height": height, "length": 124}},
            "162": {"class_type": "ResolutionSelector", "inputs": {"aspect_ratio": "9:16 (Portrait Widescreen)", "megapixels": 0.5}},
        },
    )
    spec = job_spec(store, job + "-t001")
    assert spec["aspect_label"] == orientation
    assert (spec["width"], spec["height"]) == (width, height)

@pytest.mark.parametrize('field,value', [('trim',[3,1]),('trim',[True,3]),('trim',[0,float('inf')]),('crop',[.7,0,.5,1]),('crop',[0,0,0,1])])
def test_video_request_rejects_invalid_windows_before_processing(field,value):
    with pytest.raises(ValueError):
        video_processing.VideoRequest(file='h3_mobile/source.mp4',**{field:value})


def test_video_crop_uses_xywh_contract(tmp_path,monkeypatch):
    module=video_processing.video_module()
    source=tmp_path/'h3_mobile/source.mp4';source.parent.mkdir();source.write_bytes(b'fixture')
    commands=[]
    def run(command,**kwargs):
        commands.append(command);__import__('pathlib').Path(command[-1]).write_bytes(b'prepared')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(module.subprocess,'run',run)
    # A valid offset crop rejected by the previous x1/y1/x2/y2 interpretation.
    module.prepare_video(tmp_path,'h3_mobile/source.mp4',64,96,22,crop_json='[0.6,0.2,0.3,0.4]')
    filters=commands[0][commands[0].index('-vf')+1]
    assert 'crop=iw*0.3:ih*0.4:iw*0.6:ih*0.2' in filters


def test_cancelled_request_keeps_cpu_lock_until_work_finishes(monkeypatch,tmp_path):
    import asyncio
    from threading import Event
    entered,finish=Event(),Event()
    def prepare(*args,**kwargs):
        entered.set();assert finish.wait(5);return 'fixture.mkv'
    monkeypatch.setattr(video_processing,'video_module',lambda:SimpleNamespace(prepare_video=prepare))
    monkeypatch.setattr(video_processing,'browser_preview',lambda *args:'fixture.mp4')
    monkeypatch.setattr(video_processing,'get_settings',lambda:SimpleNamespace(comfyui_input_dir=tmp_path))
    async def scenario():
        task=asyncio.create_task(asyncio.to_thread(video_processing.prepare_preview,{}))
        try:
            assert await asyncio.to_thread(entered.wait,3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
            assert video_processing.PREVIEW_LOCK.locked()
            with pytest.raises(__import__('fastapi').HTTPException) as error:video_processing.prepare_preview({})
            assert error.value.status_code==409
        finally:finish.set()
    asyncio.run(scenario())
    assert not video_processing.PREVIEW_LOCK.locked()

@pytest.mark.parametrize('failure,status,expected',[(subprocess.TimeoutExpired('private-command',1),504,'shorter interval'),(FileNotFoundError('private-path'),503,'FFmpeg'),(ValueError('Selected window contains no video frames'),422,'Move the start earlier'),(RuntimeError('private-detail'),422,'source plays')])
def test_video_errors_are_actionable_without_exposing_internal_details(monkeypatch,tmp_path,failure,status,expected):
    app=FastAPI();app.include_router(video_processing.router)
    monkeypatch.setattr(video_processing,'require_admin',lambda *a,**kw:None)
    source=tmp_path/'source.mp4';source.touch()
    monkeypatch.setattr(video_processing,'media_path',lambda *a:source)
    def fail(settings):raise failure
    monkeypatch.setattr(video_processing,'prepare_preview',fail)
    response=TestClient(app).post('/api/v1/generator/process-video',json={'file':'h3_mobile/source.mp4'})
    assert response.status_code==status
    detail=response.json()['detail']
    assert expected in detail and 'diagnostic ID' in detail and 'Original preserved' in detail
    assert 'private-' not in detail
