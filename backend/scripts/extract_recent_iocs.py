#!/usr/bin/env python3

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.article import Article
from app.models.ioc import IOC, ItemIOC
from app.models.item import Item
from app.services.ioc_extraction import extract_iocs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract IOC entities for recent ThreatLens items.")
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

    rows_seen = 0
    updated_items = 0
    linked_total = 0

    try:
        query = (
            select(Item.id, Item.title, Item.summary, Article.text)
            .outerjoin(Article, Article.item_id == Item.id)
            .where(Item.first_seen_at >= cutoff)
            .order_by(Item.first_seen_at.desc())
        )
        if args.limit:
            query = query.limit(args.limit)

        rows = session.execute(query).all()
        rows_seen = len(rows)

        for row in rows:
            extracted = extract_iocs(title=row.title, summary=row.summary, article_text=row.text)

            by_key: dict[tuple[str, str], dict[str, object]] = {}
            for match in extracted:
                key = (match.type, match.value_norm)
                entry = by_key.get(key)
                if entry is None:
                    by_key[key] = {
                        "raw": match.value_raw,
                        "sections": {match.source_section},
                        "occurrences": 1,
                        "confidence": match.confidence,
                    }
                    continue
                entry["sections"] = set(entry["sections"]).union({match.source_section})
                entry["occurrences"] = int(entry["occurrences"]) + 1
                entry["confidence"] = max(float(entry["confidence"]), match.confidence)

            linked_ioc_ids: set = set()
            now = datetime.now(timezone.utc)

            for (ioc_type, value_norm), info in by_key.items():
                ioc = session.scalar(select(IOC).where(IOC.type == ioc_type, IOC.value_norm == value_norm))
                if ioc is None:
                    ioc = IOC(
                        type=ioc_type,
                        value_raw=str(info["raw"]),
                        value_norm=value_norm,
                        first_seen_at=now,
                        last_seen_at=now,
                    )
                    session.add(ioc)
                    session.flush()
                else:
                    ioc.last_seen_at = now
                    session.add(ioc)
                    session.flush()

                linked_ioc_ids.add(ioc.id)
                link = session.scalar(select(ItemIOC).where(ItemIOC.item_id == row.id, ItemIOC.ioc_id == ioc.id))
                if link is None:
                    link = ItemIOC(item_id=row.id, ioc_id=ioc.id)

                link.source_section = ",".join(sorted(set(info["sections"])))
                link.occurrences = int(info["occurrences"])
                link.confidence = float(info["confidence"])
                session.add(link)

            if linked_ioc_ids:
                session.query(ItemIOC).filter(ItemIOC.item_id == row.id, ItemIOC.ioc_id.notin_(linked_ioc_ids)).delete(
                    synchronize_session=False
                )
            else:
                session.query(ItemIOC).filter(ItemIOC.item_id == row.id).delete(synchronize_session=False)

            updated_items += 1
            linked_total += len(by_key)

        session.commit()
        print(
            {
                "window_days": args.days,
                "rows_seen": rows_seen,
                "updated_items": updated_items,
                "linked_iocs": linked_total,
            }
        )
    finally:
        session.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
