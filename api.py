from pathlib import Path
import re
import shutil
import time
import zipfile

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from agent.planner import create_plan
from agent.executor import execute_plan, PROJECTS_DIR
from agent.logger import log, get_logs, clear_logs


# ============================================================
# OPTIONAL HISTORY MODULE
# ============================================================

try:
    from agent.history import (
        create_task_record,
        get_history,
        get_task,
        clear_history,
    )
except ImportError:
    create_task_record = None
    get_history = None
    get_task = None
    clear_history = None


# ============================================================
# PATHS
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

GENERATED_DIR = BASE_DIR / "generated"
PROJECTS_DIR = GENERATED_DIR / "projects"

DASHBOARD_FILE = BASE_DIR / "dashboard.html"


GENERATED_DIR.mkdir(
    parents=True,
    exist_ok=True,
)

PROJECTS_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="AutoDev Agent",
    description=(
        "Autonomous AI software development agent"
    ),
    version="3.0.0",
)


# ============================================================
# REQUEST MODEL
# ============================================================

class TaskRequest(BaseModel):

    task: str = Field(
        ...,
        min_length=1,
        description="Task for AutoDev Agent",
    )

    inputs: dict = Field(
        default_factory=dict,
        description="Runtime inputs for the task",
    )


# ============================================================
# HELPERS
# ============================================================

def safe_project_name(name: str) -> str:
    """
    Convert a project name into a safe filesystem name.
    """

    if not name:
        return "Generated_Project"

    name = str(name).strip()

    name = re.sub(
        r"[^a-zA-Z0-9_-]+",
        "_",
        name,
    )

    name = re.sub(
        r"_+",
        "_",
        name,
    )

    name = name.strip("._-")

    if not name:
        return "Generated_Project"

    return name[:80]


def resolve_project_path(
    project_name: str,
) -> Path | None:
    """
    Safely resolve a generated project directory.
    """

    if not project_name:
        return None

    safe_name = Path(
        project_name
    ).name

    if safe_name != project_name:
        return None

    project_path = (
        PROJECTS_DIR / safe_name
    ).resolve()

    projects_root = (
        PROJECTS_DIR.resolve()
    )

    try:
        project_path.relative_to(
            projects_root
        )
    except ValueError:
        return None

    if not project_path.exists():
        return None

    if not project_path.is_dir():
        return None

    return project_path


def create_project_zip(
    project_name: str,
) -> Path | None:
    """
    Create a ZIP archive for a generated project.
    """

    project_path = resolve_project_path(
        project_name
    )

    if project_path is None:
        return None

    zip_path = (
        PROJECTS_DIR /
        f"{project_path.name}.zip"
    )

    try:

        with zipfile.ZipFile(
            zip_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:

            for file_path in project_path.rglob("*"):

                if not file_path.is_file():
                    continue

                relative_path = (
                    file_path.relative_to(
                        project_path.parent
                    )
                )

                archive.write(
                    file_path,
                    arcname=str(relative_path),
                )

        return zip_path

    except Exception as exc:

        log(
            f"❌ Failed to create ZIP: {exc}"
        )

        return None


def get_project_files(
    project_path: Path,
) -> list:

    files = []

    if not project_path.exists():
        return files

    for file_path in sorted(
        project_path.rglob("*")
    ):

        if not file_path.is_file():
            continue

        relative_path = (
            file_path.relative_to(
                project_path
            )
        )

        files.append(
            {
                "path": relative_path.as_posix(),
                "size": file_path.stat().st_size,
            }
        )

    return files


# ============================================================
# HOME
# ============================================================

@app.get("/")
def home():

    return {
        "message": (
            "AutoDev Agent API is running 🚀"
        ),
        "version": "3.0.0",
        "architecture": (
            "structured multi-file agent"
        ),
        "dashboard": "/dashboard",
        "docs": "/docs",
    }


# ============================================================
# DASHBOARD
# ============================================================

@app.get("/dashboard")
def dashboard():

    if not DASHBOARD_FILE.exists():

        raise HTTPException(
            status_code=404,
            detail="dashboard.html not found.",
        )

    return FileResponse(
        path=str(DASHBOARD_FILE),
        media_type="text/html",
    )


# ============================================================
# RUN TASK
# ============================================================

@app.post("/run-task")
def run_task(
    request: TaskRequest,
):

    clear_logs()

    start_time = time.perf_counter()

    plan = None
    result = None
    project_name = None
    task_id = None

    try:

        # ====================================================
        # PLAN
        # ====================================================

        log(
            "🧠 Understanding requirements..."
        )

        plan = create_plan(
            request.task,
            request.inputs,
        )

        if not isinstance(plan, dict):

            raise RuntimeError(
                "Planner returned an invalid "
                "project specification."
            )

        project_name = plan.get(
            "project_name"
        )

        log(
            f"📋 Project planned: "
            f"{project_name}"
        )

        log(
            f"📁 Files planned: "
            f"{len(plan.get('files', []))}"
        )

        # ====================================================
        # EXECUTE
        # ====================================================

        log(
            "🏗 Generating project..."
        )

        result = execute_plan(
            plan
        )

        if not isinstance(result, dict):

            raise RuntimeError(
                "Executor returned an invalid result."
            )

        # ====================================================
        # PROJECT DETAILS
        # ====================================================

        workspace_name = result.get(
            "workspace_name"
        )

        workspace_path = result.get(
            "workspace_path"
        )

        generated_files = result.get(
            "files",
            [],
        )

        if workspace_name:

            zip_path = create_project_zip(
                workspace_name
            )

        else:

            zip_path = None

        if zip_path:

            log(
                f"📦 Project ZIP created: "
                f"{zip_path.name}"
            )

        # ====================================================
        # STATUS
        # ====================================================

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

        current_logs = get_logs()

        # ====================================================
        # HISTORY
        # ====================================================

        if create_task_record:

            try:

                history_record = (
                    create_task_record(
                        task=request.task,
                        status=status,
                        plan=plan,
                        logs=current_logs,
                        result={
                            **result,
                            "zip_file": (
                                zip_path.name
                                if zip_path
                                else None
                            ),
                        },
                        generated_file=(
                            workspace_name
                        ),
                        duration_seconds=duration,
                        inputs=request.inputs,
                    )
                )

                task_id = history_record.get(
                    "id"
                )

            except Exception as exc:

                log(
                    "⚠️ History save failed: "
                    f"{exc}"
                )

        # ====================================================
        # URLS
        # ====================================================

        project_url = None
        zip_url = None

        if workspace_name:

            project_url = (
                f"/projects/{workspace_name}"
            )

            zip_url = (
                f"/download-project/"
                f"{workspace_name}"
            )

        # ====================================================
        # RESPONSE
        # ====================================================

        return {

            "status": status,

            "task": request.task,

            "task_id": task_id,

            "project_name": project_name,

            "plan": plan,

            "logs": get_logs(),

            "workspace_name": workspace_name,

            "workspace_path": workspace_path,

            "files": generated_files,

            "project_url": project_url,

            "zip_url": zip_url,

            "duration_seconds": round(
                duration,
                3,
            ),

            "result": result,
        }

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

        current_logs = get_logs()

        # ====================================================
        # SAVE FAILURE TO HISTORY
        # ====================================================

        if create_task_record:

            try:

                history_record = (
                    create_task_record(
                        task=request.task,
                        status="error",
                        plan=plan,
                        logs=current_logs,
                        result={
                            "success": False,
                            "error": error_message,
                        },
                        generated_file=(
                            project_name
                        ),
                        duration_seconds=duration,
                        inputs=request.inputs,
                    )
                )

                task_id = history_record.get(
                    "id"
                )

            except Exception:
                pass

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


# ============================================================
# LIST PROJECTS
# ============================================================

@app.get("/projects")
def list_projects():

    if not PROJECTS_DIR.exists():

        return {
            "count": 0,
            "projects": [],
        }

    projects = []

    for project_path in sorted(
        PROJECTS_DIR.iterdir(),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    ):

        if not project_path.is_dir():
            continue

        files = get_project_files(
            project_path
        )

        projects.append(
            {
                "name": project_path.name,
                "files": files,
                "file_count": len(files),
                "view_url": (
                    f"/projects/"
                    f"{project_path.name}"
                ),
                "download_url": (
                    f"/download-project/"
                    f"{project_path.name}"
                ),
            }
        )

    return {
        "count": len(projects),
        "projects": projects,
    }


# ============================================================
# GET PROJECT DETAILS
# ============================================================

@app.get("/projects/{project_name}")
def get_project(
    project_name: str,
):

    project_path = resolve_project_path(
        project_name
    )

    if project_path is None:

        raise HTTPException(
            status_code=404,
            detail="Generated project not found.",
        )

    return {
        "project_name": project_path.name,
        "files": get_project_files(
            project_path
        ),
    }


# ============================================================
# VIEW PROJECT FILE
# ============================================================

@app.get(
    "/projects/{project_name}/file/{file_path:path}"
)
def view_project_file(
    project_name: str,
    file_path: str,
):

    project_path = resolve_project_path(
        project_name
    )

    if project_path is None:

        raise HTTPException(
            status_code=404,
            detail="Generated project not found.",
        )

    requested = (
        project_path / file_path
    ).resolve()

    try:

        requested.relative_to(
            project_path.resolve()
        )

    except ValueError:

        raise HTTPException(
            status_code=400,
            detail="Invalid file path.",
        )

    if not requested.exists():
        raise HTTPException(
            status_code=404,
            detail="Project file not found.",
        )

    if not requested.is_file():
        raise HTTPException(
            status_code=400,
            detail="Requested path is not a file.",
        )

    return FileResponse(
        path=str(requested),
        media_type="text/plain",
    )


# ============================================================
# DOWNLOAD PROJECT ZIP
# ============================================================

@app.get(
    "/download-project/{project_name}"
)
def download_project(
    project_name: str,
):

    project_path = resolve_project_path(
        project_name
    )

    if project_path is None:

        raise HTTPException(
            status_code=404,
            detail="Generated project not found.",
        )

    zip_path = create_project_zip(
        project_name
    )

    if zip_path is None:

        raise HTTPException(
            status_code=500,
            detail="Failed to create project ZIP.",
        )

    return FileResponse(
        path=str(zip_path),
        filename=zip_path.name,
        media_type="application/zip",
    )


# ============================================================
# DELETE PROJECT
# ============================================================

@app.delete(
    "/projects/{project_name}"
)
def delete_project(
    project_name: str,
):

    project_path = resolve_project_path(
        project_name
    )

    if project_path is None:

        raise HTTPException(
            status_code=404,
            detail="Generated project not found.",
        )

    try:

        shutil.rmtree(
            project_path
        )

        zip_path = (
            PROJECTS_DIR /
            f"{project_path.name}.zip"
        )

        if zip_path.exists():
            zip_path.unlink()

        log(
            f"🗑️ Deleted project: "
            f"{project_path.name}"
        )

        return {
            "status": "success",
            "message": (
                "Project deleted successfully."
            ),
            "project_name": project_path.name,
        }

    except Exception as exc:

        log(
            f"❌ Failed to delete project: "
            f"{exc}"
        )

        raise HTTPException(
            status_code=500,
            detail="Failed to delete project.",
        )


# ============================================================
# LEGACY GENERATED FILE LIST
# ============================================================

@app.get("/generated-files")
def list_generated_files():

    files = []

    if not GENERATED_DIR.exists():

        return {
            "count": 0,
            "files": [],
        }

    for file in sorted(
        GENERATED_DIR.glob("*.py"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    ):

        files.append(
            {
                "name": file.name,
                "view_url": (
                    f"/view/{file.name}"
                ),
                "download_url": (
                    f"/download/{file.name}"
                ),
                "delete_url": (
                    f"/generated-files/"
                    f"{file.name}"
                ),
            }
        )

    return {
        "count": len(files),
        "files": files,
    }


# ============================================================
# LEGACY VIEW GENERATED FILE
# ============================================================

@app.get("/view/{filename}")
def view_generated_file(
    filename: str,
):

    if not filename.endswith(".py"):

        raise HTTPException(
            status_code=400,
            detail="Only Python files are supported.",
        )

    file_path = (
        GENERATED_DIR /
        Path(filename).name
    )

    if not file_path.exists():

        raise HTTPException(
            status_code=404,
            detail="Generated file not found.",
        )

    return FileResponse(
        path=str(file_path),
        media_type="text/plain",
    )


# ============================================================
# LEGACY DOWNLOAD GENERATED FILE
# ============================================================

@app.get("/download/{filename}")
def download_generated_file(
    filename: str,
):

    if not filename.endswith(".py"):

        raise HTTPException(
            status_code=400,
            detail="Only Python files are supported.",
        )

    file_path = (
        GENERATED_DIR /
        Path(filename).name
    )

    if not file_path.exists():

        raise HTTPException(
            status_code=404,
            detail="Generated file not found.",
        )

    return FileResponse(
        path=str(file_path),
        filename=file_path.name,
        media_type="text/x-python",
    )


# ============================================================
# LEGACY DELETE GENERATED FILE
# ============================================================

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

    safe_name = Path(
        filename
    ).name

    if safe_name != filename:

        raise HTTPException(
            status_code=400,
            detail="Invalid filename.",
        )

    file_path = (
        GENERATED_DIR /
        safe_name
    )

    if not file_path.exists():

        raise HTTPException(
            status_code=404,
            detail="Generated file not found.",
        )

    try:

        file_path.unlink()

        log(
            f"🗑️ Deleted generated file: "
            f"{filename}"
        )

        return {
            "status": "success",
            "message": (
                "Generated file deleted successfully."
            ),
            "filename": filename,
        }

    except Exception as exc:

        log(
            f"❌ Failed to delete "
            f"{filename}: {exc}"
        )

        raise HTTPException(
            status_code=500,
            detail="Failed to delete generated file.",
        )


# ============================================================
# TASK HISTORY
# ============================================================

@app.get("/history")
def task_history(
    limit: int = 50,
):

    if get_history is None:

        raise HTTPException(
            status_code=500,
            detail="History module is not available.",
        )

    history = get_history(
        limit
    )

    return {
        "count": len(history),
        "tasks": history,
    }


# ============================================================
# SINGLE TASK HISTORY
# ============================================================

@app.get("/history/{task_id}")
def single_task_history(
    task_id: str,
):

    if get_task is None:

        raise HTTPException(
            status_code=500,
            detail="History module is not available.",
        )

    task = get_task(
        task_id
    )

    if task is None:

        raise HTTPException(
            status_code=404,
            detail="Task history not found.",
        )

    return task


# ============================================================
# CLEAR HISTORY
# ============================================================

@app.delete("/history")
def delete_task_history():

    if clear_history is None:

        raise HTTPException(
            status_code=500,
            detail="History module is not available.",
        )

    clear_history()

    return {
        "status": "success",
        "message": "Task history cleared.",
    }