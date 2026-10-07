from pathlib import Path
import re
import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from agent.planner import create_plan
from agent.executor import execute_plan
from agent.logger import log, get_logs, clear_logs
from agent.storage import get_generated_file


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

DASHBOARD_FILE = BASE_DIR / "dashboard.html"


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="AutoDev Agent",
    description="Autonomous AI software development agent",
    version="2.1.0",
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
        default_factory=dict
    )


# ============================================================
# FRIENDLY FILE NAME
# ============================================================

def generate_friendly_name(task: str) -> str:

    text = task.lower().strip()

    # Specific tasks first
    if "area" in text and "circle" in text:
        name = "Area_Calculator"

    elif "area" in text and "rectangle" in text:
        name = "Rectangle_Area_Calculator"

    elif "calculator" in text or "calculate" in text:
        name = "Calculator"

    elif (
        "rest api" in text
        or "restful api" in text
        or "api" in text
    ):
        name = "REST_API"

    elif "web scraper" in text or "scrape" in text:
        name = "Web_Scraper"

    elif "file organizer" in text or "organize files" in text:
        name = "File_Organizer"

    elif "todo" in text or "to-do" in text:
        name = "Todo_App"

    elif "csv" in text and (
        "analy" in text
        or "process" in text
        or "read" in text
    ):
        name = "CSV_Analyzer"

    elif "json" in text and (
        "api" in text
        or "process" in text
        or "parser" in text
    ):
        name = "JSON_Processor"

    elif "password" in text:
        name = "Password_Manager"

    elif "login" in text or "authentication" in text:
        name = "Authentication_System"

    elif "weather" in text:
        name = "Weather_App"

    elif "chatbot" in text or "chat bot" in text:
        name = "Chatbot"

    elif "machine learning" in text:
        name = "Machine_Learning_Model"

    elif "fastapi" in text:
        name = "FastAPI_App"

    elif "flask" in text:
        name = "Flask_App"

    elif "database" in text or "sql" in text:
        name = "Database_App"

    else:

        cleaned = re.sub(
            r"^(create|build|make|develop|write|generate)\s+",
            "",
            text,
        )

        cleaned = re.sub(
            r"^(a|an|the)\s+",
            "",
            cleaned,
        )

        words = re.findall(
            r"[a-zA-Z0-9]+",
            cleaned,
        )

        stop_words = {
            "python",
            "program",
            "script",
            "application",
            "app",
            "using",
            "with",
            "for",
            "to",
            "that",
            "which",
            "should",
            "can",
            "be",
            "and",
        }

        words = [
            word
            for word in words
            if word not in stop_words
        ]

        words = words[:4]

        if words:
            name = "_".join(
                word.capitalize()
                for word in words
            )
        else:
            name = "Generated_Code"

    name = re.sub(
        r"[^a-zA-Z0-9_]+",
        "_",
        name,
    )

    name = re.sub(
        r"_+",
        "_",
        name,
    )

    name = name.strip("_")

    if not name:
        name = "Generated_Code"

    return name


# ============================================================
# UNIQUE FILE NAME
# ============================================================

def get_unique_filename(base_name: str) -> str:

    GENERATED_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    candidate = (
        GENERATED_DIR /
        f"{base_name}.py"
    )

    counter = 2

    while candidate.exists():

        candidate = (
            GENERATED_DIR /
            f"{base_name}_{counter}.py"
        )

        counter += 1

    return candidate.name


# ============================================================
# RENAME GENERATED FILE
# ============================================================

def rename_generated_file(
    original_filename: str,
    task: str,
):

    if not original_filename:
        return None

    original_path = get_generated_file(
        original_filename
    )

    if original_path is None:
        return original_filename

    if not original_path.exists():
        return original_filename

    friendly_base = generate_friendly_name(
        task
    )

    new_filename = get_unique_filename(
        friendly_base
    )

    new_path = GENERATED_DIR / new_filename

    try:

        original_path.rename(
            new_path
        )

        return new_filename

    except Exception as exc:

        log(
            f"⚠️ Could not rename generated file: {exc}"
        )

        return original_filename


# ============================================================
# HOME
# ============================================================

@app.get("/")
def home():

    return {
        "message": "AutoDev Agent API is running 🚀",
        "version": "2.1.0",
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
    request: TaskRequest
):

    clear_logs()

    start_time = time.perf_counter()

    plan = None
    result = None
    filename = None

    try:

        # ----------------------------------------------------
        # PLAN
        # ----------------------------------------------------

        log(
            "🧠 Generating plan..."
        )

        plan = create_plan(
            request.task
        )

        # ----------------------------------------------------
        # EXECUTE
        # ----------------------------------------------------

        log(
            "🚀 Executing generated code..."
        )

        result = execute_plan(
            plan,
            request.inputs,
        )

        # ----------------------------------------------------
        # GENERATED FILE
        # ----------------------------------------------------

        if isinstance(result, dict):

            filename = result.get(
                "generated_file"
            )

        # ----------------------------------------------------
        # FRIENDLY NAME
        # ----------------------------------------------------

        if filename:

            renamed = rename_generated_file(
                filename,
                request.task,
            )

            if renamed:

                filename = renamed

                result[
                    "generated_file"
                ] = filename

        # ----------------------------------------------------
        # STATUS
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # HISTORY
        # ----------------------------------------------------

        task_id = None

        if create_task_record:

            try:

                history_record = create_task_record(
                    task=request.task,
                    status=status,
                    plan=plan,
                    logs=current_logs,
                    result=result,
                    generated_file=filename,
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

        # ----------------------------------------------------
        # URLS
        # ----------------------------------------------------

        download_url = None
        view_url = None

        if filename:

            download_url = (
                f"/download/{filename}"
            )

            view_url = (
                f"/view/{filename}"
            )

        # ----------------------------------------------------
        # RESPONSE
        # ----------------------------------------------------

        return {

            "status": status,

            "task": request.task,

            "task_id": task_id,

            "plan": plan,

            "logs": get_logs(),

            "generated_file": filename,

            "view_url": view_url,

            "download_url": download_url,

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
            f"❌ Agent error: {error_message}"
        )

        current_logs = get_logs()

        task_id = None

        if create_task_record:

            try:

                history_record = create_task_record(
                    task=request.task,
                    status="error",
                    plan=plan,
                    logs=current_logs,
                    result={
                        "success": False,
                        "error": error_message,
                    },
                    generated_file=filename,
                    duration_seconds=duration,
                    inputs=request.inputs,
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
# VIEW GENERATED FILE
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
            detail="Generated file not found.",
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
            detail="Generated file not found.",
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
# DELETE GENERATED FILE
# ============================================================

@app.delete("/generated-files/{filename}")
def delete_generated_file(
    filename: str
):

    # Only allow Python files
    if not filename.endswith(".py"):
        raise HTTPException(
            status_code=400,
            detail="Only generated Python files can be deleted.",
        )

    file_path = get_generated_file(
        filename
    )

    if file_path is None:
        raise HTTPException(
            status_code=404,
            detail="Generated file not found.",
        )

    if not file_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Generated file not found.",
        )

    try:

        deleted_name = file_path.name

        file_path.unlink()

        log(
            f"🗑️ Deleted generated file: {deleted_name}"
        )

        return {
            "status": "success",
            "message": "Generated file deleted successfully.",
            "filename": deleted_name,
        }

    except Exception as exc:

        log(
            f"❌ Failed to delete {filename}: {exc}"
        )

        raise HTTPException(
            status_code=500,
            detail="Failed to delete generated file.",
        )


# ============================================================
# LIST GENERATED FILES
# ============================================================

@app.get("/generated-files")
def list_generated_files():

    if not GENERATED_DIR.exists():

        return {
            "count": 0,
            "files": [],
        }

    files = []

    for file in sorted(
        GENERATED_DIR.glob("*.py"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    ):

        files.append({

            "name": file.name,

            "view_url":
                f"/view/{file.name}",

            "download_url":
                f"/download/{file.name}",

            "delete_url":
                f"/generated-files/{file.name}",
        })

    return {

        "count": len(files),

        "files": files,
    }


# ============================================================
# TASK HISTORY
# ============================================================

@app.get("/history")
def task_history(
    limit: int = 50
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
    task_id: str
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