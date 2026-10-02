import subprocess
import tempfile
from agent.fixer import fix_code


def execute_plan(plan):

    code = extract_code(plan)

    if not code:
        return {"success": False, "error": "No code found"}

    for attempt in range(3):
        result = run_code(code)

        if result["success"]:
            return result

        print("🛠 Fixing code...")
        code = extract_code(fix_code(code, result["stderr"]))

    return {"success": False, "error": "Failed after retries"}


def extract_code(text):
    if "```python" in text:
        return text.split("```python")[1].split("```")[0]
    return text


def run_code(code):
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".py") as f:
            f.write(code.encode())
            path = f.name

        result = subprocess.run(
            ["python", path],
            capture_output=True,
            text=True,
            timeout=10
        )

        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr
        }

    except Exception as e:
        return {"success": False, "stderr": str(e)}