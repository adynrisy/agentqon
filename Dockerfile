# AgentQon — production image (prepared 2026-10-10 for the go-live step)
# Single image used for both the API and the worker (different commands).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    AGENTQON_AUTH_MODE=accounts \
    AGENTQON_BIND_HOST=0.0.0.0

WORKDIR /srv/omnimind

# Shield H1: dependencies are pinned exactly and hash-locked;
# pip verifies every artifact's SHA-256 during this build.
COPY requirements.txt ./
RUN pip install --require-hashes -r requirements.txt

COPY app ./app
COPY apps ./apps
COPY benchmarks ./benchmarks

# Runtime data (SQLite fallback / vault key file) lives on a mounted volume.
# Shield C2: the image defaults to ACCOUNTS auth (never dev-header
# auth) and the server process runs as a non-root user. The app also
# refuses, at startup, any dev-auth + non-loopback combination — the
# ENV above and the code agree, so a misconfigured override fails
# loudly instead of serving an unauthenticated owner API.
RUN mkdir -p /srv/omnimind/data \
    && useradd --uid 10001 --create-home omnimind \
    && chown -R omnimind:omnimind /srv/omnimind
VOLUME ["/srv/omnimind/data"]
USER omnimind

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health')"

# API (default). Worker overrides this command in compose / platform config.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
