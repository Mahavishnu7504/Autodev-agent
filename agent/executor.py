import shutil
import subprocess
import sys
import uuid

from pathlib import Path
from typing import Optional

from agent.logger import log


MAX_RETRIES = 3
EXECUTION_TIMEOUT = 30

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECTS_DIR = BASE_DIR / "generated" / "projects"

PROJECTS_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


def _safe_project_name(name: str) -> str:
    """
    Convert an LLM-generated project name into a safe directory name.
    """

    if not name:
        return "Generated_Project"

    cleaned = "".join(
        character
        if character.isalnum() or character in "_-"
        else "_"
        for character in name.strip()
    )

    cleaned = cleaned.strip("._-")

    if not cleaned:
        cleaned = "Generated_Project"

    return cleaned[:80]


def _create_workspace(project_name: str) -> tuple[str, Path]:
    """
    Create a unique workspace for one generated project.
    """

    safe_name = _safe_project_name(project_name)

    workspace_id = uuid.uuid4().hex[:8]

    workspace_name = (
        f"{safe_name}_{workspace_id}"
    )

    workspace = PROJECTS_DIR / workspace_name

    workspace.mkdir(
        parents=True,
        exist_ok=False,
    )

    return workspace_name, workspace


def _safe_relative_path(path: str) -> Path:
    """
    Validate a generated project-relative path.
    """

    if not isinstance(path, str):
        raise ValueError(
            "Project file path must be a string."
        )

    normalized = path.replace("\\", "/").strip()

    if not normalized:
        raise ValueError(
            "Project file path cannot be empty."
        )

    if normalized.startswith("/"):
        raise ValueError(
            f"Absolute path is not allowed: {path}"
        )

    parts = normalized.split("/")

    if ".." in parts:
        raise ValueError(
            f"Path traversal detected: {path}"
        )

    if ":" in parts[0]:
        raise ValueError(
            f"Drive path is not allowed: {path}"
        )

    return Path(*parts)


def _write_project_files(
    workspace: Path,
    files: list,
) -> list[str]:
    """
    Write all generated project files into the workspace.
    """

    written_files = []

    for file_info in files:

        relative_path = _safe_relative_path(
            file_info["path"]
        )

        content = file_info["content"]

        target = workspace / relative_path

        target.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        target.write_text(
            content,
            encoding="utf-8",
        )

        written_files.append(
            relative_path.as_posix()
        )

        log(
            f"📄 Created: "
            f"{relative_path.as_posix()}"
        )

    return written_files


def _run_command(
    command: str,
    workspace: Path,
) -> dict:

    if not command:
        return {
            "success": True,
            "stdout": "",
            "stderr": "",
            "return_code": 0,
        }

    log(
        f"⚙️ Running: {command}"
    )

    try:

        result = subprocess.run(
            command,
            cwd=str(workspace),
            shell=True,
            capture_output=True,
            text=True,
            timeout=EXECUTION_TIMEOUT,
        )

        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "return_code": result.returncode,
        }

    except subprocess.TimeoutExpired:

        return {
            "success": False,
            "stdout": "",
            "stderr": (
                f"Command timed out after "
                f"{EXECUTION_TIMEOUT} seconds."
            ),
            "return_code": None,
        }

    except Exception as exc:

        return {
            "success": False,
            "stdout": "",
            "stderr": str(exc),
            "return_code": None,
        }


def _find_entry_file(spec: dict) -> Optional[str]:
    """
    Find a sensible Python entry file.
    """

    preferred = [
        "app/main.py",
        "main.py",
        "app.py",
        "src/main.py",
    ]

    file_paths = {
        file_info["path"].replace("\\", "/")
        for file_info in spec.get("files", [])
    }

    for candidate in preferred:

        if candidate in file_paths:
            return candidate

    python_files = [
        path
        for path in file_paths
        if path.endswith(".py")
        and not path.startswith("tests/")
    ]

    if python_files:
        return sorted(python_files)[0]

    return None


def execute_project(
    project_spec: dict,
) -> dict:

    log("🚀 Starting project execution...")

    project_name = project_spec.get(
        "project_name",
        "Generated_Project",
    )

    workspace_name, workspace = _create_workspace(
        project_name
    )

    log(
        f"📁 Workspace created: {workspace_name}"
    )

    written_files = _write_project_files(
        workspace,
        project_spec.get("files", []),
    )

    run_command = project_spec.get(
        "run_command",
        "",
    )

    test_command = project_spec.get(
        "test_command",
        "",
    )

    result = {
        "success": False,
        "workspace_name": workspace_name,
        "workspace_path": str(workspace),
        "project_name": project_name,
        "files": written_files,
        "run_result": None,
        "test_result": None,
    }

    # ------------------------------------------------------------
    # TESTS
    # ------------------------------------------------------------

    if test_command:

        log("🧪 Running project tests...")

        test_result = _run_command(
            test_command,
            workspace,
        )

        result["test_result"] = test_result

        if not test_result["success"]:

            log(
                "❌ Tests failed:\n"
                + test_result["stderr"]
            )

            result["error"] = (
                "Project tests failed."
            )

            return result

        log("✅ Tests passed.")

    # ------------------------------------------------------------
    # RUN APPLICATION
    # ------------------------------------------------------------

    if run_command:

        log("▶️ Running project...")

        run_result = _run_command(
            run_command,
            workspace,
        )

        result["run_result"] = run_result

        if not run_result["success"]:

            log(
                "❌ Project execution failed:\n"
                + run_result["stderr"]
            )

            result["error"] = (
                "Project execution failed."
            )

            return result

        log("✅ Project executed successfully.")

    else:

        entry_file = _find_entry_file(
            project_spec
        )

        if entry_file:

            log(
                f"🐍 Running entry file: "
                f"{entry_file}"
            )

            run_result = _run_command(
                f'"{sys.executable}" "{entry_file}"',
                workspace,
            )

            result["run_result"] = run_result

            if not run_result["success"]:

                log(
                    "❌ Entry file failed:\n"
                    + run_result["stderr"]
                )

                result["error"] = (
                    "Project execution failed."
                )

                return result

            log(
                "✅ Entry file executed successfully."
            )

    result["success"] = True

    log(
        "🎉 Project completed successfully."
    )

    return result


def execute_plan(
    plan,
    inputs: Optional[dict] = None,
) -> dict:
    """
    Backward-compatible entry point.

    The new planner returns a structured project specification.
    """

    if not isinstance(plan, dict):

        return {
            "success": False,
            "error": (
                "Executor expected a structured "
                "project specification."
            ),
        }

    return execute_project(plan)