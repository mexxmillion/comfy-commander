"""One cost-bounded, manually selected Vast H3 worker for Tensor Master."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from vastai import VastAI

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "state" / "managed-rental.json"
LOG = ROOT / "state" / "managed-rental.log"
BUNDLE = ROOT / "state" / "h3-cloud-runtime.tar.gz"
IMAGE = "pytorch/pytorch:2.10.0-cuda13.0-cudnn9-runtime"
PROFILES = {"ref2v-int8", "ref2v-singularity", "fasth3-omni", "flf2v-int8", "qwen-image-21"}


def settings():
    sys.path.insert(0, str(ROOT))
    from backend.app.config import get_settings
    return get_settings()


KEY = settings().vast_api_key_file
SSH_KEY = settings().cloud_ssh_key_file
SSH_PUBLIC_KEY = Path(str(SSH_KEY) + ".pub")


def validate_ssh_key_files() -> None:
    if not SSH_KEY.is_file() or not SSH_PUBLIC_KEY.is_file():
        raise RuntimeError(
            "Cloud worker SSH key is not configured. Open Setup & Models â†’ Compute â†’ "
            "Cloud SSH access and create or configure your key pair. No GPU rental was started."
        )


def configure_state(path: str | Path) -> None:
    """Bind this manager process to one rental's isolated state and logs."""
    global STATE, LOG
    STATE = Path(path).resolve()
    LOG = STATE.with_suffix(".log")


def phase_log(name: str) -> Path:
    return STATE.with_name(f"{STATE.stem}-{name}.log")


def now() -> datetime:
    return datetime.now(UTC)


def read_state() -> dict:
    return json.loads(STATE.read_text(encoding="utf-8"))


def write_state(record: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE.with_suffix(".partial")
    temporary.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    temporary.replace(STATE)


def update(**values: object) -> dict:
    record = read_state()
    if record.get("destroyed_at") and values.get("status") != "destroyed":
        raise RuntimeError("Rental has already been destroyed")
    record.update(values)
    write_state(record)
    return record


def log(message: str) -> None:
    line = f"{now().isoformat()} {message}\n"
    with LOG.open("a", encoding="utf-8") as stream:
        stream.write(line)


def vast_api_key() -> str:
    from backend.app.credential_paths import provider_secret
    return provider_secret("vast", settings().vast_api_key_file)


def vast() -> VastAI:
    return VastAI(api_key=vast_api_key(), raw=True)


MAX_DOWNLOAD_COST_PER_GB = 0.01   # default for callers without settings; see Settings.max_download_cost_per_gb


def eligible_offer(offer: dict, *, offer_id: int, max_price: float,
                   max_download_cost: float = MAX_DOWNLOAD_COST_PER_GB, require_datacenter: bool = True,
                   min_inet_down: float = 2000) -> bool:
    try:
        return (int(offer["id"]) == offer_id
                and (offer.get("datacenter") is True or not require_datacenter)
                and float(offer.get("cuda_max_good") or 0) >= 13
                and float(offer.get("inet_down") or 0) >= min_inet_down
                and int(offer.get("direct_port_count") or 0) >= 1
                and float(offer["dph_total"]) <= max_price
                and float(offer.get("reliability") or 0) >= 0.98
                and float(offer.get("inet_down_cost") or 0) <= max_download_cost)
    except (KeyError, TypeError, ValueError):
        return False


def first_hour_cost(offer, profile: str = "ref2v-int8") -> float:
    """Model-sync download + one hour of GPU: what the worker bills before it is worth anything."""
    sys.path.insert(0, str(ROOT))
    from backend.app.rental_cost import vast_estimate
    return vast_estimate(offer.raw, profile)["first_hour_cost"]


def select_rental_offer(candidates: list, *, requested_id: int, max_price: float,
                        max_download_cost: float = MAX_DOWNLOAD_COST_PER_GB, profile: str = "ref2v-int8"):
    """Prefer the picked listing, then the cheapest equivalent safe listing.

    Vast listing IDs can disappear between the picker response and instance
    creation. The operator has already approved the profile and price ceiling,
    so a same-class fallback avoids failing before billing starts. "Cheapest"
    includes the per-GB model download, not only the hourly rate.
    """
    eligible = [offer for offer in candidates if eligible_offer(
        offer.raw, offer_id=offer.id, max_price=max_price, max_download_cost=max_download_cost)]
    selected = next((offer for offer in eligible if offer.id == requested_id), None)
    return selected or (min(eligible, key=lambda offer: first_hour_cost(offer, profile)) if eligible else None)


def ranked_rental_offers(candidates: list, *, requested_id: int, max_price: float,
                         max_download_cost: float = MAX_DOWNLOAD_COST_PER_GB, profile: str = "ref2v-int8",
                         require_datacenter: bool = True, min_inet_down: float = 2000) -> list:
    # require_datacenter=False only when the operator picked a specific non-datacenter host (rental record flag)
    eligible = [offer for offer in candidates if eligible_offer(
        offer.raw, offer_id=offer.id, max_price=max_price, max_download_cost=max_download_cost,
        require_datacenter=require_datacenter, min_inet_down=min_inet_down)]
    return sorted(eligible, key=lambda offer: (
        offer.id != requested_id, first_hour_cost(offer, profile)))


def instance_problem(instance_id: int) -> str | None:
    """Why a rented Vast instance can no longer work (stopped, exited, gone), or None while it is running.

    Vast stops instances on its own (e.g. when account credit runs out) and they keep billing for disk until destroyed.
    """
    try:
        instance = vast().show_instance(id=int(instance_id))
    except Exception:  # noqa: BLE001 - an API hiccup is not proof the instance died
        return None
    if instance is None:
        return "Vast no longer lists the instance"
    actual, intended = str(instance.get("actual_status") or ""), str(instance.get("intended_status") or "")
    if intended == "stopped" or actual in {"exited", "stopped", "offline"}:
        return f"Vast reports the instance {actual or intended} (intended: {intended or 'unknown'})"
    return None


def check_credit(price_per_hour: float, profile: str) -> None:
    """Refuse to rent when the Vast balance cannot even pay for the model sync plus a short run.

    With no credit, Vast stops the instance mid-sync, after the download has already been billed.
    """
    sys.path.insert(0, str(ROOT))
    from backend.app.config import get_settings
    from backend.app.rental_cost import profile_sync_gb
    settings = get_settings()
    user = vast().show_user() or {}
    if "credit" not in user:
        return
    credit = float(user.get("credit") or 0)
    needed = profile_sync_gb(profile) * settings.max_download_cost_per_gb + price_per_hour * settings.min_credit_gpu_hours
    if credit < needed:
        raise RuntimeError(f"Vast credit ${credit:.2f} is below the ${needed:.2f} needed for the model sync and "
                           f"{settings.min_credit_gpu_hours:g} h of GPU; add credit at console.vast.ai/billing")


def validate_bundle_manifest(bundle: Path | None = None, manifest_path: Path | None = None,
                             community_path: Path | None = None) -> None:
    """Reject stale model payloads before either provider creates a paid instance."""
    bundle = bundle or BUNDLE
    manifest_path = manifest_path or ROOT / 'runtime/h3/models.lock.json'
    expected = json.loads(manifest_path.read_text(encoding='utf-8'))
    community_path = community_path or ROOT / 'state/community-loras.json'
    expected_community = (json.loads(community_path.read_text(encoding='utf-8'))
                          if community_path.is_file() else None)
    with tarfile.open(bundle, 'r:gz') as archive:
        member = next((item for item in archive.getmembers()
                       if item.name.replace('\\', '/').removeprefix('./') == 'h3-runtime/models.lock.json'), None)
        if member is None:
            raise RuntimeError('Cloud runtime bundle has no model manifest; rebuild with build_local.ps1 -BundleOnly before renting')
        stream = archive.extractfile(member)
        if stream is None:
            raise RuntimeError('Cloud runtime bundle model manifest is not a file')
        with stream:
            bundled = json.load(stream)
        community_member = next((item for item in archive.getmembers()
                                 if item.name.replace('\\', '/').removeprefix('./') ==
                                 'h3-runtime/community-loras.json'), None)
        bundled_community = None
        if community_member is not None:
            community_stream = archive.extractfile(community_member)
            if community_stream is None:
                raise RuntimeError('Cloud runtime community LoRA manifest is not a file')
            with community_stream:
                bundled_community = json.load(community_stream)
    if bundled != expected:
        raise RuntimeError('Cloud runtime bundle model manifest is stale; rebuild with build_local.ps1 -BundleOnly before renting')
    if bundled_community != expected_community:
        raise RuntimeError('Cloud runtime bundle community LoRAs are stale; rebuild with build_local.ps1 -BundleOnly before renting')


def validate_startup_scripts(runtime: Path | None = None) -> None:
    """Parse the exact normalized Linux scripts before allocating paid capacity."""
    runtime = runtime or ROOT / "runtime/h3"
    git = shutil.which("git")
    git_bash = Path(git).resolve().parents[1] / "bin/bash.exe" if git else None
    bash = str(git_bash) if git_bash and git_bash.is_file() else (shutil.which("bash") if os.name != "nt" else None)
    if not bash:
        raise RuntimeError("Linux startup validation requires Bash (included with Git for Windows). No GPU was allocated.")
    for name in ("remote_prepare.sh", "remote_sync.sh", "remote_start.sh"):
        payload = normalized_script(runtime / name)
        result = subprocess.run([bash, "-n"], input=payload, capture_output=True, timeout=15,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
        if result.returncode:
            raise RuntimeError(f"Linux startup syntax validation failed for {name}; no GPU was allocated")


def preflight() -> None:
    configured = settings()
    from backend.app.credential_paths import provider_secret
    if not provider_secret("vast", configured.vast_api_key_file):
        raise RuntimeError("Missing Vast API key")
    if not provider_secret("huggingface", configured.huggingface_token_file):
        raise RuntimeError("Missing Hugging Face token")
    validate_ssh_key_files()
    from backend.app.cloud_ssh import vast_public_key_registered
    if not vast_public_key_registered(vast(), SSH_PUBLIC_KEY.read_text(encoding='utf-8')):
        raise RuntimeError('Worker SSH key is not registered with Vast. Open Settings → Connections → Vast Validate & prepare cloud access. No GPU rental was started.')
    for path in (BUNDLE,):
        if not path.is_file():
            raise RuntimeError(f"Missing required private file: {path.name}")
    validate_bundle_manifest(community_path=configured.community_lora_manifest)
    sys.path.insert(0, str(ROOT))
    from backend.app.auth import ensure_token
    from backend.app.config import get_settings
    ensure_token(get_settings().token_file)
    with httpx.Client(timeout=5) as api:
        api.get("http://127.0.0.1:8000/health").raise_for_status()


def ssh_bootstrap_command() -> str:
    public_key = SSH_PUBLIC_KEY.read_text(encoding="utf-8").strip()
    if not public_key.startswith(("ssh-ed25519 ", "ssh-rsa ", "ecdsa-sha2-")):
        raise RuntimeError("Invalid Tensor Master SSH public key")
    return ("umask 077; mkdir -p /root/.ssh; "
            f"printf '%s\\n' {shlex.quote(public_key)} >> /root/.ssh/authorized_keys; "
            "sleep 3; chown -R root:root /root/.ssh; "
            "chmod 700 /root /root/.ssh; chmod 600 /root/.ssh/authorized_keys; "
            "echo tensor-master-ready")


def ssh_arguments(record: dict) -> list[str]:
    return ["-i", str(SSH_KEY), "-p", str(record["ssh_port"]),
            "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ServerAliveInterval=30", f"root@{record['ssh_host']}"]


class SSHTransportError(RuntimeError):
    pass


def run_ssh(record: dict, command: str, *, timeout: int, logfile: Path) -> None:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with logfile.open("a", encoding="utf-8") as output:
        result = subprocess.run(["ssh", *ssh_arguments(record), command], stdout=output,
                                stderr=subprocess.STDOUT, timeout=timeout,
                                creationflags=flags, check=False)
    if result.returncode:
        if result.returncode == 255:
            raise SSHTransportError(f'SSH connection interrupted; see {logfile.name}')
        tail = logfile.read_text(encoding="utf-8", errors="replace")[-4000:]
        if "pipefail" in tail and "invalid option name" in tail:
            raise RuntimeError("Commander startup script has Windows line endings; Linux rejected pipefail. "
                               f"Deployment bug; see {logfile.name}")
        raise RuntimeError(f"Remote {command.split()[0]} failed; see {logfile.name}")


def run_remote_phase(record: dict, command: str, *, timeout: int, logfile: Path) -> None:
    """Keep setup alive across SSH resets; reconnect to the same bounded instance."""
    phase = 'models' if logfile == phase_log('models') else 'prepare'
    base = f'/workspace/commander-{phase}'
    # flock prevents duplicate setup processes if the start acknowledgement is lost.
    runner = (f'echo $$ > {base}.pid; {command} > {base}.log 2>&1; '
              f'code=$?; printf "%s\\n" "$code" > {base}.exit.tmp; '
              f'mv {base}.exit.tmp {base}.exit')
    start = (f'command -v flock >/dev/null || exit 78; '
             f'if test -f {base}.exit || (test -f {base}.pid && kill -0 "$(cat {base}.pid)" 2>/dev/null); '
             f'then :; else nohup flock -n {base}.lock bash -c {shlex.quote(runner)} '
             f'</dev/null >/dev/null 2>&1 & fi')
    end = min(time.monotonic() + timeout,
              time.monotonic() + max(0, (datetime.fromisoformat(record['deadline']) - now()).total_seconds()))
    started, offset, disconnected = False, 1, False
    outage_started = None
    download_retries, retry_started = 0, None
    while time.monotonic() < end:
        if read_state().get('destroyed_at'):
            raise RuntimeError('Rental terminated while setup was in progress')
        try:
            if not started:
                run_ssh(record, start, timeout=20, logfile=LOG)
                started = True
            poll = (f'if test -f {base}.log; then tail -c +{offset} {base}.log; fi; '
                    f'printf "\\n__COMMANDER_EXIT__"; '
                    f'if test -f {base}.exit; then cat {base}.exit; else echo RUNNING; fi')
            result = subprocess.run(['ssh', *ssh_arguments(record), poll], capture_output=True,
                                    timeout=20, check=False,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:
                raise ConnectionError('SSH transport unavailable')
            payload, marker, status = result.stdout.rpartition(b'\n__COMMANDER_EXIT__')
            if not marker:
                raise ConnectionError('Incomplete setup status response')
            if payload:
                with logfile.open('ab') as output:
                    output.write(payload)
                offset += len(payload)
            if disconnected:
                log(f'{phase}: SSH reconnected; continuing on the same instance')
                disconnected = False
            outage_started = None
            status = status.strip()
            if status == b'0':
                return
            if status != b'RUNNING':
                evidence = logfile.read_text(encoding='utf-8', errors='replace')[-8000:].lower()
                needs_human = any(reason in evidence for reason in ('401', '403', 'gated repo', 'checksum', 'did not match the lock'))
                transient_download = phase == 'models' or any(reason in evidence for reason in
                    ('readtimeouterror', 'connection reset', 'temporary failure', 'timed out'))
                if transient_download and not needs_human and download_retries < 2:
                    retry_started = retry_started or time.monotonic()
                    if time.monotonic() - retry_started < 120 and end - time.monotonic() > 15:
                        download_retries += 1
                        log(f'{phase} download failed; retry {download_retries}/2 on the same instance using cached partial files. Original deadline unchanged.')
                        run_ssh(record, f'rm -f {base}.exit {base}.pid', timeout=20, logfile=LOG)
                        started, offset = False, 1
                        time.sleep(10)
                        continue
                raise RuntimeError(f'Linux {phase} process exited with code {status.decode("ascii", errors="replace")[:20]}; see {logfile.name}')
        except (OSError, subprocess.TimeoutExpired, ConnectionError, SSHTransportError):
            outage_started = outage_started or time.monotonic()
            if not disconnected:
                log(f'{phase}: SSH interrupted; remote setup continues. Retrying for up to two minutes within the original deadline; no new GPU will be rented.')
            disconnected = True
            if time.monotonic() - outage_started >= 120:
                raise TimeoutError(f'{phase}: SSH reconnection failed continuously for two minutes; remote setup logs will be captured before cleanup')
        time.sleep(5)
    raise TimeoutError(f'{phase}: setup/reconnection exceeded the original rental deadline; see {logfile.name}')


def retain_for_diagnosis(record: dict, error: Exception) -> bool:
    """Hold genuine setup failures for repair; the independent watchdog bounds cost."""
    if not record.get('deadline') or record.get('destroyed_at'):
        return False
    if isinstance(error, TimeoutError) and any(reason in str(error) for reason in ('two minutes', 'original rental deadline', 'SSH never accepted')):
        return False
    if record.get('status') not in {'preparing', 'syncing_models', 'starting', 'waiting_for_worker', 'transferring', 'ssh_connecting'}:
        return False
    if now() >= datetime.fromisoformat(record['deadline']):
        return False
    update(status='needs_attention', recovery_message='Instance retained for diagnosis and same-instance repair. Still billable; original automatic termination deadline unchanged.')
    log('Setup needs attention; retaining this instance and logs for repair. No replacement rented. Original watchdog deadline remains active.')
    return True


def transfer(record: dict, local: Path, remote_name: str) -> None:
    # Editors on Windows can rewrite a shell script despite Git's eol attribute.
    # Normalize the transmitted copy; never modify the checkout or secret files.
    if remote_name.endswith(".sh"):
        payload = normalized_script(local)
        with tempfile.TemporaryDirectory(prefix="commander-script-") as directory:
            normalized = Path(directory) / local.name
            normalized.write_bytes(payload)
            _transfer_file(record, normalized, remote_name)
        return
    _transfer_file(record, local, remote_name)


def normalized_script(path: Path) -> bytes:
    payload = path.read_bytes().removeprefix(b"\xef\xbb\xbf").replace(b"\r\n", b"\n")
    if b"\r" in payload or b"\x00" in payload or not payload.startswith(b"#!/usr/bin/env bash\n"):
        raise RuntimeError(f"Invalid Linux startup script: {path.name}; no GPU was allocated")
    return payload


def _transfer_file(record: dict, local: Path, remote_name: str) -> None:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    args = ["scp", "-i", str(SSH_KEY), "-P", str(record["ssh_port"]),
            "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
            str(local), f"root@{record['ssh_host']}:/workspace/{remote_name}"]
    with LOG.open("a", encoding="utf-8") as output:
        result = subprocess.run(args, stdout=output, stderr=subprocess.STDOUT,
                                timeout=300, creationflags=flags, check=False)
    if result.returncode:
        raise RuntimeError(f"Transfer of {remote_name} failed")


def worker_ready(worker: dict, worker_id: str, profile: str) -> bool:
    return (worker.get("id") == worker_id
            and worker.get("runtime") == f"pocket-h3-cloud-{profile}"
            and worker.get("status") in {"idle", "busy", "ready"})


def transfer_secret(record: dict, value: str, remote_name: str) -> None:
    """Send a secret over SSH stdin so it never appears in args or logs."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    args = ["ssh", *ssh_arguments(record), f"umask 077; cat > /workspace/{remote_name}"]
    with LOG.open("a", encoding="utf-8") as output:
        result = subprocess.run(args, input=value + "\n", text=True, stdout=output,
                                stderr=subprocess.STDOUT, timeout=60,
                                creationflags=flags, check=False)
    if result.returncode:
        raise RuntimeError(f"Transfer of {remote_name} failed")


def start_tunnel(record: dict) -> subprocess.Popen:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with phase_log("tunnel").open("a", encoding="utf-8") as output:
        return subprocess.Popen(
            ["ssh", *ssh_arguments(record)[:-1], "-o", "ExitOnForwardFailure=yes",
             "-N", "-R", "127.0.0.1:8000:127.0.0.1:8000",
             ssh_arguments(record)[-1]],
            stdin=subprocess.DEVNULL, stdout=output, stderr=output,
            creationflags=flags,
        )


def reconnect_tunnel(record: dict) -> subprocess.Popen:
    """Reconnect a dropped tunnel, but give up (so the caller destroys the instance) once Vast says it is no longer
    running or it stays unreachable past the unresponsive limit -- previously this retried until the 3 h deadline
    while a stopped instance kept billing."""
    from backend.app.config import get_settings
    log("Reverse SSH tunnel disconnected; reconnecting without stopping ComfyUI")
    give_up = now() + timedelta(minutes=get_settings().unresponsive_destroy_minutes)
    attempt = 0
    while True:
        if read_state().get("destroyed_at") or now() >= datetime.fromisoformat(record["deadline"]):
            raise RuntimeError("Rental ended during tunnel recovery")
        problem = instance_problem(record["instance_id"]) if record.get("instance_id") else None
        if problem:
            raise RuntimeError(problem)
        if now() >= give_up:
            raise TimeoutError(f"Tunnel could not reconnect for {get_settings().unresponsive_destroy_minutes} minutes")
        tunnel = start_tunnel(record)
        time.sleep(5)
        if tunnel.poll() is None:
            log(f"Reverse SSH tunnel restored on attempt {attempt + 1}")
            return tunnel
        time.sleep(5)
        attempt += 1


def credential_files(record):
    """Provision only this worker's identity and the scoped Pocket identity."""
    import hashlib

    from backend.app.config import get_settings
    from backend.app.identity import authority
    identity = authority(get_settings().token_file)
    worker_id = record.get('worker_id') or f"{record.get('provider', 'vast')}-{record['instance_id']}"
    directory = get_settings().token_file.parent / 'provisioned'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (hashlib.sha256(worker_id.encode()).hexdigest() + '.credential')
    if path.exists():
        principal = identity.principal(path.read_text().strip())
        if not principal or principal['role'] != 'worker' or principal['subject'] != worker_id:
            raise RuntimeError('Saved worker identity was revoked; explicitly re-enroll it on the host')
    else:
        token = identity.enroll_worker(worker_id)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(token)
    identity.export()
    return [(path, 'tensor-master-token'),
            (get_settings().token_file.with_name('pocket_bridge_token'), 'tensor-pocket-token')]


def provision_prompt_key(record: dict) -> None:
    from backend.app.prompt_secret import read_key
    key = read_key()
    if not key:
        return
    result = subprocess.run(["ssh", *ssh_arguments(record),
                             "umask 077; cat > /workspace/openrouter-key"],
                            input=key.encode(), stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=30,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise RuntimeError("Could not provision the prompt-enhancement credential")
    update(prompt_enhancement=True)


def ensure_remote_services(record: dict) -> None:
    """Restart crashed processes on the same GPU; never restart a live render."""
    if record.get('profile') not in PROFILES:
        raise RuntimeError('Unsupported saved worker profile')
    worker_id = record.get('worker_id') or f"{'runpod' if record.get('provider') == 'runpod' else 'vast'}-{record['instance_id']}"
    provider = 'Runpod' if record.get('provider') == 'runpod' else 'Vast.ai'
    command = (f"H3_PROFILE={shlex.quote(record['profile'])} TENSOR_WORKER_ID={shlex.quote(worker_id)} "
               f"TENSOR_WORKER_PROVIDER={shlex.quote(provider)} TENSOR_WORKER_PRICE_PER_HOUR={float(record['price_per_hour']):.6f} "
               'bash /workspace/remote_start.sh')
    run_ssh(record, command, timeout=120, logfile=LOG)


def capture_remote_logs(record: dict) -> None:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    for name in ("worker", "comfy"):
        result = subprocess.run(
            ["ssh", *ssh_arguments(record), f"tail -c 250000 /workspace/h3-{name}.log"],
            capture_output=True, timeout=20, creationflags=flags, check=False,
        )
        if result.returncode == 0:
            phase_log(name).write_bytes(result.stdout)


def destroy_instance(record: dict, reason: str) -> None:
    instance_id = record.get("instance_id")
    if not instance_id or record.get("destroyed_at"):
        return
    instance = vast().show_instance(id=int(instance_id))
    if instance:
        result = vast().destroy_instance(id=int(instance_id))
        if isinstance(result, dict) and result.get("success") is False:
            raise RuntimeError("Vast did not confirm destruction")
        for _ in range(6):
            time.sleep(5)
            if vast().show_instance(id=int(instance_id)) is None:
                break
        else:
            raise RuntimeError("Vast still reports the instance after destroy")
    update(status="destroyed", destroyed_at=now().isoformat(), destroy_reason=reason,
           ssh_host=None, ssh_port=None)
    log(f"Instance {instance_id} destroyed: {reason}")


def launch(record: dict, *, resume_existing: bool = False) -> None:
    if resume_existing and (record.get("status") != "needs_attention" or record.get("destroyed_at") or not record.get("instance_id") or record.get("worker_ready_at")):
        raise RuntimeError("Only an existing retained setup failure can be resumed; no GPU was allocated")
    tunnel = None
    instance_created = False
    try:
        preflight()
        if record["profile"] not in PROFILES or not 15 <= int(record["max_minutes"]) <= 180:
            raise RuntimeError("Unsupported profile or lifetime")
        sys.path.insert(0, str(ROOT))
        from backend.app.config import get_settings
        from backend.app.providers.base import OfferSearch
        from backend.app.providers.vast import VastProvider
        settings = get_settings()
        if resume_existing:
            if now() >= datetime.fromisoformat(record['deadline']):
                raise RuntimeError('Original rental deadline has expired; cannot resume')
            instance_id = int(record['instance_id'])
            instance_created = True
            record = update(status='reconnecting', error=None, recovery_message=None)
            log('Resuming setup on the same retained instance; no create request, original deadline unchanged')
        else:
            maximum = min(float(record["max_price_per_hour"]), settings.max_gpu_price_per_hour)
            from backend.app.rental_cost import MIN_CLOUD_VRAM_GB, VAST_GPUS
            candidates = VastProvider(vast_api_key()).search_offers(
                OfferSearch(VAST_GPUS, MIN_CLOUD_VRAM_GB, maximum, 0.98, 150,
                            # Vast ignores id= filters; narrow by the picking filters instead so the picked host is on the page
                            extra_query=f"inet_down>={float(record.get('min_inet_down', 2000)):g} direct_port_count>=1 cuda_max_good>=13"))
            requested_offer_id = int(record["offer_id"])
            # If the picked listing vanished, only substitute the same GPU model (never a 4090 for a 5090).
            picked = next((offer for offer in candidates if offer.id == requested_offer_id), None)
            wanted_gpu = record.get("gpu_name") or (picked.gpu_name if picked else None)
            if wanted_gpu:
                candidates = [offer for offer in candidates if offer.gpu_name == wanted_gpu]
            choices = ranked_rental_offers(candidates, requested_id=requested_offer_id,
                                           max_price=maximum, max_download_cost=settings.max_download_cost_per_gb,
                                           profile=record["profile"],
                                           require_datacenter=not record.get("allow_non_datacenter", False),
                                           min_inet_down=float(record.get("min_inet_down", 2000)))
            from backend.app.rental_cost import vast_estimate
            from backend.app.rental_preferences import offer_allowed, record_preferences
            preferences = record_preferences(record)
            eligible_count = len(choices)
            choices = [offer for offer in choices if offer_allowed(
                {"gpu": offer.gpu_name, "vram_gb": offer.vram_gb, "price_per_hour": offer.price_per_hour,
                 **vast_estimate(offer.raw, record["profile"])}, preferences)]
            if record.get("exact_offer"):               # operator picked this host (e.g. for its bandwidth): no substitutes
                choices = [offer for offer in choices if offer.id == requested_offer_id]
            if not choices:
                log(f"Offer recheck: {len(candidates)} candidates; {eligible_count} meet host safeguards; 0 meet confirmed GPU/VRAM/budget filters. GPU={preferences.gpu_class}, minimum VRAM={preferences.minimum_vram_gb} GB, maximum GPU rate=${maximum:g}/hr, budget=${preferences.max_budget_usd:g}, lifetime={preferences.max_minutes} min. Selected listing present={picked is not None}.")
                raise RuntimeError("No equivalent compatible offer remains under the confirmed GPU, VRAM, hourly price and total budget filters. Refresh offers or review Settings > Rentals; no GPU was allocated. See the rental diagnostics for filter details.")
            check_credit(choices[0].price_per_hour, record["profile"])
            active = vast().show_instances() or []
            total_rate = sum(float(item.get("dph_total") or 0) for item in active)
            if len(active) >= settings.max_active_gpus:
                raise RuntimeError("Fleet count or hourly budget limit would be exceeded")
            selected = None
            result = None
            for choice in choices[:5]:
                if total_rate + choice.price_per_hour > settings.max_total_price_per_hour:
                    continue
                try:
                    attempted = vast().create_instance(
                        id=choice.id, image=IMAGE, disk=150,
                        label=f"tensor-master-h3-{choice.id}", ssh=True,
                        direct=True, cancel_unavail=True,
                        onstart_cmd=ssh_bootstrap_command())
                except Exception as exc:
                    # Retry only an unavailable offer, not billing or invalid arguments.
                    if getattr(getattr(exc, "response", None), "status_code", None) == 400:
                        error = exc.response.json()
                        detail = str(error.get("msg") or error.get("error") or "Vast rejected rental")[:500]
                        log(f"Vast rejected offer {choice.id}: {detail}")
                        if "no_such_ask" in detail:
                            continue
                        raise RuntimeError(f"Vast rental rejected: {detail}") from exc
                    raise
                if attempted.get("success") and attempted.get("new_contract"):
                    selected, result = choice, attempted
                    break
                raise RuntimeError("Vast did not confirm the rental")
            if selected is None or result is None:
                raise RuntimeError("Equivalent Vast offers disappeared before a contract could be created")
            if selected.id != requested_offer_id:
                log(f"Using equivalent offer {selected.id} at ${selected.price_per_hour:.3f}/hour instead of expired offer {requested_offer_id}")
                record = update(offer_id=selected.id, substituted_offer_id=requested_offer_id)
            instance_id = int(result["new_contract"])
            instance_created = True
            started = now()
            record = update(instance_id=instance_id, status="booting", rented_at=started.isoformat(),
                            price_per_hour=selected.price_per_hour, gpu=selected.gpu_name,
                            deadline=(started + timedelta(minutes=int(record["max_minutes"]))).isoformat())
            log(f"Rented instance {instance_id} at ${selected.price_per_hour:.3f}/hour")
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            with LOG.open("a", encoding="utf-8") as output:
                subprocess.Popen([sys.executable, str(ROOT / "runtime/h3/vast_watchdog.py"),
                                  str(instance_id), "--minutes", str(record["max_minutes"]),
                                  "--key-file", str(KEY), "--state-file", str(STATE)],
                                 stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                 creationflags=flags, close_fds=True)
        while now() < datetime.fromisoformat(record["deadline"]):
            instance = vast().show_instance(id=instance_id)
            provider_status = (instance.get('actual_status') or instance.get('cur_state')) if instance else None
            if provider_status in {'stopped', 'exited', 'offline'}:
                raise RuntimeError(f"Provider reports instance {provider_status} before SSH startup")
            if provider_status == 'loading':
                update(status='waiting_for_container', provider_stage='Container image download / build')
                time.sleep(10)
                continue
            if instance and instance.get("ssh_host") and instance.get("ssh_port"):
                record = update(status="transferring", ssh_host=instance["ssh_host"],
                                ssh_port=int(instance["ssh_port"]))
                break
            time.sleep(10)
        else:
            raise TimeoutError("Rental did not reach SSH before the deadline")
        update(status="ssh_connecting")
        ssh_deadline = min(now() + timedelta(seconds=120), datetime.fromisoformat(record['deadline']))
        while now() < ssh_deadline:
            try:
                run_ssh(record, "true", timeout=20, logfile=LOG)
                record = update(status='transferring', ssh_ready_at=now().isoformat())
                break
            except (RuntimeError, subprocess.TimeoutExpired):
                time.sleep(10)
        else:
            raise TimeoutError("SSH never accepted a connection")
        for path, remote_name in [
            (BUNDLE, "h3-cloud-runtime.tar.gz"),
            (ROOT / "runtime/h3/remote_prepare.sh", "remote_prepare.sh"),
            (ROOT / "runtime/h3/remote_sync.sh", "remote_sync.sh"),
            (ROOT / "runtime/h3/remote_start.sh", "remote_start.sh"),
        ] + credential_files(record):
            transfer(record, path, remote_name)
        from backend.app.credential_paths import provider_secret
        transfer_secret(record, provider_secret("huggingface", settings.huggingface_token_file), "hf_read.txt")
        run_ssh(record, "chmod 600 /workspace/hf_read.txt /workspace/tensor-master-token /workspace/tensor-pocket-token",
                timeout=60, logfile=LOG)
        provision_prompt_key(record)
        if resume_existing:
            run_ssh(record, 'for phase in prepare models; do base=/workspace/commander-$phase; '
                    'if test -f "$base.exit" && test "$(cat "$base.exit")" != 0; '
                    'then rm -f "$base.exit" "$base.pid"; fi; done', timeout=20, logfile=LOG)
        update(status="preparing")
        run_remote_phase(record, f"H3_PROFILE={shlex.quote(record['profile'])} bash /workspace/remote_prepare.sh", timeout=1800,
                logfile=phase_log("prepare"))
        update(status="syncing_models", runtime_ready_at=now().isoformat())
        run_remote_phase(record, f"H3_PROFILE={record['profile']} bash /workspace/remote_sync.sh",
                timeout=2700, logfile=phase_log("models"))
        update(status="starting", models_ready_at=now().isoformat())
        tunnel = start_tunnel(record)
        time.sleep(2)
        if tunnel.poll() is not None:
            raise RuntimeError("Reverse SSH tunnel could not start")
        worker_id = f"vast-{instance_id}"
        command = (f"H3_PROFILE={record['profile']} TENSOR_WORKER_ID={worker_id} "
                   f"TENSOR_WORKER_PRICE_PER_HOUR={selected.price_per_hour:.6f} "
                   "bash /workspace/remote_start.sh")
        run_ssh(record, command, timeout=120, logfile=LOG)
        update(status="waiting_for_worker", comfy_started_at=now().isoformat())
        from backend.app.store import QueueStore
        queue = QueueStore(settings.database_url)
        for _ in range(60):
            if any(worker_ready(item, worker_id, record["profile"])
                   for item in queue.snapshot()["workers"]):
                update(status="ready", worker_ready_at=now().isoformat())
                break
            if tunnel.poll() is not None:
                tunnel = reconnect_tunnel(record)
            time.sleep(5)
        else:
            raise TimeoutError("Cloud worker did not register within five minutes")
        idle_since = now()
        last_logs = now() - timedelta(seconds=31)
        unresponsive = settings.unresponsive_destroy_minutes * 60
        while now() < datetime.fromisoformat(read_state()["deadline"]):
            if read_state().get("destroyed_at"):
                break
            if tunnel.poll() is not None:
                tunnel = reconnect_tunnel(record)
            if (now() - last_logs).total_seconds() >= 30:
                problem = instance_problem(instance_id)
                if problem:
                    raise RuntimeError(problem)
                try:
                    capture_remote_logs(record)
                    ensure_remote_services(record)
                except (OSError, RuntimeError, subprocess.TimeoutExpired):
                    pass
                last_logs = now()
            snapshot = queue.snapshot()
            mine = next((item for item in snapshot["workers"] if item["id"] == worker_id), None)
            if mine is None or float(mine.get("heartbeat_age_seconds") or 0) > unresponsive:
                # The lease on any task it held expires and the task returns to the queue on its own.
                raise RuntimeError(f"Worker {worker_id} stopped heartbeating for "
                                   f"{settings.unresponsive_destroy_minutes} minutes")
            cloud_busy = any(item["id"] == worker_id and item["status"] == "busy"
                             for item in snapshot["workers"])
            if cloud_busy or queue.pending_for_runtime(f"pocket-h3-cloud-{record['profile']}"):
                idle_since = now()
            elif (now() - idle_since).total_seconds() >= settings.idle_destroy_minutes * 60:
                destroy_instance(read_state(), "Idle limit reached")
                break
            time.sleep(10)
        if not read_state().get("destroyed_at"):
            destroy_instance(read_state(), "Maximum lifetime reached")
    except Exception as exc:
        log(f"Worker setup failed: {type(exc).__name__}: {exc}")
        try:
            current = read_state()
        except (OSError, ValueError):
            current = record
        if not current.get("destroyed_at"):
            update(error=str(exc)[:1500], failed_stage=current.get("status", "startup"), failed_at=now().isoformat())
        if instance_created:
            try:
                current = read_state()
                if current.get("instance_id"):
                    if current.get("ssh_host"):
                        try:
                            capture_remote_logs(current)
                        except (OSError, RuntimeError, subprocess.TimeoutExpired):
                            pass
                    stage = "Failed after ready" if current.get("worker_ready_at") else "Setup failed"
                    if retain_for_diagnosis(current, exc):
                        return
                    destroy_instance(current, f"{stage}: {type(exc).__name__}: {str(exc)[:160]}")
                else:
                    vast().destroy_instance(id=instance_id)
            except Exception as cleanup_exc:
                update(status="destroy_failed", error=str(cleanup_exc)[:300])
                log(f"URGENT: destroy failed: {cleanup_exc}")
        else:
            update(status="failed", error=str(exc)[:300])
        raise
    finally:
        if tunnel and tunnel.poll() is None:
            tunnel.terminate()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("launch", "resume", "destroy"))
    parser.add_argument("--reason", default="Operator requested")
    parser.add_argument("--state-file")
    args = parser.parse_args()
    if args.state_file:
        configure_state(args.state_file)
    if args.action in {"launch", "resume"}:
        sys.path.insert(0, str(ROOT))
        from backend.process_lock import process_lock
        with process_lock(STATE.with_suffix('.manager.lock')) as owned:
            if owned:
                launch(read_state(), resume_existing=args.action == "resume")
    else:
        destroy_instance(read_state(), args.reason)


if __name__ == "__main__":
    main()
