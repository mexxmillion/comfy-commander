"""Download only the pinned Hugging Face weights for one Pocket H3 profile."""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import json
import re
import time
from pathlib import Path

LOCK_FILE = Path(__file__).with_name("models.lock.json")
COMMUNITY_FILE = Path(__file__).with_name("community-loras.json")
HEX_REVISION = re.compile(r"^[0-9a-fA-F]{40,64}$")
HEX_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


class SyncProgress:
    """Throttled machine-readable progress; never emit repository URLs or tokens."""

    def __init__(self, artifacts, *, clock=time.monotonic, emit=None):
        self.total = sum(item['size'] for item in artifacts)
        self.files = len(artifacts)
        self.completed = self.ready = 0
        self.clock = clock
        self.emit = emit or (lambda event: print(json.dumps(event), flush=True))
        self.last_time = clock()
        self.last_bytes = 0
        self.speed = None
        self.item = None
        self.current = 0

    def report(self, downloaded=None, *, stage='downloading', force=False):
        if downloaded is not None:
            self.current = min(self.item['size'], max(self.current, int(downloaded)))
        now = self.clock()
        elapsed = now - self.last_time
        if not force and elapsed < 2:
            return
        done = self.completed + self.current
        delta = done - self.last_bytes
        if stage == 'downloading' and elapsed > 0 and delta > 0:
            rate = delta / elapsed
            self.speed = rate if self.speed is None else .25 * rate + .75 * self.speed
        self.emit(dict(event='sync_progress', stage=stage, bytes=done, total_bytes=self.total,
                       files_ready=self.ready, files_total=self.files,
                       file=self.item['file'] if self.item else '', file_bytes=self.current,
                       file_total_bytes=self.item['size'] if self.item else 0,
                       bytes_per_second=self.speed,
                       eta_seconds=round(max(0, self.total - done) / self.speed) if self.speed else None))
        self.last_time, self.last_bytes = now, done

    def begin(self, item):
        self.item, self.current = item, 0
        self.report(stage='checking', force=True)

    def finish(self):
        self.current = self.item['size']
        self.ready += 1
        self.report(stage='ready' if self.ready == self.files else 'verified', force=True)
        self.completed += self.current
        self.current = 0

    def tqdm_class(self):
        from tqdm.auto import tqdm
        tracker = self

        class ProgressBar(tqdm):
            def __init__(self, *args, **kwargs):
                kwargs.update(file=io.StringIO(), disable=False)
                super().__init__(*args, **kwargs)
                tracker.report(self.n, stage="checking", force=True)

            def update(self, n=1):
                result = super().update(n)
                tracker.report(self.n)
                return result

        return ProgressBar


def community_artifacts(profile: str) -> list[dict]:
    if not COMMUNITY_FILE.is_file():
        return []
    data = json.loads(COMMUNITY_FILE.read_text(encoding="utf-8"))
    if data.get("version") != 1 or not isinstance(data.get("loras"), list):
        raise ValueError("Unsupported community LoRA manifest")
    selected = []
    for item in data["loras"]:
        if profile not in item.get("profiles", []):
            continue
        if (item.get("local_dir") != "loras"
                or not HEX_SHA256.fullmatch(str(item.get("sha256", "")))
                or not HEX_REVISION.fullmatch(str(item.get("revision", "")))
                or not isinstance(item.get("size"), int) or item["size"] <= 0):
            raise ValueError("Invalid community LoRA manifest entry")
        selected.append({key: item[key] for key in ("repo", "revision", "file", "local_dir", "size", "sha256")})
    return selected


def selected_artifacts(profile: str) -> list[dict]:
    manifest = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    groups = set(manifest["profiles"][profile])
    return [item for item in manifest["artifacts"] if item["group"] in groups] + community_artifacts(profile)


def target_path(models_dir: Path, artifact: dict) -> Path:
    root = models_dir.resolve()
    target = (root / artifact["local_dir"] / artifact["file"]).resolve()
    if not target.is_relative_to(root):
        raise ValueError("Model path leaves the ComfyUI models directory")
    return target


def verify_artifact(path: Path, artifact: dict) -> None:
    if path.stat().st_size != artifact["size"]:
        raise RuntimeError(f"Downloaded model did not match the lock file: {artifact['file']}")
    expected_hash = artifact.get("sha256")
    if not expected_hash:
        return
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest().lower() != expected_hash.lower():
        raise RuntimeError(f"Downloaded model checksum did not match the lock file: {artifact['file']}")


def download_pinned_file(item, models_dir, progress):
    from huggingface_hub import hf_hub_download
    from huggingface_hub.errors import HfHubHTTPError
    try:
        return Path(hf_hub_download(repo_id=item['repo'], filename=item['file'],
            revision=item['revision'], local_dir=(models_dir / item['local_dir']).resolve(),
            tqdm_class=progress.tqdm_class(), token=os.environ.get('HF_TOKEN', '').strip() or False))
    except HfHubHTTPError as error:
        if getattr(error.response, 'status_code', None) in {401, 403}:
            raise RuntimeError(f"Model access denied: {item['repo']}/{item['file']}. "
                "This file may be gated/private or the configured token lacks access. "
                "Add an authorized Hugging Face read token in Settings > Connections and retry setup on the same GPU.") from None
        raise


def main() -> None:
    manifest = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--profile", choices=manifest["profiles"])
    selection.add_argument("--all", action="store_true", help="Restore every managed Pocket model and LoRA once")
    parser.add_argument("--models-dir", type=Path, required=True)
    parser.add_argument("--list", action="store_true", help="Show files and size without downloading")
    args = parser.parse_args()

    artifacts = manifest['artifacts'] if args.all else selected_artifacts(args.profile)
    if args.all and COMMUNITY_FILE.is_file():
        dynamic = []
        for profile in manifest["profiles"]:
            dynamic.extend(community_artifacts(profile))
        known = {(item["repo"], item["revision"], item["file"]) for item in artifacts}
        for item in dynamic:
            identity = (item["repo"], item["revision"], item["file"])
            if identity not in known:
                artifacts.append(item)
                known.add(identity)
    total = sum(item["size"] for item in artifacts)
    print(f"{args.profile or 'all profiles'}: {len(artifacts)} files, {total / 1024**3:.1f} GiB")
    if args.list:
        for item in artifacts:
            print(f"{item['repo']} -> {target_path(args.models_dir, item)}")
        return

    progress = SyncProgress(artifacts)
    for item in artifacts:
        progress.begin(item)
        started = time.monotonic()
        destination = target_path(args.models_dir, item)
        cached = destination.is_file() and destination.stat().st_size == item["size"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        print(f"Syncing {item['repo']}/{item['file']}", flush=True)
        downloaded = destination if cached else download_pinned_file(item, args.models_dir, progress)
        if downloaded.resolve() != destination:
            raise RuntimeError(f"Downloaded model did not match the lock file: {item['file']}")
        progress.report(item['size'], stage='verifying', force=True)
        verify_artifact(destination, item)
        progress.finish()
        elapsed = time.monotonic() - started
        print(json.dumps({"event": "model_ready", "file": item["file"], "bytes": item["size"],
                          "seconds": round(elapsed, 2), "cached_before": cached,
                          "effective_MBps": None if cached else round(item["size"] / max(elapsed, .001) / 1e6, 2)}), flush=True)


if __name__ == "__main__":
    main()
