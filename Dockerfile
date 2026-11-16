# syntax=docker/dockerfile:1
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN WEB_OUT_DIR=/out npm run build

FROM python:3.13-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libffi-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
RUN python -m venv /opt/venv
COPY pyproject.toml ./
COPY src ./src
RUN /opt/venv/bin/pip install .

FROM python:3.13-slim
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONSAFEPATH=1 \
    HOST=0.0.0.0 \
    PORT=8080 \
    DATA_DIR=/data \
    WEB_DIST_DIR=/app/static
RUN useradd --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin aliasfox \
    && mkdir /data \
    && chown 10001:10001 /data
COPY --from=builder /opt/venv /opt/venv
COPY --from=web /out /app/static
USER 10001:10001
WORKDIR /data
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:${PORT:-8080}/healthz', timeout=4).status == 200 else 1)"
CMD ["python", "-m", "aliasfox"]
