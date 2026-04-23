FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7

ARG BUILD_DATE=unknown
ARG VCS_REF=unknown

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt requirements-lock.txt /app/
RUN pip install --no-cache-dir -r /app/requirements-lock.txt

COPY . /app
RUN chmod +x /app/scripts/start-api.sh \
    && mkdir -p /usr/share/doc/threatlens \
    && mkdir -p /usr/share/doc/threatlens/licenses \
    && cp /app/compliance/LICENSE /usr/share/doc/threatlens/LICENSE \
    && cp /app/compliance/README.md /usr/share/doc/threatlens/README.md \
    && cp /app/compliance/THIRD_PARTY_NOTICES.md /usr/share/doc/threatlens/THIRD_PARTY_NOTICES.md \
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

LABEL org.opencontainers.image.title="ThreatLens API" \
      org.opencontainers.image.description="ThreatLens FastAPI backend image" \
      org.opencontainers.image.url="https://github.com/PatrikSi/threatlens" \
      org.opencontainers.image.source="https://github.com/PatrikSi/threatlens" \
      org.opencontainers.image.documentation="https://github.com/PatrikSi/threatlens/blob/main/README.md" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.vendor="ThreatLens contributors" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}"

CMD ["/app/scripts/start-api.sh"]
