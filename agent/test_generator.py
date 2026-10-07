import json
import re
import time
from pathlib import Path
from typing import Any, Dict

from groq import Groq

from config import GROQ_API_KEY, MODEL_FALLBACKS, MAX_RETRIES
from agent.logger import log


client = Groq(api_key=GROQ_API_KEY)

DEFAULT_TEST_COMMAND = "python -m unittest discover -s tests -v"


def _extract_json(text: str) -> Dict[str, Any]:
    text = (text or "").strip()

    if not text:
        raise ValueError("Test generator returned an empty response.")

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)

    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")

    if start >= 0 and end > start:
        try:
            payload = json.loads(text[start:end + 1])

            if isinstance(payload, dict):
                return payload

        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Test generator returned invalid JSON: {exc}"
            ) from exc

    raise ValueError("Test generator returned invalid JSON.")


def _normalize_test_path(path: str) -> str:
    normalized = (
        str(path or "")
        .replace("\\", "/")
        .strip()
        .lstrip("/")
    )

    if not normalized:
        raise ValueError("Generated test path cannot be empty.")

    candidate = Path(normalized)

    if candidate.is_absolute():
        raise ValueError(
            f"Absolute test path is not allowed: {path}"
        )

    if ".." in candidate.parts:
        raise ValueError(
            f"Unsafe generated test path: {path}"
        )

    if not normalized.startswith("tests/"):
        normalized = f"tests/{Path(normalized).name}"

    return normalized


def _validate_tests(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError(
            "Test generator response must be a JSON object."
        )

    tests = payload.get("tests")

    if not isinstance(tests, list):
        raise ValueError(
            "Test generator response must contain a tests list."
        )

    clean_tests = []
    seen_paths = set()

    for item in tests:
        if not isinstance(item, dict):
            continue

        path = _normalize_test_path(
            item.get("path", "")
        )

        content = str(
            item.get("content", "")
        ).strip()

        if not content:
            continue

        if path in seen_paths:
            raise ValueError(
                f"Duplicate generated test file: {path}"
            )

        seen_paths.add(path)

        clean_tests.append({
            "path": path,
            "content": content,
        })

    test_command = str(
        payload.get("test_command")
        or DEFAULT_TEST_COMMAND
    ).strip()

    return {
        "tests": clean_tests,
        "test_command": test_command,
    }


def _format_specification(
    specification: Dict[str, Any],
) -> str:
    if not specification:
        return (
            "No explicit behavior specification was provided. "
            "Use only the USER TASK and actual PROJECT SOURCE. "
            "Do not invent requirements."
        )

    return json.dumps(
        specification,
        ensure_ascii=False,
        indent=2,
    )


def _build_source_context(
    project: Dict[str, str],
) -> str:
    chunks = []

    for path in sorted(project):
        if path.startswith("tests/"):
            continue

        content = str(project[path])

        chunks.append(
            f"===== FILE: {path} =====\n"
            f"{content}\n"
        )

    return "\n".join(chunks)


def generate_tests(
    task: str,
    project: Dict[str, str],
    specification: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """
    Generate deterministic Python unittest files for the generated project.

    The planner's behavior specification is the shared contract between
    implementation and testing.
    """

    if not project:
        return {
            "tests": [],
            "test_command": DEFAULT_TEST_COMMAND,
            "error": "No project source was provided.",
        }

    task = str(task or "").strip()
    specification = specification or {}

    source = _build_source_context(project)
    specification_text = _format_specification(
        specification
    )

    prompt = f"""
You are AutoDev's dedicated test-generation engineer.

Generate a focused Python unittest suite for the project below.

USER TASK:
{task}

SHARED BEHAVIOR SPECIFICATION:
{specification_text}

PROJECT SOURCE:
{source}

============================================================
TEST CONTRACT
============================================================

The SHARED BEHAVIOR SPECIFICATION is the primary contract.

The generated tests must validate behavior that the application
actually implements.

Do NOT invent requirements.

Do NOT add new business rules.

Do NOT assume unspecified output formats.

Do NOT test behavior merely because it would be convenient or
common for this type of application.

When behavior is ambiguous, use the behavior explicitly defined
by the specification.

The current project source must also be respected.

============================================================
TESTING RULES
============================================================

1. Test observable public behavior.

2. Prefer direct testing of public functions and classes.

3. Use the project's real import paths.

4. Do not import functions, classes, or modules that do not exist.

5. Do not test implementation details unnecessarily.

6. Avoid brittle exact-output assertions unless the specification
   explicitly requires an exact output format.

7. For CLI applications, prefer testing the underlying application
   logic rather than terminal formatting.

8. Test CLI output only when the specification explicitly defines
   the output.

9. Test important business rules defined by the specification.

10. Test relevant edge cases only when they are defined or clearly
    implied by the specification.

11. Tests must be deterministic.

12. Tests must not require network access.

13. Tests must not require external services.

14. Tests must not require user interaction.

15. Never use input().

16. Never modify application source files.

17. Use Python standard-library unittest whenever practical.

18. Keep the test suite focused and proportional to the project.

19. Normally generate between 1 and 4 test files.

20. Every test file must be complete and runnable.

21. Tests must run from the project root.

22. Tests must be compatible with:

    python -m unittest discover -s tests -v

23. Do not create tests for functionality absent from the source.

24. Do not create tests for functionality absent from the
    behavior specification.

25. Do not require exact ordering unless the specification requires it.

26. Do not require exact whitespace unless the specification requires it.

27. Do not require sample data that the specification does not define.

28. Do not create hidden assumptions about file names, UI layout,
    formatting, persistence, or business rules.

============================================================
SELF-CHECK
============================================================

Before returning JSON, verify:

- Every imported module exists.
- Every imported class/function exists.
- Every test targets real project behavior.
- Every tested behavior is supported by the specification,
  user task, or actual source.
- No test introduces a new requirement.
- No unnecessary formatting assumptions exist.
- No network or external service is required.
- No interactive input is required.
- The tests can run with unittest discovery.
- The test suite is deterministic.
- The generated tests do not modify application source files.

============================================================
OUTPUT
============================================================

Return ONLY valid JSON.

Required format:

{{
  "test_command": "python -m unittest discover -s tests -v",
  "tests": [
    {{
      "path": "tests/test_feature.py",
      "content": "complete Python unittest source"
    }}
  ]
}}

Do not return markdown.

Do not return code fences.

Do not return explanations.

Do not return text outside the JSON object.
"""

    last_error = None

    for model in MODEL_FALLBACKS:
        for attempt in range(MAX_RETRIES):
            try:
                log(
                    f"🧪 Generating tests with {model} "
                    f"(attempt {attempt + 1}/{MAX_RETRIES})"
                )

                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are AutoDev's test engineer. "
                                "Generate robust Python unittest "
                                "suites based strictly on the supplied "
                                "behavior specification and project "
                                "source. Never invent requirements. "
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

                raw = response.choices[0].message.content
                payload = _extract_json(raw)
                validated = _validate_tests(payload)

                if not validated["tests"]:
                    raise ValueError(
                        "Test generator produced no usable test files."
                    )

                log(
                    f"✅ Generated "
                    f"{len(validated['tests'])} test file(s)."
                )

                return validated

            except Exception as exc:
                last_error = str(exc)

                log(
                    f"⚠️ Test generation failed: "
                    f"{last_error}"
                )

                time.sleep(0.5)

    log("❌ Test generation exhausted all model attempts.")

    return {
        "tests": [],
        "test_command": DEFAULT_TEST_COMMAND,
        "error": last_error,
    }