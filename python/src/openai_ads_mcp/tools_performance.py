"""Joined delivery + conversion performance for OpenAI Ads."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ._core import *

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PERF_LEVELS = {"campaign", "ad_group", "ad", "ad_account"}
_CONVERSION_LEVELS = {"campaign", "ad_group", "ad"}


def _api_aggregation_level(level: str) -> str:
    """Conversion insights reject ad_account; roll account totals up from campaigns."""
    if level == "ad_account":
        return "campaign"
    return level


def _parse_date(name: str, value: str | None) -> tuple[date | None, str | None]:
    if value is None:
        return None, None
    if not isinstance(value, str) or not _DATE_RE.match(value.strip()):
        return None, _bad_request(f"{name} must be YYYY-MM-DD.")
    try:
        return date.fromisoformat(value.strip()), None
    except ValueError:
        return None, _bad_request(f"{name} must be a valid calendar date.")


def _resolve_timezone(name: str | None) -> ZoneInfo:
    if not name:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


def _default_window(tz: ZoneInfo) -> tuple[date, date]:
    today = datetime.now(tz).date()
    return today - timedelta(days=6), today


def _unix_window(start: date, end: date, tz: ZoneInfo) -> tuple[int, int]:
    """Inclusive calendar dates → half-open unix hours in the account timezone."""
    start_dt = datetime(start.year, start.month, start.day, tzinfo=tz)
    end_exclusive = datetime(end.year, end.month, end.day, tzinfo=tz) + timedelta(days=1)
    start_unix = int(start_dt.timestamp())
    end_unix = int(end_exclusive.timestamp())
    start_unix -= start_unix % 3600
    if end_unix % 3600:
        end_unix += 3600 - (end_unix % 3600)
    return start_unix, end_unix


def _safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return float(numerator) / float(denominator)


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _nested_metric(row: dict[str, Any], entity: str, key: str) -> float | None:
    direct = _as_float(row.get(key))
    if direct is not None:
        return direct
    dotted = _as_float(row.get(f"{entity}.{key}"))
    if dotted is not None:
        return dotted
    nested = row.get(entity)
    if isinstance(nested, dict):
        nested_value = _as_float(nested.get(key))
        if nested_value is not None:
            return nested_value
    return _as_float(row.get(f"{entity}_{key}"))


def _entity_id_from_row(row: dict[str, Any], entity: str) -> str | None:
    nested = row.get(entity)
    if isinstance(nested, dict) and isinstance(nested.get("id"), str):
        return nested["id"]
    for key in (f"{entity}.id", f"{entity}_id", "entity_id", "id"):
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _entity_name_from_row(row: dict[str, Any], entity: str) -> str | None:
    nested = row.get(entity)
    if isinstance(nested, dict) and isinstance(nested.get("name"), str):
        return nested["name"]
    for key in (f"{entity}.name", f"{entity}_name", "name"):
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _conversion_value_map(value: Any) -> tuple[dict[str, float] | None, str | None]:
    if value is None:
        return {}, None
    parsed, err = _coerce_json(value, "conversion_value_by_entity")
    if err:
        return None, err
    if isinstance(parsed, dict):
        out: dict[str, float] = {}
        for key, amount in parsed.items():
            amount_value = _as_float(amount)
            if amount_value is None:
                return None, _bad_request("conversion_value_by_entity values must be numbers.")
            out[str(key)] = amount_value
        return out, None
    if isinstance(parsed, list):
        out = {}
        for index, item in enumerate(parsed):
            if not isinstance(item, dict):
                return None, _bad_request(f"conversion_value_by_entity[{index}] must be an object.")
            entity_id = item.get("entity_id") or item.get("id")
            amount_value = _as_float(item.get("value") if "value" in item else item.get("conversion_value"))
            if not isinstance(entity_id, str) or not entity_id:
                return None, _bad_request(f"conversion_value_by_entity[{index}].entity_id is required.")
            if amount_value is None:
                return None, _bad_request(f"conversion_value_by_entity[{index}].value must be a number.")
            out[entity_id] = amount_value
        return out, None
    return None, _bad_request("conversion_value_by_entity must be an object or list of {entity_id, value}.")


def _efficiency_row(
    *,
    entity_id: str,
    entity_name: str | None,
    impressions: float,
    clicks: float,
    spend: float,
    conversions: float,
    conversion_value: float | None,
) -> dict[str, Any]:
    return {
        "entity_id": entity_id,
        "entity_name": entity_name,
        "impressions": impressions,
        "clicks": clicks,
        "spend": spend,
        "conversions": conversions,
        "conversion_value": conversion_value,
        "ctr": _safe_div(clicks, impressions),
        "cpc": _safe_div(spend, clicks),
        "cpm": _safe_div(spend * 1000, impressions),
        "conversion_rate": _safe_div(conversions, clicks),
        "cpa": _safe_div(spend, conversions),
        "roas": _safe_div(conversion_value, spend),
        "revenue_per_click": _safe_div(conversion_value, clicks),
    }


def _sum_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    impressions = sum(float(row.get("impressions") or 0) for row in rows)
    clicks = sum(float(row.get("clicks") or 0) for row in rows)
    spend = sum(float(row.get("spend") or 0) for row in rows)
    conversions = sum(float(row.get("conversions") or 0) for row in rows)
    values = [row.get("conversion_value") for row in rows if isinstance(row.get("conversion_value"), (int, float))]
    conversion_value = sum(float(value) for value in values) if values else None
    return _efficiency_row(
        entity_id="totals",
        entity_name="totals",
        impressions=impressions,
        clicks=clicks,
        spend=spend,
        conversions=conversions,
        conversion_value=conversion_value,
    )


async def _list_campaign_ids(client: OpenAIAdsClient) -> tuple[list[str], dict[str, str]]:
    names: dict[str, str] = {}
    ids: list[str] = []
    after: str | None = None
    for _ in range(20):
        params = _optional_params(limit=200, order="desc", after=after)
        payload = await client.get("/campaigns", params=params)
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            break
        for row in rows:
            if not isinstance(row, dict):
                continue
            campaign_id = row.get("id")
            if isinstance(campaign_id, str) and campaign_id:
                ids.append(campaign_id)
                if isinstance(row.get("name"), str):
                    names[campaign_id] = row["name"]
        if not payload.get("has_more"):
            break
        after = payload.get("last_id")
        if not isinstance(after, str) or not after:
            break
    return ids, names


@ads_tool(open_world=True)
async def get_performance(
    aggregation_level: Literal["campaign", "ad_group", "ad", "ad_account"] = "campaign",
    start_date: str | None = None,
    end_date: str | None = None,
    entity_ids: Any = None,
    average_order_value: float | None = None,
    conversion_value_by_entity: Any = None,
    response_format: Literal["concise", "detailed"] = "concise",
) -> str:
    """Join ChatGPT Ads delivery insights with attributed conversions and compute CPA/ROAS.

    This is OpenAI Ads / ChatGPT Ads, not Google Ads. Delivery reporting has no
    conversions/ROAS fields, so this tool:
    1. Pulls impressions, clicks, and spend from delivery insights.
    2. Pulls attributed conversion counts from /conversions/insights.
    3. Computes conversion_rate, CPA, and ROAS on the server.

    aggregation_level must be campaign, ad_group, or ad for the conversion API.
    Pass ad_account as a shortcut for all campaigns with account totals in
    `totals` (still queried at campaign grain).

    ROAS needs revenue. Pass average_order_value (major currency units) or
    conversion_value_by_entity. Without that, conversion_value and roas stay null.

    Dates are inclusive YYYY-MM-DD in the ad account timezone. Defaults to the
    last 7 calendar days including today.

    Ingest events with send_conversions; configure settings with manage_conversions.
    Empty conversions usually means no attributed events yet.
    """
    if level_err := _validate_option("aggregation_level", aggregation_level, _PERF_LEVELS):
        return level_err
    api_level = _api_aggregation_level(aggregation_level)
    if api_level not in _CONVERSION_LEVELS:
        return _bad_request("aggregation_level must be campaign, ad_group, ad, or ad_account.")
    if average_order_value is not None and average_order_value < 0:
        return _bad_request("average_order_value must be >= 0.")
    value_map, value_err = _conversion_value_map(conversion_value_by_entity)
    if value_err:
        return value_err
    start, start_err = _parse_date("start_date", start_date)
    if start_err:
        return start_err
    end, end_err = _parse_date("end_date", end_date)
    if end_err:
        return end_err
    if (start is None) != (end is None):
        return _bad_request("Provide both start_date and end_date, or omit both for the last 7 days.")
    if start is not None and end is not None and end < start:
        return _bad_request("end_date must be on or after start_date.")

    ids, ids_err = _coerce_string_list(entity_ids, "entity_ids")
    if ids_err:
        return ids_err

    client, client_err = _get_client_or_error()
    if client_err:
        return client_err

    try:
        account = await client.get("/ad_account")
        timezone_name = account.get("timezone") if isinstance(account, dict) else None
        tz = _resolve_timezone(timezone_name if isinstance(timezone_name, str) else None)
        if start is None or end is None:
            start, end = _default_window(tz)

        start_unix, end_unix = _unix_window(start, end, tz)
        now_unix = int(datetime.now(timezone.utc).timestamp())
        if end_unix > now_unix + 3600:
            end_unix = now_unix - (now_unix % 3600) + 3600
        if end_unix <= start_unix:
            return _bad_request("Resolved time window is empty. Check start_date/end_date.")

        campaign_names: dict[str, str] = {}
        if not ids:
            if api_level != "campaign":
                return _bad_request(
                    f"entity_ids are required when aggregation_level={aggregation_level}. "
                    "Omit entity_ids only with campaign or ad_account (all campaigns)."
                )
            ids, campaign_names = await _list_campaign_ids(client)
            if not ids:
                return _ok({
                    "aggregation_level": api_level,
                    "requested_aggregation_level": aggregation_level,
                    "start_date": start.isoformat(),
                    "end_date": end.isoformat(),
                    "timezone": str(tz),
                    "rows": [],
                    "totals": _efficiency_row(
                        entity_id="totals",
                        entity_name="totals",
                        impressions=0,
                        clicks=0,
                        spend=0,
                        conversions=0,
                        conversion_value=None,
                    ),
                    "notes": ["No campaigns found. Create or activate campaigns before reading performance."],
                })

        entity = api_level
        delivery_fields = [
            f"{entity}.id",
            f"{entity}.name",
            f"{entity}.impressions",
            f"{entity}.clicks",
            f"{entity}.spend",
            f"{entity}.ctr",
            f"{entity}.cpc",
            f"{entity}.cpm",
        ]
        delivery_range = json.dumps(
            {"type": "unix_range", "start": start_unix, "end": end_unix},
            separators=_COMPACT_SEPARATORS,
        )
        delivery = await client.get(
            "/ad_account/insights",
            params=_optional_params(
                time_granularity="none",
                aggregation_level=api_level,
                time_ranges=[delivery_range],
                fields=delivery_fields,
                limit=min(max(len(ids), 20), 2000),
            ),
        )
        conversions = await client.post(
            "/conversions/insights",
            json={
                "aggregation_level": api_level,
                "time_granularity": "none",
                "time_ranges": [delivery_range],
                "entity_ids": ids,
            },
        )

        delivery_rows = delivery.get("data") if isinstance(delivery, dict) else []
        conversion_rows = conversions.get("data") if isinstance(conversions, dict) else []
        if not isinstance(delivery_rows, list):
            delivery_rows = []
        if not isinstance(conversion_rows, list):
            conversion_rows = []

        by_id: dict[str, dict[str, Any]] = {}
        for entity_id in ids:
            by_id[entity_id] = {
                "entity_id": entity_id,
                "entity_name": campaign_names.get(entity_id),
                "impressions": 0.0,
                "clicks": 0.0,
                "spend": 0.0,
                "conversions": 0.0,
            }

        for row in delivery_rows:
            if not isinstance(row, dict):
                continue
            entity_id = _entity_id_from_row(row, entity)
            if not entity_id:
                continue
            bucket = by_id.setdefault(
                entity_id,
                {
                    "entity_id": entity_id,
                    "entity_name": None,
                    "impressions": 0.0,
                    "clicks": 0.0,
                    "spend": 0.0,
                    "conversions": 0.0,
                },
            )
            name = _entity_name_from_row(row, entity)
            if name:
                bucket["entity_name"] = name
            for key in ("impressions", "clicks", "spend"):
                metric = _nested_metric(row, entity, key)
                if metric is not None:
                    bucket[key] = float(bucket.get(key) or 0) + metric

        for row in conversion_rows:
            if not isinstance(row, dict):
                continue
            entity_id = row.get("entity_id")
            if not isinstance(entity_id, str) or not entity_id:
                continue
            bucket = by_id.setdefault(
                entity_id,
                {
                    "entity_id": entity_id,
                    "entity_name": campaign_names.get(entity_id),
                    "impressions": 0.0,
                    "clicks": 0.0,
                    "spend": 0.0,
                    "conversions": 0.0,
                },
            )
            conversions_count = _as_float(row.get("conversions"))
            if conversions_count is not None:
                bucket["conversions"] = float(bucket.get("conversions") or 0) + conversions_count

        rows: list[dict[str, Any]] = []
        for entity_id, bucket in by_id.items():
            conversions_count = float(bucket.get("conversions") or 0)
            conversion_value = value_map.get(entity_id) if value_map else None
            if conversion_value is None and average_order_value is not None:
                conversion_value = conversions_count * float(average_order_value)
            rows.append(
                _efficiency_row(
                    entity_id=entity_id,
                    entity_name=bucket.get("entity_name"),
                    impressions=float(bucket.get("impressions") or 0),
                    clicks=float(bucket.get("clicks") or 0),
                    spend=float(bucket.get("spend") or 0),
                    conversions=conversions_count,
                    conversion_value=conversion_value,
                )
            )

        rows.sort(key=lambda item: item.get("spend") or 0, reverse=True)
        totals = _sum_rows(rows)
        notes = [
            "This is ChatGPT Ads / OpenAI Ads reporting, not Google Ads.",
            "Delivery metrics come from /ad_account/insights; conversion counts come from /conversions/insights.",
            "CPA = spend / conversions. conversion_rate = conversions / clicks.",
        ]
        if aggregation_level == "ad_account":
            notes.append(
                "aggregation_level=ad_account is rolled up from campaign rows because "
                "/conversions/insights only accepts campaign, ad_group, or ad."
            )
        if totals.get("conversion_value") is None:
            notes.append(
                "ROAS is null because Ads conversion insights return counts only. "
                "Pass average_order_value or conversion_value_by_entity to estimate revenue."
            )
        if totals.get("conversions") == 0:
            notes.append(
                "No attributed conversions in this window. Confirm manage_conversions event settings "
                "and that send_conversions (or the pixel) is receiving events."
            )

        payload = {
            "aggregation_level": api_level,
            "requested_aggregation_level": aggregation_level,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "timezone": str(tz),
            "applied_time_range": {
                "type": "unix_range",
                "start": start_unix,
                "end": end_unix,
                "duration_hours": (end_unix - start_unix) / 3600,
            },
            "entity_ids": ids,
            "rows": rows,
            "totals": totals,
            "notes": notes,
        }
        return _ok_sized(
            payload,
            response_format,
            follow_up="Pass average_order_value for ROAS, or narrow entity_ids / dates. Ingest events with send_conversions.",
        )
    except OpenAIAdsAPIError as e:
        return _err(e)


__all__ = ("get_performance",)
