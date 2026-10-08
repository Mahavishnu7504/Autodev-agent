"""
AutoDev Agent - Intelligent Autonomous Project Repair

The repair engine:
- uses the deterministic failure diagnosis
- respects the behavior specification
- protects Python package structure
- avoids retrying a model after a clear rate-limit/TPD failure
- keeps repair output strict and minimal
"""

import json
import re
import time
from pathlib import Path
from typing import Any, Dict

from groq import Groq

from config import GROQ_API_KEY, MODEL_FALLBACKS, MAX_RETRIES
from agent.logger import log


client = Groq(api_key=GROQ_API_KEY)

RATE_LIMIT_MARKERS = (
    "rate limit",
    "rate_limit",
    "tokens per day",
    "tpd",
    "too many requests",
    "429",
)


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
        content = item.get("content")
        if not path or content is None or not str(content).strip():
            continue

        normalized = path.replace("\\", "/").lstrip("/")
        candidate = Path(normalized)
        if ".." in candidate.parts or candidate.is_absolute():
            raise ValueError(f"Unsafe repair path: {path}")

        clean.append({
            "path": normalized,
            "content": str(content),
        })

    if not clean:
        raise ValueError("Repair engine returned no usable file changes.")

    return {
        "files": clean,
        "summary": str(payload.get("summary", "")).strip(),
    }


def _structure_rules(project: Dict[str, str]) -> str:
    paths = {path.replace("\\", "/") for path in project}
    rules = []

    if any(path == "app/__init__.py" or path.startswith("app/") for path in paths):
        rules.extend([
            "- The project already has an app/ package. NEVER create or replace it with app.py.",
            "- Preserve app/__init__.py and package imports unless the failure explicitly requires a safe import correction.",
            "- Do not collapse app/ into a single app.py module.",
        ])

    if any(path == "src/__init__.py" or path.startswith("src/") for path in paths):
        rules.extend([
            "- The project already has a src/ package. NEVER create or replace it with src.py.",
            "- Preserve the src/ package boundary.",
        ])

    if not rules:
        rules.append("- Preserve the existing project structure; do not invent a competing package/module layout.")

    return "\n".join(rules)


def repair_project(
    task: str,
    project: Dict[str, str],
    failure: Dict[str, Any],
    attempt: int,
) -> Dict[str, Any]:
    source = "".join(
        f"\n===== FILE: {path} =====\n{project[path]}\n"
        for path in sorted(project)
    )

    behavior_specification = failure.get("behavior_specification") or {}
    diagnosis = failure.get("diagnosis") or {}
    failure_text = str(failure.get("summary") or "")

    prompt = f"""
You are AutoDev's autonomous senior software repair engineer.

ORIGINAL USER TASK:
{task}

BEHAVIOR SPECIFICATION — SINGLE SOURCE OF TRUTH:
{json.dumps(behavior_specification, ensure_ascii=False, indent=2)}

FAILURE DIAGNOSIS:
{json.dumps(diagnosis, ensure_ascii=False, indent=2)}

FAILURE DETAILS:
{failure_text}

CURRENT PROJECT:
{source}

REPAIR ATTEMPT:
{attempt}

Your job is to repair the existing project so its required behavior works and the validation command can pass.

Rules:
1. Return ONLY valid JSON.
2. JSON shape:
{{
  "summary": "short explanation of the root-cause repair",
  "files": [
    {{
      "path": "relative/path.py",
      "content": "complete replacement file"
    }}
  ]
}}
3. Return COMPLETE FILE CONTENT for every changed file.
4. Make the smallest coherent root-cause repair.
5. Preserve every requirement in the behavior specification.
6. Do NOT invent new user requirements.
7. Do NOT remove tests, weaken assertions, or change tests.
8. Do NOT create unnecessary files.
9. Do NOT use input() or require interactive input for the default execution path.
10. Keep dependencies minimal and compatible with the existing project.
11. Preserve public classes/functions/import paths unless the diagnosis proves they are wrong.
12. Preserve the existing package/module boundaries.
13. Do not return markdown fences.
14. Paths must be relative to the project root.
15. If the failure is an import/structure error, repair the import or structure rather than rewriting the project architecture.
16. If the failure is a logic error, change implementation behavior rather than weakening validation.
17. If the application has a sensible no-argument/default execution path, preserve or restore it so AutoDev's configured run command can complete.
18. Never replace an existing Python package directory with a same-named .py module.

STRUCTURE SAFETY:
{_structure_rules(project)}
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
                                "Return strict JSON only and preserve project structure."
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

                # A clear rate/TPD limit will not become better by
                # retrying the same model immediately. Fall through
                # to the next configured fallback model.
                if any(marker in last_error.lower() for marker in RATE_LIMIT_MARKERS):
                    log(
                        f"⏭️ Skipping remaining retries for {model}; "
                        "rate limit detected. Trying next fallback model."
                    )
                    break

                time.sleep(0.5)

    return {
        "files": [],
        "summary": "",
        "error": last_error,
    }
