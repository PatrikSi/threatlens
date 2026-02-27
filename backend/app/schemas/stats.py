from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class FeedStats(BaseModel):
    feed_id: str
    feed_name: str
    total_items: int
    items_in_window: int
    error_items: int
    content_fetched_items: int
    last_published_at: datetime | None
    last_seen_at: datetime | None


class DailyVolumePoint(BaseModel):
    date: str
    count: int


class StatusPoint(BaseModel):
    status: str
    count: int


class DomainPoint(BaseModel):
    domain: str
    count: int


class TotalsSummary(BaseModel):
    feeds_total: int
    feeds_enabled: int
    feeds_disabled: int
    items_total: int
    items_new: int
    items_content_fetched: int
    items_error: int
    articles_total: int


class ActivitySummary(BaseModel):
    items_last_24h: int
    items_last_7d: int
    items_last_30d: int


class DerivedSummary(BaseModel):
    extraction_success_rate_pct: float
    error_rate_pct: float
    avg_items_per_day_window: float


class StatsOverviewResponse(BaseModel):
    generated_at: datetime
    window_days: int
    totals: TotalsSummary
    activity: ActivitySummary
    derived: DerivedSummary
    status_breakdown: list[StatusPoint]
    daily_volume: list[DailyVolumePoint]
    feed_breakdown: list[FeedStats]
    top_domains: list[DomainPoint]


class FeedTimeSeriesPoint(BaseModel):
    date: str
    count: int


class FeedTimeSeriesSeries(BaseModel):
    feed_id: str
    feed_name: str
    points: list[FeedTimeSeriesPoint]


class FeedTimeSeriesResponse(BaseModel):
    generated_at: datetime
    window_days: int
    series: list[FeedTimeSeriesSeries]


class ActivityHeatmapDayRow(BaseModel):
    day: str
    counts: list[int]


class ActivityHeatmapResponse(BaseModel):
    generated_at: datetime
    window_days: int
    bucket_unit: Literal["hour", "day"]
    bucket_labels: list[str]
    rows: list[ActivityHeatmapDayRow]
    max_count: int


class SignalRadarAxisPoint(BaseModel):
    category: str
    count: int
    pct: float


class SignalRadarResponse(BaseModel):
    generated_at: datetime
    window_days: int
    total: int
    max_count: int
    axes: list[SignalRadarAxisPoint]
