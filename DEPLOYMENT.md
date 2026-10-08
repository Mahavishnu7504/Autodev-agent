# AutoDev Agent — Deployment

## Local Docker test

1. Copy `.env.example` to `.env`.
2. Put your Groq API key in `.env`.
3. Build and start:

```powershell
docker compose up --build
```

4. Open:

- http://127.0.0.1:8000/dashboard
- http://127.0.0.1:8000/docs
- http://127.0.0.1:8000/health
- http://127.0.0.1:8000/system/status

## Stop

```powershell
docker compose down
```

The named Docker volumes preserve task state and generated projects.

## Important security boundary

The current AutoDev executor runs generated Python locally inside the application runtime.

Therefore this Docker setup is a **deployment packaging milestone, not a claim of safe public multi-tenant execution**.

Before exposing arbitrary generated-code execution to untrusted public users, add an execution sandbox with:

- isolated container/process per generated project
- CPU and memory limits
- hard execution timeout
- isolated writable filesystem
- restricted/no network access by default
- non-root execution
- process cleanup
- per-job resource quotas

Do not mount the Docker host socket into the AutoDev API container.

## Production checklist

- Keep `GROQ_API_KEY` in the hosting platform's secret manager.
- Put the API behind HTTPS/reverse proxy.
- Add authentication before public access.
- Add request rate limiting.
- Add sandboxed code execution before accepting untrusted users.
- Keep generated-project storage persistent.
- Keep task state persistent.
- Monitor `/health` and `/system/status`.
