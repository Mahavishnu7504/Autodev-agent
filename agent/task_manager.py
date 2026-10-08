from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from agent.executor import execute_plan
from agent.logger import clear_logs, get_logs
from agent.planner import create_plan

APP_STATE_DIR = Path(
    os.getenv(
        "AUTODEV_STATE_DIR",
        Path.home() / ".autodev_agent",
    )
)

TASK_STATE_FILE = APP_STATE_DIR / "tasks.json"

MAX_STORED_LOGS = 500


class TaskCancelled(Exception):
    """Raised when a task is cooperatively cancelled."""


class TaskManager:
    """Persistent background task manager with recovery, cancellation,
    retry support and controlled concurrency.

    AutoDev currently uses a global logger, so only one pipeline
    execution is allowed at a time.
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
        "cancelled": 100,
        "failed": 100,
    }

    TERMINAL_STATUSES = {
        "completed",
        "failed",
        "cancelled",
    }

    ACTIVE_STATUSES = {
        "queued",
        "running",
    }

    def __init__(
        self,
        max_history: int = 50,
        max_concurrent_tasks: int = 1,
    ) -> None:
        self.max_history = max(
            1,
            max_history,
        )

        self.max_concurrent_tasks = max(
            1,
            max_concurrent_tasks,
        )

        self._execution_lock = threading.Lock()
        self._lock = threading.RLock()

        self._tasks: Dict[
            str,
            Dict[str, Any],
        ] = {}

        self._order: list[str] = []

        self._threads: Dict[
            str,
            threading.Thread,
        ] = {}

        self._load_state()
        self._recover_after_restart()

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(
            timezone.utc
        ).isoformat()

    @staticmethod
    def _json_safe(
        value: Any,
    ) -> Any:
        if value is None:
            return None

        if isinstance(
            value,
            (
                str,
                int,
                float,
                bool,
            ),
        ):
            return value

        if isinstance(
            value,
            Path,
        ):
            return str(value)

        if isinstance(
            value,
            dict,
        ):
            return {
                str(key): TaskManager._json_safe(
                    item
                )
                for key, item in value.items()
            }

        if isinstance(
            value,
            (
                list,
                tuple,
                set,
            ),
        ):
            return [
                TaskManager._json_safe(item)
                for item in value
            ]

        return str(value)

    def _snapshot(
        self,
        record: Dict[str, Any],
    ) -> Dict[str, Any]:
        with self._lock:
            result = dict(record)

            result["logs"] = list(
                record.get(
                    "logs",
                    [],
                )
            )

            result["inputs"] = dict(
                record.get(
                    "inputs",
                    {},
                )
                or {}
            )

            return self._json_safe(
                result
            )

    def _save_state_locked(self) -> None:
        APP_STATE_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        payload = {
            "version": 2,
            "updated_at": self._timestamp(),
            "tasks": [
                self._json_safe(
                    self._tasks[task_id]
                )
                for task_id in self._order
                if task_id in self._tasks
            ],
        }

        temp_file = TASK_STATE_FILE.with_suffix(
            ".tmp"
        )

        temp_file.write_text(
            json.dumps(
                payload,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        temp_file.replace(
            TASK_STATE_FILE
        )

    def _load_state(self) -> None:
        try:
            if not TASK_STATE_FILE.is_file():
                return

            payload = json.loads(
                TASK_STATE_FILE.read_text(
                    encoding="utf-8"
                )
            )

            raw_tasks = payload.get(
                "tasks",
                [],
            )

            if not isinstance(
                raw_tasks,
                list,
            ):
                return

            with self._lock:
                for item in raw_tasks:
                    if not isinstance(
                        item,
                        dict,
                    ):
                        continue

                    task_id = str(
                        item.get(
                            "task_id",
                            "",
                        )
                    ).strip()

                    task = str(
                        item.get(
                            "task",
                            "",
                        )
                    ).strip()

                    if not task_id or not task:
                        continue

                    item.setdefault(
                        "logs",
                        [],
                    )

                    item.setdefault(
                        "inputs",
                        {},
                    )

                    self._tasks[
                        task_id
                    ] = item

                    self._order.append(
                        task_id
                    )

                self._order = list(
                    dict.fromkeys(
                        self._order
                    )
                )[
                    :self.max_history
                ]

                self._tasks = {
                    task_id: self._tasks[
                        task_id
                    ]
                    for task_id in self._order
                    if task_id in self._tasks
                }

        except Exception:
            self._tasks = {}
            self._order = []

    def _recover_after_restart(
        self,
    ) -> None:
        changed = False

        with self._lock:
            for task_id in list(
                self._order
            ):
                record = self._tasks.get(
                    task_id
                )

                if not record:
                    continue

                status = record.get(
                    "status"
                )

                if status in {
                    "running",
                    "queued",
                }:
                    record["status"] = "failed"
                    record["phase"] = "failed"
                    record["progress"] = 100
                    record["error"] = (
                        "Task interrupted by "
                        "AutoDev server restart."
                    )
                    record[
                        "recovery_state"
                    ] = "interrupted"
                    record[
                        "completed_at"
                    ] = self._timestamp()
                    record[
                        "cancel_requested"
                    ] = False

                    changed = True

            if changed:
                self._save_state_locked()

    def create_task(
        self,
        task: str,
        inputs: Optional[dict] = None,
    ) -> Dict[str, Any]:
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
            "cancel_requested": False,
            "recovery_state": None,
            "retry_of": None,
            "thread_started": False,
        }

        with self._lock:
            self._tasks[
                task_id
            ] = record

            self._order.insert(
                0,
                task_id,
            )

            self._prune_locked()
            self._save_state_locked()

        return self._snapshot(
            record
        )

    def _prune_locked(self) -> None:
        while len(
            self._order
        ) > self.max_history:
            old_id = self._order.pop()

            self._tasks.pop(
                old_id,
                None,
            )

    def start_task(
        self,
        task_id: str,
    ) -> None:
        with self._lock:
            record = self._tasks.get(
                task_id
            )

            if record is None:
                raise KeyError(
                    f"Task not found: {task_id}"
                )

            if record.get(
                "status"
            ) in self.TERMINAL_STATUSES:
                return

            existing = self._threads.get(
                task_id
            )

            if (
                existing
                and existing.is_alive()
            ):
                return

            record["status"] = "queued"
            record["phase"] = "queued"
            record["progress"] = 0
            record["thread_started"] = True
            record["error"] = None
            record["cancel_requested"] = False

            self._save_state_locked()

        thread = threading.Thread(
            target=self._run_task,
            args=(task_id,),
            name=f"autodev-{task_id[:8]}",
            daemon=True,
        )

        with self._lock:
            self._threads[
                task_id
            ] = thread

        thread.start()

    def cancel_task(
        self,
        task_id: str,
    ) -> Dict[str, Any]:
        with self._lock:
            record = self._tasks.get(
                task_id
            )

            if record is None:
                raise KeyError(
                    f"Task not found: {task_id}"
                )

            status = record.get(
                "status"
            )

            if status in self.TERMINAL_STATUSES:
                return self._snapshot(
                    record
                )

            record[
                "cancel_requested"
            ] = True

            if status == "queued":
                record["status"] = "cancelled"
                record["phase"] = "cancelled"
                record["progress"] = 100
                record[
                    "completed_at"
                ] = self._timestamp()
                record["error"] = (
                    "Task cancelled before "
                    "execution started."
                )

            self._save_state_locked()

            return self._snapshot(
                record
            )

    def retry_task(
        self,
        task_id: str,
    ) -> Dict[str, Any]:
        with self._lock:
            record = self._tasks.get(
                task_id
            )

            if record is None:
                raise KeyError(
                    f"Task not found: {task_id}"
                )

            if record.get(
                "status"
            ) not in self.TERMINAL_STATUSES:
                raise ValueError(
                    "Only completed, failed, "
                    "or cancelled tasks can "
                    "be retried."
                )

            task = str(
                record.get(
                    "task",
                    "",
                )
            )

            inputs = dict(
                record.get(
                    "inputs",
                    {},
                )
                or {}
            )

        retry = self.create_task(
            task,
            inputs,
        )

        with self._lock:
            retry_record = self._tasks[
                retry["task_id"]
            ]

            retry_record[
                "retry_of"
            ] = task_id

            self._save_state_locked()

        self.start_task(
            retry["task_id"]
        )

        return (
            self.get_task(
                retry["task_id"]
            )
            or retry
        )

    def get_task(
        self,
        task_id: str,
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            record = self._tasks.get(
                task_id
            )

            if record is None:
                return None

            return self._snapshot(
                record
            )

    def list_tasks(
        self,
        limit: int = 20,
    ) -> list[Dict[str, Any]]:
        with self._lock:
            ids = self._order[
                :max(
                    1,
                    limit,
                )
            ]

            return [
                self._snapshot(
                    self._tasks[task_id]
                )
                for task_id in ids
                if task_id in self._tasks
            ]

    def _update(
        self,
        task_id: str,
        persist: bool = True,
        **changes: Any,
    ) -> None:
        with self._lock:
            record = self._tasks.get(
                task_id
            )

            if record is None:
                return

            record.update(
                changes
            )

            if persist:
                self._save_state_locked()

    def _is_cancel_requested(
        self,
        task_id: str,
    ) -> bool:
        with self._lock:
            record = self._tasks.get(
                task_id
            )

            return bool(
                record
                and record.get(
                    "cancel_requested"
                )
            )

    def _check_cancelled(
        self,
        task_id: str,
    ) -> None:
        if self._is_cancel_requested(
            task_id
        ):
            raise TaskCancelled(
                "Task cancellation requested."
            )

    @classmethod
    def _phase_from_logs(
        cls,
        logs: list[str],
    ) -> tuple[str, int]:
        text = "\n".join(
            logs[-40:]
        ).lower()

        if any(
            x in text
            for x in (
                "zip created",
                "project zip",
                "packaging",
            )
        ):
            return (
                "packaging",
                95,
            )

        if (
            "quality gate" in text
            or "quality_gate" in text
        ):
            return (
                "quality_gate",
                85,
            )

        if any(
            x in text
            for x in (
                "repair attempt",
                "autonomous repair",
                "repairing",
            )
        ):
            return (
                "repairing",
                70,
            )

        if any(
            x in text
            for x in (
                "failure diagnosis",
                "diagnos",
                "analyzing failure",
            )
        ):
            return (
                "diagnosing",
                60,
            )

        if any(
            x in text
            for x in (
                "running tests",
                "test validation",
                "test suite",
                "tests",
            )
        ):
            return (
                "testing",
                50,
            )

        if any(
            x in text
            for x in (
                "generating project",
                "generating files",
                "writing project",
                "project generated",
            )
        ):
            return (
                "generating",
                25,
            )

        return (
            "planning",
            10,
        )

    def _sync_live_state(
        self,
        task_id: str,
    ) -> None:
        logs = list(
            get_logs()
        )[
            -MAX_STORED_LOGS:
        ]

        phase, progress = (
            self._phase_from_logs(
                logs
            )
        )

        with self._lock:
            record = self._tasks.get(
                task_id
            )

            if (
                record
                and record["status"]
                == "running"
            ):
                record["logs"] = logs
                record["phase"] = phase
                record[
                    "progress"
                ] = progress

                self._save_state_locked()

    def _run_task(
        self,
        task_id: str,
    ) -> None:
        started = time.perf_counter()

        try:
            with self._execution_lock:
                self._check_cancelled(
                    task_id
                )

                self._update(
                    task_id,
                    status="running",
                    phase="planning",
                    progress=10,
                    started_at=self._timestamp(),
                    error=None,
                )

                record = self.get_task(
                    task_id
                )

                if record is None:
                    return

                clear_logs()

                self._update(
                    task_id,
                    logs=[],
                )

                self._check_cancelled(
                    task_id
                )

                plan = create_plan(
                    record["task"],
                    record.get(
                        "inputs",
                        {},
                    ),
                )

                self._check_cancelled(
                    task_id
                )

                self._update(
                    task_id,
                    plan=plan,
                    phase="generating",
                    progress=25,
                    logs=list(
                        get_logs()
                    )[
                        -MAX_STORED_LOGS:
                    ],
                )

                self._check_cancelled(
                    task_id
                )

                result = execute_plan(
                    plan=plan,
                    inputs=record.get(
                        "inputs",
                        {},
                    ),
                    task=record["task"],
                )

                self._sync_live_state(
                    task_id
                )

                self._check_cancelled(
                    task_id
                )

                success = bool(
                    result.get(
                        "success"
                    )
                )

                duration = round(
                    time.perf_counter()
                    - started,
                    3,
                )

                final_status = (
                    "completed"
                    if success
                    else "failed"
                )

                self._update(
                    task_id,
                    status=final_status,
                    phase=final_status,
                    progress=100,
                    result=result,
                    logs=list(
                        get_logs()
                    )[
                        -MAX_STORED_LOGS:
                    ],
                    completed_at=self._timestamp(),
                    duration_seconds=duration,
                    cancel_requested=False,
                )

        except TaskCancelled as exc:
            self._update(
                task_id,
                status="cancelled",
                phase="cancelled",
                progress=100,
                error=str(exc),
                completed_at=self._timestamp(),
                duration_seconds=round(
                    time.perf_counter()
                    - started,
                    3,
                ),
                logs=list(
                    get_logs()
                )[
                    -MAX_STORED_LOGS:
                ],
            )

        except Exception as exc:
            self._sync_live_state(
                task_id
            )

            self._update(
                task_id,
                status="failed",
                phase="failed",
                progress=100,
                error=(
                    f"{type(exc).__name__}: "
                    f"{exc}"
                ),
                completed_at=self._timestamp(),
                duration_seconds=round(
                    time.perf_counter()
                    - started,
                    3,
                ),
                logs=list(
                    get_logs()
                )[
                    -MAX_STORED_LOGS:
                ],
            )

        finally:
            with self._lock:
                self._threads.pop(
                    task_id,
                    None,
                )

                self._save_state_locked()