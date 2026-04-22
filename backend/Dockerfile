FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends build-essential && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-lock.txt /app/
RUN pip install --no-cache-dir -r /app/requirements-lock.txt

COPY . /app
RUN chmod +x /app/scripts/start-api.sh \
    && mkdir -p /usr/share/doc/threatlens \
    && mkdir -p /usr/share/doc/threatlens/licenses \
    && cp /app/compliance/LICENSE /usr/share/doc/threatlens/LICENSE \
    && cp /app/compliance/THIRD_PARTY_NOTICES.md /usr/share/doc/threatlens/THIRD_PARTY_NOTICES.md \
    && cp /app/compliance/licenses/*.txt /usr/share/doc/threatlens/licenses/ \
    && cp /app/requirements.txt /usr/share/doc/threatlens/backend-requirements.txt \
    && cp /app/requirements-lock.txt /usr/share/doc/threatlens/backend-requirements-lock.txt \
    && python /app/scripts/generate_dependency_inventory.py \
      --backend-output /usr/share/doc/threatlens/backend-runtime-dependencies.txt \
      --backend-metadata-output /usr/share/doc/threatlens/backend-runtime-package-metadata.json \
      --skip-frontend
RUN addgroup --system app && adduser --system --ingroup app app && chown -R app:app /app

ENV PYTHONPATH=/app
USER app

CMD ["/app/scripts/start-api.sh"]
