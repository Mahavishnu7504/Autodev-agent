import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


BASE_DIR = Path(__file__).resolve().parent.parent
HISTORY_FILE = BASE_DIR / "task_history.json"

MAX_HISTORY = 100

_lock = threading.Lock()


def _load_history():
    if not HISTORY_FILE.exists():
        return []

    try:
        with open(
            HISTORY_FILE,
            "r",
            encoding="utf-8"
        ) as file:
            data = json.load(file)

        if isinstance(data, list):
            return data

        return []

    except Exception:
        return []


def _save_history(history):
    temp_file = HISTORY_FILE.with_suffix(".tmp")

    with open(
        temp_file,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            history,
            file,
            indent=2,
            ensure_ascii=False,
            default=str
        )

    temp_file.replace(HISTORY_FILE)


def create_task_record(
    task,
    status,
    plan=None,
    logs=None,
    result=None,
    generated_file=None,
    duration_seconds=None,
    inputs=None
):
    record = {
        "id": str(uuid4()),
        "timestamp": datetime.now(
            timezone.utc
        ).isoformat(),

        "task": task,

        "status": status,

        "inputs": inputs or {},

        "generated_file": generated_file,

        "duration_seconds":
            round(
                duration_seconds,
                3
            )
            if duration_seconds is not None
            else None,

        "plan": plan,

        "logs": logs or [],

        "result": result
    }

    with _lock:

        history = _load_history()

        history.insert(
            0,
            record
        )

        history = history[:MAX_HISTORY]

        _save_history(history)

    return record


def get_history(limit=50):

    with _lock:

        history = _load_history()

    try:
        limit = int(limit)
    except Exception:
        limit = 50

    limit = max(
        1,
        min(
            limit,
            MAX_HISTORY
        )
    )

    return history[:limit]


def get_task(task_id):

    with _lock:

        history = _load_history()

    for task in history:

        if task.get("id") == task_id:
            return task

    return None


def clear_history():

    with _lock:

        _save_history([])

    return True