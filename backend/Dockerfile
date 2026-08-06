FROM python:3.12.13-slim-bookworm

ARG BUILD_DATE=unknown
ARG APP_VERSION=1.5.0
ARG VCS_REF=unknown

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_VERSION=${APP_VERSION}

WORKDIR /app

COPY requirements.txt requirements-lock.txt /app/
RUN pip install --no-cache-dir -r /app/requirements-lock.txt

COPY . /app
RUN chmod +x /app/scripts/start-api.sh \
    && mkdir -p /usr/share/doc/threatlens \
    && mkdir -p /usr/share/doc/threatlens/licenses \
    && cp /app/compliance/LICENSE /usr/share/doc/threatlens/LICENSE \
    && cp /app/compliance/README.md /usr/share/doc/threatlens/README.md \
    && cp /app/compliance/licenses/*.txt /usr/share/doc/threatlens/licenses/ \
    && cp /app/requirements.txt /usr/share/doc/threatlens/backend-requirements.txt \
    && cp /app/requirements-lock.txt /usr/share/doc/threatlens/backend-requirements-lock.txt \
    && python /app/scripts/generate_dependency_inventory.py \
      --backend-output /usr/share/doc/threatlens/backend-runtime-dependencies.txt \
      --backend-metadata-output /usr/share/doc/threatlens/backend-runtime-package-metadata.json \
      --backend-legal-output-dir /usr/share/doc/threatlens/backend-runtime-package-legal \
      --backend-os-output /usr/share/doc/threatlens/backend-os-packages.txt \
      --backend-os-legal-output-dir /usr/share/doc/threatlens/backend-os-package-legal \
      --skip-frontend
RUN addgroup --system app && adduser --system --ingroup app app && chown -R app:app /app

ENV PYTHONPATH=/app
USER app

LABEL org.opencontainers.image.title="ThreatLens Backend" \
      org.opencontainers.image.description="ThreatLens FastAPI and Celery backend image" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.vendor="ThreatLens contributors" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}"

CMD ["/app/scripts/start-api.sh"]
