"""enforce singleton settings and normalize existing feed URLs

Revision ID: 0022_settings_feed_urls
Revises: 0021_user_auth_token_version
Create Date: 2026-04-11
"""

from __future__ import annotations

from collections import defaultdict
from urllib.parse import urlsplit, urlunsplit

from alembic import op
import sqlalchemy as sa


revision = "0022_settings_feed_urls"
down_revision = "0021_user_auth_token_version"
branch_labels = None
depends_on = None


def _build_netloc(*, scheme: str, hostname: str, port: int | None, username: str | None = None, password: str | None = None) -> str:
    credentials = ""
    if username:
        credentials = username
        if password is not None:
            credentials = f"{credentials}:{password}"
        credentials = f"{credentials}@"

    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        return f"{credentials}{hostname}"
    if port:
        return f"{credentials}{hostname}:{port}"
    return f"{credentials}{hostname}"

def upgrade() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    tables = {
        name: sa.Table(name, metadata, autoload_with=bind)
        for name in (
            "feeds",
            "items",
            "articles",
            "item_classifications",
            "item_ai_enrichments",
            "item_state",
            "item_tags",
            "item_iocs",
            "tag_feedback_events",
            "ai_task_runs",
            "ai_usage_events",
            "ai_daily_brief_source_items",
            "notification_webhook_deliveries",
            "notification_webhooks",
            "tagging_rules",
            "saved_views",
            "ai_settings",
            "tagging_settings",
        )
    }

    _normalize_existing_feed_urls(bind, tables)
    _collapse_singleton_table(bind, tables["ai_settings"])
    _collapse_singleton_table(bind, tables["tagging_settings"])

    op.add_column("ai_settings", sa.Column("singleton_key", sa.Integer(), nullable=True, server_default="1"))
    op.execute("UPDATE ai_settings SET singleton_key = 1 WHERE singleton_key IS NULL")
    op.alter_column("ai_settings", "singleton_key", nullable=False, server_default="1")
    op.create_unique_constraint("uq_ai_settings_singleton_key", "ai_settings", ["singleton_key"])

    op.add_column("tagging_settings", sa.Column("singleton_key", sa.Integer(), nullable=True, server_default="1"))
    op.execute("UPDATE tagging_settings SET singleton_key = 1 WHERE singleton_key IS NULL")
    op.alter_column("tagging_settings", "singleton_key", nullable=False, server_default="1")
    op.create_unique_constraint("uq_tagging_settings_singleton_key", "tagging_settings", ["singleton_key"])


def downgrade() -> None:
    op.drop_constraint("uq_tagging_settings_singleton_key", "tagging_settings", type_="unique")
    op.drop_column("tagging_settings", "singleton_key")
    op.drop_constraint("uq_ai_settings_singleton_key", "ai_settings", type_="unique")
    op.drop_column("ai_settings", "singleton_key")


def _collapse_singleton_table(bind, table: sa.Table) -> None:
    order_columns = []
    if "updated_at" in table.c:
        order_columns.append(table.c.updated_at.desc())
    if "created_at" in table.c:
        order_columns.append(table.c.created_at.desc())
    order_columns.append(table.c.id.desc())

    rows = bind.execute(sa.select(table.c.id).order_by(*order_columns)).scalars().all()
    for row_id in rows[1:]:
        bind.execute(sa.delete(table).where(table.c.id == row_id))


def _normalize_existing_feed_urls(bind, tables: dict[str, sa.Table]) -> None:
    feeds = tables["feeds"]
    rows = bind.execute(sa.select(feeds).order_by(feeds.c.created_at.asc(), feeds.c.id.asc())).mappings().all()

    grouped: dict[str, list[sa.RowMapping]] = defaultdict(list)
    for row in rows:
        normalized = _normalize_feed_url(row["url"])
        if not normalized:
            continue
        grouped[normalized].append(row)

    for normalized_url, group in grouped.items():
        if len(group) == 1:
            row = group[0]
            if row["url"] != normalized_url:
                bind.execute(
                    sa.update(feeds)
                    .where(feeds.c.id == row["id"])
                    .values(url=normalized_url)
                )
            continue

        canonical = _select_canonical_feed_row(group)
        canonical_id = canonical["id"]
        duplicate_ids = [row["id"] for row in group if row["id"] != canonical_id]

        for duplicate_id in duplicate_ids:
            _merge_feed(bind, tables, canonical_id=canonical_id, duplicate_id=duplicate_id)

        bind.execute(
            sa.update(feeds)
            .where(feeds.c.id == canonical_id)
            .values(url=normalized_url)
        )


def _merge_feed(bind, tables: dict[str, sa.Table], *, canonical_id, duplicate_id) -> None:
    feeds = tables["feeds"]
    canonical_feed = bind.execute(sa.select(feeds).where(feeds.c.id == canonical_id)).mappings().one()
    duplicate_feed = bind.execute(sa.select(feeds).where(feeds.c.id == duplicate_id)).mappings().one()
    merged_feed_values = _merge_feed_values(canonical_feed, duplicate_feed)
    if merged_feed_values:
        bind.execute(sa.update(feeds).where(feeds.c.id == canonical_id).values(**merged_feed_values))

    items = tables["items"]
    canonical_items = bind.execute(
        sa.select(items)
        .where(items.c.feed_id == canonical_id, items.c.source_guid.is_not(None))
        .order_by(items.c.first_seen_at.asc(), items.c.id.asc())
    ).mappings().all()
    canonical_by_source_guid = {row["source_guid"]: row["id"] for row in canonical_items}

    duplicate_items = bind.execute(
        sa.select(items)
        .where(items.c.feed_id == duplicate_id)
        .order_by(items.c.first_seen_at.asc(), items.c.id.asc())
    ).mappings().all()

    for item_row in duplicate_items:
        source_guid = item_row["source_guid"]
        if source_guid and source_guid in canonical_by_source_guid:
            _merge_item(bind, tables, winner_item_id=canonical_by_source_guid[source_guid], loser_item_id=item_row["id"])
            continue

        bind.execute(
            sa.update(items)
            .where(items.c.id == item_row["id"])
            .values(feed_id=canonical_id)
        )
        if source_guid:
            canonical_by_source_guid[source_guid] = item_row["id"]

    _replace_feed_references(bind, tables, from_feed_id=duplicate_id, to_feed_id=canonical_id)
    bind.execute(sa.delete(tables["feeds"]).where(tables["feeds"].c.id == duplicate_id))


def _merge_item(bind, tables: dict[str, sa.Table], *, winner_item_id, loser_item_id) -> None:
    items = tables["items"]
    winner = bind.execute(sa.select(items).where(items.c.id == winner_item_id)).mappings().one()
    loser = bind.execute(sa.select(items).where(items.c.id == loser_item_id)).mappings().one()

    merged_item_values: dict[str, object] = {}
    if not winner["canonical_url"] and loser["canonical_url"]:
        merged_item_values["canonical_url"] = loser["canonical_url"]
    if not winner["summary"] and loser["summary"]:
        merged_item_values["summary"] = loser["summary"]
    if winner["published_at"] is None and loser["published_at"] is not None:
        merged_item_values["published_at"] = loser["published_at"]
    if winner["status"] != "content_fetched" and loser["status"] == "content_fetched":
        merged_item_values["status"] = "content_fetched"
        merged_item_values["last_error"] = None
    elif not winner["last_error"] and loser["last_error"]:
        merged_item_values["last_error"] = loser["last_error"]

    winner_first_seen = winner["first_seen_at"]
    loser_first_seen = loser["first_seen_at"]
    if winner_first_seen is None or (loser_first_seen is not None and loser_first_seen < winner_first_seen):
        merged_item_values["first_seen_at"] = loser_first_seen

    if merged_item_values:
        bind.execute(sa.update(items).where(items.c.id == winner_item_id).values(**merged_item_values))

    _merge_article(bind, tables["articles"], winner_item_id=winner_item_id, loser_item_id=loser_item_id)
    _merge_singleton_child(bind, tables["item_classifications"], winner_item_id=winner_item_id, loser_item_id=loser_item_id)
    _merge_item_ai_enrichment(bind, tables["item_ai_enrichments"], winner_item_id=winner_item_id, loser_item_id=loser_item_id)
    _merge_item_state(bind, tables["item_state"], winner_item_id=winner_item_id, loser_item_id=loser_item_id)
    _merge_item_tags(bind, tables["item_tags"], winner_item_id=winner_item_id, loser_item_id=loser_item_id)
    _merge_item_iocs(bind, tables["item_iocs"], winner_item_id=winner_item_id, loser_item_id=loser_item_id)

    for table_name in ("tag_feedback_events", "ai_task_runs", "ai_usage_events", "ai_daily_brief_source_items", "notification_webhook_deliveries"):
        table = tables[table_name]
        bind.execute(
            sa.update(table)
            .where(table.c.item_id == loser_item_id)
            .values(item_id=winner_item_id)
        )

    bind.execute(sa.delete(items).where(items.c.id == loser_item_id))


def _merge_article(bind, articles: sa.Table, *, winner_item_id, loser_item_id) -> None:
    winner = bind.execute(sa.select(articles).where(articles.c.item_id == winner_item_id)).mappings().first()
    loser = bind.execute(sa.select(articles).where(articles.c.item_id == loser_item_id)).mappings().first()
    if loser is None:
        return
    if winner is None:
        bind.execute(sa.update(articles).where(articles.c.item_id == loser_item_id).values(item_id=winner_item_id))
        return

    merged_values: dict[str, object] = {}
    if not winner["text"] and loser["text"]:
        for column_name in (
            "final_url",
            "retrieved_at",
            "http_status",
            "content_type",
            "title_extracted",
            "text",
            "extraction_method",
            "language",
            "word_count",
            "fetch_ms",
            "error",
        ):
            merged_values[column_name] = loser[column_name]
    elif not winner["error"] and loser["error"]:
        merged_values["error"] = loser["error"]

    if merged_values:
        bind.execute(sa.update(articles).where(articles.c.item_id == winner_item_id).values(**merged_values))
    bind.execute(sa.delete(articles).where(articles.c.item_id == loser_item_id))


def _merge_singleton_child(bind, table: sa.Table, *, winner_item_id, loser_item_id) -> None:
    winner = bind.execute(sa.select(table).where(table.c.item_id == winner_item_id)).mappings().first()
    loser = bind.execute(sa.select(table).where(table.c.item_id == loser_item_id)).mappings().first()
    if loser is None:
        return
    if winner is None:
        bind.execute(sa.update(table).where(table.c.item_id == loser_item_id).values(item_id=winner_item_id))
        return
    bind.execute(sa.delete(table).where(table.c.item_id == loser_item_id))


def _merge_item_ai_enrichment(bind, table: sa.Table, *, winner_item_id, loser_item_id) -> None:
    winner = bind.execute(sa.select(table).where(table.c.item_id == winner_item_id)).mappings().first()
    loser = bind.execute(sa.select(table).where(table.c.item_id == loser_item_id)).mappings().first()
    if loser is None:
        return
    if winner is None:
        bind.execute(sa.update(table).where(table.c.item_id == loser_item_id).values(item_id=winner_item_id))
        return

    loser_is_better = (winner["status"] != "ready" and loser["status"] == "ready") or (not winner["summary_text"] and loser["summary_text"])
    if loser_is_better:
        bind.execute(
            sa.update(table)
            .where(table.c.item_id == winner_item_id)
            .values(
                status=loser["status"],
                source_hash=loser["source_hash"],
                summary_text=loser["summary_text"],
                relevance_score=loser["relevance_score"],
                relevance_label=loser["relevance_label"],
                relevance_reasons_json=loser["relevance_reasons_json"],
                provider=loser["provider"],
                model=loser["model"],
                prompt_tokens=loser["prompt_tokens"],
                completion_tokens=loser["completion_tokens"],
                total_tokens=loser["total_tokens"],
                latency_ms=loser["latency_ms"],
                error=loser["error"],
                generated_at=loser["generated_at"],
            )
        )
    bind.execute(sa.delete(table).where(table.c.item_id == loser_item_id))


def _merge_item_state(bind, table: sa.Table, *, winner_item_id, loser_item_id) -> None:
    winner_rows = {
        row["user_id"]: row
        for row in bind.execute(sa.select(table).where(table.c.item_id == winner_item_id)).mappings().all()
    }
    loser_rows = bind.execute(sa.select(table).where(table.c.item_id == loser_item_id)).mappings().all()
    for row in loser_rows:
        winner = winner_rows.get(row["user_id"])
        if winner is None:
            bind.execute(
                sa.update(table)
                .where(sa.and_(table.c.user_id == row["user_id"], table.c.item_id == loser_item_id))
                .values(item_id=winner_item_id)
            )
            continue

        bind.execute(
            sa.update(table)
            .where(sa.and_(table.c.user_id == row["user_id"], table.c.item_id == winner_item_id))
            .values(
                is_read=bool(winner["is_read"] or row["is_read"]),
                is_starred=bool(winner["is_starred"] or row["is_starred"]),
                note=winner["note"] or row["note"],
                updated_at=max(filter(None, [winner["updated_at"], row["updated_at"]]), default=winner["updated_at"]),
            )
        )
        bind.execute(
            sa.delete(table).where(sa.and_(table.c.user_id == row["user_id"], table.c.item_id == loser_item_id))
        )


def _merge_item_tags(bind, table: sa.Table, *, winner_item_id, loser_item_id) -> None:
    winner_rows = {
        row["tag_id"]: row
        for row in bind.execute(sa.select(table).where(table.c.item_id == winner_item_id)).mappings().all()
    }
    loser_rows = bind.execute(sa.select(table).where(table.c.item_id == loser_item_id)).mappings().all()
    for row in loser_rows:
        winner = winner_rows.get(row["tag_id"])
        if winner is None:
            bind.execute(
                sa.update(table)
                .where(sa.and_(table.c.item_id == loser_item_id, table.c.tag_id == row["tag_id"]))
                .values(item_id=winner_item_id)
            )
            continue

        if row["confidence"] > winner["confidence"]:
            bind.execute(
                sa.update(table)
                .where(sa.and_(table.c.item_id == winner_item_id, table.c.tag_id == row["tag_id"]))
                .values(
                    confidence=row["confidence"],
                    source=row["source"],
                    rules_version=row["rules_version"],
                    updated_at=max(filter(None, [winner["updated_at"], row["updated_at"]]), default=winner["updated_at"]),
                )
            )
        bind.execute(sa.delete(table).where(sa.and_(table.c.item_id == loser_item_id, table.c.tag_id == row["tag_id"])))


def _merge_item_iocs(bind, table: sa.Table, *, winner_item_id, loser_item_id) -> None:
    winner_rows = {
        row["ioc_id"]: row
        for row in bind.execute(sa.select(table).where(table.c.item_id == winner_item_id)).mappings().all()
    }
    loser_rows = bind.execute(sa.select(table).where(table.c.item_id == loser_item_id)).mappings().all()
    for row in loser_rows:
        winner = winner_rows.get(row["ioc_id"])
        if winner is None:
            bind.execute(
                sa.update(table)
                .where(sa.and_(table.c.item_id == loser_item_id, table.c.ioc_id == row["ioc_id"]))
                .values(item_id=winner_item_id)
            )
            continue

        bind.execute(
            sa.update(table)
            .where(sa.and_(table.c.item_id == winner_item_id, table.c.ioc_id == row["ioc_id"]))
            .values(
                occurrences=int(winner["occurrences"] or 0) + int(row["occurrences"] or 0),
                confidence=max(float(winner["confidence"] or 0), float(row["confidence"] or 0)),
            )
        )
        bind.execute(sa.delete(table).where(sa.and_(table.c.item_id == loser_item_id, table.c.ioc_id == row["ioc_id"])))


def _replace_feed_references(bind, tables: dict[str, sa.Table], *, from_feed_id, to_feed_id) -> None:
    deliveries = tables["notification_webhook_deliveries"]
    bind.execute(
        sa.update(deliveries)
        .where(deliveries.c.feed_id == from_feed_id)
        .values(feed_id=to_feed_id)
    )

    for table_name in ("notification_webhooks", "tagging_rules"):
        table = tables[table_name]
        rows = bind.execute(sa.select(table.c.id, table.c.feed_ids_json)).mappings().all()
        for row in rows:
            current = list(row["feed_ids_json"] or [])
            updated = _replace_id_list(current, from_feed_id, to_feed_id)
            if updated != current:
                bind.execute(
                    sa.update(table)
                    .where(table.c.id == row["id"])
                    .values(feed_ids_json=updated)
                )

    saved_views = tables["saved_views"]
    rows = bind.execute(sa.select(saved_views.c.id, saved_views.c.query_json)).mappings().all()
    for row in rows:
        current = row["query_json"]
        updated = _replace_nested_feed_id(current, str(from_feed_id), str(to_feed_id))
        if updated != current:
            bind.execute(
                sa.update(saved_views)
                .where(saved_views.c.id == row["id"])
                .values(query_json=updated)
            )


def _merge_feed_values(canonical_feed: sa.RowMapping, duplicate_feed: sa.RowMapping) -> dict[str, object]:
    merged: dict[str, object] = {}

    if _is_placeholder_feed_name(canonical_feed):
        duplicate_name = _clean_text(duplicate_feed["name"])
        duplicate_url = _clean_text(duplicate_feed["url"])
        if duplicate_name and duplicate_name != duplicate_url:
            merged["name"] = duplicate_name

    for field_name in ("description", "site_url", "language"):
        if not canonical_feed[field_name] and duplicate_feed[field_name]:
            merged[field_name] = duplicate_feed[field_name]

    canonical_activity = _feed_activity_timestamp(canonical_feed)
    duplicate_activity = _feed_activity_timestamp(duplicate_feed)
    if duplicate_activity is not None and (canonical_activity is None or duplicate_activity > canonical_activity):
        for field_name in ("etag", "last_modified", "last_error"):
            if duplicate_feed[field_name]:
                merged[field_name] = duplicate_feed[field_name]
        if duplicate_feed["error_count"] is not None:
            merged["error_count"] = duplicate_feed["error_count"]

    merged["last_fetch_at"] = _max_datetime(canonical_feed["last_fetch_at"], duplicate_feed["last_fetch_at"])
    merged["last_success_at"] = _max_datetime(canonical_feed["last_success_at"], duplicate_feed["last_success_at"])
    if duplicate_feed["error_count"] and int(duplicate_feed["error_count"]) > int(canonical_feed["error_count"] or 0):
        merged["error_count"] = duplicate_feed["error_count"]
        if duplicate_feed["last_error"]:
            merged["last_error"] = duplicate_feed["last_error"]

    return {key: value for key, value in merged.items() if canonical_feed.get(key) != value}


def _select_canonical_feed_row(group: list[sa.RowMapping]) -> sa.RowMapping:
    return max(group, key=_feed_merge_score)


def _feed_merge_score(row: sa.RowMapping) -> tuple[int, int, object, object, str]:
    return (
        _feed_config_specificity(row),
        1 if not _is_placeholder_feed_name(row) else 0,
        _feed_activity_timestamp(row) or row["created_at"],
        row["created_at"],
        str(row["id"]),
    )


def _feed_config_specificity(row: sa.RowMapping) -> int:
    if row["fetch_mode"] == "schedule" and row["schedule_cron"]:
        return 2
    if row["fetch_mode"] == "interval" and int(row["fetch_interval_seconds"] or 0) not in (0, 1800):
        return 1
    return 0


def _feed_activity_timestamp(row: sa.RowMapping):
    return _max_datetime(row["last_success_at"], row["last_fetch_at"])


def _clean_text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _is_placeholder_feed_name(row: sa.RowMapping) -> bool:
    name = _clean_text(row["name"])
    url = _clean_text(row["url"])
    return not name or name == url


def _replace_id_list(values: list[str], from_id, to_id) -> list[str]:
    updated: list[str] = []
    to_id_text = str(to_id)
    from_id_text = str(from_id)
    for value in values:
        candidate = to_id_text if value == from_id_text else value
        if candidate not in updated:
            updated.append(candidate)
    return updated


def _replace_nested_feed_id(value, from_id: str, to_id: str):
    if isinstance(value, list):
        return [_replace_nested_feed_id(entry, from_id, to_id) for entry in value]
    if isinstance(value, dict):
        return {key: _replace_nested_feed_id(entry, from_id, to_id) for key, entry in value.items()}
    if value == from_id:
        return to_id
    return value


def _max_datetime(left, right):
    if left is None:
        return right
    if right is None:
        return left
    return right if right > left else left


def _normalize_feed_url(url: str | None) -> str:
    if not url:
        return ""

    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return ""

    scheme = (parts.scheme or "http").lower()
    hostname = (parts.hostname or "").lower()
    if not hostname:
        return ""

    try:
        port = parts.port
    except ValueError:
        return ""

    netloc = _build_netloc(
        scheme=scheme,
        hostname=hostname,
        port=port,
        username=parts.username,
        password=parts.password,
    )

    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/")
        if not path:
            path = "/"

    return urlunsplit((scheme, netloc, path, parts.query, ""))
