"""Runpod lifecycle using the same Pocket setup bundle as the Vast worker."""
from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.app.config import get_settings
from backend.app.providers.runpod import client
from backend.app.store import QueueStore
from runtime.h3 import managed_worker as shared


def preflight():
    client()
    for path in (shared.SSH_KEY, Path(str(shared.SSH_KEY) + ".pub"), shared.BUNDLE,
                 get_settings().huggingface_token_file, get_settings().token_file):
        if not path.is_file():
            raise RuntimeError(f"Missing required private file: {path.name}")
    shared.validate_bundle_manifest(community_path=get_settings().community_lora_manifest)


def destroy(record, reason):
    if record.get("destroyed_at"):
        return
    pod_id = record["instance_id"]
    provider = client()
    if provider.get_instance(pod_id) is not None:
        provider.destroy_instance(pod_id)
    for _ in range(12):
        if provider.get_instance(pod_id) is None:
            current = shared.read_state()
            if current.get("instance_id") == pod_id:
                shared.update(status="destroyed", destroyed_at=shared.now().isoformat(),
                              destroy_reason=reason, ssh_host=None, ssh_port=None)
            shared.log(f"Runpod {pod_id} destroyed: {reason}")
            return
        time.sleep(5)
    raise RuntimeError("Runpod still reports this Pod after deletion; check the Runpod console")


def watchdog(pod_id, deadline):
    while shared.now() < datetime.fromisoformat(deadline):
        time.sleep(min(30, max(0, (datetime.fromisoformat(deadline) - shared.now()).total_seconds())))
    for attempt in range(5):
        try:
            # Bound to the original Pod even if the operator has since rented another.
            destroy({"instance_id": pod_id}, "Watchdog maximum lifetime")
            return
        except Exception:
            if attempt == 4:
                raise
            time.sleep(15)


def launch(record):
    provider = client()
    tunnel = None
    pod_id = None
    create_attempted = False
    try:
        preflight()
        settings = get_settings()
        if record["profile"] not in shared.PROFILES:
            raise RuntimeError("Unsupported H3 profile")
        minutes = int(record["max_minutes"])
        if not 15 <= minutes <= min(180, settings.max_instance_lifetime_hours * 60):
            raise RuntimeError("Invalid rental lifetime")
        maximum = min(float(record["max_price_per_hour"]), settings.max_gpu_price_per_hour)
        selected = next((item for item in provider.offers(maximum)
                         if item["id"] == record["offer_id"]), None)
        if not selected:
            raise RuntimeError("Selected Secure Cloud GPU is no longer available within the price ceiling")
        existing = provider.list_instances()
        # Fail closed on unknown account costs, including stopped Pods with storage charges.
        rates = [float(item["costPerHr"]) for item in existing]
        if shared.KEY.is_file():
            vast_instances = shared.vast().show_instances() or []
            rates += [float(item["dph_total"]) for item in vast_instances]
        if len(rates) >= settings.max_active_gpus or sum(rates) + maximum > settings.max_total_price_per_hour:
            raise RuntimeError("Fleet count or hourly budget would be exceeded")
        name = "tensor-master-h3-" + uuid.uuid4().hex[:12]
        shared.update(status="creating", request_name=name, error=None)
        create_attempted = True
        try:
            pod = provider.create_instance(selected["id"], name=name,
                public_key=Path(str(shared.SSH_KEY) + ".pub").read_text(encoding="utf-8"))
        except Exception:
            # A timed-out create may have succeeded. Never issue a second POST.
            matches = [item for item in provider.list_instances() if item.get("name") == name]
            if len(matches) != 1:
                shared.update(status="creation_unknown", error="Check Runpod console for " + name + "; do not retry rental blindly")
                raise
            pod = matches[0]
        pod_id = str(pod["id"])
        started = shared.now()
        deadline = (started + timedelta(minutes=minutes)).isoformat()
        record = shared.update(instance_id=pod_id, provider="runpod", worker_id=f"runpod-{pod_id}",
            status="booting", rented_at=started.isoformat(), deadline=deadline,
            gpu=selected["gpu"], price_per_hour=maximum)
        with shared.LOG.open("a", encoding="utf-8") as output:
            subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "watchdog",
                              "--pod-id", pod_id, "--deadline", deadline,
                              "--state-file", str(shared.STATE)],
                             stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        actual_price = float(pod["costPerHr"])
        if not 0 < actual_price <= maximum:
            raise RuntimeError("Allocated Pod price is missing or exceeds the approved ceiling")
        record = shared.update(price_per_hour=actual_price)
        shared.log(f"Runpod {pod_id} rented at ${actual_price:.3f}/hour; public PyTorch image")
        while shared.now() < datetime.fromisoformat(deadline):
            pod = provider.get_instance(pod_id)
            if pod is None:
                raise RuntimeError("Runpod Pod disappeared during startup")
            port = (pod.get("portMappings") or {}).get("22")
            if pod.get("publicIp") and port:
                record = shared.update(ssh_host=pod["publicIp"], ssh_port=int(port), status="ssh_connecting")
                try:
                    shared.run_ssh(record, "true", timeout=20, logfile=shared.LOG)
                    record = shared.update(ssh_ready_at=shared.now().isoformat())
                    break
                except (RuntimeError, subprocess.TimeoutExpired):
                    pass
            time.sleep(10)
        else:
            raise TimeoutError("Runpod did not reach SSH before the deadline")
        for path, name in [(shared.BUNDLE, "h3-cloud-runtime.tar.gz"),
            (ROOT / "runtime/h3/remote_prepare.sh", "remote_prepare.sh"),
            (ROOT / "runtime/h3/remote_sync.sh", "remote_sync.sh"),
            (ROOT / "runtime/h3/remote_start.sh", "remote_start.sh"),
            (get_settings().huggingface_token_file, "hf_read.txt")] + shared.credential_files(record):
            shared.transfer(record, path, name)
        shared.run_ssh(record, "chmod 600 /workspace/hf_read.txt /workspace/tensor-master-token /workspace/tensor-pocket-token", timeout=60, logfile=shared.LOG)
        shared.provision_prompt_key(record)
        shared.update(status="preparing")
        shared.run_ssh(record, f"H3_PROFILE={shlex.quote(record['profile'])} bash /workspace/remote_prepare.sh", timeout=1800,
                       logfile=shared.phase_log("prepare"))
        shared.update(status="syncing_models", runtime_ready_at=shared.now().isoformat())
        shared.run_ssh(record, f"H3_PROFILE={record['profile']} bash /workspace/remote_sync.sh", timeout=2700,
                       logfile=shared.phase_log("models"))
        shared.update(status="starting", models_ready_at=shared.now().isoformat())
        tunnel = shared.start_tunnel(record)
        time.sleep(2)
        if tunnel.poll() is not None:
            raise RuntimeError("Reverse SSH tunnel could not start")
        worker_id = f"runpod-{pod_id}"
        shared.run_ssh(record, f"H3_PROFILE={record['profile']} TENSOR_WORKER_ID={worker_id} "
            f"TENSOR_WORKER_PROVIDER=Runpod TENSOR_WORKER_PRICE_PER_HOUR={actual_price:.6f} "
            "bash /workspace/remote_start.sh", timeout=120, logfile=shared.LOG)
        shared.update(status="waiting_for_worker", comfy_started_at=shared.now().isoformat())
        queue = QueueStore(settings.database_url)
        for _ in range(60):
            if any(item["id"] == worker_id and item["status"] != "offline" for item in queue.snapshot()["workers"]):
                shared.update(status="ready", worker_ready_at=shared.now().isoformat())
                break
            if tunnel.poll() is not None:
                tunnel = shared.reconnect_tunnel(record)
            time.sleep(5)
        else:
            raise TimeoutError("Runpod worker did not register")
        idle_since = shared.now()
        while shared.now() < datetime.fromisoformat(deadline):
            if shared.read_state().get("destroyed_at"):
                return
            if tunnel.poll() is not None:
                tunnel = shared.reconnect_tunnel(record)
            try:
                shared.capture_remote_logs(record)
                shared.ensure_remote_services(record)
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                pass
            busy = any(item["id"] == worker_id and item["status"] == "busy" for item in queue.snapshot()["workers"])
            if busy or queue.pending_for_runtime(f"pocket-h3-cloud-{record['profile']}"):
                idle_since = shared.now()
            elif (shared.now() - idle_since).total_seconds() >= settings.idle_destroy_minutes * 60:
                destroy(shared.read_state(), "Idle limit reached")
                return
            time.sleep(30)
        destroy(shared.read_state(), "Maximum lifetime reached")
    except Exception as exc:
        shared.log(f"Runpod worker failed: {type(exc).__name__}: {exc}")
        if pod_id:
            try:
                destroy({"instance_id": pod_id}, "Setup or connection failed")
            except Exception as cleanup_error:  # noqa: BLE001 - retain the original error and flag cleanup for the operator
                shared.log(f"Runpod cleanup failed: {type(cleanup_error).__name__}")
                shared.update(status="destroy_failed", error="Check Runpod console; cleanup not confirmed")
        elif create_attempted:
            shared.update(status="creation_unknown", error="Creation outcome uncertain; check Runpod console before retrying")
        else:
            shared.update(status="failed", error=str(exc)[:300])
        raise
    finally:
        if tunnel and tunnel.poll() is None:
            tunnel.terminate()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["launch", "destroy", "watchdog"])
    parser.add_argument("--pod-id")
    parser.add_argument("--deadline")
    parser.add_argument("--state-file")
    args = parser.parse_args()
    if args.state_file:
        shared.configure_state(args.state_file)
    if args.action == "watchdog":
        watchdog(args.pod_id, args.deadline)
    elif args.action == "destroy":
        destroy(shared.read_state(), "Operator requested")
    else:
        from backend.process_lock import process_lock
        with process_lock(shared.STATE.with_suffix('.manager.lock')) as owned:
            if owned:
                launch(shared.read_state())
