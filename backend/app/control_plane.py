from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from threading import Lock
from uuid import uuid4


@dataclass
class Worker:
    id: str
    name: str
    provider: str
    gpu: str
    architecture: str
    vram_gb: int
    price_per_hour: float
    status: str = "ready"
    current_task_id: str | None = None


@dataclass
class Task:
    id: str
    job_id: str
    number: int
    status: str = "queued"
    worker_id: str | None = None
    progress: int = 0
    artifact: str | None = None


@dataclass
class Job:
    id: str
    name: str
    workflow: str
    policy: str
    min_vram_gb: int
    architecture: str
    created_at: str
    tasks: list[Task] = field(default_factory=list)


class ControlPlane:
    """First product slice of the scheduler behind the web UI."""

    def __init__(self) -> None:
        self._lock = Lock()
        self.events: list[dict[str, str]] = []
        self.workers: dict[str, Worker] = {
            "home-3090": Worker(
                id="home-3090",
                name="Home workstation",
                provider="Local",
                gpu="RTX 3090",
                architecture="ampere",
                vram_gb=24,
                price_per_hour=0,
            )
        }
        self.jobs: dict[str, Job] = {}

    def snapshot(self) -> dict:
        with self._lock:
            workers = [self._worker_dict(worker) for worker in self.workers.values()]
            jobs = [self._job_dict(job) for job in reversed(self.jobs.values())]
            tasks = [task for job in self.jobs.values() for task in job.tasks]
            task_rows = []
            for job in reversed(self.jobs.values()):
                for task in job.tasks:
                    row = asdict(task)
                    row.update(
                        {
                            "job_name": job.name,
                            "workflow": job.workflow,
                            "requirement": f"{job.min_vram_gb} GB+ / {job.architecture}",
                        }
                    )
                    task_rows.append(row)
            return {
                "workers": workers,
                "jobs": jobs,
                "tasks": task_rows,
                "events": list(reversed(self.events[-30:])),
                "metrics": {
                    "active_workers": len(workers),
                    "burn_rate": sum(worker.price_per_hour for worker in self.workers.values()),
                    "queued": sum(task.status == "queued" for task in tasks),
                    "running": sum(task.status == "running" for task in tasks),
                    "completed": sum(task.status == "completed" for task in tasks),
                },
            }

    def submit_job(
        self,
        *,
        name: str,
        workflow: str,
        policy: str,
        task_count: int,
        min_vram_gb: int,
        architecture: str,
    ) -> Job:
        with self._lock:
            job_id = f"job-{uuid4().hex[:8]}"
            job = Job(
                id=job_id,
                name=name.strip() or "Untitled render",
                workflow=workflow,
                policy=policy,
                min_vram_gb=min_vram_gb,
                architecture=architecture,
                created_at=datetime.now(UTC).isoformat(),
                tasks=[
                    Task(id=f"{job_id}-t{n:03}", job_id=job_id, number=n)
                    for n in range(1, task_count + 1)
                ],
            )
            self.jobs[job.id] = job
            self._event("job", f"Submitted {job.name} with {task_count} tasks")
            self._schedule()
            return job

    def add_demo_worker(self, gpu: str) -> Worker:
        profiles = {
            "rtx-5090": ("RTX 5090", "blackwell", 32, 0.78, "Vast.ai"),
            "rtx-6000": ("RTX 6000 Blackwell", "blackwell", 96, 1.62, "DigitalOcean"),
            "rtx-4090": ("RTX 4090", "ada", 24, 0.54, "Vast.ai"),
        }
        if gpu not in profiles:
            raise ValueError("Unknown worker profile")
        with self._lock:
            model, architecture, vram, price, provider = profiles[gpu]
            worker = Worker(
                id=f"demo-{gpu}-{uuid4().hex[:4]}",
                name=f"{provider} worker",
                provider=provider,
                gpu=model,
                architecture=architecture,
                vram_gb=vram,
                price_per_hour=price,
            )
            self.workers[worker.id] = worker
            self._event("worker", f"{worker.gpu} registered from {worker.provider}")
            self._schedule()
            return worker

    def complete_task(self, worker_id: str) -> None:
        with self._lock:
            worker = self.workers.get(worker_id)
            if not worker or not worker.current_task_id:
                return
            for job in self.jobs.values():
                for task in job.tasks:
                    if task.id == worker.current_task_id:
                        task.status = "completed"
                        task.progress = 100
                        task.artifact = f"outputs/{task.job_id}/clip-{task.number:03}.mp4"
                        self._event("output", f"{task.id} completed on {worker.gpu}")
            worker.current_task_id = None
            worker.status = "ready"
            self._schedule()

    def drain_worker(self, worker_id: str) -> None:
        with self._lock:
            worker = self.workers.get(worker_id)
            if not worker or worker.provider == "Local":
                return
            if worker.current_task_id:
                worker.status = "draining"
                self._event("worker", f"{worker.gpu} will drain after its current task")
            else:
                self._event("worker", f"{worker.gpu} was removed from the fleet")
                del self.workers[worker_id]

    def _schedule(self) -> None:
        for worker in self.workers.values():
            if worker.status != "ready" or worker.current_task_id:
                continue
            task = self._next_compatible_task(worker)
            if task:
                task.status = "running"
                task.progress = 37
                task.worker_id = worker.id
                worker.status = "busy"
                worker.current_task_id = task.id
                self._event("lease", f"{task.id} leased to {worker.gpu}")

    def _next_compatible_task(self, worker: Worker) -> Task | None:
        for job in self.jobs.values():
            if worker.vram_gb < job.min_vram_gb:
                continue
            if job.architecture != "any" and worker.architecture != job.architecture:
                continue
            for task in job.tasks:
                if task.status == "queued":
                    return task
        return None

    @staticmethod
    def _job_dict(job: Job) -> dict:
        data = asdict(job)
        counts = {state: 0 for state in ("queued", "running", "completed", "failed")}
        for task in job.tasks:
            counts[task.status] += 1
        data["counts"] = counts
        data["progress"] = round(100 * counts["completed"] / len(job.tasks)) if job.tasks else 0
        return data

    @staticmethod
    def _worker_dict(worker: Worker) -> dict:
        data = asdict(worker)
        if worker.status == "busy":
            data.update(
                {"gpu_load": 94, "vram_used": round(worker.vram_gb * 0.88, 1), "temperature": 68}
            )
        else:
            data.update({"gpu_load": 2, "vram_used": 1.1, "temperature": 39})
        data["endpoint"] = "LAN" if worker.provider == "Local" else "Provider tunnel"
        return data

    def _event(self, kind: str, message: str) -> None:
        self.events.append(
            {
                "time": datetime.now(UTC).strftime("%H:%M:%S"),
                "kind": kind,
                "message": message,
            }
        )


control_plane = ControlPlane()
