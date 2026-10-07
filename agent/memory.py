"""
AutoDev Agent - Persistent Experience Memory

Stores successful AutoDev experiences so future tasks can benefit from
previous plans, execution results, and repair history.
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from chromadb import Client
from chromadb.config import Settings

from agent.logger import log


# ============================================================
# PATHS
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent
MEMORY_DIR = BASE_DIR / "memory"

MEMORY_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# CHROMA
# ============================================================

client = Client(
    Settings(
        persist_directory=str(MEMORY_DIR)
    )
)

collection = client.get_or_create_collection(
    name="autodev"
)


# ============================================================
# HELPERS
# ============================================================

def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _compact_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    """
    Store useful planning information without storing complete
    source-code contents inside memory.
    """

    if not isinstance(plan, dict):
        return {}

    files = []

    for item in plan.get("files", []):
        if not isinstance(item, dict):
            continue

        path = str(item.get("path", "")).strip()

        if path:
            files.append(path)

    return {
        "project_name": str(
            plan.get("project_name", "")
        ).strip(),

        "summary": str(
            plan.get("summary", "")
        ).strip(),

        "language": str(
            plan.get("language", "")
        ).strip(),

        "run_command": str(
            plan.get("run_command", "")
        ).strip(),

        "test_command": str(
            plan.get("test_command", "")
        ).strip(),

        "files": files,
    }


def _compact_result(result: Any) -> Dict[str, Any]:
    """
    Extract only useful execution information.

    Avoid storing huge logs, source code, or generated artifacts.
    """

    if not isinstance(result, dict):
        return {
            "success": False,
            "repair_attempts": 0,
        }

    repair_history = result.get(
        "repair_history",
        []
    )

    compact_repairs = []

    if isinstance(repair_history, list):
        for repair in repair_history:
            if not isinstance(repair, dict):
                continue

            changed_files = repair.get(
                "files_changed",
                repair.get("changed_files", [])
            )

            if not isinstance(changed_files, list):
                changed_files = []

            compact_repairs.append({
                "attempt": repair.get("attempt"),
                "phase": repair.get("phase"),
                "success": repair.get("success"),
                "files_changed": [
                    str(path)
                    for path in changed_files
                ],
            })

    return {
        "success": bool(
            result.get("success", False)
        ),

        "repair_attempts": _safe_int(
            result.get("repair_attempts", 0)
        ),

        "repair_history": compact_repairs,

        "test_passed": bool(
            result.get("test_passed", False)
        ),

        "application_passed": bool(
            result.get("application_passed", False)
        ),
    }


# ============================================================
# SAVE MEMORY
# ============================================================

def save_memory(
    task: str,
    plan: Dict[str, Any],
    result: Dict[str, Any] | None = None,
) -> str | None:
    """
    Save one completed AutoDev experience.

    Returns:
        Memory ID on success.
        None if memory storage fails.

    Memory is intentionally compact so the vector database does not
    become filled with complete source files or massive logs.
    """

    try:
        task = str(task or "").strip()

        if not task:
            raise ValueError(
                "Cannot save memory without a task."
            )

        compact_plan = _compact_plan(
            plan
        )

        compact_result = _compact_result(
            result
        )

        experience = {
            "task": task,

            "plan": compact_plan,

            "result": compact_result,

            "saved_at": datetime.now(
                timezone.utc
            ).isoformat(),
        }

        document = json.dumps(
            experience,
            ensure_ascii=False,
        )

        metadata = {
            "task": task[:1000],

            "project_name": str(
                compact_plan.get(
                    "project_name",
                    ""
                )
            )[:500],

            "success": bool(
                compact_result.get(
                    "success",
                    False
                )
            ),

            "repair_attempts": _safe_int(
                compact_result.get(
                    "repair_attempts",
                    0
                )
            ),

            "saved_at": experience[
                "saved_at"
            ],
        }

        memory_id = str(
            uuid.uuid4()
        )

        collection.add(
            documents=[document],
            metadatas=[metadata],
            ids=[memory_id],
        )

        log(
            f"🧠 Memory saved: "
            f"{compact_plan.get('project_name', 'unknown')} "
            f"| repairs="
            f"{compact_result.get('repair_attempts', 0)}"
        )

        return memory_id

    except Exception as exc:
        # Memory should never break the main AutoDev pipeline.
        log(
            f"⚠️ Memory save failed: {exc}"
        )
        return None


# ============================================================
# SEARCH MEMORY
# ============================================================

def search_memory(
    task: str,
    n_results: int = 3,
) -> List[Dict[str, Any]]:
    """
    Retrieve the most relevant previous AutoDev experiences.

    Returns a list of structured memory objects.
    """

    try:
        task = str(task or "").strip()

        if not task:
            return []

        count = collection.count()

        if count <= 0:
            return []

        n_results = max(
            1,
            min(
                int(n_results),
                count,
            )
        )

        results = collection.query(
            query_texts=[task],
            n_results=n_results,
        )

        documents = (
            results.get("documents") or [[]]
        )[0]

        metadatas = (
            results.get("metadatas") or [[]]
        )[0]

        distances = (
            results.get("distances") or [[]]
        )[0]

        memories: List[Dict[str, Any]] = []

        for index, document in enumerate(
            documents
        ):
            try:
                experience = json.loads(
                    document
                )

                if not isinstance(
                    experience,
                    dict,
                ):
                    continue

            except (
                json.JSONDecodeError,
                TypeError,
            ):
                continue

            metadata = {}

            if index < len(metadatas):
                metadata = (
                    metadatas[index] or {}
                )

            distance = None

            if index < len(distances):
                distance = distances[index]

            memories.append({
                "task": experience.get(
                    "task",
                    metadata.get(
                        "task",
                        ""
                    ),
                ),

                "project_name": (
                    experience
                    .get("plan", {})
                    .get(
                        "project_name",
                        metadata.get(
                            "project_name",
                            "",
                        ),
                    )
                ),

                "summary": (
                    experience
                    .get("plan", {})
                    .get(
                        "summary",
                        "",
                    )
                ),

                "language": (
                    experience
                    .get("plan", {})
                    .get(
                        "language",
                        "",
                    )
                ),

                "run_command": (
                    experience
                    .get("plan", {})
                    .get(
                        "run_command",
                        "",
                    )
                ),

                "test_command": (
                    experience
                    .get("plan", {})
                    .get(
                        "test_command",
                        "",
                    )
                ),

                "files": (
                    experience
                    .get("plan", {})
                    .get(
                        "files",
                        [],
                    )
                ),

                "success": (
                    experience
                    .get("result", {})
                    .get(
                        "success",
                        metadata.get(
                            "success",
                            False,
                        ),
                    )
                ),

                "repair_attempts": (
                    experience
                    .get("result", {})
                    .get(
                        "repair_attempts",
                        metadata.get(
                            "repair_attempts",
                            0,
                        ),
                    )
                ),

                "repair_history": (
                    experience
                    .get("result", {})
                    .get(
                        "repair_history",
                        [],
                    )
                ),

                "saved_at": experience.get(
                    "saved_at",
                    metadata.get(
                        "saved_at",
                        "",
                    ),
                ),

                "distance": distance,
            })

        log(
            f"🔎 Memory search: "
            f"{len(memories)} relevant experience(s)"
        )

        return memories

    except Exception as exc:
        # Retrieval failure should never stop planning.
        log(
            f"⚠️ Memory search failed: {exc}"
        )
        return []


# ============================================================
# FORMAT MEMORY FOR LLM PROMPTS
# ============================================================

def format_memories_for_prompt(
    memories: List[Dict[str, Any]],
) -> str:
    """
    Convert retrieved memories into concise planner context.
    """

    if not memories:
        return (
            "No relevant previous AutoDev experiences "
            "were found."
        )

    sections = []

    for index, memory in enumerate(
        memories,
        start=1,
    ):
        files = memory.get(
            "files",
            [],
        )

        if not isinstance(files, list):
            files = []

        changed_files = []

        for repair in memory.get(
            "repair_history",
            [],
        ):
            if not isinstance(
                repair,
                dict,
            ):
                continue

            changed_files.extend(
                repair.get(
                    "files_changed",
                    [],
                )
            )

        section = f"""
Previous Experience #{index}

Task:
{memory.get("task", "")}

Project:
{memory.get("project_name", "")}

Summary:
{memory.get("summary", "")}

Language:
{memory.get("language", "")}

Run Command:
{memory.get("run_command", "")}

Test Command:
{memory.get("test_command", "")}

Project Files:
{", ".join(files) if files else "None"}

Previous Result:
{"SUCCESS" if memory.get("success") else "FAILED"}

Repair Attempts:
{memory.get("repair_attempts", 0)}

Files Changed During Repair:
{", ".join(changed_files) if changed_files else "None"}
""".strip()

        sections.append(section)

    return "\n\n".join(
        sections
    )


# ============================================================
# MEMORY STATS
# ============================================================

def memory_count() -> int:
    """
    Return the number of stored experiences.
    """

    try:
        return collection.count()
    except Exception as exc:
        log(
            f"⚠️ Memory count failed: {exc}"
        )
        return 0