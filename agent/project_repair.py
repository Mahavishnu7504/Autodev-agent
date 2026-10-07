"""
AutoDev Agent - Autonomous Project Repair Engine

Given the complete current project and an execution/test failure, asks the
LLM for the smallest coherent set of file replacements needed to repair the
project.
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

    raise ValueError("Repair engine returned invalid JSON.")


def _validate_patch(payload: Dict[str, Any]) -> Dict[str, Any]:
    files = payload.get("files")

    if not isinstance(files, list):
        raise ValueError("Repair response must contain a files list.")

    clean = []

    for item in files:
        if not isinstance(item, dict):
            continue

        path = str(item.get("path", "")).strip()
        content = str(item.get("content", ""))

        if not path or not content.strip():
            continue

        normalized = path.replace("\\", "/").lstrip("/")

        if ".." in Path(normalized).parts:
            raise ValueError(f"Unsafe repair path: {path}")

        clean.append({
            "path": normalized,
            "content": content,
        })

    if not clean:
        raise ValueError("Repair engine returned no usable file changes.")

    return {
        "files": clean,
        "summary": str(payload.get("summary", "")).strip(),
    }


def repair_project(
    task: str,
    project: Dict[str, str],
    failure: Dict[str, Any],
    attempt: int,
) -> Dict[str, Any]:
    source_chunks = []

    for path in sorted(project):
        source_chunks.append(
            f"\n===== FILE: {path} =====\n{project[path]}\n"
        )

    source = "".join(source_chunks)

    failure_text = (
        f"TEST RESULT:\n{failure.get('test_result', {})}\n\n"
        f"RUN RESULT:\n{failure.get('run_result', {})}\n"
    )

    prompt = f"""
You are AutoDev's autonomous senior software repair engineer.

ORIGINAL USER TASK:
{task}

CURRENT PROJECT:
{source}

FAILURE FROM ATTEMPT {attempt}:
{failure_text}

Your job is to repair the project so the tests and application can run.

Rules:
1. Return ONLY valid JSON.
2. JSON shape:
{{
  "summary": "short explanation of the repair",
  "files": [
    {{
      "path": "relative/path.py",
      "content": "complete replacement file"
    }}
  ]
}}
3. Return COMPLETE FILE CONTENT for every changed file.
4. You may modify multiple files when the bug crosses module boundaries.
5. Do not invent files that are unnecessary.
6. Preserve the user's original requirements.
7. Do not remove tests just to make the suite pass.
8. Do not weaken assertions merely to hide failures.
9. Do not use input().
10. Keep dependencies minimal.
11. Fix the root cause, not just the visible symptom.
12. Do not return markdown fences.
13. Paths must be relative to the project root.
14. Prefer the smallest coherent repair.
"""

    last_error = None

    for model in MODEL_FALLBACKS:
        for model_attempt in range(MAX_RETRIES):
            try:
                log(
                    f"🛠️ Repairing project with {model} "
                    f"(attempt {model_attempt + 1}/{MAX_RETRIES})"
                )

                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are an autonomous multi-file debugger. "
                                "Return strict JSON only."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0,
                )

                payload = _extract_json(
                    response.choices[0].message.content
                )

                patch = _validate_patch(payload)

                log(
                    f"🔧 Repair produced {len(patch['files'])} "
                    f"file change(s)."
                )

                return patch

            except Exception as exc:
                last_error = str(exc)
                log(f"⚠️ Repair attempt failed: {last_error}")
                time.sleep(0.5)

    return {
        "files": [],
        "summary": "",
        "error": last_error,
    }
