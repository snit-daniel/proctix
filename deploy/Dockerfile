FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng ffmpeg && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid 10001 proctix && useradd --uid 10001 --gid 10001 --no-create-home proctix
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY pyproject.toml ./
COPY proctix ./proctix
COPY scripts/fetch_models.py scripts/fetch_auraface.py ./scripts/
RUN python scripts/fetch_models.py && mkdir -p /app/var/evidence && chown -R 10001:10001 /app/var
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import socket; s=socket.create_connection(('localhost',8000),3); s.close()"
CMD ["uvicorn", "proctix.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "172.29.0.2", "--no-access-log"]
