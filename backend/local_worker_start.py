"""Reconnect a local worker to this installation, retaining its task checkpoint."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import psutil

from backend.app.config import get_settings
from backend.process_lock import process_lock
from backend.service_start import ROOT, start


def local_endpoint(value):
    parsed = urlsplit(value)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.username:
        raise RuntimeError("Local worker startup requires a configured localhost ComfyUI address.")
    return parsed.port or 80


def worker_owner(worker_id, comfy_url, controller):
    port = local_endpoint(comfy_url)
    for process in psutil.process_iter(["cmdline"]):
        try:
            command = process.info.get("cmdline") or []
            if "backend.worker" not in command:
                continue
            env = process.environ()
            try:
                same_comfy = local_endpoint(env.get("COMFYUI_URL", "http://127.0.0.1:8188")) == port
            except RuntimeError:
                continue
            if not same_comfy:
                continue
            if (Path(process.cwd()).resolve() == ROOT
                    and env.get("TENSOR_WORKER_ID", "local-3090") == worker_id
                    and env.get("TENSOR_MASTER_URL", "http://127.0.0.1:8000").rstrip("/") == controller):
                return process.pid
            raise RuntimeError("Another worker is connected to this ComfyUI, possibly through another dispatcher. Drain/stop that worker before reconnecting; this launcher never interrupts its render or starts a competing worker.")
        except (psutil.Error, OSError):
            continue
    return None


def connect():
    settings = get_settings()
    local_endpoint(settings.comfyui_url)
    config_file = ROOT / "state/local-worker-start.json"
    config = json.loads(config_file.read_text(encoding="utf-8")) if config_file.is_file() else {}
    worker_id = config.get("worker_id", "local-gpu-1")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", worker_id):
        raise RuntimeError("Invalid saved local worker identity.")
    controller = "http://127.0.0.1:8000"
    start(controller, 8000)
    with process_lock(ROOT / "state/local-worker-start.lock") as owned:
        if not owned:
            raise RuntimeError("A local worker connection is already starting.")
        existing = worker_owner(worker_id, settings.comfyui_url, controller)
        if existing:
            return worker_id, existing, False
        try:
            httpx.get(settings.comfyui_url.rstrip("/") + "/system_stats", timeout=3).raise_for_status()
        except httpx.HTTPError:
            raise RuntimeError("Start your configured ComfyUI first, then double-click Start Local Worker.cmd. Settings > Compute shows missing models and nodes.") from None
        if not settings.token_file.is_file():
            raise RuntimeError("Complete Commander's local setup first. Credentials are read from its saved credential directory.")
        env = os.environ.copy()
        env.update(TENSOR_MASTER_URL=controller, TENSOR_WORKER_ID=worker_id,
                   TENSOR_WORKER_NAME=str(config.get("name", "Local GPU")),
                   TENSOR_WORKER_PROVIDER="Local", TENSOR_WORKER_RUNTIME="pocket-h3-local",
                   TENSOR_WORKER_STATE_DIR=str(ROOT / "state/local-workers"),
                   COMFYUI_URL=settings.comfyui_url, PYTHONUNBUFFERED="1")
        with (ROOT / "state/community-worker.out.log").open("ab") as out, (ROOT / "state/community-worker.err.log").open("ab") as err:
            process = subprocess.Popen([sys.executable, "-m", "backend.worker", "--local-gpu", "1"],
                                       cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                       start_new_session=os.name != "nt")
        return worker_id, process.pid, True


def main():
    try:
        worker_id, pid, started = connect()
    except (RuntimeError, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(f"Local worker {'started' if started else 'already running'}: {worker_id} (process {pid})")
    print("Check Dispatcher > Workers for its heartbeat. Saved task checkpoints are retained.")
    if (ROOT / "state/gpu1.drain").exists():
        print("This worker is paused/draining. Resume it in Dispatcher when ready for queued work.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
