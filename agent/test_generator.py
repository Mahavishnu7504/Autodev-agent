"""
AutoDev Agent - Test Generator

Generates deterministic, project-aware Python unittest files from the
generated project source. Tests are added to the workspace before execution.
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


def _extract_json(text: str) -> Dict[str, Any]:
    text = (text or "").strip()

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])

    raise ValueError("Test generator returned invalid JSON.")


def _validate_tests(payload: Dict[str, Any]) -> Dict[str, Any]:
    tests = payload.get("tests")

    if not isinstance(tests, list):
        raise ValueError("Test generator response must contain a tests list.")

    clean = []

    for item in tests:
        if not isinstance(item, dict):
            continue

        path = str(item.get("path", "")).strip()
        content = str(item.get("content", "")).strip()

        if not path or not content:
            continue

        normalized = path.replace("\\", "/").lstrip("/")

        if ".." in Path(normalized).parts:
            raise ValueError(f"Unsafe generated test path: {path}")

        if not normalized.startswith("tests/"):
            normalized = f"tests/{Path(normalized).name}"

        clean.append({
            "path": normalized,
            "content": content,
        })

    return {
        "tests": clean,
        "test_command": (
            str(payload.get("test_command") or
                "python -m unittest discover -s tests -v")
        ),
    }


def generate_tests(
    task: str,
    project: Dict[str, str],
) -> Dict[str, Any]:
    """
    Generate tests for the current project.

    project maps relative file paths to source contents.
    """
    if not project:
        return {
            "tests": [],
            "test_command": "python -m unittest discover -s tests -v",
        }

    source_chunks = []

    for path in sorted(project):
        content = project[path]
        if path.startswith("tests/"):
            continue

        source_chunks.append(
            f"\n===== FILE: {path} =====\n{content}\n"
        )

    source = "".join(source_chunks)

    prompt = f"""
You are AutoDev's dedicated test-generation engineer.

USER TASK:
{task}

PROJECT SOURCE:
{source}

Generate a small but meaningful Python unittest suite for this project.

Rules:
1. Return ONLY valid JSON.
2. JSON shape:
{{
  "test_command": "python -m unittest discover -s tests -v",
  "tests": [
    {{
      "path": "tests/test_something.py",
      "content": "complete Python source"
    }}
  ]
}}
3. Use Python's standard-library unittest. Do not require pytest.
4. Test the important public behavior, not implementation trivia.
5. Tests must import the project's actual modules using their real paths.
6. Do not use input().
7. Do not modify application source files.
8. Keep the suite focused: normally 1-4 test files.
9. Every generated test file must be complete and runnable.
10. If the project has a CLI, prefer testing callable functions where possible.
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
                                "You generate robust Python unittest suites "
                                "and return strict JSON only."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0,
                )

                payload = _extract_json(
                    response.choices[0].message.content
                )

                validated = _validate_tests(payload)

                if not validated["tests"]:
                    raise ValueError(
                        "Test generator produced no usable test files."
                    )

                log(
                    f"✅ Generated {len(validated['tests'])} test file(s)."
                )

                return validated

            except Exception as exc:
                last_error = str(exc)
                log(f"⚠️ Test generation failed: {last_error}")
                time.sleep(0.5)

    log("❌ Test generation exhausted all model attempts.")

    return {
        "tests": [],
        "test_command": "python -m unittest discover -s tests -v",
        "error": last_error,
    }
