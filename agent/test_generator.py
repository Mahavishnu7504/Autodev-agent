"""
AutoDev Agent - Test Generator

Generates deterministic, project-aware Python unittest files.
Uses model fallback intelligently when a provider/model is rate-limited.
"""

import json
import re
import time
from pathlib import Path
from typing import Dict, List, Any

from groq import Groq

from config import GROQ_API_KEY, MODEL_FALLBACKS, MAX_RETRIES
from agent.logger import log


client = Groq(api_key=GROQ_API_KEY)


DEFAULT_TEST_COMMAND = (
    "python -m unittest discover -s tests -v"
)


# ============================================================
# HELPERS
# ============================================================

def _is_rate_limit_error(message: str) -> bool:
    text = str(message or "").lower()

    markers = (
        "rate limit",
        "rate_limit",
        "tokens per day",
        "tokens per minute",
        "tpd",
        "tpm",
        "429",
        "too many requests",
        "quota",
    )

    return any(marker in text for marker in markers)


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
        "Test generator returned invalid JSON."
    )


def _validate_tests(
    payload: Dict[str, Any],
) -> Dict[str, Any]:

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


# ============================================================
# TEST GENERATOR
# ============================================================

def generate_tests(
    task: str,
    project: Dict[str, str],
) -> Dict[str, Any]:

    if not project:
        return {
            "tests": [],
            "test_command": DEFAULT_TEST_COMMAND,
        }

    # --------------------------------------------------------
    # BUILD SOURCE CONTEXT
    # --------------------------------------------------------

    source_chunks = []

    for path in sorted(project):

        content = project[path]

        if path.startswith("tests/"):
            continue

        source_chunks.append(
            f"\n===== FILE: {path} =====\n"
            f"{content}\n"
        )

    source = "".join(source_chunks)

    # --------------------------------------------------------
    # PROMPT
    # --------------------------------------------------------

    prompt = f"""
You are AutoDev's dedicated test-generation engineer.

USER TASK:
{task}

PROJECT SOURCE:
{source}

Generate a small but meaningful Python unittest suite.

Rules:

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

3. Use Python's standard-library unittest.

4. Do not require pytest.

5. Test important public behavior.

6. Tests must import the project's actual modules
   using their real paths.

7. Never invent modules, classes or functions.

8. Do not use input().

9. Do not modify application source files.

10. Keep the suite focused.

11. Normally generate 1-4 test files.

12. Every test file must be complete and runnable.

13. If the project has a CLI, prefer testing callable
    functions where possible.

14. Use the project source as the source of truth.

15. Do not test features that are not required by
    the user task or implemented by the project.
"""

    last_error = None

    # ========================================================
    # MODEL FALLBACK LOOP
    # ========================================================

    for model in MODEL_FALLBACKS:

        rate_limited = False

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

                payload = _extract_json(
                    response
                    .choices[0]
                    .message
                    .content
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
                    "test file(s)."
                )

                return validated

            except Exception as exc:

                last_error = str(exc)

                log(
                    "⚠️ Test generation failed: "
                    f"{last_error}"
                )

                # ------------------------------------------------
                # RATE LIMIT
                # ------------------------------------------------

                if _is_rate_limit_error(
                    last_error
                ):

                    log(
                        f"⏭️ Skipping {model} "
                        "because it is rate-limited."
                    )

                    rate_limited = True
                    break

                # ------------------------------------------------
                # NORMAL RETRY
                # ------------------------------------------------

                if attempt < MAX_RETRIES - 1:

                    time.sleep(0.5)

        if rate_limited:
            continue

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
        "error": last_error,
    }