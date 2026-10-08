from pathlib import Path

import re

import shutil

import time

from fastapi import FastAPI, HTTPException

from fastapi.responses import FileResponse, HTMLResponse

from pydantic import BaseModel, Field

from agent.executor import (

    execute_plan,

    PROJECTS_DIR,

    GENERATED_DIR,

)

from agent.history import (

    create_task_record,

    get_history,

    get_task,

    clear_history,

)

from agent.logger import (

    log,

    get_logs,

    clear_logs,

)

from agent.memory import save_memory

from agent.planner import create_plan
from agent.task_manager import TaskManager

from agent.storage import get_generated_file

BASE_DIR = Path(__file__).resolve().parent

DASHBOARD_FILE = BASE_DIR / "dashboard.html"

APP_VERSION = "3.2.0"

app = FastAPI(

    title="AutoDev Agent",

    description=(

        "Autonomous AI software development agent with "

        "planning, code generation, testing, repair, "

        "memory and project management."

    ),

    version=APP_VERSION,

)

task_manager = TaskManager()

class TaskRequest(BaseModel):

    task: str = Field(

        ...,

        min_length=1,

        max_length=10000,

        description="Task for AutoDev Agent",

    )

    inputs: dict = Field(

        default_factory=dict

    )

    model_config = {

        "str_strip_whitespace": True

    }

TaskRequest.model_rebuild()

IGNORED_PROJECT_DIRS = {

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

IGNORED_PROJECT_SUFFIXES = {

    ".pyc",

    ".pyo",

    ".pyd",

}

IGNORED_PROJECT_FILES = {

    ".DS_Store",

    "Thumbs.db",

}

def is_displayable_project_file(

    path: Path,

) -> bool:

    if path.name in IGNORED_PROJECT_FILES:

        return False

    if path.suffix.lower() in IGNORED_PROJECT_SUFFIXES:

        return False

    return not any(

        part in IGNORED_PROJECT_DIRS

        for part in path.parts

    )

def resolve_project_workspace(

    workspace_name: str,

) -> Path | None:

    if not workspace_name:

        return None

    safe_name = Path(

        workspace_name

    ).name

    if safe_name != workspace_name:

        return None

    workspace = (

        PROJECTS_DIR

        / safe_name

    )

    if not workspace.exists():

        return None

    if not workspace.is_dir():

        return None

    return workspace

def resolve_project_zip(

    workspace_name: str,

) -> Path | None:

    if not workspace_name:

        return None

    safe_name = Path(

        workspace_name

    ).name

    if safe_name != workspace_name:

        return None

    zip_path = (

        PROJECTS_DIR

        / f"{safe_name}.zip"

    )

    if not zip_path.exists():

        return None

    if not zip_path.is_file():

        return None

    return zip_path

def resolve_project_file(

    workspace_name: str,

    file_path: str,

) -> Path | None:

    workspace = resolve_project_workspace(

        workspace_name

    )

    if workspace is None:

        return None

    normalized = (

        str(file_path or "")

        .replace("\\\\", "/")

        .lstrip("/")

    )

    if not normalized:

        return None

    candidate = (

        workspace / normalized

    ).resolve()

    try:

        candidate.relative_to(

            workspace.resolve()

        )

    except ValueError:

        return None

    if not candidate.exists():

        return None

    if not candidate.is_file():

        return None

    relative = candidate.relative_to(

        workspace

    )

    if not is_displayable_project_file(

        relative

    ):

        return None

    return candidate

def is_binary_file(

    path: Path,

) -> bool:

    try:

        return b"\x00" in (

            path.read_bytes()[:4096]

        )

    except Exception:

        return False

def project_name_from_workspace(

    workspace_name: str,

) -> str:

    return re.sub(

        r"_[0-9a-f]{8}$",

        "",

        workspace_name,

    )

@app.get(

    "/",

    response_class=HTMLResponse,

)

def root():

    return dashboard()

@app.get(

    "/dashboard",

    response_class=HTMLResponse,

)

def dashboard():

    if not DASHBOARD_FILE.exists():

        raise HTTPException(

            status_code=404,

            detail="dashboard.html not found.",

        )

    return HTMLResponse(

        DASHBOARD_FILE.read_text(

            encoding="utf-8"

        )

    )

@app.get("/health")

def health():

    return {

        "status": "ok",

        "service": "AutoDev Agent",

        "version": APP_VERSION,

    }

@app.post("/run-task")

def run_task(

    request: TaskRequest,

):

    start_time = time.perf_counter()

    clear_logs()

    plan = None

    result = None

    task_id = None

    try:

        task = request.task.strip()

        if not task:

            raise HTTPException(

                status_code=422,

                detail="Task cannot be empty.",

            )

        log("🧠 AutoDev task received.")

        log(f"📌 Task: {task}")

        plan = create_plan(

            task,

            request.inputs,

        )

        log(

            "🚀 Executing autonomous "

            "development pipeline."

        )

        result = execute_plan(

            plan=plan,

            inputs=request.inputs,

            task=task,

        )

        if not isinstance(result, dict):

            result = {

                "success": False,

                "error": (

                    "Executor returned "

                    "an invalid result."

                ),

            }

        success = bool(

            result.get(

                "success",

                False,

            )

        )

        status = (

            "success"

            if success

            else "error"

        )

        duration = (

            time.perf_counter()

            - start_time

        )

        workspace_name = result.get(

            "workspace_name"

        )

        zip_file = result.get(

            "zip_file"

        )

        project_url = (

            f"/projects/{workspace_name}"

            if workspace_name

            else None

        )

        zip_url = (

            f"/download-project/{workspace_name}"

            if workspace_name

            and zip_file

            else None

        )

        result["duration_seconds"] = round(

            duration,

            3,

        )

        if success:

            try:

                memory_id = save_memory(

                    task=task,

                    plan=plan,

                    result=result,

                )

                result["memory_saved"] = bool(

                    memory_id

                )

                if memory_id:

                    result["memory_id"] = (

                        memory_id

                    )

                    log(

                        "🧠 Successful experience "

                        "saved to memory."

                    )

            except Exception as exc:

                result["memory_saved"] = False

                log(

                    f"⚠️ Memory save failed: {exc}"

                )

        else:

            result["memory_saved"] = False

        try:

            history_record = create_task_record(

                task=task,

                status=status,

                plan=plan,

                logs=get_logs(),

                result=result,

                generated_file=workspace_name,

                duration_seconds=duration,

                inputs=request.inputs,

            )

            task_id = history_record.get(

                "id"

            )

        except Exception as exc:

            log(

                f"⚠️ History save failed: {exc}"

            )

        return {

            "status": status,

            "task": task,

            "task_id": task_id,

            "plan": plan,

            "logs": get_logs(),

            "project_name": result.get(

                "project_name"

            ),

            "workspace_name": workspace_name,

            "project_url": project_url,

            "zip_url": zip_url,

            "result": result,

            "duration_seconds": round(

                duration,

                3,

            ),

        }

    except HTTPException:

        raise

    except Exception as exc:

        duration = (

            time.perf_counter()

            - start_time

        )

        error_message = str(exc)

        log(

            f"❌ Agent error: "

            f"{error_message}"

        )

        try:

            history_record = create_task_record(

                task=request.task,

                status="error",

                plan=plan,

                logs=get_logs(),

                result={

                    "success": False,

                    "error": error_message,

                },

                generated_file=None,

                duration_seconds=duration,

                inputs=request.inputs,

            )

            task_id = history_record.get(

                "id"

            )

        except Exception:

            task_id = None

        return {

            "status": "error",

            "task": request.task,

            "task_id": task_id,

            "message": error_message,

            "logs": get_logs(),

            "duration_seconds": round(

                duration,

                3,

            ),

        }


@app.post("/run-task-async", status_code=202)
def run_task_async(request: TaskRequest):
    """Queue an autonomous task and return immediately."""
    from fastapi import HTTPException

    task = request.task.strip()
    if not task:
        raise HTTPException(status_code=400, detail="Task cannot be empty.")

    record = task_manager.create_task(task=task, inputs=request.inputs)
    task_manager.start_task(record["task_id"])

    return {
        "status": "accepted",
        "task_id": record["task_id"],
        "phase": record["phase"],
        "progress": record["progress"],
        "status_url": f"/tasks/{record['task_id']}",
    }


@app.get("/tasks")
def list_tasks(limit: int = 20):
    """Return recent background tasks."""
    limit = max(1, min(limit, 100))
    return {"tasks": task_manager.list_tasks(limit=limit)}


@app.get("/tasks/{task_id}")
def get_task_status(task_id: str):
    """Return live status, logs and result for a background task."""
    from fastapi import HTTPException

    record = task_manager.get_task(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Task not found.")

    return record


@app.get("/projects")

def list_projects():

    PROJECTS_DIR.mkdir(

        parents=True,

        exist_ok=True,

    )

    projects = []

    for workspace in sorted(

        PROJECTS_DIR.iterdir(),

        key=lambda item: item.stat().st_mtime,

        reverse=True,

    ):

        if not workspace.is_dir():

            continue

        files = []

        for path in workspace.rglob("*"):

            if not path.is_file():

                continue

            relative = path.relative_to(

                workspace

            )

            if not is_displayable_project_file(

                relative

            ):

                continue

            files.append(

                relative.as_posix()

            )

        zip_path = (

            PROJECTS_DIR

            / f"{workspace.name}.zip"

        )

        projects.append({

            "workspace_name": workspace.name,

            "project_name": project_name_from_workspace(

                workspace.name

            ),

            "file_count": len(files),

            "files": sorted(files),

            "zip_available": zip_path.exists(),

            "project_url": (

                f"/projects/{workspace.name}"

            ),

            "zip_url": (

                f"/download-project/{workspace.name}"

                if zip_path.exists()

                else None

            ),

        })

    return {

        "count": len(projects),

        "projects": projects,

    }

@app.get(

    "/projects/{workspace_name}"

)

def project_details(

    workspace_name: str,

):

    workspace = resolve_project_workspace(

        workspace_name

    )

    if workspace is None:

        raise HTTPException(

            status_code=404,

            detail="Project not found.",

        )

    files = []

    for path in sorted(

        workspace.rglob("*")

    ):

        if not path.is_file():

            continue

        relative = path.relative_to(

            workspace

        )

        if not is_displayable_project_file(

            relative

        ):

            continue

        relative_path = (

            relative.as_posix()

        )

        files.append({

            "path": relative_path,

            "view_url": (

                f"/projects/"

                f"{workspace_name}/file/"

                f"{relative_path}"

            ),

        })

    zip_path = (

        PROJECTS_DIR

        / f"{workspace_name}.zip"

    )

    return {

        "workspace_name": workspace_name,

        "project_name": project_name_from_workspace(

            workspace_name

        ),

        "files": files,

        "file_count": len(files),

        "zip_available": zip_path.exists(),

        "zip_url": (

            f"/download-project/{workspace_name}"

            if zip_path.exists()

            else None

        ),

    }

@app.get(

    "/projects/{workspace_name}/file/{file_path:path}"

)

def view_project_file(

    workspace_name: str,

    file_path: str,

):

    path = resolve_project_file(

        workspace_name,

        file_path,

    )

    if path is None:

        raise HTTPException(

            status_code=404,

            detail=(

                "Project file not found "

                "or unavailable."

            ),

        )

    if is_binary_file(path):

        raise HTTPException(

            status_code=400,

            detail=(

                "Binary files cannot "

                "be previewed."

            ),

        )

    return FileResponse(

        path=str(path),

        media_type="text/plain",

    )

@app.get(

    "/download-project/{workspace_name}"

)

def download_project(

    workspace_name: str,

):

    zip_path = resolve_project_zip(

        workspace_name

    )

    if zip_path is None:

        raise HTTPException(

            status_code=404,

            detail="Project ZIP not found.",

        )

    return FileResponse(

        path=str(zip_path),

        filename=zip_path.name,

        media_type="application/zip",

    )

@app.delete(

    "/projects/{workspace_name}"

)

def delete_project(

    workspace_name: str,

):

    workspace = resolve_project_workspace(

        workspace_name

    )

    if workspace is None:

        raise HTTPException(

            status_code=404,

            detail="Project not found.",

        )

    zip_path = (

        PROJECTS_DIR

        / f"{workspace_name}.zip"

    )

    try:

        shutil.rmtree(

            workspace

        )

        if zip_path.exists():

            zip_path.unlink()

        log(

            f"🗑️ Deleted project: "

            f"{workspace_name}"

        )

        return {

            "status": "success",

            "message": (

                "Project deleted successfully."

            ),

            "workspace_name": workspace_name,

        }

    except Exception as exc:

        raise HTTPException(

            status_code=500,

            detail=str(exc),

        )

@app.get(

    "/view/{filename}"

)

def view_generated_file(

    filename: str,

):

    file_path = get_generated_file(

        filename

    )

    if (

        file_path is None

        or not file_path.exists()

    ):

        raise HTTPException(

            status_code=404,

            detail="Generated file not found.",

        )

    if is_binary_file(file_path):

        raise HTTPException(

            status_code=400,

            detail=(

                "Binary files cannot "

                "be previewed."

            ),

        )

    return FileResponse(

        path=str(file_path),

        media_type="text/plain",

    )

@app.get(

    "/download/{filename}"

)

def download_generated_file(

    filename: str,

):

    file_path = get_generated_file(

        filename

    )

    if (

        file_path is None

        or not file_path.exists()

    ):

        raise HTTPException(

            status_code=404,

            detail="Generated file not found.",

        )

    return FileResponse(

        path=str(file_path),

        filename=file_path.name,

        media_type="text/x-python",

    )

@app.delete(

    "/generated-files/{filename}"

)

def delete_generated_file(

    filename: str,

):

    if not filename.endswith(".py"):

        raise HTTPException(

            status_code=400,

            detail=(

                "Only generated Python "

                "files can be deleted."

            ),

        )

    file_path = get_generated_file(

        filename

    )

    if (

        file_path is None

        or not file_path.exists()

    ):

        raise HTTPException(

            status_code=404,

            detail="Generated file not found.",

        )

    try:

        deleted_name = file_path.name

        file_path.unlink()

        log(

            f"🗑️ Deleted generated file: "

            f"{deleted_name}"

        )

        return {

            "status": "success",

            "message": (

                "Generated file deleted successfully."

            ),

            "filename": deleted_name,

        }

    except Exception as exc:

        raise HTTPException(

            status_code=500,

            detail=str(exc),

        )

@app.get(

    "/generated-files"

)

def list_generated_files():

    GENERATED_DIR.mkdir(

        parents=True,

        exist_ok=True,

    )

    files = []

    for file in sorted(

        GENERATED_DIR.glob("*.py"),

        key=lambda item: item.stat().st_mtime,

        reverse=True,

    ):

        files.append({

            "name": file.name,

            "view_url": (

                f"/view/{file.name}"

            ),

            "download_url": (

                f"/download/{file.name}"

            ),

            "delete_url": (

                f"/generated-files/{file.name}"

            ),

        })

    return {

        "count": len(files),

        "files": files,

    }

@app.get("/history")

def task_history(

    limit: int = 50,

):

    limit = max(

        1,

        min(limit, 200),

    )

    history = get_history(

        limit

    )

    return {

        "count": len(history),

        "tasks": history,

    }

@app.get(

    "/history/{task_id}"

)

def single_task_history(

    task_id: str,

):

    task = get_task(

        task_id

    )

    if task is None:

        raise HTTPException(

            status_code=404,

            detail="Task history not found.",

        )

    return task

@app.delete("/history")

def delete_task_history():

    clear_history()

    return {

        "status": "success",

        "message": "Task history cleared.",

    }
