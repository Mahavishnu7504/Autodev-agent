"""
AutoDev Agent - Test Generator

Generates deterministic, project-aware Python unittest files.

Features:
- Shared behavior specification support
- Strict JSON extraction and validation
- Project-aware imports
- Model fallback support
- Immediate skip on rate-limited models
- Retry only for transient failures
- Exponential backoff
- Safe test path normalization
- Standard-library unittest
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

from groq import Groq

from config import GROQ_API_KEY, MODEL_FALLBACKS, MAX_RETRIES
from agent.logger import log


client = Groq(api_key=GROQ_API_KEY)

DEFAULT_TEST_COMMAND = (
    "python -m unittest discover -s tests -v"
)

RETRY_DELAYS = (
    0.75,
    1.5,
    3.0,
)

MAX_SOURCE_CHARS = 24000
MAX_SPEC_CHARS = 10000


# ============================================================
# HELPERS
# ============================================================

def _is_rate_limit_error(message: str) -> bool:
    """
    Detect provider/model quota and rate-limit failures.

    Rate-limit errors are NEVER retried against the same model.
    """

    text = str(message or "").lower()

    markers = (
        "rate limit",
        "rate_limit",
        "rate limit reached",
        "rate_limit_exceeded",
        "tokens per day",
        "tokens per minute",
        "tpd",
        "tpm",
        "429",
        "too many requests",
        "quota",
        "quota exceeded",
        "requests per day",
        "requests per minute",
        "usage limit",
        "limit reached",
    )

    return any(
        marker in text
        for marker in markers
    )


def _is_transient_error(message: str) -> bool:
    """
    Detect temporary failures where retrying can help.
    """

    text = str(message or "").lower()

    markers = (
        "timeout",
        "timed out",
        "temporarily unavailable",
        "temporary failure",
        "connection reset",
        "connection aborted",
        "service unavailable",
        "internal server error",
        "bad gateway",
        "gateway timeout",
        "502",
        "503",
        "504",
    )

    return any(
        marker in text
        for marker in markers
    )


def _extract_json(
    text: str,
) -> Dict[str, Any]:
    """
    Extract JSON even if the model wraps it
    inside a markdown code block.
    """

    text = (text or "").strip()

    if not text:
        raise ValueError(
            "Test generator returned an empty response."
        )

    if text.startswith("```"):
        text = re.sub(
            r"^```(?:json)?\s*",
            "",
            text,
            flags=re.IGNORECASE,
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
                "Test generator JSON root must be an object."
            )

        return payload

    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")

        if start >= 0 and end > start:

            try:
                payload = json.loads(
                    text[start:end + 1]
                )

                if not isinstance(payload, dict):
                    raise ValueError(
                        "Test generator JSON root must be an object."
                    )

                return payload

            except json.JSONDecodeError:
                pass

    raise ValueError(
        "Test generator returned invalid JSON."
    )


def _validate_tests(
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Validate and normalize generated test files.
    """

    tests = payload.get("tests")

    if not isinstance(tests, list):
        raise ValueError(
            "Test generator response must contain a tests list."
        )

    clean = []

    for item in tests:

        if not isinstance(item, dict):
            continue

        path = str(
            item.get("path", "")
        ).strip()

        content = str(
            item.get("content", "")
        ).strip()

        if not path or not content:
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
                f"Unsafe generated test path: {path}"
            )

        if not normalized.startswith("tests/"):
            normalized = (
                f"tests/{Path(normalized).name}"
            )

        if not normalized.endswith(".py"):
            normalized += ".py"

        clean.append({
            "path": normalized,
            "content": content,
        })

    return {
        "tests": clean,
        "test_command": (
            str(
                payload.get("test_command")
                or DEFAULT_TEST_COMMAND
            ).strip()
        ),
    }


def _build_source_context(
    project: Dict[str, str],
) -> str:
    """
    Build bounded project source context.
    """

    source_chunks = []
    total_chars = 0

    for path in sorted(project):

        if path.startswith("tests/"):
            continue

        content = str(
            project[path]
        )

        chunk = (
            f"\n===== FILE: {path} =====\n"
            f"{content}\n"
        )

        if (
            total_chars + len(chunk)
            > MAX_SOURCE_CHARS
        ):
            log(
                "⚠️ Test source context reached "
                f"{MAX_SOURCE_CHARS} characters."
            )
            break

        source_chunks.append(chunk)
        total_chars += len(chunk)

    return "".join(source_chunks)


def _build_specification_context(
    specification: Optional[Dict[str, Any]],
) -> str:
    """
    Serialize the shared behavior contract.

    The behavior specification is the primary test contract.
    """

    if not specification:
        return (
            "No separate behavior specification was provided. "
            "Infer requirements only from the user task and "
            "implemented source."
        )

    try:
        serialized = json.dumps(
            specification,
            indent=2,
            ensure_ascii=False,
        )

        if len(serialized) > MAX_SPEC_CHARS:
            serialized = serialized[
                :MAX_SPEC_CHARS
            ] + "\n...[truncated]"

        return serialized

    except Exception:
        return str(specification)


def _model_list() -> list[str]:
    """
    Normalize fallback models and remove duplicates.
    """

    models = []

    for model in MODEL_FALLBACKS:

        model_name = str(
            model or ""
        ).strip()

        if (
            model_name
            and model_name not in models
        ):
            models.append(model_name)

    return models


# ============================================================
# TEST GENERATOR
# ============================================================

def generate_tests(
    task: str,
    project: Dict[str, str],
    specification: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Generate tests from:

    1. Shared behavior specification
    2. User task
    3. Actual generated project source

    `specification` is optional for backward compatibility.
    """

    if not project:
        return {
            "tests": [],
            "test_command": DEFAULT_TEST_COMMAND,
            "error": (
                "No project files available "
                "for test generation."
            ),
        }

    # --------------------------------------------------------
    # SOURCE
    # --------------------------------------------------------

    source = _build_source_context(
        project
    )

    if not source.strip():
        return {
            "tests": [],
            "test_command": DEFAULT_TEST_COMMAND,
            "error": (
                "No application source files "
                "available."
            ),
        }

    # --------------------------------------------------------
    # BEHAVIOR SPECIFICATION
    # --------------------------------------------------------

    specification_context = (
        _build_specification_context(
            specification
        )
    )

    # --------------------------------------------------------
    # PROMPT
    # --------------------------------------------------------

    prompt = f"""
You are AutoDev's dedicated test-generation engineer.

Your job is to create deterministic Python unittest
tests for the generated project.

============================================================
USER TASK
============================================================

{task}

============================================================
SHARED BEHAVIOR SPECIFICATION
============================================================

{specification_context}

============================================================
PROJECT SOURCE
============================================================

{source}

============================================================
TEST GENERATION RULES
============================================================

1. Return ONLY valid JSON.

2. JSON shape:

{{
  "test_command":
    "python -m unittest discover -s tests -v",

  "tests": [
    {{
      "path":
        "tests/test_something.py",

      "content":
        "complete Python source"
    }}
  ]
}}

3. Use Python standard-library unittest.

4. Do not require pytest.

5. Test every important required behavior.

6. The behavior specification is the primary
   testing contract.

7. The actual project source is the source of truth
   for imports, class names and function names.

8. Never invent modules, classes or functions.

9. Tests must use the project's real import paths.

10. If app/ exists, preserve the app package structure.

11. Do not create app.py when app/ already exists.

12. Do not modify application source files.

13. Do not use input().

14. Do not require network access.

15. Do not require external services.

16. Do not require environment secrets.

17. Prefer deterministic assertions.

18. Test important edge cases from the behavior
    specification.

19. Test division-by-zero or other explicit error
    behavior when required.

20. Verify exact exception types when specified.

21. Verify exact exception messages when the behavior
    specification explicitly defines them.

22. If the project contains a CLI/demo, test callable
    functions where possible instead of depending only
    on console output.

23. Normally generate 1-4 focused test files.

24. Every generated test file must be complete and
    directly runnable.

25. Do not weaken assertions just to make tests pass.

26. Do not test requirements that were not requested
    or specified.

27. Do not add new application features.

28. Do not use random values.

29. Do not use sleep().

30. Keep tests fast.

============================================================
IMPORTANT
============================================================

The generated tests must validate the existing project.

Do NOT redesign the project.

Do NOT rewrite application code.

Do NOT assume missing functionality exists.

Return only the JSON object.
"""

    last_error: str | None = None

    models = _model_list()

    if not models:
        return {
            "tests": [],
            "test_command": DEFAULT_TEST_COMMAND,
            "error": "MODEL_FALLBACKS is empty.",
        }

    log(
        "🧪 Test generation starting with "
        f"{len(models)} configured model(s)."
    )

    # ========================================================
    # MODEL FALLBACK LOOP
    # ========================================================

    for model_index, model in enumerate(models):

        log(
            f"🧪 Trying test model {model} "
            f"({model_index + 1}/{len(models)})"
        )

        # ----------------------------------------------------
        # RETRY LOOP
        # ----------------------------------------------------

        for attempt in range(MAX_RETRIES):

            try:

                log(
                    f"🧪 Generating tests with {model} "
                    f"(attempt {attempt + 1}/{MAX_RETRIES})"
                )

                response = (
                    client.chat.completions.create(
                        model=model,
                        messages=[
                            {
                                "role": "system",
                                "content": (
                                    "You generate robust "
                                    "Python unittest suites "
                                    "and return strict "
                                    "JSON only."
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

                content = (
                    response.choices[0]
                    .message
                    .content
                )

                payload = _extract_json(
                    content
                )

                validated = _validate_tests(
                    payload
                )

                if not validated["tests"]:
                    raise ValueError(
                        "Test generator produced "
                        "no usable test files."
                    )

                log(
                    "✅ Generated "
                    f"{len(validated['tests'])} "
                    f"test file(s) using {model}."
                )

                return validated

            except Exception as exc:

                last_error = str(exc)

                log(
                    "⚠️ Test generation failed "
                    f"with {model}: {last_error}"
                )

                # ------------------------------------------------
                # RATE LIMIT
                # ------------------------------------------------

                if _is_rate_limit_error(
                    last_error
                ):

                    log(
                        f"⏭️ {model} is rate-limited. "
                        "Skipping remaining retries "
                        "and moving to the next model."
                    )

                    break

                # ------------------------------------------------
                # NON-TRANSIENT ERROR
                # ------------------------------------------------

                if not _is_transient_error(
                    last_error
                ):

                    log(
                        f"⏭️ {model} returned a "
                        "non-transient error. "
                        "Moving to the next fallback model."
                    )

                    break

                # ------------------------------------------------
                # TRANSIENT ERROR
                # ------------------------------------------------

                if attempt < MAX_RETRIES - 1:

                    delay = RETRY_DELAYS[
                        min(
                            attempt,
                            len(RETRY_DELAYS) - 1,
                        )
                    ]

                    log(
                        "🔄 Temporary failure. "
                        f"Retrying {model} "
                        f"in {delay:.1f}s..."
                    )

                    time.sleep(delay)

        log(
            f"➡️ Moving past test model {model}."
        )

    # ========================================================
    # ALL MODELS FAILED
    # ========================================================

    log(
        "❌ Test generation exhausted "
        "all available models."
    )

    return {
        "tests": [],
        "test_command": DEFAULT_TEST_COMMAND,
        "error": (
            last_error
            or "All configured test-generation models failed."
        ),
    }