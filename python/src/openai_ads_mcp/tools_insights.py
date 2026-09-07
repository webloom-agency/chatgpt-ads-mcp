"""Insights MCP tools for OpenAI Ads."""

from __future__ import annotations

from datetime import datetime, timezone

from ._core import *

_INSIGHT_SCOPES = {"account", "campaign", "ad_group", "ad"}
_TIME_GRANULARITIES = {"hourly", "daily", "monthly", "none"}
_SEGMENTS = {"product", "country", "device"}
_FILTER_OPERATORS = {"IN", "GREATER_THAN", "LESS_THAN"}
_INSIGHT_AGGREGATION_LEVELS = {
    "account": {"ad_account", "campaign", "ad_group", "ad"},
    "campaign": {"campaign", "ad_group", "ad"},
    "ad_group": {"ad_group", "ad"},
    "ad": {"ad"},
}
_SEGMENT_GROUP_ORDER_VALUES = {"ad_account", "campaign", "ad_group", "ad", "product", "country", "device"}
_INCLUDES = {"zero_impression_items", "zero_impression_products"}
_TIME_RANGE_TYPES = {"unix_range", "hour_range", "date_range"}

# Without fields[], Ads only returns id/start/end — metrics stay empty for clients.
_DEFAULT_INSIGHT_FIELDS = [
    "impressions",
    "clicks",
    "spend",
    "ctr",
    "cpc",
    "cpm",
    "conversions",
    "campaign.id",
    "campaign.name",
    "ad_group.id",
    "ad_group.name",
    "ad.id",
    "ad.name",
    "metadata.readable_time",
]
_SUMMARY_METRIC_KEYS = (
    "impressions",
    "clicks",
    "spend",
    "ctr",
    "cpc",
    "cpm",
    "conversions",
)


def _insights_path(scope: str, entity_id: str | None) -> tuple[str | None, str | None]:
    if scope == "account":
        return "/ad_account/insights", None
    if not entity_id:
        return None, _bad_request("entity_id is required for campaign, ad_group, and ad insights.")
    if scope == "campaign":
        return f"/campaigns/{entity_id}/insights", None
    if scope == "ad_group":
        return f"/ad_groups/{entity_id}/insights", None
    if scope == "ad":
        return f"/ads/{entity_id}/insights", None
    return None, _bad_request("scope must be account, campaign, ad_group, or ad.")


def _align_unix_hour(value: int, *, round_up: bool = False) -> int:
    """Ads requires unix_range bounds on full-hour boundaries."""
    if value % 3600 == 0:
        return value
    floored = value - (value % 3600)
    return floored + 3600 if round_up else floored


def _normalize_time_range(value: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    range_type = value.get("type")
    if isinstance(range_type, str):
        if range_type not in _TIME_RANGE_TYPES:
            return None, _bad_request("time_range.type must be unix_range, hour_range, or date_range.")
        if range_type == "unix_range":
            start = value.get("start")
            end = value.get("end")
            if not isinstance(start, int) or not isinstance(end, int):
                return None, _bad_request("unix_range requires integer start and end (Unix seconds).")
            start = _align_unix_hour(start, round_up=False)
            end = _align_unix_hour(end, round_up=True)
            if end <= start:
                return None, _bad_request(
                    "unix_range end must be after start after hour alignment. "
                    "Prefer omitting time_range (API defaults to recent days) or use full-hour timestamps."
                )
            now = int(datetime.now(timezone.utc).timestamp())
            if start > now + 3600:
                return None, _bad_request("unix_range start is in the future.")
            return {"type": "unix_range", "start": start, "end": end}, None
        return value, None

    legacy_keys = [key for key in _TIME_RANGE_TYPES if isinstance(value.get(key), dict)]
    if len(legacy_keys) != 1:
        return None, _bad_request("time_range must include type: unix_range, hour_range, or date_range.")
    range_type = legacy_keys[0]
    return _normalize_time_range({"type": range_type, **value[range_type]})


def _one_time_range(value: Any) -> tuple[list[str] | None, str | None]:
    if value is None:
        return None, None
    if isinstance(value, str):
        parsed, err = _coerce_json(value, "time_range")
        if err:
            return None, err
        value = parsed
    if isinstance(value, dict):
        normalized, err = _normalize_time_range(value)
        if err:
            return None, err
        return [json.dumps(normalized, separators=_COMPACT_SEPARATORS)], None
    if isinstance(value, list):
        if len(value) != 1:
            return None, _bad_request("time_range accepts one range object.")
        item = value[0]
        if isinstance(item, str):
            parsed, err = _coerce_json(item, "time_range")
            if err:
                return None, err
            item = parsed
        if not isinstance(item, dict):
            return None, _bad_request("time_range must contain an object.")
        normalized, err = _normalize_time_range(item)
        if err:
            return None, err
        return [json.dumps(normalized, separators=_COMPACT_SEPARATORS)], None
    return None, _bad_request("time_range must be an object or JSON object string.")


def _validate_filters(value: Any) -> tuple[list[str] | None, str | None]:
    encoded, err = _json_query_list(value, "filters")
    if err or encoded is None:
        return None, err
    for item in encoded:
        try:
            parsed = json.loads(item)
        except json.JSONDecodeError:
            return None, _bad_request("filters must be JSON objects.")
        if parsed.get("operator") not in _FILTER_OPERATORS:
            return None, _bad_request("filter operator must be IN, GREATER_THAN, or LESS_THAN.")
        if "field" not in parsed or "value" not in parsed:
            return None, _bad_request("Each filter must include field, operator, and value.")
    return encoded, None


def _validate_sort(value: Any) -> tuple[list[str] | None, str | None]:
    encoded, err = _json_query_list(value, "sort")
    if err or encoded is None:
        return None, err
    for item in encoded:
        try:
            parsed = json.loads(item)
        except json.JSONDecodeError:
            return None, _bad_request("sort must be JSON objects.")
        if parsed.get("direction") not in {"asc", "desc"}:
            return None, _bad_request("sort direction must be asc or desc.")
        if "field" not in parsed:
            return None, _bad_request("Each sort entry must include field and direction.")
    return encoded, None


def _row_metric(row: dict[str, Any], key: str) -> Any:
    if key in row:
        return row[key]
    metrics = row.get("metrics")
    if isinstance(metrics, dict) and key in metrics:
        return metrics[key]
    return None


def _summarize_insights(payload: Any) -> dict[str, Any] | None:
    """Build a compact, LLM-friendly rollup so clients are not stuck on opaque list ids."""
    if not isinstance(payload, dict):
        return None
    rows = payload.get("data")
    if not isinstance(rows, list) or not rows:
        return {"row_count": 0, "note": "No insight rows returned for this query."}

    totals: dict[str, float] = {key: 0.0 for key in ("impressions", "clicks", "spend", "conversions")}
    campaigns: dict[str, dict[str, Any]] = {}
    sample_rows: list[dict[str, Any]] = []

    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in totals:
            value = _row_metric(row, key)
            if isinstance(value, (int, float)):
                totals[key] += float(value)

        campaign = row.get("campaign") if isinstance(row.get("campaign"), dict) else {}
        campaign_id = campaign.get("id") or row.get("campaign.id") or row.get("entity_id") or row.get("id")
        campaign_name = campaign.get("name") or row.get("campaign.name")
        if isinstance(campaign_id, str):
            bucket = campaigns.setdefault(
                campaign_id,
                {
                    "campaign_id": campaign_id,
                    "campaign_name": campaign_name,
                    "impressions": 0.0,
                    "clicks": 0.0,
                    "spend": 0.0,
                    "conversions": 0.0,
                },
            )
            if campaign_name and not bucket.get("campaign_name"):
                bucket["campaign_name"] = campaign_name
            for key in ("impressions", "clicks", "spend", "conversions"):
                value = _row_metric(row, key)
                if isinstance(value, (int, float)):
                    bucket[key] += float(value)

        if len(sample_rows) < 10:
            sample = {
                key: _row_metric(row, key)
                for key in _SUMMARY_METRIC_KEYS
                if _row_metric(row, key) is not None
            }
            readable = row.get("metadata", {})
            if isinstance(readable, dict) and readable.get("readable_time"):
                sample["readable_time"] = readable["readable_time"]
            elif row.get("metadata.readable_time"):
                sample["readable_time"] = row["metadata.readable_time"]
            if campaign_name:
                sample["campaign_name"] = campaign_name
            if campaign_id:
                sample["campaign_id"] = campaign_id
            if row.get("start_time") is not None:
                sample["start_time"] = row.get("start_time")
            if row.get("end_time") is not None:
                sample["end_time"] = row.get("end_time")
            if sample:
                sample_rows.append(sample)

    by_campaign = sorted(
        campaigns.values(),
        key=lambda item: item.get("spend", 0),
        reverse=True,
    )[:20]
    for item in by_campaign:
        impressions = item.get("impressions") or 0
        clicks = item.get("clicks") or 0
        spend = item.get("spend") or 0
        item["ctr"] = (clicks / impressions) if impressions else None
        item["cpc"] = (spend / clicks) if clicks else None
        item["cpm"] = ((spend / impressions) * 1000) if impressions else None

    impressions = totals["impressions"]
    clicks = totals["clicks"]
    spend = totals["spend"]
    return {
        "row_count": len(rows),
        "totals": {
            **totals,
            "ctr": (clicks / impressions) if impressions else None,
            "cpc": (spend / clicks) if clicks else None,
            "cpm": ((spend / impressions) * 1000) if impressions else None,
        },
        "by_campaign": by_campaign,
        "sample_rows": sample_rows,
    }


@ads_tool(open_world=True)
async def get_insights(
    scope: Literal["account", "campaign", "ad_group", "ad"],
    entity_id: str | None = None,
    time_granularity: Literal["hourly", "daily", "monthly", "none"] = "daily",
    aggregation_level: Literal["ad_account", "campaign", "ad_group", "ad"] | None = None,
    time_range: Any = None,
    segments: Any = None,
    override_segment_group_order: Any = None,
    includes: Any = None,
    fields: Any = None,
    filters: Any = None,
    sort: Any = None,
    limit: int = 20,
    after: str | None = None,
    before: str | None = None,
    response_format: Literal["concise", "detailed"] = "concise",
) -> str:
    """Get performance insights for account, campaign, ad group, or ad scope.

    Prefer omitting time_range so the API uses its recent default window.
    If you pass unix_range, start/end must be Unix seconds on full-hour
    boundaries (this tool hour-aligns them). Do not invent far-future ranges.

    Defaults fields to impressions, clicks, spend, ctr, cpc, cpm, conversions,
    and names so responses include usable metrics. Returns a `summary` rollup.

    Args:
        scope: account, campaign, ad_group, or ad.
        entity_id: Required for non-account scopes.
        time_granularity: hourly, daily, monthly, or none. Default daily.
        time_range: Optional JSON object for unix_range, hour_range, or date_range.
        segments: Optional segment list: product, country, or device.
        fields: Optional list of fields. Defaults to core metrics + names.
        filters: Optional list of JSON filter objects.
        sort: Optional list of JSON sort objects with field and direction.
        limit: Rows per page, 1-2000. Default 20.
        after: Optional cursor for forward pagination.
        before: Optional cursor for backward pagination.
        response_format: concise caps large payloads, detailed returns more rows within the context ceiling.
    """
    if scope_err := _validate_option("scope", scope, _INSIGHT_SCOPES):
        return scope_err
    if granularity_err := _validate_option("time_granularity", time_granularity, _TIME_GRANULARITIES):
        return granularity_err
    if aggregation_level is not None:
        if aggregation_err := _validate_option("aggregation_level", aggregation_level, _INSIGHT_AGGREGATION_LEVELS[scope]):
            return aggregation_err
    if limit_err := _validate_int_range("limit", limit, 1, 2000):
        return limit_err
    path, path_err = _insights_path(scope, entity_id)
    if path_err:
        return path_err
    time_ranges, time_err = _one_time_range(time_range)
    if time_err:
        return time_err
    segment_values, segments_err = _coerce_string_list(segments, "segments")
    if segments_err:
        return segments_err
    if segment_values:
        if len(segment_values) > 1:
            return _bad_request("segments supports at most one value.")
        if unknown := [segment for segment in segment_values if segment not in _SEGMENTS]:
            return _bad_request(f"Invalid segments: {', '.join(unknown)}.")
        if time_granularity == "hourly":
            return _bad_request("Segmented insights support time_granularity of none, daily, or monthly.")
    field_values, fields_err = _coerce_string_list(fields, "fields")
    if fields_err:
        return fields_err
    if field_values is None:
        field_values = list(_DEFAULT_INSIGHT_FIELDS)
    if segment_values and segment_values[0] == "product" and not (
        field_values and ("product.feed_id" in field_values or "product.item_id" in field_values)
    ):
        return _bad_request("product segments require fields to include product.feed_id or product.item_id.")
    override_values, override_err = _coerce_string_list(override_segment_group_order, "override_segment_group_order")
    if override_err:
        return override_err
    if override_values:
        if unknown := [item for item in override_values if item not in _SEGMENT_GROUP_ORDER_VALUES]:
            return _bad_request(f"Invalid override_segment_group_order values: {', '.join(unknown)}.")
        if not aggregation_level:
            return _bad_request("aggregation_level is required when override_segment_group_order is provided.")
        if not segment_values:
            return _bad_request("segments is required when override_segment_group_order is provided.")
        segment = segment_values[0]
        if (
            len(override_values) != 2
            or override_values.count(aggregation_level) != 1
            or override_values.count(segment) != 1
        ):
            return _bad_request("override_segment_group_order must include the aggregation_level and requested segment exactly once.")
    include_values, includes_err = _coerce_string_list(includes, "includes")
    if includes_err:
        return includes_err
    if include_values:
        if len(include_values) > 1:
            return _bad_request("includes supports at most one value.")
        if unknown := [include for include in include_values if include not in _INCLUDES]:
            return _bad_request(f"Invalid includes: {', '.join(unknown)}.")
        if "zero_impression_items" in include_values and segment_values:
            return _bad_request("zero_impression_items cannot be used with segments.")
        if "zero_impression_products" in include_values and (
            not segment_values or segment_values[0] != "product" or not override_values or override_values[0] != "product"
        ):
            return _bad_request("zero_impression_products requires segments=product and product first in override_segment_group_order.")
    filter_values, filters_err = _validate_filters(filters)
    if filters_err:
        return filters_err
    sort_values, sort_err = _validate_sort(sort)
    if sort_err:
        return sort_err
    client, client_err = _get_client_or_error()
    if client_err:
        return client_err
    # Account-level campaign rollups are the usual "how are campaigns doing?" ask.
    if scope == "account" and aggregation_level is None and not segment_values:
        aggregation_level = "campaign"
    params = _optional_params(
        time_granularity=time_granularity,
        aggregation_level=aggregation_level,
        time_ranges=time_ranges,
        segments=segment_values,
        override_segment_group_order=override_values,
        includes=include_values,
        fields=field_values,
        filters=filter_values,
        sort=sort_values,
        limit=limit,
        after=after,
        before=before,
    )
    try:
        payload = await client.get(path, params=params)
        summary = _summarize_insights(payload)
        if isinstance(payload, dict) and summary is not None:
            payload = {**payload, "summary": summary}
        return _ok_sized(
            payload,
            response_format,
            follow_up="Use after or before cursors, narrow time_range, or request fewer fields. Prefer omitting time_range for the API default recent window.",
        )
    except OpenAIAdsAPIError as e:
        return _err(e)


__all__ = ("get_insights",)
