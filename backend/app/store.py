"""Durable single-task job queue with expiring worker leases."""

from __future__ import annotations

import json
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import Float, ForeignKey, Integer, String, Text, create_engine, delete, func, inspect, select, text, update
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


def now() -> datetime:
    return datetime.now(UTC)


# Task priority convention: every task carries an integer priority and
# DEFAULT_PRIORITY (100) is "medium". With HIGHER_PRIORITY_FIRST = True a
# larger number is picked up sooner (1000 runs before 100; 1 is the lowest).
# Flip HIGHER_PRIORITY_FIRST to False to make 1 the top priority instead.
# Ties always fall back to submission order (oldest job first).
DEFAULT_PRIORITY = 100
MIN_PRIORITY = 1
MAX_PRIORITY = 10000
HIGHER_PRIORITY_FIRST = True


class Base(DeclarativeBase):
    pass


class WorkerRow(Base):
    __tablename__ = "workers"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    provider: Mapped[str] = mapped_column(String(32))
    gpu: Mapped[str] = mapped_column(String(128))
    architecture: Mapped[str] = mapped_column(String(32))
    vram_gb: Mapped[int] = mapped_column(Integer)
    runtime: Mapped[str] = mapped_column(String(64))
    capabilities_json: Mapped[str] = mapped_column(Text, default='[]', server_default='[]')
    price_per_hour: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(32), default="ready")
    last_seen: Mapped[str] = mapped_column(String(64))


class JobRow(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    workflow: Mapped[str] = mapped_column(String(64))
    policy: Mapped[str] = mapped_column(String(32))
    min_vram_gb: Mapped[int] = mapped_column(Integer)
    architecture: Mapped[str] = mapped_column(String(32))
    runtime: Mapped[str] = mapped_column(String(64))
    graph_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(64))


class JobRecipeRow(Base):
    __tablename__ = "job_recipes"
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), primary_key=True)
    recipe_json: Mapped[str] = mapped_column(Text)


class TaskRow(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"))
    number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    queue_removed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="")
    prompt_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    artifact_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    route_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    priority: Mapped[int] = mapped_column(Integer, default=DEFAULT_PRIORITY,
                                          server_default=str(DEFAULT_PRIORITY))


class EventRow(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    time: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text)


MAX_AUTO_ATTEMPTS = 3
# Failures caused by the worker's ComfyUI going away (restart, crash) rather than by the job itself
TRANSIENT_FAILURES = ("actively refused", "10061", "connection refused", "connecterror", "connection reset",
                      "remoteprotocolerror", "server disconnected", "timed out", "read timeout", "illegal memory access",
                      "connection aborted", "broken pipe", "comfyui restarted", "prompt vanished")


def transient_failure(message: str) -> bool:
    text = (message or "").lower()
    return any(marker in text for marker in TRANSIENT_FAILURES)


class QueueStore:
    def __init__(self, database_url: str) -> None:
        if database_url.startswith("sqlite:///./"):
            Path(database_url.removeprefix("sqlite:///./")).parent.mkdir(
                parents=True, exist_ok=True
            )
        self.engine = create_engine(database_url, pool_pre_ping=True)
        Base.metadata.create_all(self.engine)
        worker_columns = {column['name'] for column in inspect(self.engine).get_columns('workers')}
        if 'capabilities_json' not in worker_columns:
            with self.engine.begin() as connection:
                connection.exec_driver_sql("ALTER TABLE workers ADD COLUMN capabilities_json TEXT NOT NULL DEFAULT '[]'")
        # ``create_all`` does not add columns to an existing user database.
        # Keep this additive migration local and safe for upgraded installs.
        task_columns = {column["name"] for column in inspect(self.engine).get_columns("tasks")}
        if "queue_removed" not in task_columns:
            with self.engine.begin() as connection:
                connection.exec_driver_sql("ALTER TABLE tasks ADD COLUMN queue_removed INTEGER NOT NULL DEFAULT 0")
        if "route_json" not in task_columns:
            with self.engine.begin() as connection:
                connection.exec_driver_sql("ALTER TABLE tasks ADD COLUMN route_json TEXT")
        if "priority" not in task_columns:
            # Existing tasks (and inserts from a not-yet-restarted process)
            # take the medium default through the column DEFAULT.
            with self.engine.begin() as connection:
                connection.exec_driver_sql(
                    "ALTER TABLE tasks ADD COLUMN priority INTEGER NOT NULL "
                    f"DEFAULT {DEFAULT_PRIORITY}"
                )
        # The execution pool is a routing choice, not a larger VRAM class. The
        # same Pocket H3 graph that fits a local 24 GB card also fits a 24 GB
        # cloud card, so repair the earlier cloud-only 30 GB default.
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                "UPDATE jobs SET min_vram_gb = 24 "
                "WHERE workflow = 'pocket-h3' AND min_vram_gb = 30"
            )

    @staticmethod
    def _event(db: Session, kind: str, message: str) -> None:
        db.add(EventRow(time=now().isoformat(), kind=kind, message=message[:1000]))

    def register_worker(
        self,
        *,
        worker_id: str,
        name: str,
        provider: str,
        gpu: str,
        architecture: str,
        vram_gb: int,
        runtime: str,
        price_per_hour: float = 0,
        capabilities: list[str] | None = None,
    ) -> None:
        with Session(self.engine) as db, db.begin():
            worker = db.get(WorkerRow, worker_id)
            if worker is None:
                worker = WorkerRow(id=worker_id)
                db.add(worker)
                self._event(db, "worker", f"{name} registered ({gpu})")
            worker.name = name
            worker.provider = provider
            worker.gpu = gpu
            worker.architecture = architecture
            worker.vram_gb = vram_gb
            worker.runtime = runtime
            worker.capabilities_json = json.dumps(capabilities or [])
            worker.price_per_hour = price_per_hour
            worker.last_seen = now().isoformat()
            active = db.scalar(select(TaskRow).where(TaskRow.worker_id == worker_id,
                               TaskRow.status.in_(("running", "cancel_requested"))))
            worker.status = "busy" if active else "ready"

    def heartbeat(self, worker_id: str, state: str | None = None) -> bool:
        with Session(self.engine) as db, db.begin():
            worker = db.get(WorkerRow, worker_id)
            if worker is None:
                return False
            worker.last_seen = now().isoformat()
            if state:
                active = db.scalar(select(TaskRow).where(TaskRow.worker_id == worker_id,
                                   TaskRow.status.in_(['running', 'cancel_requested'])))
                worker.status = 'busy' if state == 'ready' and active else state
            return True

    def submit(
        self,
        *,
        name: str,
        graph: dict,
        workflow: str = "pocket-h3",
        policy: str = "local",
        min_vram_gb: int = 24,
        architecture: str = "any",
        runtime: str = "pocket-h3-local",
        recipe: dict | None = None,
        priority: int = DEFAULT_PRIORITY,
    ) -> str:
        job_id = f"job-{uuid4().hex[:12]}"
        with Session(self.engine) as db, db.begin():
            db.add(
                JobRow(
                    id=job_id,
                    name=name[:120],
                    workflow=workflow,
                    policy=policy,
                    min_vram_gb=min_vram_gb,
                    architecture=architecture,
                    runtime=runtime,
                    graph_json=json.dumps(graph),
                    created_at=now().isoformat(),
                )
            )
            db.add(TaskRow(id=f"{job_id}-t001", job_id=job_id, number=1,
                           priority=self.clamp_priority(priority)))
            if recipe is not None:
                db.add(JobRecipeRow(job_id=job_id, recipe_json=json.dumps(recipe)))
            self._event(db, "job", f"{job_id} submitted")
        return job_id

    def _requeue_expired(self, db: Session) -> None:
        expired = db.scalars(
            select(TaskRow).where(
                TaskRow.status.in_(("running", "cancel_requested")),
                TaskRow.lease_expires_at < now().isoformat(),
            )
        ).all()
        for task in expired:
            # A submitted Comfy prompt can keep rendering through a controller
            # reboot. Give its original worker time to reconnect before another
            # GPU is allowed to render the same task.
            if task.prompt_id and datetime.fromisoformat(task.lease_expires_at) + timedelta(hours=3) > now():
                continue
            if task.worker_id:
                worker = db.get(WorkerRow, task.worker_id)
                if worker is not None:
                    worker.status = "ready"
            if task.status == "cancel_requested":
                task.status = "cancelled"
                task.message = "Cancelled after worker lease expired"
                self._event(db, "cancelled", f"{task.id} cancellation completed after lease expiry")
            else:
                task.status = "queued"
                task.progress = 0
                self._event(db, "lease", f"{task.id} lease expired; requeued")
            task.worker_id = None
            task.lease_token = None
            task.lease_expires_at = None

    @staticmethod
    def clamp_priority(priority: int) -> int:
        return max(MIN_PRIORITY, min(MAX_PRIORITY, int(priority)))

    @staticmethod
    def _pickup_order():
        """Claim order: priority (see HIGHER_PRIORITY_FIRST), then oldest job."""
        priority = TaskRow.priority.desc() if HIGHER_PRIORITY_FIRST else TaskRow.priority.asc()
        return (priority, JobRow.created_at, TaskRow.number)

    def _queued_in_order(self, db: Session) -> list[tuple[TaskRow, JobRow]]:
        return list(db.execute(
            select(TaskRow, JobRow).join(JobRow, TaskRow.job_id == JobRow.id)
            .where(TaskRow.status == "queued").order_by(*self._pickup_order())
        ).tuples())

    def _queue_ranks(self, db: Session) -> dict[str, int]:
        """Queue position of every queued task; 1 = next in claim order."""
        return {task.id: index for index, (task, _) in enumerate(self._queued_in_order(db), 1)}

    def _locked(self, db: Session) -> None:
        if self.engine.dialect.name == "sqlite":
            db.execute(text("BEGIN IMMEDIATE"))

    def set_task_priority(self, task_id: str, priority: int) -> dict | None:
        """Set a task's priority number; returns {"priority", "rank"}.

        Returns None for unknown tasks and for running/cancelling tasks, whose
        position in the queue no longer matters. Terminal tasks keep the value
        for a later requeue (rank is None until then).
        """
        with Session(self.engine) as db, db.begin():
            self._locked(db)
            task = db.get(TaskRow, task_id)
            if task is None or task.status in {"running", "cancel_requested"}:
                return None
            previous = task.priority
            task.priority = self.clamp_priority(priority)
            if task.priority != previous:
                self._event(db, "job", f"{task.id} priority {previous} -> {task.priority}")
            db.flush()
            return {"priority": task.priority, "rank": self._queue_ranks(db).get(task.id)}

    def move_task(self, task_id: str, position: str) -> dict | None:
        """Re-rank a queued task to "top", "up", "down" or "bottom".

        Only priority numbers change: the moved task gets a priority next to
        its new neighbour, then any task above it whose number no longer keeps
        the intended order is raised just enough (ties with the default 100
        are common, so a one-step move may lift a short run of tied tasks).
        Returns {"priority", "rank", "changed": {task_id: priority}}.
        """
        if position not in {"top", "up", "down", "bottom"}:
            raise ValueError("position must be top, up, down or bottom")
        with Session(self.engine) as db, db.begin():
            self._locked(db)
            order = self._queued_in_order(db)
            index = next((i for i, (task, _) in enumerate(order) if task.id == task_id), None)
            if index is None:
                return None
            moving = order.pop(index)
            target = {"top": 0, "up": max(0, index - 1),
                      "down": min(len(order), index + 1), "bottom": len(order)}[position]
            order.insert(target, moving)
            key = lambda pair: (pair[1].created_at, pair[0].number)  # noqa: E731
            # Ranking strength: larger = earlier when HIGHER_PRIORITY_FIRST.
            sign = 1 if HIGHER_PRIORITY_FIRST else -1
            strength = {task.id: sign * task.priority for task, _ in order}
            task = moving[0]
            if target == 0 and len(order) > 1:
                strength[task.id] = strength[order[1][0].id] + (0 if key(moving) < key(order[1]) else 1)
                strength[task.id] = max(strength[task.id], sign * task.priority)
            elif target == len(order) - 1 and len(order) > 1:
                above = order[-2]
                strength[task.id] = strength[above[0].id] - (0 if key(above) < key(moving) else 1)
                strength[task.id] = min(strength[task.id], sign * task.priority)
            elif position == "up" and target + 1 < len(order):
                below = order[target + 1]
                strength[task.id] = strength[below[0].id] + (0 if key(moving) < key(below) else 1)
            elif position == "down" and target > 0:
                above = order[target - 1]
                strength[task.id] = strength[above[0].id] - (0 if key(above) < key(moving) else 1)
            # Bottom-up repair: each task must outrank the one after it, or tie
            # with it while being older. Only raises, so lower tasks never move.
            for i in range(len(order) - 2, -1, -1):
                current, nxt = order[i], order[i + 1]
                needed = strength[nxt[0].id] + (0 if key(current) < key(nxt) else 1)
                strength[current[0].id] = max(strength[current[0].id], needed)
            changed = {}
            for row, _ in order:
                value = self.clamp_priority(sign * strength[row.id])
                if value != row.priority:
                    changed[row.id] = value
                    self._event(db, "job", f"{row.id} priority {row.priority} -> {value}"
                                + (f" (moved {position})" if row.id == task.id else " (re-ranked)"))
                    row.priority = value
            db.flush()
            return {"priority": task.priority, "rank": self._queue_ranks(db).get(task.id),
                    "changed": changed}

    def save_recipe_if_missing(self, job_id: str, recipe: dict) -> None:
        """Cache a recipe rebuilt from the job graph so the gallery doesn't rebuild it on every load."""
        with Session(self.engine) as db, db.begin():
            if db.get(JobRecipeRow, job_id) is None:
                db.add(JobRecipeRow(job_id=job_id, recipe_json=json.dumps(recipe)))

    def requeue_task(self, task_id: str) -> bool:
        """Reset a terminal task while preserving its job recipe and route override."""
        with Session(self.engine) as db, db.begin():
            task = db.get(TaskRow, task_id)
            if task is None or task.status not in {"failed", "cancelled", "completed"}:
                return False
            task.status = "queued"
            task.worker_id = None
            task.lease_token = None
            task.lease_expires_at = None
            task.progress = 0
            task.message = "Requeued by operator"
            task.queue_removed = 0
            task.prompt_id = None
            task.artifact_json = None
            self._event(db, "job", f"{task.id} requeued by operator")
            return True

    @staticmethod
    def _route(task: TaskRow, job: JobRow) -> dict:
        override = json.loads(task.route_json) if task.route_json else {}
        return {
            "policy": override.get("policy", job.policy),
            "min_vram_gb": int(override.get("min_vram_gb", job.min_vram_gb)),
            "architecture": override.get("architecture", job.architecture),
            "runtime": override.get("runtime", job.runtime),
            "preferred_worker_id": override.get("preferred_worker_id"),
            'required_capabilities': ['reference-preprocess-v1'] if any(
                node.get('class_type') == 'TMPrepareReference' for node in json.loads(job.graph_json).values()) else [],
        }

    @staticmethod
    def _runtime_matches(worker: WorkerRow, route: dict) -> bool:
        runtime = route["runtime"]
        if runtime == 'pocket-reference-preprocess-v1':
            return 'reference-preprocess-v1' in json.loads(worker.capabilities_json or '[]')
        if worker.provider == "Local" and worker.runtime == "pocket-h3-local":
            return runtime in {
                worker.runtime,
                "pocket-qwen-image-21-local",
                "pocket-krea2-local", "pocket-ltx-local",
                'pocket-voice-local',
            } or (route["policy"] == "any" and runtime in {
                "pocket-h3-cloud-ref2v-int8", "pocket-h3-cloud-qwen-image-21",
                'pocket-h3-cloud-ref2v-singularity', 'pocket-h3-cloud-fasth3-omni',
            })
        return runtime == worker.runtime

    @classmethod
    def _worker_matches(cls, worker: WorkerRow, route: dict) -> bool:
        if not set(route.get('required_capabilities', [])) <= set(json.loads(worker.capabilities_json or '[]')):
            return False
        if route["preferred_worker_id"] and route["preferred_worker_id"] != worker.id:
            return False
        if worker.vram_gb < route["min_vram_gb"]:
            return False
        if route["architecture"] not in {"any", worker.architecture}:
            return False
        if route["policy"] == "local" and worker.provider != "Local":
            return False
        if route["policy"] == "standard" and worker.provider == "Local":
            return False
        return cls._runtime_matches(worker, route)

    def route_task(
        self,
        task_id: str,
        *,
        policy: str,
        min_vram_gb: int,
        architecture: str,
        runtime: str,
        preferred_worker_id: str | None = None,
        requeue: bool = False,
    ) -> dict | None:
        """Change a non-running task's dispatch route, optionally requeueing it."""
        with Session(self.engine) as db, db.begin():
            task = db.get(TaskRow, task_id)
            job = db.get(JobRow, task.job_id) if task else None
            if task is None or job is None or task.status in {"running", "cancel_requested"}:
                return None
            if preferred_worker_id:
                worker = db.get(WorkerRow, preferred_worker_id)
                if worker is None:
                    raise ValueError("Selected worker is no longer registered")
            route = {
                "policy": policy,
                "min_vram_gb": min_vram_gb,
                "architecture": architecture,
                "runtime": runtime,
                "preferred_worker_id": preferred_worker_id,
            }
            if preferred_worker_id and not self._worker_matches(worker, route):
                raise ValueError("Selected worker is incompatible with this route or runtime")
            task.route_json = json.dumps(route)
            if requeue:
                if task.status not in {"failed", "cancelled", "completed", "queued"}:
                    return None
                task.status = "queued"
                task.queue_removed = 0
                task.worker_id = None
                task.lease_token = None
                task.lease_expires_at = None
                task.progress = 0
                task.prompt_id = None
                task.artifact_json = None
            task.message = "Route updated by operator" + (" and task requeued" if requeue else "")
            target = preferred_worker_id or policy
            self._event(db, "job", f"{task.id} routed to {target}" + (" and requeued" if requeue else ""))
            return route

    def remove_task(self, task_id: str) -> bool:
        """Remove a queue entry, keeping its job and saved output accessible.

        The conditional update also protects against a worker claiming queued
        work between the operator's snapshot and this request.
        """
        with Session(self.engine) as db, db.begin():
            result = db.execute(update(TaskRow).where(
                TaskRow.id == task_id, TaskRow.status.not_in(("running", "cancel_requested"))
            ).values(queue_removed=1))
            if not result.rowcount:
                return False
            task = db.get(TaskRow, task_id)
            if task.status == "queued":
                task.status = "cancelled"
                task.message = "Removed from queue by operator"
            self._event(db, "job", f"{task_id} removed from queue; saved media retained")
            return True

    def cancel_task(self, task_id: str) -> str | None:
        """Cancel queued work or ask its active worker to interrupt ComfyUI."""
        with Session(self.engine) as db, db.begin():
            task = db.get(TaskRow, task_id)
            if task is None:
                return None
            if task.status == "queued":
                task.status = "cancelled"
                task.message = "Cancelled by operator before dispatch"
                self._event(db, "cancelled", f"{task.id} cancelled before dispatch")
                return task.status
            if task.status == "running":
                task.status = "cancel_requested"
                task.message = "Cancellation requested; waiting for worker interrupt"
                self._event(db, "cancelled", f"{task.id} cancellation requested")
                return task.status
            if task.status == "cancel_requested":
                return task.status
            return None

    def task_control(self, task_id: str, lease_token: str | None) -> dict | None:
        if not lease_token:
            return None
        with Session(self.engine) as db:
            task = db.get(TaskRow, task_id)
            if task is None or task.lease_token != lease_token:
                return None
            return {"cancel_requested": task.status == "cancel_requested"}

    def task_execution(self, task_id: str) -> dict | None:
        with Session(self.engine) as db:
            task = db.get(TaskRow, task_id)
            if task is None:
                return None
            return {"status": task.status, "worker_id": task.worker_id,
                    "prompt_id": task.prompt_id}

    def claim(self, worker_id: str) -> dict | None:
        with Session(self.engine) as db, db.begin():
            # Serialize SQLite claim writers before reading candidates; two workers
            # must never lease the same queued task.
            if self.engine.dialect.name == "sqlite":
                db.execute(text("BEGIN IMMEDIATE"))
            worker = db.get(WorkerRow, worker_id)
            self._requeue_expired(db)
            if worker is None or worker.status != "ready":
                return None
            if db.scalar(
                select(TaskRow).where(TaskRow.worker_id == worker_id, TaskRow.status == "running")
            ):
                return None
            query = (
                select(TaskRow, JobRow)
                .join(JobRow, TaskRow.job_id == JobRow.id)
                .where(TaskRow.status == "queued")
                .order_by(*self._pickup_order())
            )
            if self.engine.dialect.name == "postgresql":
                query = query.with_for_update(of=TaskRow, skip_locked=True)
            for task, job in db.execute(query):
                route = self._route(task, job)
                if not self._worker_matches(worker, route):
                    continue
                token = secrets.token_urlsafe(32)
                task.status = "running"
                task.worker_id = worker_id
                task.lease_token = token
                task.lease_expires_at = (now() + timedelta(seconds=90)).isoformat()
                task.attempt += 1
                worker.status = "busy"
                worker.last_seen = now().isoformat()
                self._event(db, "lease", f"{task.id} leased to {worker_id}")
                graph = json.loads(job.graph_json)
                # Catalog/recipes use portable paths. Resolve model subfolders
                # for Windows local workers and Linux rentals only in the lease.
                for node in graph.values():
                    inputs = node.get("inputs", {})
                    separator = "\\" if worker.provider == "Local" else "/"
                    other = "/" if worker.provider == "Local" else "\\"
                    for key in ("clip_name", "lora_name", "unet_name", "vae_name", "gate_file"):
                        if isinstance(inputs.get(key), str):
                            inputs[key] = inputs[key].replace(other, separator)
                return {
                    "task_id": task.id,
                    "job_id": job.id,
                    "lease_token": token,
                    "graph": graph,
                    "queue_seconds": round(
                        (now() - datetime.fromisoformat(job.created_at)).total_seconds(), 1
                    ),
                }
            return None

    def update_task(
        self,
        task_id: str,
        lease_token: str,
        *,
        status: str,
        progress: int | None = None,
        message: str = "",
        prompt_id: str | None = None,
        artifact: dict | None = None,
    ) -> bool:
        with Session(self.engine) as db, db.begin():
            task = db.get(TaskRow, task_id)
            if task is None or task.lease_token != lease_token:
                return False
            # Lost HTTP acknowledgements can be retried without changing a
            # completed task or submitting another render.
            if task.status in {"completed", "failed", "cancelled"}:
                return task.status == status and (not prompt_id or task.prompt_id == prompt_id)
            if task.status not in {"running", "cancel_requested"}:
                return False
            if task.status == "cancel_requested" and status not in {"cancelled", "failed"}:
                return False
            if task.status == "cancel_requested" and status == "failed":
                # Older workers report ComfyUI's interrupt as an execution
                # failure. Preserve the operator's intended terminal state.
                status = "cancelled"
            worker = db.get(WorkerRow, task.worker_id) if task.worker_id else None
            if worker is not None:
                worker.last_seen = now().isoformat()
            task.lease_expires_at = (now() + timedelta(seconds=90)).isoformat()
            if progress is not None:
                task.progress = max(0, min(100, progress))
            if message:
                task.message = message[:2000]
                self._event(db, "progress", f"{task.id}: {message[:180]}")
            if prompt_id:
                task.prompt_id = prompt_id
            if (status == "failed" and task.status == "running" and task.attempt < MAX_AUTO_ATTEMPTS
                    and transient_failure(message)):
                # ComfyUI restarted/crashed under the task: put it back in the queue instead of failing the job
                task.status = "queued"
                task.worker_id = None
                task.lease_token = None
                task.lease_expires_at = None
                task.progress = 0
                task.prompt_id = None
                task.artifact_json = None
                task.message = f"Auto-requeued after attempt {task.attempt}: {message}"[:2000]
                if worker is not None:
                    worker.status = "ready"
                self._event(db, "job", f"{task.id} auto-requeued (attempt {task.attempt}): {message[:120]}")
                return True
            if status in ("completed", "failed", "cancelled"):
                task.status = status
                task.progress = 100 if status == "completed" else task.progress
                task.artifact_json = json.dumps(artifact) if artifact else None
                worker = db.get(WorkerRow, task.worker_id) if task.worker_id else None
                if worker is not None:
                    worker.status = "ready"
                self._event(db, status, f"{task.id} {status}")
            return True

    def resume_task(self, worker_id: str, task_id: str, lease_token: str) -> bool:
        with Session(self.engine) as db, db.begin():
            task = db.get(TaskRow, task_id)
            worker = db.get(WorkerRow, worker_id)
            if (task is None or worker is None or task.worker_id != worker_id
                    or task.lease_token != lease_token
                    or task.status not in {"running", "cancel_requested"}):
                return False
            task.lease_expires_at = (now() + timedelta(seconds=90)).isoformat()
            worker.last_seen = now().isoformat()
            worker.status = "busy"
            return True

    def job_recipe(self, job_id: str) -> dict | None:
        with Session(self.engine) as db:
            row = db.get(JobRecipeRow, job_id)
            return json.loads(row.recipe_json) if row else None

    def job(self, job_id: str) -> dict | None:
        with Session(self.engine) as db:
            job = db.get(JobRow, job_id)
            if job is None:
                return None
            tasks = db.scalars(
                select(TaskRow).where(TaskRow.job_id == job_id).order_by(TaskRow.number)
            ).all()
            ranks = self._queue_ranks(db) if any(t.status == "queued" for t in tasks) else {}
            return {
                "id": job.id,
                "name": job.name,
                "workflow": job.workflow,
                "created_at": job.created_at,
                "tasks": [{**self._task_dict(task), "rank": ranks.get(task.id)} for task in tasks],
            }

    def output_records(self, limit: int = 1000) -> list[dict]:
        """Return gallery jobs immediately, retaining identity through completion."""
        with Session(self.engine) as db:
            rows = db.execute(
                select(TaskRow, JobRow, JobRecipeRow)
                .join(JobRow, TaskRow.job_id == JobRow.id)
                .outerjoin(JobRecipeRow, JobRecipeRow.job_id == JobRow.id)
                .where((TaskRow.status != "completed") | TaskRow.artifact_json.is_not(None))
                .order_by(JobRow.created_at.desc(), TaskRow.number)
                .limit(limit)
            ).all()
            return [
                {
                    "task_id": task.id,
                    "job_id": job.id,
                    "prompt_id": job.id,
                    "comfy_prompt_id": task.prompt_id,
                    "job_name": job.name,
                    "worker_id": task.worker_id,
                    "created_at": job.created_at,
                    "artifact": json.loads(task.artifact_json) if task.status == "completed" and task.artifact_json else {},
                    "status": task.status,
                    "progress": task.progress,
                    "message": task.message,
                    "recipe": json.loads(saved.recipe_json) if saved else None,
                }
                for task, job, saved in rows
            ]

    def task_graph(self, task_id: str) -> dict | None:
        with Session(self.engine) as db:
            task = db.get(TaskRow, task_id)
            job = db.get(JobRow, task.job_id) if task else None
            return json.loads(job.graph_json) if job else None

    def task_lease_valid(self, task_id: str, lease_token: str | None) -> bool:
        if not lease_token:
            return False
        with Session(self.engine) as db:
            task = db.get(TaskRow, task_id)
            return bool(task and task.status == "running" and task.lease_token and
                        hmac.compare_digest(task.lease_token, lease_token))

    def pending_for_runtime(self, runtime: str) -> int:
        runtimes = [runtime]
        if runtime.startswith('pocket-h3-cloud-'):
            runtimes.append('pocket-reference-preprocess-v1')
        with Session(self.engine) as db:
            return int(db.scalar(select(func.count()).select_from(TaskRow).join(
                JobRow, TaskRow.job_id == JobRow.id).where(
                    TaskRow.status == "queued",
                    JobRow.runtime.in_(runtimes))) or 0)

    @staticmethod
    def _task_dict(task: TaskRow) -> dict:
        return {
            "id": task.id,
            "number": task.number,
            "status": task.status,
            "worker_id": task.worker_id,
            "progress": task.progress,
            "message": task.message,
            "prompt_id": task.prompt_id,
            "artifact": json.loads(task.artifact_json) if task.artifact_json else None,
            "attempt": task.attempt,
            "priority": task.priority if task.priority is not None else DEFAULT_PRIORITY,
        }

    def snapshot(self) -> dict:
        with Session(self.engine) as db, db.begin():
            self._requeue_expired(db)
            # Worker registration is ephemeral capacity, not permanent farm
            # inventory. Keep task history's worker_id strings, but remove
            # registrations that have been dead for a day so the table cannot
            # grow forever across disposable cloud instances.
            stale_before = (now() - timedelta(hours=24)).isoformat()
            db.execute(delete(WorkerRow).where(WorkerRow.last_seen < stale_before))
            workers = db.scalars(select(WorkerRow).order_by(WorkerRow.name)).all()
            pairs = db.execute(select(TaskRow, JobRow).join(JobRow, TaskRow.job_id == JobRow.id)
                               .where(TaskRow.queue_removed == 0)
                               .order_by(JobRow.created_at.desc(), TaskRow.number).limit(200)).all()
            tasks = [task for task, job in pairs]
            jobs = [job for task, job in pairs]
            counts = dict(db.execute(select(TaskRow.status, func.count()).where(
                TaskRow.queue_removed == 0).group_by(TaskRow.status)).all())
            events = db.scalars(select(EventRow).order_by(EventRow.id.desc()).limit(200)).all()
            worker_rows = []
            for worker in workers:
                online = (now() - datetime.fromisoformat(worker.last_seen)).total_seconds() < 60
                if not online:
                    # Offline workers remain in the database briefly for
                    # reconnects and diagnostics, but are not current fleet
                    # capacity and must not inflate the UI denominator.
                    continue
                worker_rows.append(
                    {
                        "id": worker.id,
                        "name": worker.name,
                        "provider": worker.provider,
                        "gpu": worker.gpu,
                        "architecture": worker.architecture,
                        "vram_gb": worker.vram_gb,
                        "runtime": worker.runtime,
                        "price_per_hour": worker.price_per_hour,
                        "status": worker.status,
                        "last_seen": worker.last_seen,
                        "heartbeat_age_seconds": round(max(0, (now() - datetime.fromisoformat(worker.last_seen)).total_seconds()), 1),
                        "current_task_id": next(
                            (
                                t.id
                                for t in tasks
                                if t.worker_id == worker.id and t.status == "running"
                            ),
                            None,
                        ),
                    }
                )
            task_rows = []
            ranks = self._queue_ranks(db)
            job_map = {job.id: job for job in jobs}
            for task in tasks:
                job = job_map.get(task.job_id)
                if job is None:
                    continue
                row = self._task_dict(task)
                route = self._route(task, job)
                row.update(
                    {
                        "job_name": job.name,
                        "workflow": job.workflow,
                        "job_id": job.id,
                        "created_at": job.created_at,
                        "rank": ranks.get(task.id),
                        "runtime": route["runtime"],
                        "policy": route["policy"],
                        "min_vram_gb": route["min_vram_gb"],
                        "architecture": route["architecture"],
                        "preferred_worker_id": route["preferred_worker_id"],
                        "requirement": f"{route['min_vram_gb']} GB+ / {route['architecture']} / {route['runtime']}",
                    }
                )
                task_rows.append(row)
            return {
                "workers": worker_rows,
                "jobs": [],
                "tasks": task_rows,
                "events": [
                    {"id": e.id, "time": e.time[11:19], "timestamp": e.time, "kind": e.kind, "message": e.message} for e in events
                ],
                "metrics": {
                    "active_workers": sum(w["status"] != "offline" for w in worker_rows),
                    "burn_rate": sum(
                        w["price_per_hour"] for w in worker_rows if w["status"] != "offline"
                    ),
                    "queued": counts.get("queued", 0),
                    "running": counts.get("running", 0),
                    "active_tasks": sum(counts.get(status, 0) for status in ('queued', 'running', 'cancel_requested')),
                    "completed": counts.get("completed", 0),
                    "failed": counts.get("failed", 0),
                    "total_tasks": sum(counts.values()),
                },
            }
