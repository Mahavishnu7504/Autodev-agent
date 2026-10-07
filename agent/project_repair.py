"""
AutoDev Agent - Autonomous Project Repair Engine

Repairs generated projects while preserving:
- project structure
- behavior specification
- existing package/module boundaries
- test integrity
"""

import json
import re
import time
from pathlib import Path
from typing import Dict, Any

from groq import Groq

from config import GROQ_API_KEY, MODEL_FALLBACKS, MAX_RETRIES
from agent.logger import log


client = Groq(api_key=GROQ_API_KEY)

MAX_SOURCE_CHARS = 18000
MAX_FAILURE_CHARS = 6000
MAX_FILE_CHARS = 7000
MAX_REPAIR_FILES = 8


def _extract_json(text: str) -> Dict[str, Any]:
    text = (text or "").strip()

    if not text:
        raise ValueError(
            "Repair engine returned an empty response."
        )

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
        "Repair engine returned invalid JSON."
    )


def _validate_patch(
    payload: Dict[str, Any],
) -> Dict[str, Any]:

    if not isinstance(payload, dict):
        raise ValueError(
            "Repair response must be an object."
        )

    files = payload.get("files")

    if not isinstance(files, list):
        raise ValueError(
            "Repair response must contain a files list."
        )

    clean = []

    for item in files:

        if not isinstance(item, dict):
            continue

        path = str(
            item.get("path", "")
        ).strip()

        content = item.get("content")

        if not path or content is None:
            continue

        normalized = (
            path
            .replace("\\", "/")
            .strip()
            .lstrip("/")
        )

        parts = Path(normalized).parts

        if ".." in parts:
            raise ValueError(
                f"Unsafe repair path: {path}"
            )

        if not normalized:
            continue

        clean.append(
            {
                "path": normalized,
                "content": str(content),
            }
        )

    if not clean:
        raise ValueError(
            "Repair engine returned no usable file changes."
        )

    return {
        "files": clean,
        "summary": str(
            payload.get("summary", "")
        ).strip(),
    }


def _format_behavior_specification(
    specification: Any,
) -> str:

    if not isinstance(specification, dict):
        return "{}"

    return json.dumps(
        specification,
        ensure_ascii=False,
        indent=2,
    )


def _format_failure(
    failure: Dict[str, Any],
) -> str:

    parts = []

    phase = failure.get(
        "phase",
        "unknown",
    )

    parts.append(
        f"FAILURE PHASE:\n{phase}"
    )

    test_result = failure.get(
        "test_result"
    )

    if isinstance(test_result, dict):

        parts.append(
            "TEST COMMAND:\n"
            + str(
                test_result.get(
                    "command",
                    "",
                )
            )
        )

        parts.append(
            "TEST STDERR:\n"
            + str(
                test_result.get(
                    "stderr",
                    "",
                )
            )
        )

        parts.append(
            "TEST STDOUT:\n"
            + str(
                test_result.get(
                    "stdout",
                    "",
                )
            )
        )

    run_result = failure.get(
        "run_result"
    )

    if isinstance(run_result, dict):

        parts.append(
            "APPLICATION COMMAND:\n"
            + str(
                run_result.get(
                    "command",
                    "",
                )
            )
        )

        parts.append(
            "APPLICATION STDERR:\n"
            + str(
                run_result.get(
                    "stderr",
                    "",
                )
            )
        )

        parts.append(
            "APPLICATION STDOUT:\n"
            + str(
                run_result.get(
                    "stdout",
                    "",
                )
            )
        )

    result = "\n\n".join(parts)

    if len(result) > MAX_FAILURE_CHARS:
        result = (
            "[failure output truncated]\n\n"
            + result[-MAX_FAILURE_CHARS:]
        )

    return result


def _build_source(
    project: Dict[str, str],
) -> str:

    chunks = []
    total = 0

    for path in sorted(project):

        content = str(
            project.get(path, "")
        )

        if not content:
            continue

        remaining = (
            MAX_SOURCE_CHARS - total
        )

        if remaining <= 0:
            break

        content = content[
            :min(
                MAX_FILE_CHARS,
                remaining,
            )
        ]

        chunks.append(
            f"\n===== FILE: {path} =====\n"
            f"{content}\n"
        )

        total += len(content)

    return "".join(chunks)


def _structure_rules(
    project: Dict[str, str],
) -> str:

    rules = []

    paths = set(project.keys())

    has_app_package = any(
        path.startswith("app/")
        for path in paths
    )

    has_src_package = any(
        path.startswith("src/")
        for path in paths
    )

    if has_app_package:
        rules.append(
            "- The existing app/ package is part of "
            "the project architecture."
        )
        rules.append(
            "- Do NOT replace app/ with app.py."
        )
        rules.append(
            "- Do NOT move app/main.py to app.py."
        )
        rules.append(
            "- Preserve app/__init__.py when present."
        )

    if has_src_package:
        rules.append(
            "- Preserve the existing src/ package structure."
        )

    rules.extend(
        [
            "- Prefer modifying existing files.",
            "- Do not rename modules unless the failure "
              "clearly requires it.",
            "- Do not replace a package directory with "
              "a same-named Python module.",
            "- Preserve import paths used by the tests.",
            "- Preserve the run command contract.",
        ]
    )

    return "\n".join(rules)


def repair_project(
    task: str,
    project: Dict[str, str],
    failure: Dict[str, Any],
    attempt: int,
) -> Dict[str, Any]:

    behavior_specification = failure.get(
        "behavior_specification",
        {},
    )

    source = _build_source(
        project
    )

    failure_text = _format_failure(
        failure
    )

    structure_rules = _structure_rules(
        project
    )

    specification_text = (
        _format_behavior_specification(
            behavior_specification
        )
    )

    prompt = f"""
You are AutoDev's autonomous senior software repair engineer.

Your job is to repair an existing generated project.

============================================================
ORIGINAL USER TASK
============================================================

{task}

============================================================
BEHAVIOR SPECIFICATION
============================================================

{specification_text}

This behavior specification is the contract.

Do not change the intended behavior merely to make tests pass.

============================================================
CURRENT PROJECT
============================================================

{source}

============================================================
PROJECT STRUCTURE RULES
============================================================

{structure_rules}

============================================================
FAILURE
============================================================

{failure_text}

============================================================
REPAIR OBJECTIVE
============================================================

Fix the root cause of the failure.

Preserve:
- behavior specification
- project architecture
- existing public APIs
- valid tests
- import paths
- run/test commands

Do not redesign the project.

============================================================
CRITICAL REPAIR RULES
============================================================

1. Return ONLY valid JSON.

2. Return this exact shape:

{{
  "summary": "short explanation",
  "files": [
    {{
      "path": "relative/path.py",
      "content": "complete replacement file"
    }}
  ]
}}

3. Return COMPLETE content for every changed file.

4. Modify the smallest coherent set of files.

5. Multiple files may be changed when necessary.

6. NEVER delete tests.

7. NEVER weaken test assertions.

8. NEVER invent new requirements.

9. NEVER replace a package directory with a same-named .py file.

10. Preserve existing package structure.

11. If app/ exists, do NOT create app.py as a replacement.

12. If app/main.py exists, preserve app/main.py unless
    the failure specifically requires changing it.

13. Preserve __init__.py files.

14. Do not introduce input().

15. Keep dependencies minimal.

16. Do not add network calls.

17. Do not add unnecessary architecture.

18. Do not return markdown fences.

19. Fix the actual root cause.

20. Prefer modifying existing files instead of creating
    alternative duplicate modules.

============================================================
FINAL SELF-CHECK
============================================================

Before returning JSON verify:

- Every changed path is relative.
- Existing package boundaries remain intact.
- No package was converted into a module.
- Existing imports remain valid.
- Behavior specification remains satisfied.
- Tests are not deleted or weakened.
- Run command remains meaningful.
- The repaired project is internally consistent.

Return ONLY JSON.
"""

    last_error = None

    models = list(MODEL_FALLBACKS)

    # --------------------------------------------------------
    # MODEL FALLBACK
    # --------------------------------------------------------

    for model in models:

        # If the 20B model is exhausted/rate limited,
        # immediately move to the next model.
        retries = MAX_RETRIES

        for model_attempt in range(retries):

            try:

                log(
                    f"🛠️ Repairing with {model} "
                    f"(attempt {model_attempt + 1}/"
                    f"{retries})"
                )

                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are an autonomous "
                                "multi-file debugger. "
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

                raw = (
                    response
                    .choices[0]
                    .message
                    .content
                )

                payload = _extract_json(
                    raw
                )

                patch = _validate_patch(
                    payload
                )

                log(
                    f"🔧 Repair produced "
                    f"{len(patch['files'])} "
                    f"file change(s)."
                )

                return patch

            except Exception as exc:

                last_error = str(exc)

                error_lower = (
                    last_error.lower()
                )

                log(
                    f"⚠️ Repair generation failed: "
                    f"{last_error}"
                )

                # ------------------------------------------------
                # IMPORTANT:
                # Rate limits should NOT waste another retry.
                # ------------------------------------------------

                if (
                    "rate limit" in error_lower
                    or "429" in error_lower
                    or "tokens per day" in error_lower
                    or "tokens per minute" in error_lower
                    or "tpd" in error_lower
                    or "tpm" in error_lower
                ):

                    log(
                        f"⏭️ Skipping {model} "
                        f"and moving to fallback model."
                    )

                    break

                # Empty/invalid model output can be retried once.
                time.sleep(0.3)

    return {
        "files": [],
        "summary": "",
        "error": last_error,
    }