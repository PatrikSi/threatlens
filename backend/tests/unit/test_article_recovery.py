from datetime import datetime, timedelta, timezone

from app.services.article_recovery import (
    ARTICLE_REPAIR_SOFT_RETRY_DELAY,
    article_fetch_repair_cutoff,
    article_fetch_repair_floor,
    article_soft_repair_cutoff,
    is_fast_retryable_article_error,
    is_soft_retryable_article_error,
)


def test_article_repair_cutoffs_keep_soft_retries_slower_than_fast_retries():
    now = datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc)

    fast_cutoff = article_fetch_repair_cutoff(dispatch_after_seconds=300, now=now)
    soft_cutoff = article_soft_repair_cutoff(dispatch_after_seconds=300, now=now)

    assert fast_cutoff == now - timedelta(minutes=5)
    assert soft_cutoff == now - ARTICLE_REPAIR_SOFT_RETRY_DELAY


def test_article_repair_floor_limits_reprocessing_age():
    now = datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc)

    floor = article_fetch_repair_floor(now=now)

    assert floor < now
    assert (now - floor) >= timedelta(hours=23)


def test_article_error_matchers_distinguish_fast_and_soft_retry_conditions():
    assert is_fast_retryable_article_error("http_status:503") is True
    assert is_fast_retryable_article_error("network_or_rate_limit_error:timed out") is True
    assert is_fast_retryable_article_error("non_html_response") is False

    assert is_soft_retryable_article_error("non_html_response") is True
    assert is_soft_retryable_article_error("readability_error:no_content") is True
    assert is_soft_retryable_article_error("http_status:503") is False
