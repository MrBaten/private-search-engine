FROM python:3.11-slim

# No .pyc files (read-only root fs friendly) and unbuffered stdout/stderr.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Tests, caches and dev files are excluded via .dockerignore.
COPY . .

# Run as an unprivileged user.
RUN useradd --system --no-create-home --uid 10001 appuser
USER appuser

EXPOSE 5000

# python:3.11-slim has no curl, so probe /healthz with urllib.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/healthz', timeout=3).status == 200 else 1)"]

# One worker on purpose: the result cache is in-process memory, so a single
# worker process (with 8 threads for concurrency) keeps one shared cache.
# Privacy: gunicorn's access log is OFF by default. Never add --access-logfile,
# it would record request lines and client addresses.
CMD ["gunicorn", "--workers", "1", "--threads", "8", "--bind", "0.0.0.0:5000", "--timeout", "30", "app:app"]
