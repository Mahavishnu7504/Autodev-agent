from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from agent.executor import execute_plan
from agent.logger import clear_logs, get_logs
from agent.planner import create_plan


class TaskManager:
    """Lightweight in-process background task manager.

    AutoDev currently uses a global logger, so executions are serialized to
    keep logs from different projects from mixing together.
    """

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
    }

    def __init__(self, max_history: int = 50) -> None:
        self.max_history = max_history
        self._tasks: Dict[str, Dict[str, Any]] = {}
        self._order: list[str] = []
        self._lock = threading.RLock()
        self._execution_lock = threading.Lock()

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _snapshot(self, record: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            result = dict(record)
            result["logs"] = list(record.get("logs", []))
            result["inputs"] = dict(record.get("inputs") or {})
            return result

    def create_task(self, task: str, inputs: Optional[dict] = None) -> Dict[str, Any]:
        task_id = uuid.uuid4().hex
        record = {
            "task_id": task_id,
            "task": task,
            "inputs": inputs or {},
            "status": "queued",
            "phase": "queued",
            "progress": 0,
            "created_at": self._timestamp(),
            "started_at": None,
            "completed_at": None,
            "duration_seconds": None,
            "plan": None,
            "result": None,
            "error": None,
            "logs": [],
        }

        with self._lock:
            self._tasks[task_id] = record
            self._order.insert(0, task_id)
            while len(self._order) > self.max_history:
                old_id = self._order.pop()
                self._tasks.pop(old_id, None)

        return self._snapshot(record)

    def start_task(self, task_id: str) -> None:
        thread = threading.Thread(
            target=self._run_task,
            args=(task_id,),
            name=f"autodev-{task_id[:8]}",
            daemon=True,
        )
        thread.start()

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            record = self._tasks.get(task_id)
            return None if record is None else self._snapshot(record)

    def list_tasks(self, limit: int = 20) -> list[Dict[str, Any]]:
        with self._lock:
            ids = self._order[:max(1, limit)]
            return [
                self._snapshot(self._tasks[task_id])
                for task_id in ids
                if task_id in self._tasks
            ]

    def _update(self, task_id: str, **changes: Any) -> None:
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id].update(changes)

    @classmethod
    def _phase_from_logs(cls, logs: list[str]) -> tuple[str, int]:
        text = "\n".join(logs[-30:]).lower()

        if any(x in text for x in ("zip created", "project zip", "packaging")):
            return "packaging", 95
        if "quality gate" in text or "quality_gate" in text:
            return "quality_gate", 85
        if any(x in text for x in ("repair attempt", "autonomous repair", "repairing")):
            return "repairing", 70
        if any(x in text for x in ("failure diagnosis", "diagnos", "analyzing failure")):
            return "diagnosing", 60
        if any(x in text for x in ("running tests", "test validation", "test suite", "tests")):
            return "testing", 50
        if any(x in text for x in ("generating project", "generating files", "writing project", "project generated")):
            return "generating", 25
        return "planning", 10

    def _sync_live_state(self, task_id: str) -> None:
        logs = list(get_logs())
        phase, progress = self._phase_from_logs(logs)
        with self._lock:
            record = self._tasks.get(task_id)
            if record and record["status"] == "running":
                record["logs"] = logs
                record["phase"] = phase
                record["progress"] = progress

    def _run_task(self, task_id: str) -> None:
        # Serialize pipeline execution because agent.logger uses global state.
        with self._execution_lock:
            started = time.perf_counter()
            self._update(
                task_id,
                status="running",
                phase="planning",
                progress=10,
                started_at=self._timestamp(),
                error=None,
            )

            try:
                record = self.get_task(task_id)
                if record is None:
                    return

                clear_logs()
                self._update(task_id, logs=[])

                plan = create_plan(record["task"], record.get("inputs") or {})
                self._update(
                    task_id,
                    plan=plan,
                    phase="generating",
                    progress=25,
                    logs=list(get_logs()),
                )

                result = execute_plan(
                    plan=plan,
                    inputs=record.get("inputs") or {},
                    task=record["task"],
                )

                self._sync_live_state(task_id)
                success = bool(result.get("success"))
                duration = round(time.perf_counter() - started, 3)
                final_status = "completed" if success else "failed"

                self._update(
                    task_id,
                    status=final_status,
                    phase=final_status,
                    progress=100,
                    result=result,
                    logs=list(get_logs()),
                    completed_at=self._timestamp(),
                    duration_seconds=duration,
                )

            except Exception as exc:
                self._sync_live_state(task_id)
                self._update(
                    task_id,
                    status="failed",
                    phase="failed",
                    progress=100,
                    error=f"{type(exc).__name__}: {exc}",
                    completed_at=self._timestamp(),
                    duration_seconds=round(time.perf_counter() - started, 3),
                    logs=list(get_logs()),
                )
