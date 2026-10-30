import json

from runtime.h3.sync_models import SyncProgress
from backend.app.rental_ui import model_sync_progress


def test_progress_counts_actual_bytes_and_verifies_separately():
    clock = [0.0]
    events = []
    items = [dict(file="first.bin", size=100), dict(file="second.bin", size=100)]
    progress = SyncProgress(items, clock=lambda: clock[0], emit=events.append)
    progress.begin(items[0])
    assert events[-1]["eta_seconds"] is None
    clock[0] = 2
    progress.report(50)
    assert events[-1]["bytes"] == 50 and events[-1]["eta_seconds"] == 6
    progress.report(100, stage="verifying", force=True)
    assert events[-1]["files_ready"] == 0
    progress.finish()
    progress.begin(items[1])
    assert events[-1]["bytes"] == 100 and events[-1]["files_ready"] == 1
    progress.report(999, stage="verifying", force=True)
    progress.finish()
    assert events[-1]["bytes"] == 200 and events[-1]["stage"] == "ready"


def test_tqdm_download_hook_emits_updates():
    events = []
    clock = [0.0]
    progress = SyncProgress([dict(file="model.bin", size=100)], clock=lambda: clock[0], emit=events.append)
    progress.begin(dict(file="model.bin", size=100))
    with progress.tqdm_class()(total=100) as bar:
        clock[0] = 3
        bar.update(25)
    assert events[-1]["bytes"] == 25


def test_progress_parser_ignores_partial_and_invalid_log_lines(tmp_path):
    path = tmp_path / "rental-example.json"
    event = dict(event="sync_progress", total_bytes=1024**3, bytes=512*1024**2,
                 bytes_per_second=10000000, eta_seconds=54, file="models/weights.bin",
                 files_ready=1, files_total=3, stage="downloading")
    path.with_name("rental-example-models.log").write_text(
        json.dumps(event) + '\n' + '{"event": "sync_progress", "bytes":', encoding="utf-8")
    result = model_sync_progress(path)
    assert result["percent"] == 50 and result["downloaded_gib"] == .5
    assert result["file"] == "weights.bin" and result["eta_minutes"] == 1


def test_failed_history_is_collapsed_by_default():
    from pathlib import Path
    markup = Path("backend/app/templates/dashboard.html").read_text(encoding="utf-8")
    assert '<details class="rental-failures">' in markup
    assert '<details class="rental-failures" open>' not in markup


def test_public_download_uses_no_token_when_unconfigured(monkeypatch,tmp_path):
    import huggingface_hub
    from runtime.h3.sync_models import download_pinned_file
    monkeypatch.delenv('HF_TOKEN',raising=False)
    calls=[]
    monkeypatch.setattr(huggingface_hub,'hf_hub_download',lambda **kwargs:(calls.append(kwargs) or str(tmp_path/'model.bin')))
    item=dict(repo='public/model',file='model.bin',revision='a'*40,local_dir='models',size=1)
    progress=SyncProgress([item],emit=lambda event:None)
    assert download_pinned_file(item,tmp_path,progress)==tmp_path/'model.bin'
    assert calls[0]['token'] is False
    assert calls[0]['revision']=='a'*40
