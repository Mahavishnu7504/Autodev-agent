import re
import shutil
import subprocess
import sys
import uuid
import zipfile

from pathlib import Path

from typing import Dict, Optional, Any
from agent.quality_gate import run_quality_gate
from agent.failure_analyzer import analyze_failure
from agent.logger import log

from agent.project_repair import repair_project

from agent.test_generator import generate_tests

# ============================================================

# PATHS / CONFIG

# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

GENERATED_DIR = BASE_DIR / "generated"

PROJECTS_DIR = GENERATED_DIR / "projects"

MAX_REPAIR_ATTEMPTS = 3

COMMAND_TIMEOUT = 30

# Repair context limits.

REPAIR_MAX_FILES = 8

REPAIR_FILE_MAX_CHARS = 6000

REPAIR_CONTEXT_MAX_CHARS = 18000

REPAIR_FAILURE_MAX_CHARS = 5000

GENERATED_DIR.mkdir(

    parents=True,

    exist_ok=True,

)

PROJECTS_DIR.mkdir(

    parents=True,

    exist_ok=True,

)

# ============================================================

# ARTIFACT FILTERS

# ============================================================

IGNORED_DIRECTORIES = {

    ".git",

    ".github",

    "__pycache__",

    ".pytest_cache",

    ".mypy_cache",

    ".ruff_cache",

    ".tox",

    ".coverage",

    ".idea",

    ".vscode",

    ".venv",

    "venv",

    "env",

    "node_modules",

}

IGNORED_SUFFIXES = {

    ".pyc",

    ".pyo",

    ".pyd",

}

IGNORED_FILENAMES = {

    ".DS_Store",

    "Thumbs.db",

}

def _is_ignored_path(path: Path) -> bool:

    """

    Return True for cache, binary, IDE and development artifacts.

    """

    if path.name in IGNORED_FILENAMES:

        return True

    if path.suffix.lower() in IGNORED_SUFFIXES:

        return True

    if any(

        part in IGNORED_DIRECTORIES

        for part in path.parts

    ):

        return True

    return False

# ============================================================

# PATH HELPERS

# ============================================================

def _safe_name(value: str) -> str:

    value = re.sub(

        r"[^A-Za-z0-9_-]+",

        "_",

        str(value or "AutoDev_Project"),

    )

    value = value.strip("_")

    return value[:80] or "AutoDev_Project"

def _safe_relative_path(path: str) -> Path:

    normalized = (

        str(path)

        .replace("\\", "/")

        .strip()

        .lstrip("/")

    )

    if not normalized:

        raise ValueError(

            "Project path cannot be empty."

        )

    candidate = Path(normalized)

    if candidate.is_absolute():

        raise ValueError(

            f"Absolute project path is not allowed: {path}"

        )

    if ".." in candidate.parts:

        raise ValueError(

            f"Unsafe project path: {path}"

        )

    return candidate

# ============================================================

# COMMAND EXECUTION

# ============================================================

def _run_command(

    command: str,

    workspace: Path,

    timeout: int = COMMAND_TIMEOUT,

) -> Dict[str, Any]:

    log(

        f"▶️ Running: {command}"

    )

    try:

        result = subprocess.run(

            command,

            cwd=str(workspace),

            shell=True,

            capture_output=True,

            text=True,

            timeout=timeout,

        )

        return {

            "success": result.returncode == 0,

            "command": command,

            "stdout": result.stdout,

            "stderr": result.stderr,

            "return_code": result.returncode,

        }

    except subprocess.TimeoutExpired:

        return {

            "success": False,

            "command": command,

            "stdout": "",

            "stderr": (

                f"Command timed out after "

                f"{timeout} seconds."

            ),

            "return_code": None,

        }

    except Exception as exc:

        return {

            "success": False,

            "command": command,

            "stdout": "",

            "stderr": str(exc),

            "return_code": None,

        }

# ============================================================

# PROJECT FILE OPERATIONS

# ============================================================

def _write_project(

    workspace: Path,

    files: Dict[str, str],

) -> None:

    for relative_path, content in files.items():

        safe_path = _safe_relative_path(

            relative_path

        )

        if _is_ignored_path(safe_path):

            continue

        destination = workspace / safe_path

        destination.parent.mkdir(

            parents=True,

            exist_ok=True,

        )

        destination.write_text(

            str(content),

            encoding="utf-8",

        )

def _read_project(

    workspace: Path,

) -> Dict[str, str]:

    result: Dict[str, str] = {}

    for path in workspace.rglob("*"):

        if not path.is_file():

            continue

        relative = path.relative_to(

            workspace

        )

        if _is_ignored_path(relative):

            continue

        try:

            result[

                relative.as_posix()

            ] = path.read_text(

                encoding="utf-8"

            )

        except (

            UnicodeDecodeError,

            OSError,

        ):

            continue

    return result

def _merge_files(

    project: Dict[str, str],

    updates: list[dict],

) -> Dict[str, str]:

    merged = dict(project)

    for item in updates:

        if not isinstance(item, dict):

            continue

        path = _safe_relative_path(

            item.get("path", "")

        ).as_posix()

        if _is_ignored_path(

            Path(path)

        ):

            continue

        content = item.get("content")

        if content is None:

            continue

        merged[path] = str(content)

    return merged

# ============================================================

# CLEANUP

# ============================================================

def _validate_repair_structure(project: Dict[str, str], updates: list[dict]) -> list[dict]:
    """Reject repairs that collapse an existing Python package into a module."""
    existing_paths = {str(path).replace("\\", "/").strip() for path in project.keys()}
    has_app_package = any(path == "app/__init__.py" or path.startswith("app/") for path in existing_paths)
    has_src_package = any(path == "src/__init__.py" or path.startswith("src/") for path in existing_paths)
    valid: list[dict] = []
    for item in updates:
        if not isinstance(item, dict): continue
        raw_path = str(item.get("path", "")).strip(); content = item.get("content")
        if not raw_path or content is None: continue
        try: safe_path = _safe_relative_path(raw_path).as_posix()
        except ValueError as exc:
            log(f"⚠️ Rejected unsafe repair path {raw_path!r}: {exc}"); continue
        if _is_ignored_path(Path(safe_path)): continue
        if has_app_package and safe_path == "app.py":
            log("⚠️ Rejected unsafe repair: existing app/ package cannot be replaced by app.py."); continue
        if has_src_package and safe_path == "src.py":
            log("⚠️ Rejected unsafe repair: existing src/ package cannot be replaced by src.py."); continue
        valid.append({"path": safe_path, "content": str(content)})
    return valid

def _cleanup_generated_artifacts(

    workspace: Path,

) -> None:

    removed = 0

    for path in list(

        workspace.rglob("*")

    ):

        if not path.exists():

            continue

        relative = path.relative_to(

            workspace

        )

        if not _is_ignored_path(relative):

            continue

        try:

            if path.is_dir():

                shutil.rmtree(path)

            elif path.is_file():

                path.unlink()

            removed += 1

        except Exception as exc:

            log(

                f"⚠️ Could not remove artifact "

                f"{path}: {exc}"

            )

    if removed:

        log(

            f"🧹 Removed {removed} "

            f"generated/cache artifact(s)."

        )

# ============================================================

# COMMAND SELECTION

# ============================================================

def _choose_run_command(

    plan: Dict[str, Any],

    files: Dict[str, str],

) -> Optional[str]:

    command = str(

        plan.get("run_command") or ""

    ).strip()

    if command:

        match = re.fullmatch(

            r"(?:python|python3)\s+"

            r"([A-Za-z0-9_./\\-]+)\.py",

            command,

        )

        if match:

            script = (

                match.group(1)

                .replace("\\", "/")

            )

            parts = script.split("/")

            if len(parts) > 1:

                module = ".".join(parts)

                return (

                    f"{sys.executable} "

                    f"-m {module}"

                )

        return command

    candidates = [

        "app/main.py",

        "main.py",

        "app.py",

        "src/main.py",

    ]

    for candidate in candidates:

        if candidate not in files:

            continue

        if "/" in candidate:

            module = (

                candidate[:-3]

                .replace("/", ".")

            )

            return (

                f"{sys.executable} "

                f"-m {module}"

            )

        return (

            f"{sys.executable} "

            f"{candidate}"

        )

    return None

def _choose_test_command(

    plan: Dict[str, Any],

    files: Dict[str, str],

) -> Optional[str]:

    command = str(

        plan.get("test_command") or ""

    ).strip()

    if command:

        return command

    if any(

        path.startswith("tests/")

        for path in files

    ):

        return (

            f"{sys.executable} "

            f"-m unittest discover "

            f"-s tests -v"

        )

    return None

# ============================================================

# ZIP

# ============================================================

def create_project_zip(

    workspace: Path,

) -> Path:

    _cleanup_generated_artifacts(

        workspace

    )

    zip_path = (

        workspace.parent

        / f"{workspace.name}.zip"

    )

    if zip_path.exists():

        zip_path.unlink()

    with zipfile.ZipFile(

        zip_path,

        "w",

        compression=zipfile.ZIP_DEFLATED,

    ) as archive:

        for file in workspace.rglob("*"):

            if not file.is_file():

                continue

            relative = file.relative_to(

                workspace

            )

            if _is_ignored_path(relative):

                continue

            archive.write(

                file,

                arcname=(

                    f"{workspace.name}/"

                    f"{relative.as_posix()}"

                ),

            )

    return zip_path

# ============================================================

# REPAIR CONTEXT

# ============================================================

def _normalize_failure_text(

    failure: Dict[str, Any],

) -> str:

    parts = []

    test_result = failure.get(

        "test_result"

    )

    run_result = failure.get(

        "run_result"

    )

    if isinstance(test_result, dict):

        parts.append(

            "TEST COMMAND:\n"

            + str(

                test_result.get(

                    "command",

                    "",

                )

            )

        )

        parts.append(

            "TEST STDERR:\n"

            + str(

                test_result.get(

                    "stderr",

                    "",

                )

            )

        )

        parts.append(

            "TEST STDOUT:\n"

            + str(

                test_result.get(

                    "stdout",

                    "",

                )

            )

        )

        parts.append(

            "TEST RETURN CODE:\n"

            + str(

                test_result.get(

                    "return_code",

                    "",

                )

            )

        )

    if isinstance(run_result, dict):

        parts.append(

            "APPLICATION COMMAND:\n"

            + str(

                run_result.get(

                    "command",

                    "",

                )

            )

        )

        parts.append(

            "APPLICATION STDERR:\n"

            + str(

                run_result.get(

                    "stderr",

                    "",

                )

            )

        )

        parts.append(

            "APPLICATION STDOUT:\n"

            + str(

                run_result.get(

                    "stdout",

                    "",

                )

            )

        )

        parts.append(

            "APPLICATION RETURN CODE:\n"

            + str(

                run_result.get(

                    "return_code",

                    "",

                )

            )

        )

    text = "\n\n".join(parts)

    if len(text) > REPAIR_FAILURE_MAX_CHARS:

        text = (

            "[failure output truncated]\n\n"

            + text[-REPAIR_FAILURE_MAX_CHARS:]

        )

    return text

def _select_repair_project(

    project: Dict[str, str],

    failure_text: str,

) -> Dict[str, str]:

    normalized_failure = (

        failure_text

        .replace("\\", "/")

        .lower()

    )

    selected: Dict[str, str] = {}

    mentioned = []

    for path in project:

        normalized = (

            path

            .replace("\\", "/")

            .lower()

        )

        filename = Path(

            normalized

        ).name

        if normalized in normalized_failure:

            mentioned.append(path)

            continue

        if (

            filename

            and filename in normalized_failure

            and path.endswith(".py")

        ):

            mentioned.append(path)

    source_files = [

        path

        for path in project

        if path.endswith(".py")

        and (

            path.startswith("app/")

            or path.startswith("src/")

            or path in {

                "main.py",

                "app.py",

            }

        )

    ]

    test_files = [

        path

        for path in project

        if path.endswith(".py")

        and path.startswith("tests/")

    ]

    other_python = [

        path

        for path in project

        if path.endswith(".py")

    ]

    ordered = []

    for path in (

        mentioned

        + source_files

        + test_files

        + other_python

    ):

        if path not in ordered:

            ordered.append(path)

    total_chars = 0

    for path in ordered:

        if len(selected) >= REPAIR_MAX_FILES:

            break

        content = project.get(

            path,

            "",

        )

        if not content:

            continue

        remaining = (

            REPAIR_CONTEXT_MAX_CHARS

            - total_chars

        )

        if remaining <= 0:

            break

        max_chars = min(

            REPAIR_FILE_MAX_CHARS,

            remaining,

        )

        if len(content) > max_chars:

            content = (

                content[:max_chars]

                + "\n\n"

                "[FILE CONTENT TRUNCATED "

                "FOR REPAIR CONTEXT]"

            )

        selected[path] = content

        total_chars += len(content)

    return selected

def _build_repair_failure(
    failure: Dict[str, Any],
) -> Dict[str, Any]:

    return {
        "phase": failure.get(
            "phase",
            "unknown",
        ),
        "summary": _normalize_failure_text(
            failure
        ),
        "diagnosis": failure.get(
            "diagnosis",
            {},
        ),
        "behavior_specification": (
            failure.get(
                "behavior_specification",
                {},
            )
        ),
    }

# REPAIR ENGINE

# ============================================================

def _repair_project(

    *,

    task: str,

    workspace: Path,

    project: Dict[str, str],

    failure: Dict[str, Any],

    attempt: int,

) -> tuple[

    Dict[str, str],

    Dict[str, Any],

]:

    log(

        f"🤖 Autonomous repair "

        f"attempt {attempt}/"

        f"{MAX_REPAIR_ATTEMPTS}"

    )

    compact_failure = _build_repair_failure(

        failure

    )

    failure_text = compact_failure.get(

        "summary",

        "",

    )

    repair_context = _select_repair_project(

        project,

        failure_text,

    )

    log(

        f"🧠 Repair context: "

        f"{len(repair_context)} file(s), "

        f"{sum(len(v) for v in repair_context.values())} "

        f"characters."

    )

    patch = repair_project(

        task=task,

        project=repair_context,

        failure=compact_failure,

        attempt=attempt,

    )

    if not isinstance(patch, dict):

        patch = {

            "files": [],

            "summary": (

                "Repair engine returned "

                "an invalid response."

            ),

        }

    updates = patch.get(

        "files",

        [],

    )

    if not isinstance(updates, list):

        updates = []

    if not updates:

        log(

            "❌ Repair engine returned "

            "no file changes."

        )

        return project, patch

    valid_updates = []

    for item in updates:

        if not isinstance(item, dict):

            continue

        path = str(

            item.get(

                "path",

                "",

            )

        ).strip()

        content = item.get(

            "content"

        )

        if not path or content is None:

            continue

        safe_path = (

            _safe_relative_path(path)

            .as_posix()

        )

        if _is_ignored_path(

            Path(safe_path)

        ):

            continue

        valid_updates.append({

            "path": safe_path,

            "content": str(content),

        })

    if not valid_updates:

        log(

            "❌ Repair response contained "

            "no valid source changes."

        )

        return project, {

            **patch,

            "files": [],

        }

    valid_updates = _validate_repair_structure(project, valid_updates)

    if not valid_updates:
        log("❌ Repair rejected: no structurally safe file changes remained.")
        return project, {**patch, "files": []}

    _write_project(

        workspace,

        {

            item["path"]: item["content"]

            for item in valid_updates

        },

    )

    updated_project = _merge_files(

        project,

        valid_updates,

    )

    changed_files = [

        item["path"]

        for item in valid_updates

    ]

    log(

        "🔧 Applied repair: "

        + ", ".join(changed_files)

    )

    return updated_project, {

        **patch,

        "files": valid_updates,

    }

# ============================================================

# MAIN EXECUTION PIPELINE

# ============================================================

def execute_project(

    plan: Dict[str, Any],

    task: str = "",

    inputs: Optional[dict] = None,

) -> Dict[str, Any]:

    if not isinstance(plan, dict):

        raise TypeError(

            "execute_project expects "

            "a project specification dict."

        )

    behavior_specification = plan.get(

        "behavior_specification",

        {},

    )

    project_name = _safe_name(

        plan.get(

            "project_name"

        )

        or "AutoDev_Project"

    )

    workspace_name = (

        f"{project_name}_"

        f"{uuid.uuid4().hex[:8]}"

    )

    workspace = (

        PROJECTS_DIR

        / workspace_name

    )

    workspace.mkdir(

        parents=True,

        exist_ok=False,

    )

    log(

        f"📦 Workspace created: "

        f"{workspace}"

    )

    # ========================================================

    # LOAD PLANNER FILES

    # ========================================================

    raw_files = plan.get(

        "files"

    ) or []

    project: Dict[str, str] = {}

    for item in raw_files:

        if not isinstance(item, dict):

            continue

        path = str(

            item.get(

                "path",

                "",

            )

        ).strip()

        content = item.get(

            "content",

            "",

        )

        if (

            not path

            or not str(content).strip()

        ):

            continue

        safe_path = (

            _safe_relative_path(path)

            .as_posix()

        )

        if _is_ignored_path(

            Path(safe_path)

        ):

            continue

        project[safe_path] = str(

            content

        )

    if not project:

        raise ValueError(

            "Planner produced an empty project."

        )

    _write_project(

        workspace,

        project,

    )

    log(

        f"📝 Wrote "

        f"{len(project)} "

        f"project file(s)."

    )

    # ========================================================

    # TEST GENERATION

    # ========================================================

    log(

        "🧪 Generating tests from the "

        "shared behavior specification."

    )

    test_generation = generate_tests(

        task=(

            task

            or str(

                plan.get(

                    "summary",

                    "",

                )

            )

        ),

        project=project,

        specification=behavior_specification,

    )

    test_files = (

        test_generation.get(

            "tests",

            [],

        )

    )

    if test_files:

        valid_test_files = []

        for item in test_files:

            if not isinstance(item, dict):

                continue

            raw_path = str(

                item.get(

                    "path",

                    "",

                )

            ).strip()

            content = item.get(

                "content",

                "",

            )

            if (

                not raw_path

                or not str(content).strip()

            ):

                continue

            safe_path = (

                _safe_relative_path(

                    raw_path

                ).as_posix()

            )

            if _is_ignored_path(

                Path(safe_path)

            ):

                continue

            project[safe_path] = str(

                content

            )

            valid_test_files.append({

                "path": safe_path,

                "content": str(content),

            })

        if valid_test_files:

            _write_project(

                workspace,

                {

                    item["path"]: item["content"]

                    for item in valid_test_files

                },

            )

            test_files = valid_test_files

            plan["test_command"] = (

                test_generation.get(

                    "test_command",

                    (

                        f"{sys.executable} "

                        f"-m unittest discover "

                        f"-s tests -v"

                    ),

                )

            )

            log(

                f"🧪 Added "

                f"{len(test_files)} "

                f"generated test file(s)."

            )

        else:

            test_files = []

    else:

        error = test_generation.get(

            "error"

        )

        if error:

            log(

                f"⚠️ Test generation "

                f"did not produce tests: "

                f"{error}"

            )

    # ========================================================

    # COMMANDS

    # ========================================================

    test_command = _choose_test_command(

        plan,

        project,

    )

    run_command = _choose_run_command(

        plan,

        project,

    )

    # ========================================================

    # STATE

    # ========================================================

    repair_history = []

    test_result = None

    run_result = None

    repair_attempts = 0

    tests_passed = False

    application_passed = False

    # ========================================================

    # PHASE 1

    # TEST -> REPAIR -> RETEST

    # ========================================================

    log(

        "🧪 Starting autonomous validation."

    )

    if test_command:

        for cycle in range(

            1,

            MAX_REPAIR_ATTEMPTS + 1,

        ):

            log(

                f"🔍 Test validation "

                f"cycle {cycle}/"

                f"{MAX_REPAIR_ATTEMPTS}"

            )

            project = _read_project(

                workspace

            )

            test_result = _run_command(

                test_command,

                workspace,

            )

            if test_result["success"]:

                log(

                    "✅ Test suite passed."

                )

                tests_passed = True

                break

            failure_preview = str(

                test_result.get(

                    "stderr",

                    "",

                )

            )

            if not failure_preview:

                failure_preview = str(

                    test_result.get(

                        "stdout",

                        "",

                    )

                )

            if len(failure_preview) > 5000:

                failure_preview = (

                    "[test output truncated]\n"

                    + failure_preview[-5000:]

                )

            log(

                "❌ Tests failed.\n"

                + failure_preview

            )

            if cycle >= MAX_REPAIR_ATTEMPTS:

                break

            repair_attempts += 1
            diagnosis = analyze_failure(
                test_result=test_result,
                run_result=run_result,
                phase="tests",
                project=project,
            )

            log(
                "🔎 Failure diagnosis: "
                f"{diagnosis.get('category', 'unknown')} — "
                f"{diagnosis.get('summary', '')}"
            )

            failure = {
                "phase": "tests",
                "test_result": test_result,
                "run_result": run_result,
                "diagnosis": diagnosis,
                "behavior_specification": (
                    behavior_specification
                ),
            }

            project, patch = _repair_project(

                task=task,

                workspace=workspace,

                project=project,

                failure=failure,

                attempt=repair_attempts,

            )

            updates = patch.get(

                "files",

                [],

            )

            if not updates:

                break

            repair_history.append({

                "attempt": repair_attempts,

                "phase": "tests",

                "summary": patch.get(

                    "summary",

                    "",

                ),

                "files_changed": [

                    item["path"]

                    for item in updates

                    if isinstance(item, dict)

                    and item.get("path")

                ],
                "diagnosis": diagnosis,
            })

    else:

        tests_passed = True

        test_result = {

            "success": True,

            "command": None,

            "stdout": "",

            "stderr": "",

            "return_code": 0,

            "skipped": True,

        }

        log(

            "ℹ️ No test command available; "

            "skipping tests."

        )

    # ========================================================

    # PHASE 2

    # APPLICATION -> REPAIR -> TEST -> RERUN

    # ========================================================

    if tests_passed and run_command:

        for cycle in range(

            1,

            MAX_REPAIR_ATTEMPTS + 1,

        ):

            log(

                f"🚀 Application validation "

                f"cycle {cycle}/"

                f"{MAX_REPAIR_ATTEMPTS}"

            )

            project = _read_project(

                workspace

            )

            run_result = _run_command(

                run_command,

                workspace,

            )

            if run_result["success"]:

                log(

                    "✅ Application execution "

                    "successful."

                )

                application_passed = True

                break

            failure_preview = str(

                run_result.get(

                    "stderr",

                    "",

                )

            )

            if not failure_preview:

                failure_preview = str(

                    run_result.get(

                        "stdout",

                        "",

                    )

                )

            if len(failure_preview) > 5000:

                failure_preview = (

                    "[application output truncated]\n"

                    + failure_preview[-5000:]

                )

            log(

                "❌ Application execution "

                "failed.\n"

                + failure_preview

            )

            if cycle >= MAX_REPAIR_ATTEMPTS:

                break

            repair_attempts += 1
            diagnosis = analyze_failure(
                test_result=test_result,
                run_result=run_result,
                phase="application",
                project=project,
            )

            log(
                "🔎 Failure diagnosis: "
                f"{diagnosis.get('category', 'unknown')} — "
                f"{diagnosis.get('summary', '')}"
            )

            failure = {
                "phase": "application",
                "test_result": test_result,
                "run_result": run_result,
                "diagnosis": diagnosis,
                "behavior_specification": (
                    behavior_specification
                ),
            }

            project, patch = _repair_project(

                task=task,

                workspace=workspace,

                project=project,

                failure=failure,

                attempt=repair_attempts,

            )

            updates = patch.get(

                "files",

                [],

            )

            if not updates:

                break

            changed_files = [

                item["path"]
                for item in updates
                if isinstance(item, dict)
                and item.get("path")

            ]

            repair_history.append({

                "attempt": repair_attempts,
                "phase": "application",
                "summary": patch.get(
                    "summary",
                    "",

                ),

                "files_changed": changed_files,
                "diagnosis": diagnosis,
            })

            # ------------------------------------------------
            # Application repair must preserve tests.
            # ------------------------------------------------

            log(
                "🧪 Re-running tests "
                "after application repair."

            )
            project = _read_project(
                workspace

            )

            if test_command:
                test_result = _run_command(
                    test_command,
                    workspace,

                )
                if not test_result["success"]:

                    log(
                        "❌ Repair caused "
                        "test failure."
                    )

                    tests_passed = False
                    continue
                log(

                    "✅ Tests still pass "
                    "after repair."

                )

                tests_passed = True

    elif tests_passed and not run_command:
        application_passed = True

    # =======================================================
    # FINAL STATUS
    # =======================================================

    tests_success = bool(

        test_result
        and test_result.get(
            "success"

        )

    )

    application_success = bool(

        run_result
        and run_result.get(
            "success"

        )

    )

    if run_command:

        success = (
            tests_success
            and application_success

        )

    else:
        success = tests_success

    # ========================================================
    # AUTONOMOUS QUALITY GATE
    # ========================================================

    # Evaluate the raw workspace before cleanup so the gate can detect
    # caches, package collisions, syntax/import defects and other issues.
    try:
        quality_gate = run_quality_gate(
            workspace=workspace,
            plan=plan,
            test_result=test_result,
            run_result=run_result,
            behavior_specification=behavior_specification,
        )
    except Exception as exc:
        log(f"❌ Quality gate crashed: {exc}")
        quality_gate = {
            "quality_gate": "v1",
            "passed": False,
            "tests_passed": tests_success,
            "application_passed": application_success if run_command else True,
            "failed_checks": [f"Quality gate execution error: {exc}"],
            "checks": {},
            "warnings": [],
        }

    if quality_gate.get("passed"):
        log("🛡️ AUTONOMOUS QUALITY GATE PASSED")
    else:
        log("❌ AUTONOMOUS QUALITY GATE FAILED")
        for failed_check in quality_gate.get("failed_checks", []):
            log(f"   ❌ {failed_check}")

    success = bool(success and quality_gate.get("passed", False))

    # ========================================================
    # FINAL CLEANUP
    # ========================================================

    _cleanup_generated_artifacts(workspace)

    # ========================================================
    # ZIP
    # ========================================================

    zip_path = create_project_zip(workspace)

    final_files = sorted(
        _read_project(
            workspace
        ).keys()

    )

    log(

        f"📦 Project ZIP created: "
        f"{zip_path.name}"

    )

    # ========================================================
    # FINAL RESULT
    # ========================================================

    return {

        "success": success,
        "project_name": project_name,
        "workspace_name": workspace_name,
        "workspace_path": str(
            workspace

        ),

        "files": final_files,
        "behavior_specification": (
            behavior_specification

        ),

        "test_generation": {

            "generated": bool(
                test_files

            ),

            "count": len(
                test_files

            ),

            "error": test_generation.get(
                "error"

            ),

        },

        "test_result": test_result,
        "run_result": run_result,
        "tests_passed": tests_success,

        "application_passed": (
            application_success
            if run_command
            else True

        ),

        "repair_attempts": repair_attempts,
        "repair_history": repair_history,
        "quality_gate": quality_gate,
        "zip_file": zip_path.name,
        "zip_path": str(
            zip_path

        ),

    }

# ============================================================

# COMPATIBILITY WRAPPER

# ============================================================

def execute_plan(

    plan: Dict[str, Any],
    inputs: Optional[dict] = None,
    task: str = "",

) -> Dict[str, Any]:

    return execute_project(

        plan=plan,
        task=task,
        inputs=inputs,

    )
