import re
import subprocess
import sys
from typing import Optional

from agent.fixer import fix_code
from agent.logger import log
from agent.storage import (
    create_generated_file,
    update_generated_file,
)

MAX_RETRIES = 3
EXECUTION_TIMEOUT = 15


def extract_python_code(text: str) -> Optional[str]:
    match = re.search(
        r"```python\s*(.*?)```",
        text,
        re.DOTALL | re.IGNORECASE
    )

    if match:
        return match.group(1).strip()

    return None


def clean_fixed_code(text: str) -> str:
    extracted = extract_python_code(text)

    if extracted:
        return extracted

    text = text.replace("```python", "")
    text = text.replace("```", "")

    return text.strip()


def inject_inputs(code: str, inputs: dict) -> str:
    if not inputs:
        return code

    lines = [
        "# AutoDev runtime inputs"
    ]

    for key, value in inputs.items():
        if not key.isidentifier():
            continue

        lines.append(
            f"{key} = {repr(value)}"
        )

    lines.append("")

    return "\n".join(lines) + code


def run_code(file_path: str) -> dict:
    try:
        log("⚙️ Running generated code...")

        result = subprocess.run(
            [sys.executable, file_path],
            capture_output=True,
            text=True,
            timeout=EXECUTION_TIMEOUT
        )

        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "return_code": result.returncode
        }

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "stdout": "",
            "stderr": (
                f"Execution timed out after "
                f"{EXECUTION_TIMEOUT} seconds."
            ),
            "return_code": None
        }

    except Exception as exc:
        return {
            "success": False,
            "stdout": "",
            "stderr": str(exc),
            "return_code": None
        }


def execute_plan(
    plan: str,
    inputs: Optional[dict] = None
) -> dict:

    log("🚀 Starting execution...")

    inputs = inputs or {}

    code = extract_python_code(plan)

    if not code:
        log("❌ No Python code found.")

        return {
            "success": False,
            "error": "No Python code found."
        }

    code = inject_inputs(code, inputs)

    filename, file_path = create_generated_file(code)

    log(f"💾 Generated code saved: {file_path}")

    last_result = None

    for attempt in range(1, MAX_RETRIES + 1):

        log(
            f"🔁 Execution attempt "
            f"{attempt}/{MAX_RETRIES}"
        )

        result = run_code(file_path)
        last_result = result

        if result["success"]:
            log("✅ Execution successful.")

            return {
                "success": True,
                "stdout": result["stdout"],
                "stderr": result["stderr"],
                "attempts": attempt,
                "generated_file": filename,
                "file_path": file_path
            }

        log(
            "❌ Execution error:\n"
            + result["stderr"]
        )

        if attempt >= MAX_RETRIES:
            break

        log("🛠 Attempting automatic repair...")

        fixed_response = fix_code(
            code,
            result["stderr"]
        )

        fixed_code = clean_fixed_code(
            fixed_response
        )

        if not fixed_code:
            log("❌ Fixer returned empty code.")
            break

        code = fixed_code

        update_generated_file(
            file_path,
            code
        )

        log(
            "💾 Fixed version saved to "
            + file_path
        )

    log("❌ Maximum retries reached.")

    return {
        "success": False,
        "error": "Execution failed after retries.",
        "stdout": (
            last_result["stdout"]
            if last_result
            else ""
        ),
        "stderr": (
            last_result["stderr"]
            if last_result
            else ""
        ),
        "attempts": MAX_RETRIES,
        "generated_file": filename,
        "file_path": file_path
    }