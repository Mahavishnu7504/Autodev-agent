from __future__ import annotations

import io
import os
import subprocess
import tarfile
import time
import uuid
from pathlib import Path

DEFAULT_TIMEOUT = 30
DEFAULT_MEMORY = "512m"
DEFAULT_CPUS = "1.0"
DEFAULT_IMAGE = "python:3.12-slim"
MAX_OUTPUT_CHARS = 10000
SKIP_NAMES = {
    ".git", ".venv", "venv", "__pycache__", "node_modules",
    ".pytest_cache", ".mypy_cache",
}


def _docker_env() -> dict[str, str]:
    return os.environ.copy()


def _docker_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True, text=True, timeout=10, env=_docker_env(),
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        return False


def _result(success, command, stdout="", stderr="", exit_code=None,
            timed_out=False, duration=0, error=None):
    return {
        "success": bool(success),
        "exit_code": exit_code,
        "command": command,
        "stdout": stdout[-MAX_OUTPUT_CHARS:],
        "stderr": stderr[-MAX_OUTPUT_CHARS:],
        "timed_out": timed_out,
        "duration_seconds": round(duration, 3),
        "error": error,
    }


def _project_archive(source: Path) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for item in source.iterdir():
            if item.name in SKIP_NAMES:
                continue
            archive.add(item, arcname=item.name, recursive=True)
    return buffer.getvalue()


def run_in_sandbox(
    project_path: str | Path,
    run_command: str,
    timeout: int = DEFAULT_TIMEOUT,
    memory: str = DEFAULT_MEMORY,
    cpus: str = DEFAULT_CPUS,
    image: str = DEFAULT_IMAGE,
) -> dict:
    """Execute generated code in a disposable isolated container.

    Files are streamed into a tmpfs workspace through stdin. No bind mounts
    from the AutoDev container or host are used.
    """
    started = time.monotonic()
    source = Path(project_path).resolve()

    if not source.is_dir():
        return _result(False, run_command, stderr=f"Project directory does not exist: {source}",
                       error="missing_project")
    if not run_command or not run_command.strip():
        return _result(False, run_command, stderr="Execution command is empty.",
                       error="empty_command")
    try:
        timeout = max(1, min(int(timeout), 300))
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT

    env = _docker_env()

    def docker(args, call_timeout=30):
        return subprocess.run(
            ["docker", *args], capture_output=True, text=True,
            timeout=call_timeout, env=env,
        )

    if not _docker_available():
        return _result(False, run_command,
                       stderr="Docker daemon is unavailable or the Docker CLI is missing.",
                       error="docker_unavailable")

    container = f"autodev-sandbox-{uuid.uuid4().hex[:16]}"
    created = False
    started_container = False

    try:
        image_check = docker(["image", "inspect", image], call_timeout=15)
        if image_check.returncode != 0:
            pull = docker(["pull", image], call_timeout=180)
            if pull.returncode != 0:
                return _result(False, run_command, stderr=pull.stderr or pull.stdout,
                               duration=time.monotonic() - started,
                               error="sandbox_image_pull_failed")

        create = docker([
            "create", "--name", container,
            "--network", "none",
            "--cpus", str(cpus), "--memory", str(memory),
            "--pids-limit", "128",
            "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=64m,uid=1000,gid=1000",
            "--tmpfs", "/workspace:rw,nosuid,nodev,size=128m,uid=1000,gid=1000",
            "--workdir", "/workspace",
            "--entrypoint", "sh", image, "-c", "sleep 3600",
        ], call_timeout=45)
        if create.returncode != 0:
            return _result(False, run_command, stderr=create.stderr or create.stdout,
                           duration=time.monotonic() - started,
                           error="container_create_failed")
        created = True

        start = docker(["start", container], call_timeout=20)
        if start.returncode != 0:
            return _result(False, run_command, stderr=start.stderr or start.stdout,
                           duration=time.monotonic() - started,
                           error="container_start_failed")
        started_container = True

        try:
            archive_bytes = _project_archive(source)
        except (OSError, tarfile.TarError) as exc:
            return _result(False, run_command, stderr=f"Project packaging failed: {exc}",
                           duration=time.monotonic() - started,
                           error="project_archive_failed")

        # Use the sandbox image's Python to extract the archive into tmpfs.
        # This avoids docker cp and avoids host-path bind mounts entirely.
        extract_code = (
            "import sys,tarfile; "
            "tarfile.open(fileobj=sys.stdin.buffer, mode='r|').extractall("
            "path='/workspace', filter='data')"
        )
        extracted = subprocess.run(
            ["docker", "exec", "-i", "--user", "1000:1000", container,
             "python", "-c", extract_code],
            input=archive_bytes, capture_output=True, timeout=45, env=env,
        )
        if extracted.returncode != 0:
            detail = (extracted.stderr or extracted.stdout).decode(errors="replace")
            return _result(False, run_command, stderr=detail or "Project extraction failed.",
                           duration=time.monotonic() - started,
                           error="project_copy_failed")

        try:
            result = docker(
                ["exec", "--user", "1000:1000", container,
                 "sh", "-lc", run_command],
                call_timeout=timeout + 5,
            )
            return _result(
                result.returncode == 0, run_command,
                result.stdout or "", result.stderr or "",
                exit_code=result.returncode,
                duration=time.monotonic() - started,
                error=None if result.returncode == 0 else "execution_failed",
            )
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout or b""
            err = exc.stderr or b""
            out = out.decode(errors="replace") if isinstance(out, bytes) else out
            err = err.decode(errors="replace") if isinstance(err, bytes) else err
            return _result(False, run_command, out,
                           err or f"Execution timed out after {timeout} seconds.",
                           timed_out=True, duration=time.monotonic() - started,
                           error="timeout")

    except (OSError, subprocess.TimeoutExpired) as exc:
        return _result(False, run_command,
                       stderr=f"Sandbox infrastructure error: {exc}",
                       duration=time.monotonic() - started,
                       error="sandbox_infrastructure_error")
    finally:
        if created:
            try:
                if started_container:
                    docker(["kill", container], call_timeout=10)
            except Exception:
                pass
            try:
                docker(["rm", "-f", container], call_timeout=15)
            except Exception:
                pass


def sandbox_healthcheck() -> dict:
    available = _docker_available()
    return {
        "available": available,
        "docker": "available" if available else "unavailable",
        "default_timeout": DEFAULT_TIMEOUT,
        "default_memory": DEFAULT_MEMORY,
        "default_cpus": DEFAULT_CPUS,
        "network": "disabled for generated-code containers",
        "container_cleanup": True,
        "transport": "streamed tar + docker exec; no bind mounts",
    }
