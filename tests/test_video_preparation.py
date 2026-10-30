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
    result = client.post(
        "/api/v1/generator/process-video", json={"file": "h3_mobile/source.mp4"}
    ).json()
    assert result["reference"]["preparation"] == {
        "source": "h3_mobile/source.mp4",
        "preview": "h3_mobile/video-prepared/fixture.mp4",
        "width": 544,
        "height": 960,
        "frame_count": 124,
    }
    assert result["graph"]["video_prepare"]["class_type"] == "TMPrepareVideo"
    assert calls[-1] == {"mutation": True}


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
