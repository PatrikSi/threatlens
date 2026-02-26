from app.tasks.celery_app import celery_app


@celery_app.task(name="app.tasks.feed_tasks.fetch_feed")
def fetch_feed(feed_id: str):
    # Implemented in milestone 2.
    return {"status": "todo", "feed_id": feed_id}


@celery_app.task(name="app.tasks.feed_tasks.fetch_article")
def fetch_article(item_id: str):
    # Implemented in milestone 2.
    return {"status": "todo", "item_id": item_id}
