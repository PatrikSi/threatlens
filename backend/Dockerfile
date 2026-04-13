FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends build-essential && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app
RUN chmod +x /app/scripts/start-api.sh /app/scripts/bootstrap.sh
RUN addgroup --system app && adduser --system --ingroup app app && chown -R app:app /app

ENV PYTHONPATH=/app
USER app

CMD ["/app/scripts/start-api.sh"]
