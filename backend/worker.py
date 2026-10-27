"""Local Pocket H3 worker: leases jobs and runs them in the existing ComfyUI."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx
from websockets.exceptions import ConnectionClosed, WebSocketException
from websockets.sync.client import connect

from backend.app.config import get_settings
from backend.app.h3_media import h3_input_paths
from backend.worker_recovery import Checkpoint, LocalModelLoadGate, RuntimeLogMonitor, execution_timings, runtime_memory_fault, windows_memory_headroom


class ControllerUnavailable(RuntimeError):
    pass


def release_local_model_cache(comfy, *, pressure_only=False):
    # /free is processed between prompts by ComfyUI, never mid-sampling.
    # Reset node/model caches too: UnloadAllModels alone retains their handles.
    try:
        if pressure_only:
            commit, physical = windows_memory_headroom()
            if commit >= 36 and physical >= 12:
                return False  # Keep useful caches while the host has capacity.
        comfy.post('/free', json={'unload_models': True, 'free_memory': True}).raise_for_status()
        return True
    except (httpx.HTTPError, OSError) as error:
        print(f'Local model cache cleanup unavailable: {type(error).__name__}', flush=True)
        return False  # A saved render must still be returned to its owner.


def h3_peak_commit_budget(graph):
    # Audit: 26.6 GiB peak for the local INT8 stack on small renders. Budget
    # 32 GiB plus two decoded-frame buffers for larger duration/resolution.
    duration = float(graph.get('133', {}).get('inputs', {}).get('value', 5))
    megapixels = float(graph.get('162', {}).get('inputs', {}).get('megapixels', 1))
    frames = max(5, round(duration * 24))
    frames += (5 - frames % 17) % 17
    return 32 + 2 * megapixels * 1_000_000 * frames * 3 * 4 / 1024**3


def controller_request(api, method, path, **kwargs):
    response = api.request(method, path, **kwargs)
    response.raise_for_status()
    return response


def retry_transfer(operation, report):
    deadline = time.monotonic() + 15 * 60
    while True:
        try:
            return operation()
        except httpx.HTTPError as error:
            if isinstance(error, httpx.HTTPStatusError) and error.response.status_code < 500 and error.response.status_code not in {408, 429}:
                raise
            if time.monotonic() >= deadline:
                raise ControllerUnavailable('Transfer will resume when the controller reconnects') from error
            report(message='Waiting for controller connection; render is preserved')
            time.sleep(5)


@contextmanager
def optional_websocket(url):
    try:
        socket = connect(url, open_timeout=10)
    except (OSError, TimeoutError, WebSocketException):
        socket = None
    try:
        yield socket
    finally:
        if socket is not None:
            socket.close()


def architecture_for(gpu_name: str) -> str:
    if "5090" in gpu_name or "blackwell" in gpu_name.lower():
        return "blackwell"
    if "4090" in gpu_name or "ada" in gpu_name.lower():
        return "ada"
    return "ampere"


def find_artifact(history_entry: dict) -> dict | None:
    for key in ("gifs", "videos", "images", "audio"):
        for output in history_entry.get("outputs", {}).values():
            for item in output.get(key, []):
                if isinstance(item, dict) and item.get("filename"):
                    return {
                        "filename": item["filename"],
                        "subfolder": item.get("subfolder", ""),
                        "type": item.get("type", "output"),
                        "kind": key,
                    }
    return None


def stage_cloud_inputs(api: httpx.Client, task: dict, input_root: Path,
                       keepalive: Callable[[], None] | None = None, every: float = 20.0) -> None:
    """Download the task's inputs. Large inputs (e.g. a 160 MB ProRes for RIFE) can take longer than the 90 s task
    lease over a slow tunnel; `keepalive` is called every `every` seconds so the controller does not requeue the task."""
    root = input_root.resolve()
    last = time.monotonic()
    for asset in sorted(h3_input_paths(task["graph"])):
        destination = (root / asset).resolve()
        if not destination.is_relative_to(root):
            raise ValueError("Input path escapes ComfyUI input folder")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with api.stream("GET", f"/api/v1/tasks/{task['task_id']}/inputs/{asset}",
                        headers={"X-Task-Lease": task["lease_token"]}) as response:
            response.raise_for_status()
            with destination.open("wb") as output:
                for chunk in response.iter_bytes():
                    output.write(chunk)
                    if keepalive is not None and time.monotonic() - last >= every:
                        keepalive()
                        last = time.monotonic()


def return_cloud_output(api: httpx.Client, task: dict, artifact: dict,
                        output_root: Path) -> dict:
    root = output_root.resolve()
    filename = Path(artifact["filename"]).name
    path = (root / artifact.get("subfolder", "") / filename).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise FileNotFoundError("ComfyUI output file is unavailable")
    with path.open("rb") as stream:
        response = api.put(f"/api/v1/tasks/{task['task_id']}/output/{filename}",
                           headers={"X-Task-Lease": task["lease_token"]},
                           content=stream)
    response.raise_for_status()
    artifact["url"] = response.json()["url"]
    return artifact


def normalize_cloud_model_paths(graph: dict) -> None:
    """Preserve model subfolders while translating Windows selectors for Linux ComfyUI."""
    for node in graph.values():
        for key, value in node.get("inputs", {}).items():
            if key in {"lora_name", "gate_file", "unet_name", "clip_name", "vae_name"} and isinstance(value, str):
                node["inputs"][key] = value.replace("\\", "/")


def publish_reference_caches(api, task, entry, output_root, report):
    """Return reusable intermediate images to the controller, including from cloud GPUs."""
    expected = set()
    for node in task['graph'].values():
        if node.get('class_type') != 'TMPrepareReference' or not node['inputs'].get('use_cache'):
            continue
        inputs = node['inputs']
        for key, suffix, enabled in (('isolation_key', 'rgba', inputs['isolate']),
                                    ('depth_key', 'depth', inputs['depth']), ('cache_key', 'rgb', True)):
            if enabled and inputs.get(key):
                expected.add(f'{inputs[key]}-{suffix}.png')
    seen = set()
    for output in entry.get('outputs', {}).values():
        for asset in output.get('reference_cache', []):
            filename = asset.get('filename')
            if filename not in expected or filename in seen:
                continue
            seen.add(filename)
            path = (Path(output_root) / asset.get('subfolder', '') / filename).resolve()
            if not path.is_relative_to(Path(output_root).resolve()) or not path.is_file():
                raise ValueError('Preprocessing cache is outside the worker output directory')
            def upload_cache(path=path, name=filename):
                with path.open('rb') as stream:
                    return controller_request(api, 'PUT', f"/api/v1/tasks/{task['task_id']}/reference-cache/{name}",
                        content=stream, headers={'X-Task-Lease': task['lease_token']})
            retry_transfer(upload_cache, report)


def run_task(api: httpx.Client, comfy: httpx.Client, task: dict, checkpoint: Checkpoint | None = None, local_gpu: int | None = None) -> None:
    task_id = task["task_id"]
    lease_token = task["lease_token"]
    client_id = uuid4().hex
    accepted_at: float | None = None
    first_progress_at: float | None = None
    last_progress_at: float | None = None
    cloud_mode = os.getenv("TENSOR_WORKER_PROVIDER", "Local") != "Local"
    if cloud_mode:
        # ComfyUI paths selected on Windows must address the same subfolders on Linux pods.
        normalize_cloud_model_paths(task["graph"])
    last_control_check = 0.0
    restarted = bool(task.get('prompt_id'))
    health_failures = 0
    log_path = (Path(__file__).resolve().parents[1] / 'state' / f'comfy-gpu{local_gpu}.err.log'
                if local_gpu else Path(os.getenv('COMFY_LOG_FILE', '/workspace/h3-comfy.log')))
    monitor = RuntimeLogMonitor(log_path, task.get('runtime_log_offset'))
    task['runtime_log_offset'] = monitor.offset
    local_h3 = (os.name == 'nt' and local_gpu and not cloud_mode
                and any(node.get('class_type', '').startswith('MiniMaxH3') for node in task['graph'].values()))
    load_gate = (LocalModelLoadGate(log_path.with_name('local-h3-model-loading.lock'),
                 runtime_port=8187 + local_gpu, peak_commit_gb=h3_peak_commit_budget(task['graph']))
                 if local_h3 and not task.get('prompt_id') else None)

    def report(
        status: str = "running",
        progress: int | None = None,
        message: str = "",
        prompt_id: str | None = None,
        artifact: dict | None = None,
        wait_for_controller: bool = False,
    ) -> None:
        payload = {
                "lease_token": lease_token,
                "status": status,
                "progress": progress,
                "message": message,
                "prompt_id": prompt_id,
                "artifact": artifact,
            }
        terminal = status in {'completed', 'failed', 'cancelled'}
        if checkpoint is not None and terminal:
            task['pending_report'] = payload
            checkpoint.save(task)
        deadline = time.monotonic() + 15 * 60
        while True:
            try:
                controller_request(api, 'POST', f'/api/v1/tasks/{task_id}', json=payload)
                if checkpoint is not None and terminal:
                    checkpoint.clear()
                return
            except (httpx.HTTPError, OSError) as error:
                if isinstance(error, httpx.HTTPStatusError) and error.response.status_code < 500 and error.response.status_code not in {408, 429}:
                    raise
                if not terminal and not wait_for_controller:
                    return  # Keep rendering; the next progress/heartbeat retries.
                if time.monotonic() >= deadline:
                    raise ControllerUnavailable('Output receipt saved; waiting for the controller to reconnect') from error
                time.sleep(5)

    try:
        if load_gate is not None:
            while not load_gate.ready():
                report(progress=1, message=load_gate.message, wait_for_controller=True)
                control = controller_request(api, 'GET', f'/api/v1/tasks/{task_id}/control',
                                             headers={'X-Task-Lease': lease_token})
                if control.json().get('cancel_requested'):
                    report(status='cancelled', message='Cancelled while waiting for model-loading memory')
                    return
                time.sleep(5)
        if cloud_mode and not task.get('prompt_id'):
            report(progress=1, message="Fetching Pocket input files")
            retry_transfer(lambda: stage_cloud_inputs(api, task, Path(os.getenv("COMFY_INPUT_DIR", "/opt/ComfyUI/input")),
                                                      keepalive=lambda: report(progress=1, message="Fetching Pocket input files")),
                           report)
        websocket_url = (
            str(comfy.base_url)
            .rstrip("/")
            .replace("http://", "ws://")
            .replace("https://", "wss://")
        )
        with optional_websocket(f"{websocket_url}/ws?clientId={client_id}") as ws:
            # Reserve and persist the ID before submission. After a crash,
            # inspect Comfy history/queue before deciding to submit it again.
            prompt_id = task.get('prompt_id') or str(uuid5(NAMESPACE_URL, task_id + lease_token))
            task['prompt_id'] = prompt_id
            if checkpoint is not None:
                checkpoint.save(task)
            # Register the reserved prompt before Comfy accepts it. A lost
            # controller connection cannot make the queue assign it elsewhere.
            report(progress=1, message='Reserving ComfyUI prompt', prompt_id=prompt_id, wait_for_controller=True)
            boot_id = comfy.get('/h3-mobile/status').json().get('boot_id')
            task['comfy_boot_id'] = boot_id
            if checkpoint is not None:
                checkpoint.save(task)
            history = comfy.get(f'/history/{prompt_id}').json()
            queue = comfy.get('/queue').json()
            already_accepted = prompt_id in history or any(
                item[1] == prompt_id for key in ('queue_running', 'queue_pending') for item in queue.get(key, []))
            response = (httpx.Response(200, json={'prompt_id': prompt_id}) if already_accepted else
                        comfy.post('/prompt', json={'prompt': task['graph'], 'client_id': client_id, 'prompt_id': prompt_id}))
            if response.is_error:
                try:
                    rejection = response.json()
                    first = next(iter(rejection.get("node_errors", {}).items()), None)
                    errors = (first[1].get("errors") or [{}]) if first else [{}]
                    node_detail = f" node {first[0]}: {errors[0].get('details', '')}" if first else ""
                    reason = rejection.get("error", {}).get("message", response.reason_phrase)
                    raise RuntimeError(f"ComfyUI rejected workflow: {reason};{node_detail}")
                except (ValueError, TypeError, AttributeError):
                    response.raise_for_status()
            result = response.json()
            if result.get("error") or not result.get("prompt_id"):
                raise RuntimeError(f"ComfyUI rejected the graph: {result.get('error') or result}")
            prompt_id = result["prompt_id"]
            accepted_at = time.monotonic()
            report(progress=5, message="Accepted by ComfyUI", prompt_id=prompt_id)
            started = time.monotonic()
            last_report = started
            websocket_active = ws is not None
            while time.monotonic() - started < 60 * 60 * 3:
                if time.monotonic() - last_control_check >= 2:
                    last_control_check = time.monotonic()
                    try:
                        control = controller_request(api, 'GET', f'/api/v1/tasks/{task_id}/control',
                                                     headers={'X-Task-Lease': lease_token})
                    except httpx.TransportError:
                        control = None
                    except httpx.HTTPStatusError as error:
                        if error.response.status_code < 500:
                            raise
                        control = None
                    if control is not None and control.json().get("cancel_requested"):
                        # Delete a queued prompt when possible; interrupt handles
                        # the prompt that is already executing.
                        comfy.post("/queue", json={"delete": [prompt_id]})
                        comfy.post("/interrupt")
                        report(status="cancelled", message="Cancelled by operator", prompt_id=prompt_id)
                        return
                try:
                    message = ws.recv(timeout=1) if websocket_active else None
                except TimeoutError:
                    message = None
                except ConnectionClosed:
                    websocket_active = False
                    message = None
                if not websocket_active:
                    time.sleep(1)
                if isinstance(message, str):
                    event = json.loads(message)
                    data = event.get("data", {})
                    if data.get("prompt_id") == prompt_id:
                        if event.get("type") == "progress":
                            progress_node = task['graph'].get(str(data.get('node')), {})
                            if (load_gate is not None and int(data.get('value') or 0) > 0
                                    and progress_node.get('class_type') in {'SamplerCustomAdvanced', 'KSampler', 'KSamplerAdvanced'}):
                                load_gate.release()
                            if first_progress_at is None:
                                first_progress_at = time.monotonic()
                            last_progress_at = time.monotonic()
                            maximum = max(int(data.get("max") or 1), 1)
                            value = int(data.get("value") or 0)
                            report(
                                progress=min(95, 10 + round(85 * value / maximum)),
                                message=f"Sampling {value}/{maximum}",
                            )
                        elif event.get("type") == "execution_error":
                            raise RuntimeError(
                                str(data.get("exception_message") or "ComfyUI execution error")
                            )
                if time.monotonic() - last_report >= 15:
                    fault = monitor.check()
                    task['runtime_log_offset'] = monitor.offset
                    if fault:
                        if local_gpu:
                            log_path.with_name(f'gpu{local_gpu}.drain').touch()
                        raise RuntimeError(f'{fault} Use Restart worker & requeue in Tasks.')
                    try:
                        history_response = comfy.get(f'/history/{prompt_id}')
                        history_response.raise_for_status()
                        entry = history_response.json().get(prompt_id)
                        if entry is None:
                            try:
                                comfy.get('/system_stats').raise_for_status()
                                health_failures = 0
                            except httpx.HTTPError:
                                health_failures += 1
                                if health_failures >= 3:
                                    if local_gpu:
                                        log_path.with_name(f'gpu{local_gpu}.drain').touch()
                                    raise RuntimeError('Inference runtime health failed three consecutive checks. Use Restart worker & requeue in Tasks.')
                            status_response = comfy.get('/h3-mobile/status')
                            status_response.raise_for_status()
                            current_boot = status_response.json().get('boot_id')
                            if boot_id and current_boot and current_boot != boot_id:
                                restarted = True
                                first_progress_at = last_progress_at = None
                                queue_response = comfy.get('/queue')
                                queue_response.raise_for_status()
                                queues = queue_response.json()
                                still_queued = any(item[1] == prompt_id for key in ('queue_running', 'queue_pending') for item in queues.get(key, []))
                                if not still_queued:
                                    resubmitted = comfy.post('/prompt', json={'prompt': task['graph'], 'client_id': client_id, 'prompt_id': prompt_id})
                                    resubmitted.raise_for_status()
                                    if resubmitted.json().get('prompt_id') != prompt_id:
                                        raise RuntimeError('Generator did not confirm the resumed prompt')
                                    report(progress=5, message='Generator restarted; resuming the same task', prompt_id=prompt_id)
                                boot_id = current_boot
                                task['comfy_boot_id'] = boot_id
                                if checkpoint is not None:
                                    checkpoint.save(task)
                    except httpx.HTTPError:
                        health_failures += 1
                        if health_failures >= 3:
                            if local_gpu:
                                log_path.with_name(f'gpu{local_gpu}.drain').touch()
                            raise RuntimeError('Inference runtime is unresponsive after three checks. Use Restart worker & requeue in Tasks.')
                        report(message='Generator is reconnecting; task is preserved', prompt_id=prompt_id)
                        last_report = time.monotonic()
                        continue
                    if entry is not None:
                        status = entry.get("status", {})
                        if status.get("status_str") == "error":
                            raise RuntimeError("ComfyUI reported an execution error")
                        publish_reference_caches(api, task, entry, Path(os.getenv('COMFY_OUTPUT_DIR', str(get_settings().comfyui_input_dir.parent / 'output'))), report)
                        artifact = find_artifact(entry)
                        if artifact is None:
                            raise RuntimeError("ComfyUI finished without a saved output")
                        finished_at = time.monotonic()
                        if cloud_mode:
                            report(progress=97, message="Returning rendered output")
                            artifact = retry_transfer(lambda result=artifact: return_cloud_output(
                                api, task, result, Path(os.getenv("COMFY_OUTPUT_DIR", "/opt/ComfyUI/output"))), report)
                        measured = {
                            "queue_seconds": round(float(task.get("queue_seconds", 0)), 1),
                            "generation_seconds": round(finished_at - accepted_at, 1),
                            "before_sampling_seconds": (
                                round(first_progress_at - accepted_at, 1)
                                if first_progress_at is not None else None
                            ),
                            "sampling_seconds": (
                                round(last_progress_at - first_progress_at, 1)
                                if first_progress_at is not None and last_progress_at is not None
                                else None
                            ),
                            "after_sampling_seconds": (
                                round(finished_at - last_progress_at, 1)
                                if last_progress_at is not None else None
                            ),
                        }
                        artifact['timings'] = execution_timings(entry, measured['queue_seconds'],
                            finished_at - accepted_at, measured['before_sampling_seconds'],
                            measured['sampling_seconds'], measured['after_sampling_seconds'], restarted)
                        if local_h3:
                            release_local_model_cache(comfy, pressure_only=True)
                        report(
                            status="completed",
                            progress=100,
                            message="Output ready",
                            prompt_id=prompt_id,
                            artifact=artifact,
                        )
                        return
                    report(message="ComfyUI is still processing", prompt_id=prompt_id)
                    last_report = time.monotonic()
            raise TimeoutError("ComfyUI job exceeded the three-hour limit")
    except ControllerUnavailable as exc:
        print(str(exc), flush=True)
    except Exception as exc:  # noqa: BLE001 - worker boundary must report every task failure
        fault = runtime_memory_fault(exc)
        memory_fault = bool(fault) or any(text in str(exc).lower() for text in ('out of memory', 'cuda memory failure',
                          'resource exhaustion', 'runtime health failed', 'runtime is unresponsive'))
        if memory_fault and local_gpu:
            log_path.with_name(f'gpu{local_gpu}.drain').touch()
        message = f'{fault} Use Restart worker & requeue in Tasks. Details: {exc}' if fault else str(exc)
        report(status="failed", message=message[:1900])
        if memory_fault:
            controller_request(api, 'POST', f"/api/v1/workers/{os.getenv('TENSOR_WORKER_ID', 'local-3090')}/heartbeat",
                               json={'state': 'unhealthy'})
    finally:
        if load_gate is not None:
            load_gate.release()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--local-gpu', type=int, choices=(1, 2))
    args = parser.parse_args()
    drain_file = Path(__file__).resolve().parents[1] / 'state' / f'gpu{args.local_gpu}.drain'
    settings = get_settings()
    master_url = os.getenv("TENSOR_MASTER_URL", "http://127.0.0.1:8000")
    worker_id = os.getenv("TENSOR_WORKER_ID", "local-3090")
    provider = os.getenv("TENSOR_WORKER_PROVIDER", "Local")
    runtime = os.getenv("TENSOR_WORKER_RUNTIME", "pocket-h3-local")
    worker_name = os.getenv("TENSOR_WORKER_NAME", "Home workstation" if provider == "Local" else "Vast H3 worker")
    hourly_price = float(os.getenv("TENSOR_WORKER_PRICE_PER_HOUR", "0"))
    checkpoint = Checkpoint(Path(os.getenv('TENSOR_WORKER_STATE_DIR', str(settings.token_file.parent / 'workers'))) / f'{worker_id}.json')
    credential_path = checkpoint.path.parent / (hashlib.sha256(worker_id.encode()).hexdigest() + '.credential')
    token = (credential_path if credential_path.exists() else settings.token_file).read_text(encoding='ascii').strip()
    with (
        httpx.Client(
            base_url=master_url, headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(connect=10, read=300, write=300, pool=30),
        ) as api,
        httpx.Client(base_url=settings.comfyui_url, timeout=20) as comfy,
    ):
        def register():
            stats = comfy.get('/system_stats').json()
            device = stats['devices'][0]
            gpu_name = device['name']
            # A node is advertised only after its pinned weights are installed.
            capabilities = []
            try:
                info = comfy.get('/object_info/TMPrepareReference').json()
                if 'TMPrepareReference' in info:
                    root = Path(os.getenv('COMFY_INPUT_DIR', str(settings.comfyui_input_dir))).parent / 'models' / 'tensor-master-preprocessors'
                    if all((root / name / 'model.safetensors').is_file() for name in
                           ('grounding-dino-tiny', 'sam2.1-hiera-tiny', 'depth-anything-v2-small')):
                        capabilities.append('reference-preprocess-v1')
            except (httpx.HTTPError, ValueError):
                pass
            registration = controller_request(api, 'POST', '/api/v1/workers/register', json={
                'worker_id': worker_id, 'name': worker_name, 'provider': provider,
                'gpu': gpu_name, 'architecture': architecture_for(gpu_name),
                'vram_gb': round(device['vram_total'] / 1024**3), 'runtime': runtime,
                'price_per_hour': hourly_price,
                'capabilities': capabilities,
            })
            issued = registration.json().get('worker_token')
            if issued:
                credential_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = credential_path.with_suffix('.tmp')
                descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(descriptor, 'w') as stream:
                    stream.write(issued)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, credential_path)
                api.headers['Authorization'] = 'Bearer ' + issued

        while True:
            try:
                register()
                break
            except (httpx.HTTPError, KeyError, IndexError) as exc:
                print(f"Waiting for Tensor Master and ComfyUI: {exc}", flush=True)
                time.sleep(5)
        print(f"Worker {worker_id} registered", flush=True)
        while True:
            try:
                saved = checkpoint.load()
                if saved:
                    if saved.get('pending_report'):
                        try:
                            controller_request(api, 'POST', f"/api/v1/tasks/{saved['task_id']}", json=saved['pending_report'])
                        except httpx.HTTPStatusError as error:
                            # 403: the lease expired and the controller requeued the task; 409: it was reassigned or
                            # ended. The saved report can never be accepted, so stop retrying it and take new work.
                            if error.response.status_code not in {403, 409}:
                                raise
                            checkpoint.archive()
                            print(f"Saved report for {saved['task_id']} was refused (HTTP {error.response.status_code}); "
                                  'the task was requeued, dropping it.', flush=True)
                            continue
                        checkpoint.clear()
                    else:
                        try:
                            controller_request(api, 'POST', f'/api/v1/workers/{worker_id}/resume',
                                               json={'task_id': saved['task_id'], 'lease_token': saved['lease_token']})
                        except httpx.HTTPStatusError as error:
                            if error.response.status_code != 409:
                                raise
                            checkpoint.archive()
                            print('Saved task was already reassigned or ended; not submitting another render.', flush=True)
                            continue
                        print(f"Resuming accepted task {saved['task_id']}", flush=True)
                        run_task(api, comfy, saved, checkpoint, args.local_gpu)
                    continue
                try:      # never claim while ComfyUI is down/restarting - the job would just fail
                    comfy.get('/system_stats').raise_for_status()
                except httpx.HTTPError as exc:
                    controller_request(api, 'POST', f'/api/v1/workers/{worker_id}/heartbeat', json={'state':'unhealthy'})
                    print(f'ComfyUI not reachable, not claiming work: {exc}', flush=True)
                    time.sleep(5)
                    continue
                if args.local_gpu and drain_file.exists():
                    controller_request(api, 'POST', f'/api/v1/workers/{worker_id}/heartbeat', json={'state':'draining'})
                    time.sleep(3)
                    continue
                controller_request(api, 'POST', f'/api/v1/workers/{worker_id}/heartbeat', json={'state':'ready'})
                response = controller_request(api, 'POST', f'/api/v1/workers/{worker_id}/claim')
                task = response.json()["task"]
                if task is not None:
                    print(f"Running {task['task_id']}", flush=True)
                    checkpoint.save(task)
                    run_task(api, comfy, task, checkpoint, args.local_gpu)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    try:
                        register()
                    except httpx.HTTPError:
                        pass
                elif exc.response.status_code == 409 and checkpoint.load():
                    checkpoint.archive()
                print(f'Worker reconnecting: HTTP {exc.response.status_code}', flush=True)
            except (httpx.HTTPError, KeyError) as exc:
                print(f"Worker connection error: {exc}", flush=True)
            time.sleep(3)


if __name__ == "__main__":
    main()
