FROM python:3.12.16-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core fonts-dejavu-extra libpcre2-8-0 perl-base \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' libpcre2-8-0)" ge '10.42-1+deb12u2' \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' perl-base)" ge '5.36.0-7+deb12u4' \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt requirements-lock.txt /app/
RUN python -m pip install --no-cache-dir --upgrade pip==26.3 \
    && python -m pip install --no-cache-dir -r /app/requirements-lock.txt \
    && python -m pip uninstall --yes pip \
    && rm -rf /usr/local/lib/python3.12/ensurepip

COPY . /app
# Compile-only checks do not catch transport dependencies supplied by dev tools.
# Exercise the actual API, worker and Beat imports in the runtime-only image.
RUN python -c "import importlib.util, shutil; assert all(importlib.util.find_spec(name) is None for name in ('pip', 'ensurepip')); assert all(shutil.which(name) is None for name in ('pip', 'pip3', 'pip3.12'))"
RUN python -c "import app.main; import app.tasks.celery_app; import app.tasks.beat_scheduler; import app.tasks.bounded_beat"
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

ARG BUILD_DATE=unknown
ARG APP_VERSION=2.1.0
ARG VCS_REF=unknown
ENV APP_VERSION=${APP_VERSION}

LABEL org.opencontainers.image.title="ThreatLens Backend" \
      org.opencontainers.image.description="ThreatLens FastAPI and Celery backend image" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.vendor="ThreatLens contributors" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}"

CMD ["/app/scripts/start-api.sh"]
