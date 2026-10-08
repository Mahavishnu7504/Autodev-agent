from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional


DEFAULT_TIMEOUT = 30
DEFAULT_MEMORY = "512m"
DEFAULT_CPUS = "1.0"


def _copy_project(source: Path, destination: Path) -> None:
    """Copy a generated project while excluding common build/cache artifacts."""
    ignored_dirs = {
        ".git",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "venv",
        "env",
        "node_modules",
    }

    ignored_suffixes = {
        ".pyc",
        ".pyo",
        ".pyd",
    }

    for item in source.rglob("*"):
        relative = item.relative_to(source)

        if any(part in ignored_dirs for part in relative.parts):
            continue

        if item.is_file() and item.suffix.lower() in ignored_suffixes:
            continue

        target = destination / relative

        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def _docker_available() -> bool:
    """Return True when the Docker daemon is reachable."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def run_in_sandbox(
    project_path: str | Path,
    run_command: str,
    timeout: int = DEFAULT_TIMEOUT,
    memory: str = DEFAULT_MEMORY,
    cpus: str = DEFAULT_CPUS,
    image: str = "python:3.12-slim",
) -> dict:
    """
    Execute generated project code inside a temporary Docker container.

    Security controls:
    - temporary isolated workspace
    - no network
    - non-root user
    - memory limit
    - CPU limit
    - execution timeout
    - automatic container removal
    """

    source = Path(project_path).resolve()

    if not source.exists():
        return {
            "success": False,
            "exit_code": None,
            "stdout": "",
            "stderr": f"Project path does not exist: {source}",
            "timed_out": False,
            "duration_seconds": 0,
            "error": "missing_project",
        }

    if not source.is_dir():
        return {
            "success": False,
            "exit_code": None,
            "stdout": "",
            "stderr": f"Project path is not a directory: {source}",
            "timed_out": False,
            "duration_seconds": 0,
            "error": "invalid_project_path",
        }

    if not _docker_available():
        return {
            "success": False,
            "exit_code": None,
            "stdout": "",
            "stderr": "Docker daemon is not available.",
            "timed_out": False,
            "duration_seconds": 0,
            "error": "docker_unavailable",
        }

    timeout = max(1, int(timeout))

    temp_dir = Path(tempfile.mkdtemp(prefix="autodev-sandbox-"))

    container_name = (
        f"autodev-sandbox-{int(time.time())}-{abs(hash(str(temp_dir))) % 100000}"
    )

    start_time = time.time()

    try:
        _copy_project(source, temp_dir)

        command = [
            "docker",
            "run",
            "--rm",
            "--name",
            container_name,
            "--network",
            "none",
            "--cpus",
            cpus,
            "--memory",
            memory,
            "--pids-limit",
            "128",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--user",
            "1000:1000",
            "--workdir",
            "/workspace",
            "--mount",
            f"type=bind,source={temp_dir},target=/workspace",
            image,
            "sh",
            "-c",
            run_command,
        ]

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            duration = round(time.time() - start_time, 3)

            return {
                "success": result.returncode == 0,
                "exit_code": result.returncode,
                "stdout": result.stdout[-10000:],
                "stderr": result.stderr[-10000:],
                "timed_out": False,
                "duration_seconds": duration,
                "error": None if result.returncode == 0 else "execution_failed",
            }

        except subprocess.TimeoutExpired as exc:
            duration = round(time.time() - start_time, 3)

            return {
                "success": False,
                "exit_code": None,
                "stdout": (exc.stdout or "")[-10000:]
                if isinstance(exc.stdout, str)
                else "",
                "stderr": (exc.stderr or "")[-10000:]
                if isinstance(exc.stderr, str)
                else "",
                "timed_out": True,
                "duration_seconds": duration,
                "error": "timeout",
            }

        except OSError as exc:
            duration = round(time.time() - start_time, 3)

            return {
                "success": False,
                "exit_code": None,
                "stdout": "",
                "stderr": str(exc),
                "timed_out": False,
                "duration_seconds": duration,
                "error": "docker_execution_error",
            }

    finally:
        # Best-effort cleanup in case the container still exists.
        try:
            subprocess.run(
                ["docker", "rm", "-f", container_name],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception:
            pass

        shutil.rmtree(temp_dir, ignore_errors=True)


def sandbox_healthcheck() -> dict:
    """Return basic sandbox availability information."""
    available = _docker_available()

    return {
        "available": available,
        "docker": "available" if available else "unavailable",
        "default_timeout": DEFAULT_TIMEOUT,
        "default_memory": DEFAULT_MEMORY,
        "default_cpus": DEFAULT_CPUS,
        "network": "disabled",
        "container_cleanup": True,
    }