# AutoDev Agent — Deployment

## Local Docker Compose setup

1. Copy `.env.example` to `.env`.
2. Add your Groq API key and the other required local settings to `.env`.
3. Start the API and sandbox engine:

```powershell
docker compose up --build -d
docker compose ps
docker compose logs -f autodev
```

4. Open:

- Dashboard: http://127.0.0.1:8000/dashboard
- API docs: http://127.0.0.1:8000/docs
- Health: http://127.0.0.1:8000/health
- System status: http://127.0.0.1:8000/system/status

To stop without deleting saved volumes:

```powershell
docker compose down
```

**Do not use `docker compose down -v`** unless you intentionally want to delete named-volume data.

## Docker sandbox configuration

The API container uses the Docker CLI and connects to the separate `sandbox-engine`
service over TLS at `tcp://sandbox-engine:2376`. The Compose file configures
`DOCKER_HOST`, `DOCKER_TLS_VERIFY`, and `DOCKER_CERT_PATH` consistently.

The Docker daemon port is not published to the host. The API port is bound to
`127.0.0.1` for local use.

## Render deployment limitation

These Compose settings do **not** make Docker-in-Docker available on Render's
ordinary web service. Render must not be assumed to provide a reachable Docker
daemon or permit a privileged sidecar. If `/health` works on Render but tasks
fail with `Docker daemon is unavailable or the Docker CLI is missing`, the
sandbox is not connected in that environment.

Do not remove the sandbox check, report a failed task as successful, or execute
arbitrary generated code directly in the public API process to work around this.

For hosted execution, connect AutoDev to a separately hosted, properly isolated
sandbox service, or keep code execution in the local Compose environment and
present the hosted Render service as a limited demo until the remote sandbox is
configured.

## Security boundary

The Docker-in-Docker service uses privileged mode. Treat this Compose stack as
a controlled single-user development/resume demo, not a safe public
multi-tenant execution platform.

Before accepting arbitrary code from untrusted public users, ensure execution
uses disposable isolated containers with CPU/memory limits, hard timeouts,
restricted network access, non-root users, process cleanup, and per-job quotas.
Never mount the host Docker socket into the public API container.

Keep `GROQ_API_KEY` and authentication secrets in `.env` locally and in the
hosting provider's secret settings remotely. Never commit secrets.
