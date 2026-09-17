# syntax=docker/dockerfile:1
#
# TLS-terminating reverse proxy for the Compose deployment. Stock nginx image
# plus openssl (for the self-signed fallback cert) and our template/entrypoint.
# Uses the official image's own template-rendering entrypoint
# (/docker-entrypoint.d/20-envsubst-on-templates.sh) for the nginx config, so
# there is no custom templating logic to maintain here - only cert bootstrap.

FROM nginx:1.27.3-alpine

# Not version-pinned: the package version is tied to this Alpine base image's
# own repo snapshot, so the base image tag above is the reproducibility
# anchor. Pinning a specific openssl-pkg version here would just as easily
# break the build the day the base image updates its repo index.
RUN apk add --no-cache openssl

COPY docker/proxy/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY docker/proxy/entrypoint.sh /docker-entrypoint.d/00-tls-bootstrap.sh
RUN chmod +x /docker-entrypoint.d/00-tls-bootstrap.sh

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O- --no-check-certificate https://127.0.0.1:8443/healthz || exit 1
