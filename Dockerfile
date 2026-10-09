
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    AUTODEV_STATE_DIR=/data/tasks \
    DOCKER_HOST=tcp://sandbox-engine:2375

WORKDIR /app

# Install Docker client separately from the daemon.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        docker-cli \
        ca-certificates \
    && command -v docker \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /data/tasks /app/generated \
    && chown -R appuser:appuser /app /data

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["python", "-m", "uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
