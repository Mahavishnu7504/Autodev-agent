from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any


import chromadb


BASE_DIR = Path(__file__).resolve().parent.parent
MEMORY_DIR = BASE_DIR / "memory"

MEMORY_DIR.mkdir(
    parents=True,
    exist_ok=True,
)

_client = chromadb.PersistentClient(
    path=str(MEMORY_DIR)
)

_collection = _client.get_or_create_collection(
    name="autodev_experiences",
    metadata={
        "description": (
            "Successful AutoDev engineering experiences"
        )
    },
)


MAX_TASK_CHARS = 1000
MAX_SUMMARY_CHARS = 1500
MAX_LESSONS = 10
MAX_REPAIR_HISTORY = 10


def _safe_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    except Exception:
        return str(value)


def _clean_list(
    value: Any,
    limit: int,
) -> list[str]:
    if not isinstance(value, list):
        return []

    result = []

    for item in value:
        text = str(item).strip()

        if text:
            result.append(text[:1000])

        if len(result) >= limit:
            break

    return result


def _build_lessons(
    result: dict[str, Any],
) -> list[str]:
    lessons = _clean_list(
        result.get("lessons", []),
        MAX_LESSONS,
    )

    repair_history = result.get(
        "repair_history",
        [],
    )

    if isinstance(repair_history, list):
        for item in repair_history[:MAX_REPAIR_HISTORY]:
            if not isinstance(item, dict):
                continue

            summary = str(
                item.get("summary", "")
            ).strip()

            if summary:
                lessons.append(
                    f"Repair ({item.get('phase', 'unknown')}): "
                    f"{summary}"
                )

            if len(lessons) >= MAX_LESSONS:
                break

    return lessons[:MAX_LESSONS]


def save_memory(
    task: str,
    plan: dict[str, Any],
    result: dict[str, Any],
) -> str | None:
    """
    Save only successful AutoDev executions.
    """

    if not isinstance(result, dict):
        return None

    if not result.get("success"):
        return None

    task = str(task or "").strip()

    if not task:
        return None

    project_name = str(
        plan.get(
            "project_name",
            "Unknown Project",
        )
    ).strip()

    files = plan.get(
        "files",
        [],
    )

    file_paths = [
        str(item.get("path"))
        for item in files
        if isinstance(item, dict)
        and item.get("path")
    ]

    behavior = plan.get(
        "behavior_specification",
        {},
    )

    lessons = _build_lessons(result)

    experience = {
        "task": task[:MAX_TASK_CHARS],
        "project_name": project_name[:500],
        "summary": str(
            plan.get(
                "summary",
                "",
            )
        )[:MAX_SUMMARY_CHARS],
        "language": str(
            plan.get(
                "language",
                "python",
            )
        ),
        "behavior_specification": behavior,
        "file_paths": file_paths[:50],
        "tests_passed": bool(
            result.get(
                "tests_passed",
                False,
            )
        ),
        "application_passed": bool(
            result.get(
                "application_passed",
                False,
            )
        ),
        "repair_attempts": int(
            result.get(
                "repair_attempts",
                0,
            )
            or 0
        ),
        "lessons": lessons,
    }

    document = _safe_json(
        experience
    )

    memory_id = str(
        uuid.uuid4()
    )

    metadata = {
        "task": task[:MAX_TASK_CHARS],
        "project_name": project_name[:500],
        "language": str(
            plan.get(
                "language",
                "python",
            )
        ),
        "success": True,
        "repair_attempts": int(
            result.get(
                "repair_attempts",
                0,
            )
            or 0
        ),
    }

    _collection.add(
        ids=[memory_id],
        documents=[document],
        metadatas=[metadata],
    )

    return memory_id


def search_memory(
    task: str,
    n_results: int = 3,
) -> list[dict[str, Any]]:
    """
    Retrieve semantically similar successful experiences.
    """

    task = str(task or "").strip()

    if not task:
        return []

    try:
        total = _collection.count()

        if total == 0:
            return []

        n_results = max(
            1,
            min(
                int(n_results),
                total,
            ),
        )

        results = _collection.query(
            query_texts=[task],
            n_results=n_results,
        )

    except Exception:
        return []

    documents = (
        results.get(
            "documents",
            [[]],
        )[0]
        if results
        else []
    )

    distances = (
        results.get(
            "distances",
            [[]],
        )[0]
        if results
        else []
    )

    memories = []

    for index, document in enumerate(
        documents
    ):
        try:
            data = json.loads(
                document
            )

        except (
            json.JSONDecodeError,
            TypeError,
        ):
            data = {
                "experience": document
            }

        if index < len(distances):
            data["_distance"] = distances[index]

        memories.append(data)

    return memories


def format_memories_for_prompt(
    memories: list[dict[str, Any]],
) -> str:
    """
    Convert retrieved experiences into compact planner context.
    """

    if not memories:
        return (
            "No relevant previous experience found."
        )

    sections = []

    for index, memory in enumerate(
        memories,
        start=1,
    ):
        behavior = memory.get(
            "behavior_specification",
            {},
        )

        lessons = memory.get(
            "lessons",
            [],
        )

        sections.append(
            "\n".join([
                f"Experience {index}",
                (
                    "Task: "
                    f"{memory.get('task', '')}"
                ),
                (
                    "Project: "
                    f"{memory.get('project_name', '')}"
                ),
                (
                    "Summary: "
                    f"{memory.get('summary', '')}"
                ),
                (
                    "Language: "
                    f"{memory.get('language', 'python')}"
                ),
                (
                    "Behavior: "
                    f"{_safe_json(behavior)}"
                ),
                (
                    "Repair attempts: "
                    f"{memory.get('repair_attempts', 0)}"
                ),
                (
                    "Lessons: "
                    f"{_safe_json(lessons)}"
                ),
            ])
        )

    return "\n\n".join(
        sections
    )


def memory_count() -> int:
    try:
        return _collection.count()
    except Exception:
        return 0


def clear_memory() -> None:
    global _collection

    _client.delete_collection(
        "autodev_experiences"
    )

    _collection = _client.get_or_create_collection(
        name="autodev_experiences",
        metadata={
            "description": (
                "Successful AutoDev engineering experiences"
            )
        },
    )