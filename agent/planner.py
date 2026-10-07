import json
import time

from groq import Groq

from config import GROQ_API_KEY, MODEL_FALLBACKS, MAX_RETRIES


client = Groq(api_key=GROQ_API_KEY)


SYSTEM_PROMPT = """
You are AutoDev Agent, an autonomous AI software engineer.

Your job is to convert a natural-language software requirement into a
structured, executable project specification.

You MUST return ONLY valid JSON.

The JSON must have exactly this structure:

{
  "project_name": "Human_Readable_Project_Name",
  "summary": "Short description of the project",
  "language": "python",
  "run_command": "python app/main.py",
  "test_command": "python -m pytest -q",
  "files": [
    {
      "path": "app/main.py",
      "content": "complete file content"
    }
  ]
}

IMPORTANT RULES:

1. Generate a REAL multi-file project when appropriate.
2. Every file must contain COMPLETE source code.
3. Never use placeholders such as:
   - TODO
   - pass
   - "implement this"
   - "...existing code..."
   - "...".
4. File paths must be relative paths only.
5. Never use absolute paths.
6. Never use ".." in file paths.
7. Do not create files outside the project workspace.
8. Include requirements.txt when external Python packages are required.
9. Include README.md for non-trivial projects.
10. Include tests whenever the project is large enough to require them.
11. Tests must be executable.
12. Prefer standard Python libraries when they are sufficient.
13. Do not use input().
14. The project must be runnable without interactive user input.
15. Keep the project reasonably small and focused on the user's request.
16. Do not return Markdown.
17. Do not wrap JSON in ```json.
18. Return ONLY the JSON object.

The user's runtime inputs may be useful for examples or calculations.
Use them when appropriate.

The final project should be internally consistent:
imports must match files,
commands must match the generated structure,
and tests must match the implementation.
"""


def _extract_json(text: str) -> dict:
    """
    Extract JSON even if the model accidentally wraps it in Markdown.
    """

    text = text.strip()

    if text.startswith("```"):
        lines = text.splitlines()

        if lines and lines[0].startswith("```"):
            lines = lines[1:]

        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]

        text = "\n".join(lines).strip()

    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1 or end <= start:
        raise ValueError("Planner did not return a JSON object.")

    candidate = text[start:end + 1]

    return json.loads(candidate)


def _validate_project_spec(spec: dict) -> dict:
    """
    Validate the basic structure returned by the LLM.
    """

    if not isinstance(spec, dict):
        raise ValueError("Project specification must be an object.")

    required = [
        "project_name",
        "summary",
        "language",
        "run_command",
        "files",
    ]

    for key in required:
        if key not in spec:
            raise ValueError(
                f"Planner output missing required field: {key}"
            )

    if not isinstance(spec["files"], list):
        raise ValueError("Planner 'files' must be a list.")

    if not spec["files"]:
        raise ValueError("Planner returned no project files.")

    validated_files = []

    for item in spec["files"]:

        if not isinstance(item, dict):
            raise ValueError("Each project file must be an object.")

        path = item.get("path")
        content = item.get("content")

        if not isinstance(path, str) or not path.strip():
            raise ValueError("Project file has an invalid path.")

        if not isinstance(content, str):
            raise ValueError(
                f"Project file '{path}' has invalid content."
            )

        normalized = path.replace("\\", "/").strip()

        if normalized.startswith("/"):
            raise ValueError(
                f"Absolute path is not allowed: {path}"
            )

        if ":" in normalized.split("/")[0]:
            raise ValueError(
                f"Drive path is not allowed: {path}"
            )

        parts = normalized.split("/")

        if ".." in parts:
            raise ValueError(
                f"Path traversal detected: {path}"
            )

        if normalized in {"", ".", "./"}:
            raise ValueError(
                f"Invalid project file path: {path}"
            )

        validated_files.append(
            {
                "path": normalized,
                "content": content,
            }
        )

    spec["files"] = validated_files

    if not spec.get("test_command"):
        spec["test_command"] = ""

    return spec


def create_plan(user_task: str, inputs: dict | None = None):
    """
    Generate a structured multi-file project specification.
    """

    inputs = inputs or {}

    prompt = f"""
USER TASK:

{user_task}

RUNTIME INPUTS:

{json.dumps(inputs, indent=2, ensure_ascii=False)}

Generate the complete project specification now.

Remember:
- Return ONLY valid JSON.
- Generate complete files.
- Make the project executable.
- Include tests when appropriate.
"""

    last_error = None

    for model in MODEL_FALLBACKS:

        for attempt in range(MAX_RETRIES):

            try:

                print(
                    f"⚡ Trying model: {model} "
                    f"(attempt {attempt + 1})"
                )

                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {
                            "role": "system",
                            "content": SYSTEM_PROMPT,
                        },
                        {
                            "role": "user",
                            "content": prompt,
                        },
                    ],
                    temperature=0,
                )

                raw = response.choices[0].message.content

                spec = _extract_json(raw)

                spec = _validate_project_spec(spec)

                print(
                    f"✅ Project plan created: "
                    f"{spec['project_name']}"
                )

                print(
                    f"📁 Files planned: "
                    f"{len(spec['files'])}"
                )

                return spec

            except Exception as exc:

                last_error = str(exc)

                print(
                    f"❌ Planner failed: "
                    f"{model} -> {last_error}"
                )

                time.sleep(1)

    raise RuntimeError(
        f"All planner models failed: {last_error}"
    )