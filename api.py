from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from agent.planner import create_plan
from agent.executor import execute_plan
from agent.logger import (
    log,
    get_logs,
    clear_logs,
)
from agent.storage import get_generated_file


# ============================================================
# PATHS
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

GENERATED_DIR = BASE_DIR / "generated"

DASHBOARD_FILE = BASE_DIR / "dashboard.html"

GENERATED_DIR.mkdir(
    parents=True,
    exist_ok=True
)


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title="AutoDev Agent",
    version="1.0.0",
    description="Autonomous AI software development agent"
)


# ============================================================
# REQUEST MODEL
# ============================================================

class TaskRequest(BaseModel):

    task: str

    inputs: dict = Field(
        default_factory=dict
    )


# ============================================================
# HOME
# ============================================================

@app.get("/")
def home():

    return {
        "message": "AutoDev Agent API is running 🚀",
        "docs": "/docs",
        "dashboard": "/dashboard"
    }


# ============================================================
# DASHBOARD
# ============================================================

@app.get("/dashboard")
def dashboard():

    if not DASHBOARD_FILE.exists():

        raise HTTPException(
            status_code=404,
            detail="dashboard.html not found"
        )

    return FileResponse(
        path=str(DASHBOARD_FILE),
        media_type="text/html"
    )


# ============================================================
# RUN TASK
# ============================================================

@app.post("/run-task")
def run_task(request: TaskRequest):

    clear_logs()

    try:

        log("🧠 Generating plan...")

        plan = create_plan(
            request.task
        )

        log("🚀 Executing generated code...")

        result = execute_plan(
            plan,
            request.inputs
        )

        filename = result.get(
            "generated_file"
        )

        download_url = None
        view_url = None

        if filename:

            download_url = (
                f"/download/{filename}"
            )

            view_url = (
                f"/view/{filename}"
            )

        return {

            "status": (
                "success"
                if result.get("success")
                else "error"
            ),

            "task": request.task,

            "plan": plan,

            "logs": get_logs(),

            "generated_file": filename,

            "download_url": download_url,

            "view_url": view_url,

            "result": result
        }

    except Exception as exc:

        log(
            f"❌ Agent error: {exc}"
        )

        return {

            "status": "error",

            "message": str(exc),

            "logs": get_logs()
        }


# ============================================================
# DOWNLOAD GENERATED FILE
# ============================================================

@app.get("/download/{filename}")
def download_generated_file(
    filename: str
):

    file_path = get_generated_file(
        filename
    )

    if file_path is None:

        raise HTTPException(
            status_code=404,
            detail="Generated file not found"
        )

    return FileResponse(

        path=str(file_path),

        filename=file_path.name,

        media_type="text/x-python"
    )


# ============================================================
# VIEW GENERATED CODE
# ============================================================

@app.get("/view/{filename}")
def view_generated_file(
    filename: str
):

    file_path = get_generated_file(
        filename
    )

    if file_path is None:

        raise HTTPException(
            status_code=404,
            detail="Generated file not found"
        )

    try:

        code = file_path.read_text(
            encoding="utf-8"
        )

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=f"Unable to read generated file: {exc}"
        )

    return PlainTextResponse(
        code,
        media_type="text/plain"
    )


# ============================================================
# GENERATED FILES
# ============================================================

@app.get("/generated-files")
def list_generated_files():

    files = []

    for file in sorted(
        GENERATED_DIR.glob("*.py")
    ):

        files.append({

            "name": file.name,

            "download_url":
                f"/download/{file.name}",

            "view_url":
                f"/view/{file.name}"
        })

    return {

        "count": len(files),

        "files": files
    }