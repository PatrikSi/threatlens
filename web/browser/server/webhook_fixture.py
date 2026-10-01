"""Synthetic evidence for webhook browser tests; never mounted in production."""

from datetime import datetime, timezone
import hashlib
import uuid

from fastapi import Depends, HTTPException

from app.db.session import SessionLocal
from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item
from app.models.item_ai_enrichment import ItemAIEnrichment
from app.services.ai_config import load_active_ai_settings
from app.services.ai_enrichment_provenance import enrichment_result_provenance
from app.services.webhook_ai_events import emit_article_ai_ready


def install_webhook_controls(harness, require_control):
    @harness.post("/__browser__/webhook-article", dependencies=[Depends(require_control)])
    def create_webhook_article():
        with SessionLocal.begin() as db:
            identity = uuid.uuid4()
            feed = Feed(name="Webhook SOC fixture", url=f"https://example.com/{identity}")
            db.add(feed)
            db.flush()
            item = Item(
                feed_id=feed.id,
                title=f"Endpoint hunt evidence {identity}",
                url=f"https://example.com/article/{identity}",
                canonical_url=f"https://example.com/article/{identity}",
                summary="Synthetic SOC article summary.",
                status="content_fetched",
                dedupe_key=str(identity),
                content_hash="a" * 64,
            )
            db.add(item)
            db.flush()
            text = 'Full evidence: examine process ancestry, "parent.exe" → child.exe.\nNo external request is needed.'
            article = Article(
                item_id=item.id, final_url=item.url, http_status=200, text=text
            )
            db.add(article)
            db.flush()
            generated = datetime.now(timezone.utc)
            source_hash = hashlib.sha256(text.encode()).hexdigest()
            db.add(ItemAIEnrichment(
                item_id=item.id, status="ready", source_hash=source_hash,
                relevance_score=0.92, relevance_label="high", generated_at=generated,
                summary_text="Synthetic relevant endpoint evidence.",
                result_provenance_json=enrichment_result_provenance(
                    active=load_active_ai_settings(db), item=item, article=article,
                    classification=None, feed_name=feed.name, tag_names=[],
                    source_hash=source_hash, generated_at=generated,
                ),
            ))
            db.flush()
            event_id = emit_article_ai_ready(db, item_id=item.id)
            if event_id is None:
                raise AssertionError("Synthetic successful result did not produce its event")
            return {
                "item_id": str(item.id), "event_id": str(event_id),
                "title": item.title, "text": text,
            }

    @harness.post(
        "/__browser__/webhook-articles/{item_id}/refresh",
        dependencies=[Depends(require_control)],
    )
    def refresh_webhook_article(item_id: uuid.UUID):
        with SessionLocal.begin() as db:
            item = db.get(Item, item_id)
            if item is None:
                raise HTTPException(404, "Fixture article not found")
            item.classification_required_version += 1
            from sqlalchemy import select

            article = db.scalar(select(Article).where(Article.item_id == item_id))
            article.text = "Replacement evidence must not appear in the prior event."
            article.retrieved_at = datetime.now(timezone.utc)
            return {"refreshed": True}
