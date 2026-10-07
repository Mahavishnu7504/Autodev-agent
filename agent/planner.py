"""
AutoDev Agent - Structured Project Planner

The planner produces a complete, multi-file Python project specification.

Before planning, AutoDev searches its persistent experience memory and
injects relevant previous experiences into the planning context.
"""

import json
import re
import time
from pathlib import Path
from typing import Dict, Any

from groq import Groq

from config import GROQ_API_KEY, MODEL_FALLBACKS, MAX_RETRIES
from agent.logger import log
from agent.memory import search_memory, format_memories_for_prompt


client = Groq(api_key=GROQ_API_KEY)


def _extract_json(text: str) -> Dict[str, Any]:
    text = (text or "").strip()

    if text.startswith("```"):
        text = re.sub(
            r"^```(?:json)?\s*",
            "",
            text,
            flags=re.I,
        )

        text = re.sub(
            r"\s*```$",
            "",
            text,
        )

    try:
        return json.loads(text)

    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")

        if start >= 0 and end > start:
            return json.loads(
                text[start:end + 1]
            )

    raise ValueError(
        "Planner returned invalid JSON."
    )


def _validate_project(
    payload: Dict[str, Any],
) -> Dict[str, Any]:

    required = [
        "project_name",
        "summary",
        "language",
        "files",
    ]

    for key in required:
        if key not in payload:
            raise ValueError(
                f"Planner response missing '{key}'."
            )

    if str(
        payload["language"]
    ).lower() != "python":

        raise ValueError(
            "This AutoDev stage currently supports "
            "Python projects."
        )

    if not isinstance(
        payload["files"],
        list,
    ):
        raise ValueError(
            "Planner 'files' must be a list."
        )

    clean_files = []

    for item in payload["files"]:

        if not isinstance(
            item,
            dict,
        ):
            continue

        path = str(
            item.get("path", "")
        ).strip()

        content = str(
            item.get("content", "")
        )

        if not path or not content.strip():
            continue

        normalized = (
            path
            .replace("\\", "/")
            .lstrip("/")
        )

        if ".." in Path(
            normalized
        ).parts:

            raise ValueError(
                f"Unsafe project path: {path}"
            )

        clean_files.append({
            "path": normalized,
            "content": content,
        })

    if not clean_files:
        raise ValueError(
            "Planner produced no usable files."
        )

    payload["project_name"] = str(
        payload["project_name"]
    ).strip()

    payload["summary"] = str(
        payload["summary"]
    ).strip()

    payload["files"] = clean_files

    payload["run_command"] = str(
        payload.get("run_command") or ""
    ).strip()

    payload["test_command"] = str(
        payload.get("test_command")
        or
        "python -m unittest discover -s tests -v"
    ).strip()

    return payload


def create_plan(
    user_task: str,
    inputs: Dict[str, Any] | None = None,
) -> Dict[str, Any]:

    inputs = inputs or {}

    # ========================================================
    # MEMORY RETRIEVAL
    # ========================================================

    log(
        "🧠 Searching previous AutoDev experiences..."
    )

    memories = search_memory(
        user_task,
        n_results=3,
    )

    memory_context = (
        format_memories_for_prompt(
            memories
        )
    )

    if memories:
        log(
            f"🧠 Using {len(memories)} "
            f"previous experience(s) for planning."
        )
    else:
        log(
            "🧠 No relevant previous experience found."
        )

    # ========================================================
    # PLANNER PROMPT
    # ========================================================

    prompt = f"""
You are AutoDev's senior software architect.

Your job is to design a complete, runnable Python project
for the user's request.

============================================================
USER TASK
============================================================

{user_task}

============================================================
RUNTIME INPUTS
============================================================

{json.dumps(
    inputs,
    ensure_ascii=False,
)}

============================================================
RELEVANT PREVIOUS AUTODev EXPERIENCES
============================================================

{memory_context}

============================================================
HOW TO USE MEMORY
============================================================

Previous experiences are historical engineering knowledge.

Use them as useful hints when they are relevant.

DO NOT blindly copy previous projects.

DO NOT assume previous implementation details are correct
for the current task.

Prefer the current user's requirements over memory.

Reuse useful architectural patterns when appropriate.

If a previous project had successful repairs, consider
those lessons when designing the new project.

============================================================
PROJECT REQUIREMENTS
============================================================

Create a complete, runnable Python project.

Return ONLY valid JSON with this exact top-level shape:

{{
  "project_name": "Human_Readable_Name",
  "summary": "What the project does",
  "language": "python",
  "run_command": "python app/main.py",
  "test_command": "python -m unittest discover -s tests -v",
  "files": [
    {{
      "path": "app/main.py",
      "content": "complete source code"
    }},
    {{
      "path": "README.md",
      "content": "complete documentation"
    }},
    {{
      "path": "requirements.txt",
      "content": "dependencies, one per line"
    }}
  ]
}}

Rules:

1. Every file must contain complete content.
2. Do not use placeholders such as TODO,
   "implement here", or "...".
3. Do not use input().
4. Keep the project internally consistent.
5. Prefer a clean app/ package for non-trivial projects.
6. Include __init__.py where Python package imports
   need it.
7. Do NOT generate tests.
   Tests are generated by a separate AutoDev test agent.
8. Keep dependencies minimal.
9. The run_command must work from the project root.
10. Do not return markdown or code fences.
11. Prefer patterns from relevant previous experiences
    only when they improve the current project.
12. The generated project must be self-contained.
"""

    # ========================================================
    # MODEL FALLBACK LOOP
    # ========================================================

    last_error = None

    for model in MODEL_FALLBACKS:

        for attempt in range(
            MAX_RETRIES
        ):

            try:

                log(
                    f"🧠 Planning with {model} "
                    f"(attempt "
                    f"{attempt + 1}/"
                    f"{MAX_RETRIES})"
                )

                response = (
                    client
                    .chat
                    .completions
                    .create(
                        model=model,
                        messages=[
                            {
                                "role": "system",
                                "content": (
                                    "You are a precise "
                                    "software architect. "
                                    "Return strict JSON only."
                                ),
                            },
                            {
                                "role": "user",
                                "content": prompt,
                            },
                        ],
                        temperature=0,
                    )
                )

                plan = _validate_project(
                    _extract_json(
                        response
                        .choices[0]
                        .message
                        .content
                    )
                )

                log(
                    f"✅ Plan created: "
                    f"{plan['project_name']} "
                    f"("
                    f"{len(plan['files'])}"
                    f" files)"
                )

                return plan

            except Exception as exc:

                last_error = str(exc)

                log(
                    f"⚠️ Planner failed: "
                    f"{last_error}"
                )

                time.sleep(0.5)

    raise RuntimeError(
        f"All planner models failed: "
        f"{last_error}"
    )