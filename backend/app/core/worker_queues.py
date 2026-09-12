"""Queue names shared by dispatch, worker deployment, and operational probes."""

QUEUE_DEFAULT = "default"
QUEUE_INGEST = "ingest"
QUEUE_PROCESSING = "processing"
QUEUE_EXPORTS = "exports-v1"
QUEUE_NOTIFICATIONS = "notifications"
QUEUE_AI = "ai"
QUEUE_AI_REPORTS = "ai-reports-v2"
QUEUE_AI_REPORTS_EDITORIAL = "ai-reports-v3"
QUEUE_MAINTENANCE = "maintenance"
QUEUE_LIFECYCLE = "lifecycle-v1"

WORKER_QUEUES = (
    QUEUE_DEFAULT,
    QUEUE_INGEST,
    QUEUE_PROCESSING,
    QUEUE_EXPORTS,
    QUEUE_NOTIFICATIONS,
    QUEUE_AI,
    QUEUE_AI_REPORTS,
    QUEUE_AI_REPORTS_EDITORIAL,
    QUEUE_MAINTENANCE,
    QUEUE_LIFECYCLE,
)
