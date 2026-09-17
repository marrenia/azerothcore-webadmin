# syntax=docker/dockerfile:1
#
# Production image for the acore-webadmin app (not the TLS proxy - that's the
# stock nginx image, configured by docker-compose.yml / docker/proxy-entrypoint.sh).
#
# Multi-stage so the runtime image carries no compiler toolchain. Runs as a
# fixed non-root user, reads all configuration from the environment (or
# Docker/Compose secrets - see docker/entrypoint.sh), and never binds anything
# but 0.0.0.0:8090 *inside* its own Compose network - docker-compose.yml does
# not publish that port to the host, only the proxy's ports are published.

FROM python:3.12.7-slim-bookworm AS build

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


FROM python:3.12.7-slim-bookworm AS runtime

# tini: reaps zombies and forwards signals correctly to gunicorn as PID 1.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 10001 acoreweb \
    && useradd --system --uid 10001 --gid acoreweb --no-create-home --shell /usr/sbin/nologin acoreweb

COPY --from=build /install /usr/local
COPY app/ /app/
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh \
    && chown -R acoreweb:acoreweb /app

WORKDIR /app
USER acoreweb

# Config is generated at startup (see entrypoint.sh) into a tmpfs, never
# baked into the image and never written under /app.
ENV ACORE_WEBADMIN_CONFIG_DIR=/run/acore-webadmin/config \
    ACORE_STATE_DIR=/var/lib/acore-webadmin \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8090/livez', timeout=3).status in (200,503) else 1)"

ENTRYPOINT ["tini", "--", "/entrypoint.sh"]
CMD ["gunicorn", "--bind", "0.0.0.0:8090", "--workers", "2", "--threads", "4", \
     "--timeout", "30", "--no-control-socket", \
     "--access-logfile", "-", "--error-logfile", "-", "app:app"]
