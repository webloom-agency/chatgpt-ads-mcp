"""Ad group MCP tools for OpenAI Ads."""

from __future__ import annotations

from ._core import *

_AD_GROUP_STATES = {"activate", "pause", "archive"}
_AD_GROUP_STATUSES = {"active", "paused", "archived"}
_BILLING_EVENTS = {"impression", "click"}
_PRODUCT_SET_FILTER_OPERATORS = {"in", "gt", "gte", "lt", "lte"}
_PRODUCT_SET_FILTER_FIELDS = {
    "title",
    "body",
    "item_id",
    "offer_id",
    "price",
    "target_url",
    "image_url",
    "product_category",
    "brand",
    "seller_name",
    "external_seller_id",
    "star_rating",
    "condition",
    "age_group",
}
_PRODUCT_SET_COMPARISON_FIELDS = {"price", "star_rating"}


def _product_set_payload(product_set: Any) -> tuple[dict[str, Any] | None, str | None]:
    if product_set is None:
        return None, None
    product_set, product_set_err = _coerce_mapping(product_set, "product_set")
    if product_set_err:
        return None, product_set_err
    if feed_err := _validate_non_empty("product_set.product_feed_id", product_set.get("product_feed_id"), maximum=255):
        return None, feed_err
    payload: dict[str, Any] = {"product_feed_id": product_set["product_feed_id"].strip()}
    if product_set.get("filters") is not None:
        filters, filters_err = _coerce_list(product_set.get("filters"), "product_set.filters")
        if filters_err:
            return None, filters_err
        seen_fields: set[str] = set()
        out: list[dict[str, Any]] = []
        for index, item in enumerate(filters or []):
            if not isinstance(item, dict):
                return None, _bad_request(f"product_set.filters[{index}] must be an object.")
            field = item.get("field")
            if not isinstance(field, str) or field.strip() not in _PRODUCT_SET_FILTER_FIELDS:
                return None, _bad_request(f"product_set.filters[{index}].field is not supported.")
            field = field.strip()
            if field in seen_fields:
                return None, _bad_request("product_set.filters must not repeat the same field.")
            seen_fields.add(field)
            operator = item.get("operator")
            if not isinstance(operator, str) or operator.strip() not in _PRODUCT_SET_FILTER_OPERATORS:
                return None, _bad_request("product_set filter operator must be in, gt, gte, lt, or lte.")
            operator = operator.strip()
            if operator != "in" and field not in _PRODUCT_SET_COMPARISON_FIELDS:
                return None, _bad_request("product_set comparison filters may only use price or star_rating.")
            values, values_err = _coerce_string_list(item.get("values"), f"product_set.filters[{index}].values")
            if values_err:
                return None, values_err
            if not values:
                return None, _bad_request(f"product_set.filters[{index}].values must include at least one value.")
            out.append({"field": field, "operator": operator, "values": values})
        payload["filters"] = out
    return payload, None


def _build_ad_group_body(
    *,
    campaign_id: str | None = None,
    name: str | None = None,
    status: str | None = None,
    billing_event: str | None = None,
    max_bid_usd: float | None = None,
    context_hints: Any = None,
    product_set: Any = None,
    create: bool = False,
) -> tuple[dict[str, Any] | None, str | None]:
    body: dict[str, Any] = {}
    if create:
        if campaign_err := _validate_non_empty("campaign_id", campaign_id):
            return None, campaign_err
        body["campaign_id"] = campaign_id
    if name is not None:
        if name_err := _validate_non_empty("name", name, minimum=3, maximum=1000):
            return None, name_err
        body["name"] = name.strip()
    elif create:
        return None, _bad_request("name is required.")
    if status is not None:
        if status not in _AD_GROUP_STATUSES:
            return None, _bad_request("status must be active, paused, or archived.")
        if activation_err := _reject_activation_status(status, "create_ad_group" if create else "update_ad_group"):
            return None, activation_err
        if create and status != "paused":
            return None, _bad_request("Create tools only create paused ad groups. Use set_ad_group_state after review.")
        body["status"] = status
    elif create:
        body["status"] = "paused"
    if billing_event is not None or max_bid_usd is not None:
        if billing_event is None or max_bid_usd is None:
            return None, _bad_request("billing_event and max_bid_usd must be provided together.")
        if billing_event not in _BILLING_EVENTS:
            return None, _bad_request("billing_event must be impression or click.")
        if bid_err := _validate_float_range("max_bid_usd", float(max_bid_usd), 0.000001, 100):
            return None, bid_err
        body["bidding_config"] = {
            "billing_event_type": billing_event,
            "max_bid_micros": _usd_to_micros(float(max_bid_usd)),
        }
    elif create:
        return None, _bad_request("billing_event and max_bid_usd are required.")
    if context_hints is not None:
        hints, hints_err = _coerce_string_list(context_hints, "context_hints")
        if hints_err:
            return None, hints_err
        body["context_hints"] = hints
    parsed_product_set, product_set_err = _product_set_payload(product_set)
    if product_set_err:
        return None, product_set_err
    if parsed_product_set is not None:
        body["product_set"] = parsed_product_set
    if not body:
        return None, _bad_request("Provide at least one field to update.")
    return body, None


@ads_tool(open_world=True)
async def list_ad_groups(
    campaign_id: str,
    limit: int = 20,
    after: str | None = None,
    before: str | None = None,
    order: Literal["asc", "desc"] = "desc",
) -> str:
    """List ad groups, optionally filtered to a campaign.

    Args:
        campaign_id: Required parent campaign id.
        limit: Results per page, 1-500. Default 20.
        after: Optional cursor for forward pagination.
        before: Optional cursor for backward pagination.
        order: Sort order, asc or desc. Default desc.
    """
    if campaign_err := _validate_non_empty("campaign_id", campaign_id):
        return campaign_err
    if limit_err := _validate_int_range("limit", limit, 1, 500):
        return limit_err
    client, client_err = _get_client_or_error()
    if client_err:
        return client_err
    params = _optional_params(campaign_id=campaign_id, limit=limit, after=after, before=before, order=order)
    try:
        return _ok(await client.get("/ad_groups", params=params))
    except OpenAIAdsAPIError as e:
        return _err(e)


@ads_tool(open_world=True)
async def get_ad_group(ad_group_id: str) -> str:
    """Get one ad group by id."""
    if ad_group_err := _validate_non_empty("ad_group_id", ad_group_id):
        return ad_group_err
    client, client_err = _get_client_or_error()
    if client_err:
        return client_err
    try:
        return _ok(await client.get(f"/ad_groups/{ad_group_id}"))
    except OpenAIAdsAPIError as e:
        return _err(e)


@ads_tool(writes=True, open_world=True)
async def create_ad_group(
    campaign_id: str,
    name: str,
    billing_event: Literal["impression", "click"],
    max_bid_usd: float,
    status: Literal["paused"] = "paused",
    context_hints: Any = None,
    product_set: Any = None,
    idempotency_key: str | None = None,
) -> str:
    """Create a paused ad group under a campaign.

    Args:
        campaign_id: Parent campaign id.
        name: Ad group name, 3-1000 characters.
        billing_event: impression or click.
        max_bid_usd: Max bid converted to micros. Must be > 0 and <= 100.
        status: Must be paused. To go live, call set_ad_group_state after review.
        context_hints: Optional list of strings that steer matching context.
    """
    body, body_err = _build_ad_group_body(
        campaign_id=campaign_id,
        name=name,
        status=status,
        billing_event=billing_event,
        max_bid_usd=max_bid_usd,
        context_hints=context_hints,
        product_set=product_set,
        create=True,
    )
    if body_err:
        return body_err
    idempotency_key, idempotency_err = _validate_idempotency_key(idempotency_key)
    if idempotency_err:
        return idempotency_err
    client, client_err = _get_client_or_error()
    if client_err:
        return client_err
    try:
        kwargs = {"json": body}
        if idempotency_key:
            kwargs["idempotency_key"] = idempotency_key
        return _ok(await client.post("/ad_groups", **kwargs))
    except OpenAIAdsAPIError as e:
        return _err(e)


@ads_tool(writes=True, open_world=True)
async def update_ad_group(
    ad_group_id: str,
    name: str | None = None,
    billing_event: Literal["impression", "click"] | None = None,
    max_bid_usd: float | None = None,
    status: Literal["paused", "archived"] | None = None,
    context_hints: Any = None,
    product_set: Any = None,
) -> str:
    """Update ad group fields, including bid and context hints."""
    if ad_group_err := _validate_non_empty("ad_group_id", ad_group_id):
        return ad_group_err
    body, body_err = _build_ad_group_body(
        name=name,
        status=status,
        billing_event=billing_event,
        max_bid_usd=max_bid_usd,
        context_hints=context_hints,
        product_set=product_set,
    )
    if body_err:
        return body_err
    client, client_err = _get_client_or_error()
    if client_err:
        return client_err
    try:
        return _ok(await client.post(f"/ad_groups/{ad_group_id}", json=body))
    except OpenAIAdsAPIError as e:
        return _err(e)


@ads_tool(writes=True, destructive=True, open_world=True)
async def set_ad_group_state(
    ad_group_id: str,
    state: Literal["activate", "pause", "archive"],
) -> str:
    """Activate, pause, or archive an ad group.

    Activation can start delivery when the parent campaign and child ads are
    also active, so call this only after review.
    """
    if ad_group_err := _validate_non_empty("ad_group_id", ad_group_id):
        return ad_group_err
    if state_err := _validate_option("state", state, _AD_GROUP_STATES):
        return state_err
    client, client_err = _get_client_or_error()
    if client_err:
        return client_err
    try:
        return _ok(await client.post(f"/ad_groups/{ad_group_id}/{state}"))
    except OpenAIAdsAPIError as e:
        return _err(e)


__all__ = (
    "list_ad_groups",
    "get_ad_group",
    "create_ad_group",
    "update_ad_group",
    "set_ad_group_state",
    "_build_ad_group_body",
)
