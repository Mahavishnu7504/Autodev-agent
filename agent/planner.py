

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


DEFAULT_TEST_COMMAND = "python -m unittest discover -s tests -v"


# ============================================================
# JSON HELPERS
# ============================================================

def _extract_json(text: str) -> Dict[str, Any]:
    """
    Extract a JSON object from model output.

    Handles:
    - pure JSON
    - ```json ... ```
    - accidental surrounding text
    """

    text = (text or "").strip()

    if not text:
        raise ValueError("Planner returned empty response.")

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
        payload = json.loads(text)

        if not isinstance(payload, dict):
            raise ValueError(
                "Planner JSON response must be an object."
            )

        return payload

    except json.JSONDecodeError:
        pass

    # Try extracting the outermost JSON object.
    start = text.find("{")
    end = text.rfind("}")

    if start >= 0 and end > start:
        candidate = text[start:end + 1]

        try:
            payload = json.loads(candidate)

            if not isinstance(payload, dict):
                raise ValueError(
                    "Planner JSON response must be an object."
                )

            return payload

        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Planner returned invalid JSON: {exc}"
            ) from exc

    raise ValueError(
        "Planner returned invalid JSON."
    )


# ============================================================
# PATH VALIDATION
# ============================================================

def _normalize_project_path(path: str) -> str:
    normalized = (
        str(path or "")
        .replace("\\", "/")
        .strip()
        .lstrip("/")
    )

    if not normalized:
        raise ValueError(
            "Project file path cannot be empty."
        )

    candidate = Path(normalized)

    if candidate.is_absolute():
        raise ValueError(
            f"Absolute project path is not allowed: {path}"
        )

    if ".." in candidate.parts:
        raise ValueError(
            f"Unsafe project path: {path}"
        )

    return normalized


# ============================================================
# SPECIFICATION VALIDATION
# ============================================================

def _clean_string_list(
    value: Any,
    field_name: str,
) -> list[str]:

    if value is None:
        return []

    if not isinstance(value, list):
        raise ValueError(
            f"Planner '{field_name}' must be a list."
        )

    result = []

    for item in value:
        text = str(item).strip()

        if text:
            result.append(text)

    return result


def _validate_behavior_spec(
    specification: Any,
) -> Dict[str, Any]:
    """
    Validate and normalize the shared behavior contract.

    The specification is intentionally simple and textual. This makes it
    useful to both the implementation and test-generation agents without
    forcing an overly rigid schema.
    """

    if not isinstance(specification, dict):
        raise ValueError(
            "Planner 'behavior_specification' must be an object."
        )

    goal = str(
        specification.get("goal") or ""
    ).strip()

    if not goal:
        raise ValueError(
            "Planner behavior specification requires a goal."
        )

    clean = {
        "goal": goal,
        "features": _clean_string_list(
            specification.get("features"),
            "behavior_specification.features",
        ),
        "inputs": _clean_string_list(
            specification.get("inputs"),
            "behavior_specification.inputs",
        ),
        "outputs": _clean_string_list(
            specification.get("outputs"),
            "behavior_specification.outputs",
        ),
        "business_rules": _clean_string_list(
            specification.get("business_rules"),
            "behavior_specification.business_rules",
        ),
        "edge_cases": _clean_string_list(
            specification.get("edge_cases"),
            "behavior_specification.edge_cases",
        ),
    }

    return clean


# ============================================================
# PROJECT VALIDATION
# ============================================================

def _validate_project(
    payload: Dict[str, Any],
) -> Dict[str, Any]:

    if not isinstance(payload, dict):
        raise ValueError(
            "Planner response must be a JSON object."
        )

    required = [
        "project_name",
        "summary",
        "language",
        "files",
        "behavior_specification",
    ]

    for key in required:
        if key not in payload:
            raise ValueError(
                f"Planner response missing '{key}'."
            )

    language = str(
        payload.get("language") or ""
    ).strip().lower()

    if language != "python":
        raise ValueError(
            "This AutoDev stage currently supports "
            "Python projects."
        )

    project_name = str(
        payload.get("project_name") or ""
    ).strip()

    summary = str(
        payload.get("summary") or ""
    ).strip()

    if not project_name:
        raise ValueError(
            "Planner project_name cannot be empty."
        )

    if not summary:
        raise ValueError(
            "Planner summary cannot be empty."
        )

    files = payload.get("files")

    if not isinstance(files, list):
        raise ValueError(
            "Planner 'files' must be a list."
        )

    clean_files = []

    seen_paths = set()

    for item in files:

        if not isinstance(item, dict):
            continue

        path = _normalize_project_path(
            item.get("path", "")
        )

        content = str(
            item.get("content", "")
        )

        if not content.strip():
            continue

        if path in seen_paths:
            raise ValueError(
                f"Duplicate project file: {path}"
            )

        seen_paths.add(path)

        clean_files.append({
            "path": path,
            "content": content,
        })

    if not clean_files:
        raise ValueError(
            "Planner produced no usable files."
        )

    specification = _validate_behavior_spec(
        payload.get("behavior_specification")
    )

    run_command = str(
        payload.get("run_command") or ""
    ).strip()

    test_command = str(
        payload.get("test_command")
        or DEFAULT_TEST_COMMAND
    ).strip()

    payload["project_name"] = project_name
    payload["summary"] = summary
    payload["language"] = "python"
    payload["run_command"] = run_command
    payload["test_command"] = test_command
    payload["behavior_specification"] = specification
    payload["files"] = clean_files

    return payload


# ============================================================
# PLANNER
# ============================================================

def create_plan(
    user_task: str,
    inputs: Dict[str, Any] | None = None,
) -> Dict[str, Any]:

    user_task = str(user_task or "").strip()

    if not user_task:
        raise ValueError(
            "AutoDev task cannot be empty."
        )

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

    memory_context = format_memories_for_prompt(
        memories
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

Your job is to transform a user's natural-language software request
into ONE complete, runnable Python project.

The project will later be:
1. tested by a separate test-generation agent,
2. executed automatically,
3. repaired automatically if necessary,
4. retested,
5. packaged into a ZIP.

Therefore, your design must be internally consistent.

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
    indent=2,
)}

============================================================
RELEVANT PREVIOUS AUTODEV EXPERIENCES
============================================================

{memory_context}

============================================================
MEMORY RULES
============================================================

Previous experiences are historical engineering knowledge.

Use them only when genuinely relevant.

DO NOT blindly copy previous projects.

DO NOT assume previous implementation details are requirements.

The current USER TASK always has higher priority than memory.

Use successful previous repair patterns when they improve reliability.

============================================================
CRITICAL: BEHAVIOR CONTRACT
============================================================

You MUST create a concise "behavior_specification".

This specification is the single source of truth for the expected
application behavior.

The implementation you generate MUST follow it.

A separate test-generation agent will receive the SAME specification
and MUST write tests against it.

This prevents the implementation and tests from independently
inventing different behavior.

When the user's request is ambiguous:

- Choose a simple, sensible behavior.
- Record that decision explicitly in the behavior specification.
- Make the application implement that decision.
- Make the specification specific enough for tests to verify it.

Do NOT add unnecessary features.

Do NOT turn reasonable assumptions into large features.

============================================================
BEHAVIOR SPECIFICATION
============================================================

Return:

"behavior_specification": {{
    "goal": "One precise sentence describing the application's goal",

    "features": [
        "Concrete supported behavior"
    ],

    "inputs": [
        "What data/functions/classes the application accepts"
    ],

    "outputs": [
        "What the application returns/displays/produces"
    ],

    "business_rules": [
        "Important calculation/decision rules"
    ],

    "edge_cases": [
        "Only relevant and defined edge-case behavior"
    ]
}}

IMPORTANT:

Only include behavior that the generated application actually
implements.

Do not invent requirements merely to make the specification look
complete.

For example, if a task asks for student averages but does not define
a class average, do not silently create several competing meanings.
Choose one simple meaning if a class-level value is genuinely useful,
document it once in business_rules, and implement exactly that meaning.

============================================================
PROJECT OUTPUT
============================================================

Return ONLY valid JSON.

Exact top-level shape:

{{
  "project_name": "Human_Readable_Name",

  "summary": "What the project does",

  "language": "python",

  "run_command": "python app/main.py",

  "test_command": "python -m unittest discover -s tests -v",

  "behavior_specification": {{
    "goal": "...",
    "features": [],
    "inputs": [],
    "outputs": [],
    "business_rules": [],
    "edge_cases": []
  }},

  "files": [
    {{
      "path": "app/__init__.py",
      "content": "complete source code"
    }},
    {{
      "path": "app/main.py",
      "content": "complete source code"
    }}
  ]
}}

============================================================
PROJECT RULES
============================================================

1. Every file must contain complete content.

2. Do not use placeholders such as:
   TODO
   implement here
   ...
   pass where real behavior is required.

3. Do not use input().

4. Keep all generated files internally consistent.

5. Prefer a clean app/ package for non-trivial projects.

6. Include __init__.py when package imports require it.

7. DO NOT generate tests.
   Tests are generated separately by AutoDev.

8. Keep dependencies minimal.

9. Prefer Python standard library when practical.

10. run_command must work from the project root.

11. The generated application must actually implement the
    behavior_specification.

12. Do not create behavior in the code that is absent from the
    behavior specification unless it is purely internal.

13. Do not create a behavior specification that the code does not
    implement.

14. Avoid unnecessary architecture for small tasks.

15. Do not return markdown or code fences.

16. The project must be self-contained.

17. Use relevant memory only as engineering guidance.

18. The final generated project must be runnable without manual
    modification.

============================================================
FINAL CONSISTENCY CHECK
============================================================

Before returning JSON, mentally verify:

A. Every required feature exists in the source.

B. Every business rule is implemented exactly once.

C. Every output described in the specification is actually produced.

D. Every important input described in the specification is supported.

E. No hidden requirement was added to the project.

F. The specification and source code agree.

G. The run command points to an existing executable entry point.

H. Python imports are internally consistent.

I. No tests are included.

Return ONLY JSON.
"""

    # ========================================================
    # MODEL FALLBACK LOOP
    # ========================================================

    last_error = None

    for model in MODEL_FALLBACKS:

        for attempt in range(MAX_RETRIES):

            try:

                log(
                    f"🧠 Planning with {model} "
                    f"(attempt {attempt + 1}/"
                    f"{MAX_RETRIES})"
                )

                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are AutoDev's precise software "
                                "architect. Return strict JSON only. "
                                "The behavior specification must match "
                                "the generated implementation."
                            ),
                        },
                        {
                            "role": "user",
                            "content": prompt,
                        },
                    ],
                    temperature=0,
                )

                raw = response.choices[0].message.content

                plan = _validate_project(
                    _extract_json(raw)
                )

                log(
                    f"✅ Plan created: "
                    f"{plan['project_name']} "
                    f"({len(plan['files'])} files)"
                )

                log(
                    "📋 Behavior contract created: "
                    f"{len(plan['behavior_specification']['features'])} "
                    f"feature(s), "
                    f"{len(plan['behavior_specification']['business_rules'])} "
                    f"business rule(s)."
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
        f"All planner models failed: {last_error}"
    )