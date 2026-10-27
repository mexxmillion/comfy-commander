"""Durable worker receipts allow restarts without forgetting accepted prompts."""
import json
import os
import time
import ctypes
from pathlib import Path


def windows_memory_headroom():
    class Status(ctypes.Structure):
        _fields_ = [('length', ctypes.c_uint32), ('load', ctypes.c_uint32)] + [
            (name, ctypes.c_uint64) for name in ('total_phys', 'available_phys',
                'total_commit', 'available_commit', 'total_virtual', 'available_virtual', 'extended')]
    value = Status()
    value.length = ctypes.sizeof(value)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value)):
        raise OSError('Cannot read Windows memory headroom; model loading was not started.')
    return value.available_commit / 1024**3, value.available_phys / 1024**3


def runtime_private_commit_gb(port):
    """Read retained commit, including Windows GPU backing, without double budgeting it."""
    import psutil
    try:
        owners = {item.pid for item in psutil.net_connections(kind='tcp')
                  if item.status == psutil.CONN_LISTEN and item.laddr.port == port and item.pid}
        if len(owners) == 1:
            return psutil.Process(owners.pop()).memory_info().private / 1024**3
    except (psutil.Error, OSError, AttributeError):
        pass
    return 0  # Unavailable telemetry receives no retained-memory credit.


class LocalModelLoadGate:
    """Serialize local H3 staging and reserve commit headroom before each load.

    The lock is released at the first sampling progress event, so sufficiently
    provisioned GPUs can still sample concurrently. Closing the process releases
    its lock automatically.
    """
    def __init__(self, path, read_memory=windows_memory_headroom, *, runtime_port=None, peak_commit_gb=36):
        self.path = path
        self.read_memory = read_memory
        self.owner = None
        self.runtime_port = runtime_port
        self.peak_commit_gb = peak_commit_gb
        self.message = 'Waiting for the other GPU to finish loading models'

    def ready(self):
        if self.owner is None:
            from backend.process_lock import process_lock
            owner = process_lock(self.path)
            if not owner.__enter__():
                owner.__exit__(None, None, None)
                return False
            self.owner = owner
        retained = runtime_private_commit_gb(self.runtime_port) if self.runtime_port else 0
        required = max(8, self.peak_commit_gb - retained)
        commit, physical = self.read_memory()
        if commit < required or physical < 8:
            threshold = f'{required:.1f}'.removesuffix('.0')
            self.message = (f'Waiting for Windows memory: commit {commit:.1f}/{threshold} GB, '
                            f'RAM {physical:.1f}/8 GB free. Waiting for memory to be released by other work or apps.')
            return False
        return True

    def release(self):
        if self.owner is not None:
            self.owner.__exit__(None, None, None)
            self.owner = None


def runtime_memory_fault(message):
    text = str(message).lower()
    if any(pattern in text for pattern in ('error=1450', 'hostbuf_file_reader_read failed',
                                           'hostbuffer.read_file_slice failed')):
        return 'Windows model-streaming resource exhaustion; restart the affected worker before retrying.'
    if any(pattern in text for pattern in ('cuda error: out of memory', 'cuda out of memory',
                                           'cuda error: an illegal memory access')):
        return 'CUDA memory failure in the inference runtime.'
    return None


class RuntimeLogMonitor:
    """Read new runtime output without blaming new jobs for historical OOMs."""
    def __init__(self, path, offset=None):
        self.path = Path(path)
        self.offset = offset if offset is not None else self.path.stat().st_size if self.path.exists() else 0

    def check(self):
        if not self.path.exists():
            return None
        size = self.path.stat().st_size
        if size < self.offset:
            self.offset = 0
        with self.path.open('rb') as stream:
            stream.seek(max(self.offset, size - 65536))
            text = stream.read().decode('utf-8', errors='replace').lower()
        self.offset = size
        return runtime_memory_fault(text)


def execution_timings(entry, queue_seconds, elapsed, before=None, sampling=None, after=None, restarted=False):
    stamps = {kind: data.get('timestamp') for kind, data in entry.get('status', {}).get('messages', [])
              if isinstance(data, dict) and kind in {'execution_start', 'execution_success'}}
    start, end = stamps.get('execution_start'), stamps.get('execution_success')
    runtime = (end - start) / 1000 if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end >= start else None
    return dict(queue_seconds=round(float(queue_seconds), 1),
                generation_seconds=round(runtime if runtime is not None else elapsed, 1),
                worker_elapsed_seconds=round(elapsed, 1),
                recovery_seconds=round(max(0, elapsed - runtime), 1) if restarted and runtime is not None else None,
                before_sampling_seconds=None if restarted else before,
                sampling_seconds=None if restarted else sampling,
                after_sampling_seconds=None if restarted else after)


class Checkpoint:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict | None:
        try:
            return json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return None

    def save(self, task: dict):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.partial')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(task, stream)
        os.replace(temporary, self.path)

    def clear(self):
        self.path.unlink(missing_ok=True)

    def archive(self):
        # Preserve the receipt for inspection if an operator reassigned a task.
        if self.path.exists():
            self.path.replace(self.path.with_suffix(f'.ended-{time.time_ns()}.json'))
