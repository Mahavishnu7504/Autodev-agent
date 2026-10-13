# AutoDev Agent

**An autonomous AI software engineering agent that plans tasks, generates code, runs tests, and attempts repairs.**

[![Live Demo](https://img.shields.io/badge/Live%20Demo-Open%20AutoDev%20Agent-4f8cff?style=for-the-badge)](https://autodev-agent-vtwe.onrender.com/)
[![Python](https://img.shields.io/badge/Python-3.x-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-Backend-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/Docker-Sandbox-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)

## 🚀 Live Demo

**[Open AutoDev Agent](https://autodev-agent-vtwe.onrender.com/)**

Open the link to view the hosted dashboard. The Render free service may take a little time to wake up after inactivity. The hosted demo is intended for supported demonstration tasks; successful execution depends on the deployed executor and sandbox configuration.

## ✨ Overview

AutoDev Agent is a resume-focused project exploring how an AI coding agent can turn a natural-language software request into a structured development workflow.

The agent is designed to support:
- **Task planning** — break a request into implementation steps.
- **Code generation** — generate project files for a requested task.
- **Testing and quality checks** — validate generated code and report results.
- **Autonomous repair attempts** — use errors and test feedback to attempt fixes.
- **Background tasks** — submit work and check its status asynchronously.
- **Task and project management** — view task progress and generated project artifacts.
- **Memory** — reuse saved information where supported by the configured memory module.

> **Demo note:** This is a portfolio/resume project, not a production service. Capabilities available in the hosted demo may differ from a fully configured local Docker setup.

## 🧭 How It Works

1. Submit a software task through the dashboard or API.
2. The backend creates a task and runs the configured agent workflow.
3. The agent plans the work and generates files.
4. The configured executor and sandbox attempt to run checks and tests.
5. The API exposes task status and the available results or generated artifacts.

## 🧰 Tech Stack

- **Python**
- **FastAPI** — HTTP API
- **Groq API** — language-model access (requires a server-side API key)
- **HTML, CSS, JavaScript** — dashboard
- **Docker / Docker Compose** — local sandbox and service orchestration
- **Git and GitHub** — source control and project hosting
- **Render** — hosted backend and dashboard

## 🖥️ Run Locally

### Prerequisites

- Python 3
- Git
- Docker Desktop with Docker Compose, if you want to use the Docker sandbox

### 1. Clone the repository

```bash
git clone https://github.com/Mahavishnu7504/Autodev-agent.git
cd Autodev-agent
```

### 2. Create a virtual environment

**Windows PowerShell**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**macOS / Linux**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Create a local `.env` file based on `.env.example` if the template is present in your checkout. Add the required configuration for your setup, such as your Groq API key and API authentication settings.

**Never commit `.env`, API keys, passwords, private SSH keys, or other secrets to GitHub.** Configure production secrets in your hosting provider's environment settings.

### 5. Start the API

Use the entry point configured for your local checkout. For example, if your FastAPI application is defined in `api.py` as `app`:

```bash
uvicorn api:app --reload
```

Then open:
- Dashboard: `http://127.0.0.1:8000/dashboard`
- API docs: `http://127.0.0.1:8000/docs`
- Health: `http://127.0.0.1:8000/health`

If your local setup uses Docker Compose, follow the repository's Compose configuration and ensure the sandbox engine is running before testing code execution.

## 🔌 API Workflow

The API includes task-oriented routes such as:
- `POST /run-task-async` — submit a background task.
- `GET /tasks/{task_id}` — check task status and results.
- `GET /health` — check service health.

Other routes may require authentication depending on your environment configuration. See `/docs` on your running instance for the routes exposed by that deployment.

## 🔐 Security Notes

- Keep model API keys and passwords server-side.
- Do not expose Docker daemon ports or unrestricted code-execution endpoints publicly.
- Restrict destructive actions and arbitrary task execution in any public demo.
- Use rate limits and task timeouts when accepting public requests.
- Treat generated code as untrusted and execute it only in an appropriately isolated sandbox.

## ⚠️ Current Limitations

- Render's free web service can spin down after inactivity, so the first request may be slow.
- Free-host filesystem storage should not be treated as durable storage for generated projects, task history, or memory.
- The hosted demo's task execution depends on the sandbox and executor being available in that deployment.
- This project is intended for demonstration and learning, not production workloads or sensitive data.

## 📌 Project Status

AutoDev Agent is an actively developed portfolio project. Test the live demo with a supported example task and verify its result before relying on it for other requests.

## 👤 Author

**Maha Vishnu A**

- GitHub: [Mahavishnu7504](https://github.com/Mahavishnu7504)

---

If you find this project interesting, feel free to explore the repository and try the live demo.
