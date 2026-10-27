"""Download only the pinned Hugging Face weights for one Pocket H3 profile."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path


LOCK_FILE = Path(__file__).with_name("models.lock.json")
def selected_artifacts(profile: str) -> list[dict]:
    manifest = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    groups = set(manifest["profiles"][profile])
    return [item for item in manifest["artifacts"] if item["group"] in groups]


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
    total = sum(item["size"] for item in artifacts)
    print(f"{args.profile or 'all profiles'}: {len(artifacts)} files, {total / 1024**3:.1f} GiB")
    if args.list:
        for item in artifacts:
            print(f"{item['repo']} -> {target_path(args.models_dir, item)}")
        return

    from huggingface_hub import hf_hub_download

    for item in artifacts:
        started = time.monotonic()
        destination = target_path(args.models_dir, item)
        cached = destination.is_file() and destination.stat().st_size == item["size"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        print(f"Syncing {item['repo']}/{item['file']}", flush=True)
        downloaded = Path(
            hf_hub_download(
                repo_id=item["repo"],
                filename=item["file"],
                revision=item["revision"],
                local_dir=(args.models_dir / item["local_dir"]).resolve(),
            )
        )
        if downloaded.resolve() != destination:
            raise RuntimeError(f"Downloaded model did not match the lock file: {item['file']}")
        verify_artifact(destination, item)
        elapsed = time.monotonic() - started
        print(json.dumps({"event": "model_ready", "file": item["file"], "bytes": item["size"],
                          "seconds": round(elapsed, 2), "cached_before": cached,
                          "effective_MBps": None if cached else round(item["size"] / max(elapsed, .001) / 1e6, 2)}), flush=True)


if __name__ == "__main__":
    main()
