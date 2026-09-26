"""Synthetic-only fixture controls; never imported by production application code."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Literal
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "web/browser/server"))
import fixture_server as fixture  # noqa: E402
from fastapi import Depends  # noqa: E402
from sqlalchemy import func, select  # noqa: E402
from app.models.item_classification import ItemClassification  # noqa: E402
from app.models.ioc import ItemIOC  # noqa: E402
from app.tasks.feed_tasks import classify_item, extract_item_iocs  # noqa: E402
from app.tasks.system_health_tasks import record_queue_execution_canary  # noqa: E402

fixture.ROOT = Path(os.environ["THREATLENS_QUALIFICATION_SOURCE_ROOT"])
# The production mount must remain last, after synthetic qualification controls.
production_mount = fixture.harness.router.routes.pop()


@fixture.harness.post("/__browser__/qualification-seed", dependencies=[Depends(fixture.require_control)])
def seed(stage: Literal["exports", "processing"] = "exports"):
    with fixture.SessionLocal.begin() as db:
        feed = db.scalar(select(fixture.Feed).limit(1))
        ids = []
        for index in range(40):
            identity = uuid.uuid4()
            item = fixture.Item(id=identity, feed_id=feed.id, source_guid=str(identity),
                url=f"https://source.example.com/{identity}", title=f"Qualification {stage}-group{index % 2} article {index}",
                summary="Synthetic defensive research with CVE-2025-12345 and example.org.",
                dedupe_key=str(identity), content_hash="a" * 64, status="content_fetched")
            db.add(item)
            db.flush()
            body = "Synthetic ransomware research for defensive qualification. CVE-2025-12345 affects ProductX. example.org is a reference. "
            db.add(fixture.Article(item_id=identity, final_url=item.url, http_status=200, text=body * 180))
            ids.append(str(identity))
    return {"item_ids": ids, "groups": {"0": ids[::2], "1": ids[1::2]}, "article_chars_each": len(body) * 180}


@fixture.harness.post("/__browser__/qualification-dispatch", dependencies=[Depends(fixture.require_control)])
def dispatch():
    with fixture.SessionLocal() as db:
        ids = list(db.scalars(select(fixture.Item.id).outerjoin(ItemClassification,
            ItemClassification.item_id == fixture.Item.id).where(ItemClassification.item_id.is_(None))))
    for identity in ids:
        classify_item.apply_async(args=[str(identity)], queue="processing")
        extract_item_iocs.apply_async(args=[str(identity)], queue="processing")
    for queue in ("processing", "exports-v1", "maintenance", "notifications"):
        record_queue_execution_canary.apply_async(args=[queue], queue=queue)
    return {"classification": len(ids), "ioc": len(ids)}


@fixture.harness.get("/__browser__/qualification-progress", dependencies=[Depends(fixture.require_control)])
def progress():
    from app.core.config import get_settings
    from app.services.queue_execution_canaries import read_queue_execution_canaries
    with fixture.SessionLocal() as db:
        total = db.scalar(select(func.count(fixture.Item.id)))
        classified = db.scalar(select(func.count(ItemClassification.item_id)))
        with_iocs = db.scalar(select(func.count(func.distinct(ItemIOC.item_id))))
    canaries = read_queue_execution_canaries(settings=get_settings(),
        queues=["processing", "exports-v1", "maintenance", "notifications"])
    return {"total": total, "classified": classified, "with_iocs": with_iocs,
            "queue_execution_fresh": {queue: value.reason == "fresh" for queue, value in canaries.items()}}


fixture.harness.router.routes.append(production_mount)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    fixture.initialize()
    fixture.uvicorn.run(fixture.harness, host="127.0.0.1", port=args.port, access_log=False)
