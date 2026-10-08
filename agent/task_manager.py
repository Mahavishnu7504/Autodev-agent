from __future__ import annotations
import copy
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from agent.executor import execute_plan
from agent.logger import clear_logs, get_logs, log
from agent.planner import create_plan

class TaskManager:
    PHASE_PROGRESS = {
        "queued": 0,
        "planning": 10,
        "generating": 25,
        "testing": 50,
        "diagnosing": 60,
        "repairing": 70,
        "quality_gate": 85,
        "packaging": 95,
        "completed": 100,
        "failed": 100,
        "cancelled": 100,
    }
    MAX_LOGS = 500
    STATE_VERSION = 2
    def __init__(self, max_history: int = 50):
        self.max_history = max_history
        self._tasks: Dict[str, Dict[str, Any]] = {}
        self._order: list[str] = []
        self._queue: list[str] = []
        self._lock = threading.RLock()
        self._state_lock = threading.Lock()
        self._active_workers = 0
        self._max_workers = 1
        self._cancel_events: Dict[str, threading.Event] = {}
        self.state_dir = self._resolve_state_dir()
        self.state_file = self.state_dir / "tasks.json"
        self._load_state()
        self._recover_state()
        self._save_state()
        with self._lock:
            self._ensure_workers_locked()
    def _resolve_state_dir(self) -> Path:
        configured = os.getenv("AUTODEV_STATE_DIR")
        if configured:
            path = Path(configured).expanduser().resolve()
        elif os.name == "nt":
            local_app_data = os.getenv("LOCALAPPDATA")
            if local_app_data:
                path = Path(local_app_data) / "AutoDevAgent"
            else:
                path = Path.home() / "AppData" / "Local" / "AutoDevAgent"
        else:
            path = Path.home() / ".autodev_agent"
        path.mkdir(parents=True, exist_ok=True)
        return path
    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
    def _json_safe(self, value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, dict):
            return {
                str(key): self._json_safe(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self._json_safe(item) for item in value]
        if isinstance(value, set):
            return [self._json_safe(item) for item in value]
        return str(value)
    def _save_state(self) -> None:
        with self._state_lock:
            with self._lock:
                snapshot = {
                    "version": self.STATE_VERSION,
                    "updated_at": self._now(),
                    "queue": list(self._queue),
                    "tasks": [
                        self._json_safe(self._tasks[task_id])
                        for task_id in self._order
                        if task_id in self._tasks
                    ],
                }
            temporary = self.state_file.with_suffix(".tmp")
            try:
                temporary.write_text(
                    json.dumps(
                        snapshot,
                        indent=2,
                        ensure_ascii=False
                    ),
                    encoding="utf-8"
                )
                os.replace(
                    str(temporary),
                    str(self.state_file)
                )
            except Exception as exc:
                try:
                    if temporary.exists():
                        temporary.unlink()
                except Exception:
                    pass
                print(f"[TaskManager] State save failed: {exc}")
    def _load_state(self) -> None:
        if not self.state_file.exists():
            return
        try:
            raw = json.loads(
                self.state_file.read_text(
                    encoding="utf-8"
                )
            )
            stored_tasks = raw.get("tasks", [])
            stored_queue = raw.get("queue", [])
            if not isinstance(stored_tasks, list):
                return
            with self._lock:
                self._tasks.clear()
                self._order.clear()
                self._queue.clear()
                for task in stored_tasks:
                    if not isinstance(task, dict):
                        continue
                    task_id = str(
                        task.get("task_id") or ""
                    ).strip()
                    if not task_id:
                        continue
                    task.setdefault("logs", [])
                    task.setdefault("inputs", {})
                    task.setdefault("status", "failed")
                    task.setdefault("phase", "failed")
                    task.setdefault("progress", 100)
                    task.setdefault("cancel_requested", False)
                    task.setdefault("retry_of", None)
                    task.setdefault("retry_count", 0)
                    self._tasks[task_id] = task
                    self._order.append(task_id)
                if isinstance(stored_queue, list):
                    for task_id in stored_queue:
                        if (
                            task_id in self._tasks
                            and task_id not in self._queue
                        ):
                            self._queue.append(task_id)
                self._trim_history_locked()
        except Exception as exc:
            print(f"[TaskManager] State load failed: {exc}")
    def _recover_state(self) -> None:
        changed = False
        with self._lock:
            recovered_queue = []
            for task_id in self._order:
                task = self._tasks.get(task_id)
                if not task:
                    continue
                status = str(
                    task.get("status", "")
                ).lower()
                if status == "running":
                    task["status"] = "failed"
                    task["phase"] = "failed"
                    task["progress"] = 100
                    task["error"] = (
                        "Task was interrupted because the "
                        "AutoDev Agent server restarted."
                    )
                    task["cancel_requested"] = False
                    task["finished_at"] = self._now()
                    task["completed_at"] = task["finished_at"]
                    self._append_log_locked(
                        task_id,
                        "⚠️ Running task interrupted by server restart."
                    )
                    self._calculate_duration_locked(task)
                    changed = True
                elif status == "queued":
                    if task.get("cancel_requested"):
                        task["status"] = "cancelled"
                        task["phase"] = "cancelled"
                        task["progress"] = 100
                        task["finished_at"] = self._now()
                        task["completed_at"] = task["finished_at"]
                        self._append_log_locked(
                            task_id,
                            "⚠️ Queued task was cancelled before restart."
                        )
                        changed = True
                    else:
                        recovered_queue.append(task_id)
            existing_queue = [
                task_id
                for task_id in self._queue
                if task_id in self._tasks
                and self._tasks[task_id].get("status") == "queued"
            ]
            for task_id in recovered_queue:
                if task_id not in existing_queue:
                    existing_queue.append(task_id)
            self._queue = existing_queue
            if changed:
                self._trim_history_locked()
    def _trim_history_locked(self) -> None:
        if len(self._order) <= self.max_history:
            return
        keep = self._order[-self.max_history:]
        keep_set = set(keep)
        for task_id in list(self._tasks):
            if task_id not in keep_set:
                self._tasks.pop(task_id, None)
                self._cancel_events.pop(task_id, None)
        self._order = keep
        self._queue = [
            task_id
            for task_id in self._queue
            if task_id in keep_set
        ]
    def _append_log_locked(
        self,
        task_id: str,
        message: str
    ) -> None:
        task = self._tasks.get(task_id)
        if not task:
            return
        logs = task.setdefault("logs", [])
        if not isinstance(logs, list):
            logs = []
            task["logs"] = logs
        logs.append(str(message))
        task["logs"] = logs[-self.MAX_LOGS:]
    def _calculate_duration_locked(
        self,
        task: Dict[str, Any]
    ) -> None:
        started_at = task.get("started_at")
        finished_at = task.get("finished_at")
        if not started_at or not finished_at:
            return
        try:
            started = datetime.fromisoformat(started_at)
            finished = datetime.fromisoformat(finished_at)
            task["duration_seconds"] = round(
                (finished - started).total_seconds(),
                2
            )
        except Exception:
            pass
    def _phase_from_logs(
        self,
        logs: list[str]
    ) -> str:
        if not logs:
            return "queued"
        text = "\n".join(
            str(line).lower()
            for line in logs[-40:]
        )
        if "zip created" in text or "packaging" in text:
            return "packaging"
        if "quality gate" in text or "quality_gate" in text:
            return "quality_gate"
        if (
            "repair attempt" in text
            or "autonomous repair" in text
            or "repairing" in text
        ):
            return "repairing"
        if (
            "failure diagnosis" in text
            or "diagnos" in text
            or "analyzing failure" in text
        ):
            return "diagnosing"
        if (
            "running tests" in text
            or "test validation" in text
            or "test suite" in text
            or "tests" in text
        ):
            return "testing"
        if (
            "generating project" in text
            or "generating files" in text
            or "writing project" in text
            or "project generated" in text
            or "generating tests" in text
        ):
            return "generating"
        if (
            "planning" in text
            or "planner" in text
        ):
            return "planning"
        return "running"
    def _sync_live_state_locked(
        self,
        task_id: str,
        persist: bool = False
    ) -> None:
        task = self._tasks.get(task_id)
        if not task:
            return
        if task.get("status") != "running":
            return
        logs = get_logs()
        if not isinstance(logs, list):
            return
        clean_logs = [
            str(line)
            for line in logs[-self.MAX_LOGS:]
        ]
        if clean_logs:
            task["logs"] = clean_logs
            phase = self._phase_from_logs(clean_logs)
            task["phase"] = phase
            task["progress"] = self.PHASE_PROGRESS.get(
                phase,
                task.get("progress", 10)
            )
        if persist:
            self._save_state()
    def _monitor_task(
        self,
        task_id: str,
        stop_event: threading.Event
    ) -> None:
        while not stop_event.wait(0.5):
            with self._lock:
                task = self._tasks.get(task_id)
                if not task:
                    return
                if task.get("status") != "running":
                    return
                self._sync_live_state_locked(
                    task_id,
                    persist=False
                )
            self._save_state()
    def _ensure_workers_locked(self) -> None:
        while (
            self._active_workers < self._max_workers
            and self._queue
        ):
            self._active_workers += 1
            worker_number = self._active_workers
            thread = threading.Thread(
                target=self._worker_loop,
                name=f"autodev-worker-{worker_number}",
                daemon=True
            )
            thread.start()
    def _worker_loop(self) -> None:
        while True:
            with self._lock:
                task_id = None
                while self._queue:
                    candidate = self._queue.pop(0)
                    task = self._tasks.get(candidate)
                    if not task:
                        continue
                    if task.get("status") != "queued":
                        continue
                    if task.get("cancel_requested"):
                        self._mark_cancelled_locked(
                            candidate,
                            "Task cancelled before execution."
                        )
                        continue
                    task_id = candidate
                    break
                if task_id is None:
                    self._active_workers -= 1
                    self._save_state()
                    return
            try:
                self._run_task(task_id)
            except Exception as exc:
                self._fail_task(
                    task_id,
                    exc
                )
    def create_task(
        self,
        task: str,
        inputs: Optional[dict] = None
    ) -> Dict[str, Any]:
        task_id = uuid.uuid4().hex
        now = self._now()
        record = {
            "task_id": task_id,
            "task": task,
            "inputs": inputs or {},
            "status": "queued",
            "phase": "queued",
            "progress": 0,
            "created_at": now,
            "started_at": None,
            "finished_at": None,
            "completed_at": None,
            "duration_seconds": None,
            "plan": None,
            "result": None,
            "error": None,
            "logs": [],
            "cancel_requested": False,
            "retry_of": None,
            "retry_count": 0,
        }
        with self._lock:
            self._tasks[task_id] = record
            self._order.append(task_id)
            self._queue.append(task_id)
            self._trim_history_locked()
            self._save_state()
            self._ensure_workers_locked()
            return copy.deepcopy(record)
    def start_task(
        self,
        task_id: str
    ) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False
            status = str(
                task.get("status", "")
            ).lower()
            if status in {"running", "completed"}:
                return False
            if status == "queued" and task_id in self._queue:
                self._ensure_workers_locked()
                return True
            if status in {"failed", "cancelled"}:
                return False
            task["status"] = "queued"
            task["phase"] = "queued"
            task["progress"] = 0
            task["error"] = None
            task["cancel_requested"] = False
            if task_id not in self._queue:
                self._queue.append(task_id)
            self._save_state()
            self._ensure_workers_locked()
            return True
    def cancel_task(
        self,
        task_id: str
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            status = str(
                task.get("status", "")
            ).lower()
            if status in {"completed", "failed", "cancelled"}:
                return copy.deepcopy(task)
            task["cancel_requested"] = True
            event = self._cancel_events.get(task_id)
            if event:
                event.set()
            if status == "queued":
                self._queue = [
                    item
                    for item in self._queue
                    if item != task_id
                ]
                self._mark_cancelled_locked(
                    task_id,
                    "Task cancelled while waiting in the queue."
                )
            else:
                self._append_log_locked(
                    task_id,
                    "🛑 Cancellation requested."
                )
            self._save_state()
            return copy.deepcopy(task)
    def _mark_cancelled_locked(
        self,
        task_id: str,
        message: str
    ) -> None:
        task = self._tasks.get(task_id)
        if not task:
            return
        task["status"] = "cancelled"
        task["phase"] = "cancelled"
        task["progress"] = 100
        task["cancel_requested"] = True
        task["error"] = message
        task["finished_at"] = self._now()
        task["completed_at"] = task["finished_at"]
        self._append_log_locked(
            task_id,
            f"🛑 {message}"
        )
        self._calculate_duration_locked(task)
    def retry_task(
        self,
        task_id: str
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            original = self._tasks.get(task_id)
            if not original:
                return None
            status = str(
                original.get("status", "")
            ).lower()
            if status not in {"failed", "cancelled"}:
                return None
            retry_id = uuid.uuid4().hex
            retry_count = int(
                original.get("retry_count", 0)
            ) + 1
            now = self._now()
            record = {
                "task_id": retry_id,
                "task": original.get("task", ""),
                "inputs": copy.deepcopy(
                    original.get("inputs") or {}
                ),
                "status": "queued",
                "phase": "queued",
                "progress": 0,
                "created_at": now,
                "started_at": None,
                "finished_at": None,
                "completed_at": None,
                "duration_seconds": None,
                "plan": None,
                "result": None,
                "error": None,
                "logs": [
                    f"🔁 Retry {retry_count} created from task {task_id}."
                ],
                "cancel_requested": False,
                "retry_of": task_id,
                "retry_count": retry_count,
            }
            self._tasks[retry_id] = record
            self._order.append(retry_id)
            self._queue.append(retry_id)
            self._trim_history_locked()
            self._save_state()
            self._ensure_workers_locked()
            return copy.deepcopy(record)
    def get_task(
        self,
        task_id: str
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            if task.get("status") == "running":
                self._sync_live_state_locked(
                    task_id,
                    persist=False
                )
            return copy.deepcopy(task)
    def list_tasks(
        self,
        limit: int = 50
    ) -> list[Dict[str, Any]]:
        with self._lock:
            for task_id in self._order:
                task = self._tasks.get(task_id)
                if (
                    task
                    and task.get("status") == "running"
                ):
                    self._sync_live_state_locked(
                        task_id,
                        persist=False
                    )
            selected = self._order[-max(1, limit):]
            selected.reverse()
            return [
                copy.deepcopy(
                    self._tasks[task_id]
                )
                for task_id in selected
                if task_id in self._tasks
            ]
    def stats(self) -> Dict[str, Any]:
        with self._lock:
            tasks = list(
                self._tasks.values()
            )
            total = len(tasks)
            counts = {
                "queued": 0,
                "running": 0,
                "completed": 0,
                "failed": 0,
                "cancelled": 0,
            }
            durations = []
            for task in tasks:
                status = str(
                    task.get("status", "")
                ).lower()
                if status in counts:
                    counts[status] += 1
                duration = task.get(
                    "duration_seconds"
                )
                if isinstance(duration, (int, float)):
                    durations.append(
                        float(duration)
                    )
            completed = counts["completed"]
            average = (
                round(
                    sum(durations) /
                    len(durations),
                    2
                )
                if durations
                else 0
            )
            success_rate = (
                round(
                    completed /
                    total *
                    100,
                    2
                )
                if total
                else 0
            )
            return {
                "total": total,
                "queued": counts["queued"],
                "running": counts["running"],
                "completed": counts["completed"],
                "failed": counts["failed"],
                "cancelled": counts["cancelled"],
                "success_rate_percent": success_rate,
                "average_duration_seconds": average,
                "active_workers": self._active_workers,
                "max_concurrent_tasks": self._max_workers,
                "queue_size": len(self._queue),
            }
    get_stats = stats
    get_task_stats = stats
    def _run_task(
        self,
        task_id: str
    ) -> None:
        started = time.perf_counter()
        stop_monitor = threading.Event()
        cancel_event = threading.Event()
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            self._cancel_events[task_id] = cancel_event
            task["status"] = "running"
            task["phase"] = "planning"
            task["progress"] = 10
            task["started_at"] = self._now()
            task["finished_at"] = None
            task["completed_at"] = None
            task["error"] = None
            task["cancel_requested"] = False
            self._append_log_locked(
                task_id,
                "🚀 Worker started task execution."
            )
            self._save_state()
        clear_logs()
        monitor = threading.Thread(
            target=self._monitor_task,
            args=(
                task_id,
                stop_monitor
            ),
            daemon=True,
            name=f"autodev-monitor-{task_id[:8]}"
        )
        monitor.start()
        try:
            task = self.get_task(task_id)
            if not task:
                return
            if cancel_event.is_set():
                with self._lock:
                    self._mark_cancelled_locked(
                        task_id,
                        "Task cancelled before planning."
                    )
                return
            self._set_phase(
                task_id,
                "planning"
            )
            plan = create_plan(
                task["task"],
                task.get("inputs") or {}
            )
            if cancel_event.is_set():
                with self._lock:
                    self._mark_cancelled_locked(
                        task_id,
                        "Task cancelled after planning."
                    )
                return
            with self._lock:
                record = self._tasks.get(task_id)
                if record:
                    record["plan"] = self._json_safe(plan)
                    record["phase"] = "generating"
                    record["progress"] = 25
                    self._append_log_locked(
                        task_id,
                        "📝 Plan completed. Starting autonomous execution."
                    )
                    self._save_state()
            self._set_phase(
                task_id,
                "testing"
            )
            if cancel_event.is_set():
                with self._lock:
                    self._mark_cancelled_locked(
                        task_id,
                        "Task cancelled before execution."
                    )
                return
            result = execute_plan(
                plan=plan,
                inputs=task.get("inputs") or {},
                task=task["task"]
            )
            with self._lock:
                task = self._tasks.get(task_id)
                if not task:
                    return
                if (
                    task.get("cancel_requested")
                    or cancel_event.is_set()
                ):
                    self._mark_cancelled_locked(
                        task_id,
                        "Task cancellation requested during execution."
                    )
                    return
                task["result"] = self._json_safe(
                    result
                )
                task["status"] = (
                    "completed"
                    if result.get("success") is True
                    else "failed"
                )
                task["phase"] = (
                    "completed"
                    if task["status"] == "completed"
                    else "failed"
                )
                task["progress"] = 100
                task["finished_at"] = self._now()
                task["completed_at"] = task["finished_at"]
                task["duration_seconds"] = round(
                    time.perf_counter() - started,
                    3
                )
                if task["status"] == "completed":
                    self._append_log_locked(
                        task_id,
                        "✅ Task completed successfully."
                    )
                else:
                    self._append_log_locked(
                        task_id,
                        "❌ Task completed with failure."
                    )
                self._save_state()
        except Exception as exc:
            self._fail_task(
                task_id,
                exc
            )
        finally:
            stop_monitor.set()
            with self._lock:
                self._cancel_events.pop(
                    task_id,
                    None
                )
                self._active_workers = max(
                    0,
                    self._active_workers - 1
                )
                self._save_state()
                self._ensure_workers_locked()
    def _set_phase(
        self,
        task_id: str,
        phase: str
    ) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            if task.get("status") != "running":
                return
            task["phase"] = phase
            task["progress"] = self.PHASE_PROGRESS.get(
                phase,
                task.get("progress", 10)
            )
            self._sync_live_state_locked(
                task_id,
                persist=False
            )
            self._save_state()
    def _fail_task(
        self,
        task_id: str,
        error: Exception
    ) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            if (
                task.get("cancel_requested")
                or (
                    task_id in self._cancel_events
                    and self._cancel_events[task_id].is_set()
                )
            ):
                self._mark_cancelled_locked(
                    task_id,
                    "Task cancelled during execution."
                )
                self._save_state()
                return
            task["status"] = "failed"
            task["phase"] = "failed"
            task["progress"] = 100
            task["error"] = (
                f"{type(error).__name__}: {error}"
            )
            task["finished_at"] = self._now()
            task["completed_at"] = task["finished_at"]
            logs = get_logs()
            if isinstance(logs, list) and logs:
                task["logs"] = [
                    str(line)
                    for line in logs[-self.MAX_LOGS:]
                ]
            self._append_log_locked(
                task_id,
                f"❌ Task failed: {type(error).__name__}: {error}"
            )
            self._calculate_duration_locked(task)
            self._save_state()
    def refresh_task_state(self) -> None:
        with self._lock:
            for task_id in self._order:
                task = self._tasks.get(task_id)
                if (
                    task
                    and task.get("status") == "running"
                ):
                    self._sync_live_state_locked(
                        task_id,
                        persist=False
                    )
            self._save_state()