# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS frontend-build
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.docker.lock ./
RUN pip install --no-cache-dir --only-binary=:all: -r requirements.docker.lock \
    && groupadd --gid 10001 course \
    && useradd --uid 10001 --gid course --no-create-home --shell /usr/sbin/nologin course
COPY backend/ ./backend/
COPY scripts/docker_entrypoint.py scripts/docker_init.py scripts/docker_healthcheck.py ./scripts/
COPY --from=frontend-build /build/dist/ ./frontend/dist/
# The entrypoint reads host-owned 0600 secrets before dropping to UID/GID 10001.
# Only the short-lived database initializer retains root inside its container.
USER root
EXPOSE 8000
ENTRYPOINT ["python", "/app/scripts/docker_entrypoint.py"]
CMD ["api"]
