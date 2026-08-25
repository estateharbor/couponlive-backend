# Backend image — shared by the API and the Celery worker (worker overrides the
# command in docker-compose). python:3.12-slim; psycopg[binary]/lxml/rapidfuzz
# ship manylinux wheels, so no build toolchain is needed.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://localhost:8000/health || exit 1

# Default = API. start.sh runs `alembic upgrade head` then uvicorn.
CMD ["bash", "start.sh"]
