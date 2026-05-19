import math
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SAVED_VIEW_SCHEMA_VERSION = 1
SAVED_VIEW_LAYOUT_VERSION = 6
DEFAULT_PAGE_SIZE = 25
DEFAULT_ROLLING_DAYS = "7"
ALLOWED_PAGE_SIZES = {10, 25, 50, 100}
ALLOWED_TIME_RANGES = {"all", "24h", "7d", "30d", "days", "custom"}
ALLOWED_TIME_SORTS = {
    "published_at_desc",
    "published_at_asc",
    "first_seen_desc",
    "first_seen_asc",
}
ALLOWED_AI_RELEVANCE_FILTERS = {"all", "low", "medium", "high"}
ALLOWED_WINDOW_TYPES = {"rss", "alerts", "notes", "daily_brief"}
ALLOWED_WINDOW_SNAPS = {
    "free",
    "full",
    "left",
    "right",
    "top_left",
    "top_right",
    "bottom_left",
    "bottom_right",
}
SAVED_VIEW_QUERY_KEYS = {
    "schema_version",
    "version",
    "rss_filters",
    "alert_filters",
    "windows",
    "ui",
}
SAVED_VIEW_RSS_FILTER_KEYS = {
    "selected_feed_ids",
    "selected_tags",
    "q",
    "read_status",
    "star_status",
    "ai_relevance",
    "view_mode",
    "page_size",
    "time_range",
    "custom_since_date",
    "custom_until_date",
    "rolling_days",
    "sort",
}
SAVED_VIEW_ALERT_FILTER_KEYS = {
    "selected_alert_ids",
    "selected_categories",
    "q",
    "view_mode",
    "page_size",
    "time_range",
    "custom_since_date",
    "custom_until_date",
    "rolling_days",
    "sort",
}
SAVED_VIEW_WINDOW_KEYS = {
    "id",
    "type",
    "title",
    "snap",
    "rect",
    "controls_collapsed",
    "scratch_note",
    "time_override",
    "rss_filters",
    "alert_filters",
    "selected_daily_brief_id",
}
SAVED_VIEW_RECT_KEYS = {"x", "y", "width", "height", "xPct", "yPct", "widthPct", "heightPct"}
SAVED_VIEW_TIME_FILTER_KEYS = {"time_range", "custom_since_date", "custom_until_date", "rolling_days"}
SAVED_VIEW_UI_KEYS = {"show_advanced_filters"}
SAVED_VIEW_WINDOW_RSS_FILTER_KEYS = {
    "selected_feed_ids",
    "selected_tags",
    "q",
    "read_status",
    "star_status",
    "ai_relevance",
    "view_mode",
    "page",
    "page_size",
    "sort",
    "show_advanced_filters",
}
SAVED_VIEW_WINDOW_ALERT_FILTER_KEYS = {
    "selected_alert_ids",
    "selected_categories",
    "q",
    "view_mode",
    "page",
    "page_size",
    "sort",
}
DEFAULT_WINDOW_RECT = {
    "x": 0,
    "y": 0,
    "width": 1120,
    "height": 680,
}


def _is_mapping(value: Any) -> bool:
    return isinstance(value, Mapping)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [entry for entry in value if isinstance(entry, str)]


def _normalize_positive_int(value: Any, *, default: int, minimum: int = 1) -> int:
    if _is_number(value):
        return max(minimum, int(value))
    return default


def _normalize_page_size(value: Any) -> int:
    if _is_number(value):
        candidate = int(value)
        if candidate in ALLOWED_PAGE_SIZES:
            return candidate
    return DEFAULT_PAGE_SIZE


def _normalize_time_range(value: Any) -> str:
    if isinstance(value, str) and value in ALLOWED_TIME_RANGES:
        return value
    return "all"


def _normalize_time_sort(value: Any) -> str:
    if isinstance(value, str) and value in ALLOWED_TIME_SORTS:
        return value
    return "published_at_desc"


def _normalize_ai_relevance_filter(value: Any, *, default: str = "all") -> str:
    if isinstance(value, str) and value in ALLOWED_AI_RELEVANCE_FILTERS:
        return value
    return default if default in ALLOWED_AI_RELEVANCE_FILTERS else "all"


def _normalize_string(value: Any, *, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _normalize_rolling_days(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if _is_number(value):
        return str(int(value))
    return DEFAULT_ROLLING_DAYS


def _normalize_panel_rect(value: Any) -> dict[str, int | float] | None:
    if not _is_mapping(value):
        return None

    x = value.get("x")
    y = value.get("y")
    width = value.get("width")
    height = value.get("height")
    if not all(_is_number(candidate) for candidate in (x, y, width, height)):
        return None

    rect = {
        "x": max(0, round(x)),
        "y": max(0, round(y)),
        "width": round(width),
        "height": round(height),
    }
    if rect["width"] <= 0 or rect["height"] <= 0:
        return None

    x_pct = value.get("xPct")
    y_pct = value.get("yPct")
    width_pct = value.get("widthPct")
    height_pct = value.get("heightPct")
    if all(_is_number(candidate) for candidate in (x_pct, y_pct, width_pct, height_pct)):
        if width_pct > 0 and height_pct > 0:
            rect.update(
                {
                    "xPct": min(1.0, max(0.0, float(x_pct))),
                    "yPct": min(1.0, max(0.0, float(y_pct))),
                    "widthPct": min(1.0, float(width_pct)),
                    "heightPct": min(1.0, float(height_pct)),
                }
            )
    return rect


def _normalize_window_time_filter(value: Any) -> dict[str, str] | None:
    if not _is_mapping(value):
        return None
    return {
        "time_range": _normalize_time_range(value.get("time_range")),
        "custom_since_date": _normalize_string(value.get("custom_since_date")),
        "custom_until_date": _normalize_string(value.get("custom_until_date")),
        "rolling_days": _normalize_rolling_days(value.get("rolling_days")),
    }


def _normalize_saved_view_rss_filters(value: Any) -> dict[str, Any]:
    source = value if _is_mapping(value) else {}
    return {
        "selected_feed_ids": _normalize_string_list(source.get("selected_feed_ids")),
        "selected_tags": _normalize_string_list(source.get("selected_tags")),
        "q": _normalize_string(source.get("q")),
        "read_status": source.get("read_status") if source.get("read_status") in {"all", "read", "unread"} else "all",
        "star_status": (
            source.get("star_status") if source.get("star_status") in {"all", "starred", "unstarred"} else "all"
        ),
        "ai_relevance": _normalize_ai_relevance_filter(source.get("ai_relevance")),
        "view_mode": "expanded" if source.get("view_mode") == "expanded" else "compact",
        "page_size": _normalize_page_size(source.get("page_size")),
        "time_range": _normalize_time_range(source.get("time_range")),
        "custom_since_date": _normalize_string(source.get("custom_since_date")),
        "custom_until_date": _normalize_string(source.get("custom_until_date")),
        "rolling_days": _normalize_rolling_days(source.get("rolling_days")),
        "sort": _normalize_time_sort(source.get("sort")),
    }


def _normalize_saved_view_alert_filters(value: Any) -> dict[str, Any]:
    source = value if _is_mapping(value) else {}
    return {
        "selected_alert_ids": _normalize_string_list(source.get("selected_alert_ids")),
        "selected_categories": _normalize_string_list(source.get("selected_categories")),
        "q": _normalize_string(source.get("q")),
        "view_mode": "compact" if source.get("view_mode") == "compact" else "expanded",
        "page_size": _normalize_page_size(source.get("page_size")),
        "time_range": _normalize_time_range(source.get("time_range")),
        "custom_since_date": _normalize_string(source.get("custom_since_date")),
        "custom_until_date": _normalize_string(source.get("custom_until_date")),
        "rolling_days": _normalize_rolling_days(source.get("rolling_days")),
        "sort": _normalize_time_sort(source.get("sort")),
    }


def _normalize_window_rss_filters(
    value: Any,
    *,
    fallback: Mapping[str, Any] | None = None,
    show_advanced_fallback: bool = False,
) -> dict[str, Any]:
    source = value if _is_mapping(value) else {}
    base = fallback or {}
    selected_feed_ids = (
        _normalize_string_list(source.get("selected_feed_ids"))
        if isinstance(source.get("selected_feed_ids"), list)
        else list(base.get("selected_feed_ids", []))
    )
    selected_tags = (
        _normalize_string_list(source.get("selected_tags"))
        if isinstance(source.get("selected_tags"), list)
        else list(base.get("selected_tags", []))
    )
    return {
        "selected_feed_ids": selected_feed_ids,
        "selected_tags": selected_tags,
        "q": _normalize_string(source.get("q"), default=_normalize_string(base.get("q"))),
        "read_status": (
            source.get("read_status")
            if source.get("read_status") in {"all", "read", "unread"}
            else base.get("read_status", "all")
        ),
        "star_status": (
            source.get("star_status")
            if source.get("star_status") in {"all", "starred", "unstarred"}
            else base.get("star_status", "all")
        ),
        "ai_relevance": _normalize_ai_relevance_filter(
            source.get("ai_relevance"),
            default=_normalize_ai_relevance_filter(base.get("ai_relevance")),
        ),
        "view_mode": (
            source.get("view_mode")
            if source.get("view_mode") in {"compact", "expanded"}
            else base.get("view_mode", "compact")
        ),
        "page": _normalize_positive_int(source.get("page"), default=1),
        "page_size": _normalize_page_size(source.get("page_size") if "page_size" in source else base.get("page_size")),
        "sort": _normalize_time_sort(source.get("sort") if "sort" in source else base.get("sort")),
        "show_advanced_filters": (
            source.get("show_advanced_filters")
            if isinstance(source.get("show_advanced_filters"), bool)
            else bool(base.get("show_advanced_filters", show_advanced_fallback))
        ),
    }


def _normalize_window_alert_filters(
    value: Any,
    *,
    fallback: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    source = value if _is_mapping(value) else {}
    base = fallback or {}
    selected_alert_ids = (
        _normalize_string_list(source.get("selected_alert_ids"))
        if isinstance(source.get("selected_alert_ids"), list)
        else list(base.get("selected_alert_ids", []))
    )
    selected_categories = (
        _normalize_string_list(source.get("selected_categories"))
        if isinstance(source.get("selected_categories"), list)
        else list(base.get("selected_categories", []))
    )
    return {
        "selected_alert_ids": selected_alert_ids,
        "selected_categories": selected_categories,
        "q": _normalize_string(source.get("q"), default=_normalize_string(base.get("q"))),
        "view_mode": (
            source.get("view_mode")
            if source.get("view_mode") in {"compact", "expanded"}
            else base.get("view_mode", "expanded")
        ),
        "page": _normalize_positive_int(source.get("page"), default=1),
        "page_size": _normalize_page_size(source.get("page_size") if "page_size" in source else base.get("page_size")),
        "sort": _normalize_time_sort(source.get("sort") if "sort" in source else base.get("sort")),
    }


def _default_window_title(window_type: str, index: int) -> str:
    if window_type == "rss":
        return f"RSS Panel {index}"
    if window_type == "alerts":
        return f"Alerts Panel {index}"
    if window_type == "daily_brief":
        return f"Daily Brief Panel {index}"
    return f"Notes Panel {index}"


def _normalize_saved_view_window(
    value: Any,
    *,
    index: int,
    rss_filters: Mapping[str, Any],
    alert_filters: Mapping[str, Any],
    show_advanced_filters: bool,
) -> dict[str, Any] | None:
    if not _is_mapping(value):
        return None

    window_type = value.get("type")
    snap = value.get("snap")
    rect = _normalize_panel_rect(value.get("rect"))
    if window_type not in ALLOWED_WINDOW_TYPES or snap not in ALLOWED_WINDOW_SNAPS or rect is None:
        return None

    normalized = {
        "id": value.get("id") if isinstance(value.get("id"), str) and value.get("id") else str(uuid.uuid4()),
        "type": window_type,
        "title": (
            value.get("title")
            if isinstance(value.get("title"), str) and value.get("title")
            else _default_window_title(window_type, index)
        ),
        "snap": snap,
        "rect": rect,
        "controls_collapsed": value.get("controls_collapsed") is True,
        "scratch_note": _normalize_string(value.get("scratch_note")),
        "time_override": (
            _normalize_window_time_filter(value.get("time_override"))
            if window_type in {"rss", "alerts"}
            else None
        ),
        "rss_filters": None,
        "alert_filters": None,
        "selected_daily_brief_id": (
            value.get("selected_daily_brief_id")
            if window_type == "daily_brief"
            and isinstance(value.get("selected_daily_brief_id"), str)
            and value.get("selected_daily_brief_id")
            else None
        ),
    }

    if window_type == "rss":
        normalized["rss_filters"] = _normalize_window_rss_filters(
            value.get("rss_filters"),
            fallback=rss_filters,
            show_advanced_fallback=show_advanced_filters,
        )
    elif window_type == "alerts":
        normalized["alert_filters"] = _normalize_window_alert_filters(value.get("alert_filters"), fallback=alert_filters)

    return normalized


def _normalize_legacy_saved_view_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    legacy_filters = value.get("filters") if _is_mapping(value.get("filters")) else value
    layout = value.get("layout") if _is_mapping(value.get("layout")) else {}
    legacy_windows = layout.get("windows") if _is_mapping(layout.get("windows")) else {}
    rss_source = value.get("rss_filters") if _is_mapping(value.get("rss_filters")) else legacy_filters
    alert_source = value.get("alert_filters") if _is_mapping(value.get("alert_filters")) else {}
    ui_source = value.get("ui") if _is_mapping(value.get("ui")) else {}

    rss_filters = _normalize_saved_view_rss_filters(rss_source)
    alert_filters = _normalize_saved_view_alert_filters(alert_source)
    show_advanced_filters = ui_source.get("show_advanced_filters") is True

    windows: list[dict[str, Any]] = []
    if isinstance(value.get("windows"), list):
        for index, entry in enumerate(value["windows"], start=1):
            normalized = _normalize_saved_view_window(
                entry,
                index=index,
                rss_filters=rss_filters,
                alert_filters=alert_filters,
                show_advanced_filters=show_advanced_filters,
            )
            if normalized is not None:
                windows.append(normalized)

    if not windows:
        legacy_feed_rect = (
            _normalize_panel_rect(legacy_windows.get("feeds"))
            or _normalize_panel_rect(value.get("panel_rect"))
            or dict(DEFAULT_WINDOW_RECT)
        )
        windows.append(
            {
                "id": str(uuid.uuid4()),
                "type": "rss",
                "title": _default_window_title("rss", 1),
                "snap": "free",
                "rect": legacy_feed_rect,
                "controls_collapsed": False,
                "scratch_note": "",
                "time_override": None,
                "rss_filters": _normalize_window_rss_filters(
                    None,
                    fallback=rss_filters,
                    show_advanced_fallback=show_advanced_filters,
                ),
                "alert_filters": None,
                "selected_daily_brief_id": None,
            }
        )

    version = value.get("version")
    return {
        "schema_version": SAVED_VIEW_SCHEMA_VERSION,
        "version": int(version) if _is_number(version) and int(version) >= 1 else SAVED_VIEW_LAYOUT_VERSION,
        "rss_filters": rss_filters,
        "alert_filters": alert_filters,
        "windows": windows,
        "ui": {
            "show_advanced_filters": show_advanced_filters,
        },
    }


def _strip_mapping_extras(value: Any, *, allowed_keys: set[str]) -> Any:
    if not _is_mapping(value):
        return value
    return {key: child_value for key, child_value in value.items() if key in allowed_keys}


def _normalize_current_saved_view_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _strip_mapping_extras(value, allowed_keys=SAVED_VIEW_QUERY_KEYS)
    if "rss_filters" in normalized:
        normalized["rss_filters"] = _strip_mapping_extras(
            normalized.get("rss_filters"),
            allowed_keys=SAVED_VIEW_RSS_FILTER_KEYS,
        )
    if "alert_filters" in normalized:
        normalized["alert_filters"] = _strip_mapping_extras(
            normalized.get("alert_filters"),
            allowed_keys=SAVED_VIEW_ALERT_FILTER_KEYS,
        )
    if "ui" in normalized:
        normalized["ui"] = _strip_mapping_extras(normalized.get("ui"), allowed_keys=SAVED_VIEW_UI_KEYS)

    windows = value.get("windows")
    if not isinstance(windows, list):
        return normalized

    normalized_windows: list[Any] = []
    for entry in windows:
        if not _is_mapping(entry):
            normalized_windows.append(entry)
            continue

        normalized_window = _strip_mapping_extras(entry, allowed_keys=SAVED_VIEW_WINDOW_KEYS)
        normalized_window["rect"] = _strip_mapping_extras(
            normalized_window.get("rect"),
            allowed_keys=SAVED_VIEW_RECT_KEYS,
        )
        raw_time_override = normalized_window.get("time_override")
        normalized_time_override = _strip_mapping_extras(
            raw_time_override,
            allowed_keys=SAVED_VIEW_TIME_FILTER_KEYS,
        )
        normalized_window["time_override"] = (
            normalized_time_override if not _is_mapping(raw_time_override) or normalized_time_override else None
        )
        normalized_window["rss_filters"] = _strip_mapping_extras(
            normalized_window.get("rss_filters"),
            allowed_keys=SAVED_VIEW_WINDOW_RSS_FILTER_KEYS,
        )
        normalized_window["alert_filters"] = _strip_mapping_extras(
            normalized_window.get("alert_filters"),
            allowed_keys=SAVED_VIEW_WINDOW_ALERT_FILTER_KEYS,
        )
        normalized_windows.append(normalized_window)

    normalized["windows"] = normalized_windows
    return normalized


class SavedViewWindowTimeFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time_range: Literal["all", "24h", "7d", "30d", "days", "custom"] = "all"
    custom_since_date: str = ""
    custom_until_date: str = ""
    rolling_days: str = DEFAULT_ROLLING_DAYS


class SavedViewRssFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_feed_ids: list[str] = Field(default_factory=list)
    selected_tags: list[str] = Field(default_factory=list)
    q: str = ""
    read_status: Literal["all", "read", "unread"] = "all"
    star_status: Literal["all", "starred", "unstarred"] = "all"
    ai_relevance: Literal["all", "low", "medium", "high"] = "all"
    view_mode: Literal["compact", "expanded"] = "compact"
    page_size: Literal[10, 25, 50, 100] = DEFAULT_PAGE_SIZE
    time_range: Literal["all", "24h", "7d", "30d", "days", "custom"] = "all"
    custom_since_date: str = ""
    custom_until_date: str = ""
    rolling_days: str = DEFAULT_ROLLING_DAYS
    sort: Literal["published_at_desc", "published_at_asc", "first_seen_desc", "first_seen_asc"] = "published_at_desc"


class SavedViewAlertFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_alert_ids: list[str] = Field(default_factory=list)
    selected_categories: list[str] = Field(default_factory=list)
    q: str = ""
    view_mode: Literal["compact", "expanded"] = "expanded"
    page_size: Literal[10, 25, 50, 100] = DEFAULT_PAGE_SIZE
    time_range: Literal["all", "24h", "7d", "30d", "days", "custom"] = "all"
    custom_since_date: str = ""
    custom_until_date: str = ""
    rolling_days: str = DEFAULT_ROLLING_DAYS
    sort: Literal["published_at_desc", "published_at_asc", "first_seen_desc", "first_seen_asc"] = "published_at_desc"


class SavedViewWindowRssFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_feed_ids: list[str] = Field(default_factory=list)
    selected_tags: list[str] = Field(default_factory=list)
    q: str = ""
    read_status: Literal["all", "read", "unread"] = "all"
    star_status: Literal["all", "starred", "unstarred"] = "all"
    ai_relevance: Literal["all", "low", "medium", "high"] = "all"
    view_mode: Literal["compact", "expanded"] = "compact"
    page: int = Field(default=1, ge=1)
    page_size: Literal[10, 25, 50, 100] = DEFAULT_PAGE_SIZE
    sort: Literal["published_at_desc", "published_at_asc", "first_seen_desc", "first_seen_asc"] = "published_at_desc"
    show_advanced_filters: bool = False


class SavedViewWindowAlertFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_alert_ids: list[str] = Field(default_factory=list)
    selected_categories: list[str] = Field(default_factory=list)
    q: str = ""
    view_mode: Literal["compact", "expanded"] = "expanded"
    page: int = Field(default=1, ge=1)
    page_size: Literal[10, 25, 50, 100] = DEFAULT_PAGE_SIZE
    sort: Literal["published_at_desc", "published_at_asc", "first_seen_desc", "first_seen_asc"] = "published_at_desc"


class SavedViewPanelRect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    xPct: float | None = Field(default=None, ge=0, le=1)
    yPct: float | None = Field(default=None, ge=0, le=1)
    widthPct: float | None = Field(default=None, gt=0, le=1)
    heightPct: float | None = Field(default=None, gt=0, le=1)

    @field_validator("x", "y", "width", "height", mode="before")
    @classmethod
    def coerce_rect_numbers_to_ints(cls, value: Any):
        if _is_number(value):
            return round(value)
        return value

    @field_validator("xPct", "yPct", "widthPct", "heightPct", mode="before")
    @classmethod
    def coerce_rect_percentages_to_floats(cls, value: Any):
        if value is None:
            return value
        if _is_number(value):
            return float(value)
        return value

    @model_validator(mode="after")
    def validate_percentage_rect_payload(self):
        percentages = (self.xPct, self.yPct, self.widthPct, self.heightPct)
        if any(value is not None for value in percentages) and not all(value is not None for value in percentages):
            raise ValueError("Floating panel percentage geometry must include all percentage fields")
        return self


class SavedViewWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=255)
    type: Literal["rss", "alerts", "notes", "daily_brief"]
    title: str = Field(min_length=1, max_length=255)
    snap: Literal["free", "full", "left", "right", "top_left", "top_right", "bottom_left", "bottom_right"]
    rect: SavedViewPanelRect
    controls_collapsed: bool = False
    scratch_note: str = ""
    time_override: SavedViewWindowTimeFilter | None = None
    rss_filters: SavedViewWindowRssFilters | None = None
    alert_filters: SavedViewWindowAlertFilters | None = None
    selected_daily_brief_id: str | None = None

    @model_validator(mode="after")
    def validate_window_payload(self):
        if self.type == "rss":
            if self.rss_filters is None or self.alert_filters is not None:
                raise ValueError("RSS windows must include rss_filters and omit alert_filters")
        elif self.type == "alerts":
            if self.alert_filters is None or self.rss_filters is not None:
                raise ValueError("Alert windows must include alert_filters and omit rss_filters")
        else:
            if self.rss_filters is not None or self.alert_filters is not None:
                raise ValueError("Non-search windows cannot include feed or alert filters")

        if self.type in {"notes", "daily_brief"} and self.time_override is not None:
            raise ValueError("Only RSS and alert windows can override the dashboard time scope")

        if self.type != "daily_brief" and self.selected_daily_brief_id is not None:
            raise ValueError("Only daily brief windows can select a brief snapshot")

        return self


class SavedViewUiState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    show_advanced_filters: bool = False


class SavedViewQueryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = SAVED_VIEW_SCHEMA_VERSION
    version: int = Field(default=SAVED_VIEW_LAYOUT_VERSION, ge=1)
    rss_filters: SavedViewRssFilters
    alert_filters: SavedViewAlertFilters
    windows: list[SavedViewWindow] = Field(min_length=1)
    ui: SavedViewUiState = Field(default_factory=SavedViewUiState)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_payload(cls, value: Any):
        if not _is_mapping(value):
            return value
        if "schema_version" in value:
            return _normalize_current_saved_view_payload(value)
        return _normalize_legacy_saved_view_payload(value)


class SavedViewCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    query_json: SavedViewQueryPayload


class SavedViewUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    query_json: SavedViewQueryPayload | None = None


class SavedViewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    query_json: SavedViewQueryPayload
    created_at: datetime
