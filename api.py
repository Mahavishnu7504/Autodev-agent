from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from agent.planner import create_plan
from agent.executor import execute_plan
from agent.logger import (
    log,
    get_logs,
    clear_logs,
)
from agent.storage import get_generated_file


app = FastAPI(
    title="AutoDev Agent",
    version="1.0.0"
)


class TaskRequest(BaseModel):
    task: str
    inputs: dict = Field(
        default_factory=dict
    )


@app.get("/")
def home():
    return {
        "message": "AutoDev Agent API is running 🚀"
    }


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

        if filename:
            download_url = (
                f"/download/{filename}"
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
            detail="Generated file not found."
        )

    return FileResponse(
        path=str(file_path),
        filename=file_path.name,
        media_type="text/x-python"
    )


@app.get("/generated-files")
def list_generated_files():
    directory = Path("generated")

    if not directory.exists():
        return {
            "files": []
        }

    files = sorted(
        [
            file.name
            for file in directory.glob(
                "*.py"
            )
        ]
    )

    return {
        "count": len(files),
        "files": files
    }