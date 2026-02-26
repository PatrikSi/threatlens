#!/usr/bin/env python3

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item
from app.models.item_classification import ItemClassification
from app.services.classification import classify_item_content


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Classify recent ThreatLens items using rule-based categories.")
    parser.add_argument("--days", type=int, default=30, help="How many days back to include (default: 30).")
    parser.add_argument("--limit", type=int, default=0, help="Optional max number of items to process (0 = all in window).")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.days <= 0:
        raise SystemExit("--days must be > 0")
    if args.limit < 0:
        raise SystemExit("--limit must be >= 0")

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    session = SessionLocal()
    processed = 0
    skipped_up_to_date = 0

    try:
        query = (
            select(Item.id, Item.title, Item.summary, Feed.name, Article.text)
            .join(Feed, Feed.id == Item.feed_id)
            .outerjoin(Article, Article.item_id == Item.id)
            .where(Item.first_seen_at >= cutoff)
            .order_by(Item.first_seen_at.desc())
        )
        if args.limit:
            query = query.limit(args.limit)

        rows = session.execute(query).all()

        for row in rows:
            result = classify_item_content(
                title=row.title,
                summary=row.summary,
                article_text=row.text,
                feed_name=row.name,
            )

            classification = session.get(ItemClassification, row.id)
            if classification is None:
                classification = ItemClassification(item_id=row.id)

            if (
                classification.source_hash == result.source_hash
                and classification.rules_version == result.rules_version
                and classification.primary_category == result.primary_category
            ):
                skipped_up_to_date += 1
                continue

            classification.primary_category = result.primary_category
            classification.secondary_categories = result.secondary_categories
            classification.confidence = result.confidence
            classification.scores_json = result.scores
            classification.matched_terms_json = result.matched_terms
            classification.source_hash = result.source_hash
            classification.rules_version = result.rules_version
            classification.classified_at = datetime.now(timezone.utc)

            session.add(classification)
            processed += 1

        session.commit()
        print(
            {
                "window_days": args.days,
                "rows_seen": len(rows),
                "updated": processed,
                "already_up_to_date": skipped_up_to_date,
            }
        )
    finally:
        session.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
