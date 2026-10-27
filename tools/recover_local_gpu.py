"""Explicit operator recovery: restart one failed local runtime, then requeue its task."""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.config import get_settings
from backend.app.store import QueueStore, TaskRow
from backend.process_lock import process_lock


def recovery_target(store, task_id):
    task = store.task_execution(task_id)
    if not task or task['status'] not in {'failed', 'cancelled'}:
        raise ValueError('Only failed or cancelled tasks can restart a worker. Stop a running task first.')
    gpu = {'local-3090': 1, 'local-3090-gpu2': 2}.get(task['worker_id'])
    if not gpu:
        raise ValueError('This action supports the two local GPU workers only.')
    with Session(store.engine) as db:
        active = db.scalar(select(TaskRow).where(TaskRow.worker_id == task['worker_id'],
                          TaskRow.status.in_(['running', 'cancel_requested'])))
        if active:
            raise ValueError('This worker has another active task. Let it finish first.')
    return gpu, task


def worker_recovery_target(store, worker_id):
    gpu = {'local-3090': 1, 'local-3090-gpu2': 2}.get(worker_id)
    if not gpu:
        raise ValueError('Only the two local workers support this action.')
    worker = next((w for w in store.snapshot()['workers'] if w['id'] == worker_id), None)
    if not worker or worker['current_task_id']:
        raise ValueError('Stop the running task in Tasks before restarting this worker.')
    with Session(store.engine) as db:
        if db.scalar(select(TaskRow).where(TaskRow.worker_id == worker_id, TaskRow.status == 'cancel_requested')):
            raise ValueError('Wait for the task cancellation before restarting this worker.')
    return gpu, {'prompt_id': None}


def recovery_message(store, task_id, message):
    with Session(store.engine) as db, db.begin():
        task = db.get(TaskRow, task_id)
        if task and task.status in {'failed', 'cancelled'}:
            task.message = message
            store._event(db, 'recovery', f'{task_id}: {message}')


def main():
    parser = argparse.ArgumentParser()
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument('--task')
    target.add_argument('--gpu', type=int, choices=(1,2))
    args = parser.parse_args()
    store = QueueStore(get_settings().database_url)
    worker_id = 'local-3090' if args.gpu == 1 else 'local-3090-gpu2'
    target_info = lambda: recovery_target(store, args.task) if args.task else worker_recovery_target(store, worker_id)
    gpu, task = target_info()
    state = ROOT / 'state'
    with process_lock(state / f'gpu{gpu}-recovery.lock') as owned:
        if not owned:
            raise RuntimeError('Recovery is already running for this GPU.')
        (state / f'gpu{gpu}.drain').touch()
        time.sleep(4)  # Let an in-flight claim finish before deciding it is safe to stop.
        gpu, task = target_info()
        if args.task:
            recovery_message(store, args.task, f'Restarting GPU {gpu} through the secure launcher; saved job will be requeued when ready.')
        try:
            queue = httpx.get(f'http://127.0.0.1:{8187+gpu}/queue', timeout=5)
        except httpx.TransportError:
            queue = None
        if queue is not None and queue.is_success:
            prompts = [item[1] for key in ('queue_running', 'queue_pending') for item in queue.json().get(key, [])]
            if any(prompt != task['prompt_id'] for prompt in prompts):
                raise RuntimeError('The runtime contains another prompt; recovery left it untouched.')
        subprocess.run(['pwsh', '-NoProfile', '-NonInteractive', '-File',
                        str(ROOT / 'tools/recover_local_gpu.ps1'), '-Gpu', str(gpu)], check=True, cwd=ROOT)
        if args.task and not store.requeue_task(args.task):
            raise RuntimeError('Worker restarted, but task state changed; review it before requeuing.')
        print(json.dumps({'task': args.task, 'gpu': gpu, 'status': 'requeued' if args.task else 'ready'}), flush=True)


if __name__ == '__main__':
    main()
