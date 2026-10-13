from __future__ import annotations

import base64
import hmac
import os
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

# This service must run on a dedicated Docker-enabled host. Do not set
# AUTODEV_RUNNER_URL on the runner itself: it must use its local Docker daemon.
from agent.sandbox_runner import _run_local_in_sandbox, sandbox_healthcheck

app = FastAPI(title="AutoDev Remote Sandbox Runner", version="1.0.0")
MAX_ARCHIVE_BYTES = int(os.getenv("AUTODEV_RUNNER_MAX_ARCHIVE_BYTES", str(8 * 1024 * 1024)))
RUNNER_TOKEN = os.getenv("AUTODEV_RUNNER_TOKEN", "").strip()


def _authorized(token: str | None) -> bool:
    return bool(RUNNER_TOKEN and token and hmac.compare_digest(token, RUNNER_TOKEN))


@app.get("/health")
def health(x_autodev_token: str | None = Header(default=None)) -> dict[str, Any]:
    if not _authorized(x_autodev_token):
        raise HTTPException(status_code=401, detail="Unauthorized")
    status = sandbox_healthcheck()
    if not status.get("available"):
        raise HTTPException(status_code=503, detail="Docker sandbox unavailable")
    return status


@app.post("/v1/execute")
async def execute(
    request: Request,
    x_autodev_token: str | None = Header(default=None),
    x_autodev_command: str = Header(default=""),
    x_autodev_timeout: int = Header(default=30),
    x_autodev_memory: str = Header(default="512m"),
    x_autodev_cpus: str = Header(default="1.0"),
    x_autodev_image: str = Header(default="python:3.12-slim"),
) -> JSONResponse:
    if not _authorized(x_autodev_token):
        raise HTTPException(status_code=401, detail="Unauthorized")
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/x-tar":
        raise HTTPException(status_code=415, detail="Expected application/x-tar")
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Empty project archive")
    if len(body) > MAX_ARCHIVE_BYTES:
        raise HTTPException(status_code=413, detail="Project archive exceeds configured size limit")
    try:
        command = base64.urlsafe_b64decode(x_autodev_command.encode("ascii")).decode("utf-8")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid command header")
    if not command.strip():
        raise HTTPException(status_code=400, detail="Command is empty")

    timeout = max(1, min(x_autodev_timeout, 300))
    with tempfile.TemporaryDirectory(prefix="autodev-runner-") as temp_dir:
        workspace = Path(temp_dir) / "workspace"
        workspace.mkdir()
        try:
            import io
            with tarfile.open(fileobj=io.BytesIO(body), mode="r:") as archive:
                # Python's data filter blocks path traversal and unsafe links.
                archive.extractall(path=workspace, filter="data")
        except (tarfile.TarError, OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"Invalid project archive: {exc}")
        result = _run_local_in_sandbox(
            project_path=workspace,
            run_command=command,
            timeout=timeout,
            memory=x_autodev_memory,
            cpus=x_autodev_cpus,
            image=x_autodev_image,
        )
        return JSONResponse(content=result)
