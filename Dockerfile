# syntax=docker/dockerfile:1

FROM nginxinc/nginx-unprivileged:1.27-alpine-slim

LABEL org.opencontainers.image.title="Pacer Speedometer" \
      org.opencontainers.image.description="A GPS speedometer that runs in phone browsers with zero server dependencies" \
      org.opencontainers.image.source="https://github.com/vishai12345/speedometer"

# Copy static web assets to Nginx html directory
# UID 101 is the default unprivileged 'nginx' user
COPY --chown=101:101 index.html engine.js /usr/share/nginx/html/

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -qO- http://localhost:8080/ || exit 1

