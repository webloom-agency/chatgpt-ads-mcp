import { z } from "zod";

import {
  badRequest,
  coerceJson,
  coerceStringList,
  getClientOrError,
  handleApiError,
  isRecord,
  jsonQueryList,
  okSized,
  optionalParams,
  validateIntRange,
  validateOption,
  type AdsToolDefinition,
  type JsonRecord,
  type ToolArgs,
} from "../core.js";

const INSIGHT_SCOPES = new Set(["account", "campaign", "ad_group", "ad"]);
const TIME_GRANULARITIES = new Set(["hourly", "daily", "monthly", "none"]);
const SEGMENTS = new Set(["product", "country", "device"]);
const FILTER_OPERATORS = new Set(["IN", "GREATER_THAN", "LESS_THAN"]);
const INSIGHT_AGGREGATION_LEVELS: Record<string, Set<string>> = {
  account: new Set(["ad_account", "campaign", "ad_group", "ad"]),
  campaign: new Set(["campaign", "ad_group", "ad"]),
  ad_group: new Set(["ad_group", "ad"]),
  ad: new Set(["ad"]),
};
const SEGMENT_GROUP_ORDER_VALUES = new Set(["ad_account", "campaign", "ad_group", "ad", "product", "country", "device"]);
const INCLUDES = new Set(["zero_impression_items", "zero_impression_products"]);
const TIME_RANGE_TYPES = new Set(["unix_range", "hour_range", "date_range"]);

function insightsPath(scope: string, entityId: unknown): [string | null, string | null] {
  if (scope === "account") return ["/ad_account/insights", null];
  if (!entityId) return [null, badRequest("entity_id is required for campaign, ad_group, and ad insights.")];
  if (scope === "campaign") return [`/campaigns/${entityId}/insights`, null];
  if (scope === "ad_group") return [`/ad_groups/${entityId}/insights`, null];
  if (scope === "ad") return [`/ads/${entityId}/insights`, null];
  return [null, badRequest("scope must be account, campaign, ad_group, or ad.")];
}

function normalizeTimeRange(value: JsonRecord): [JsonRecord | null, string | null] {
  if (typeof value.type === "string") {
    if (!TIME_RANGE_TYPES.has(value.type)) {
      return [null, badRequest("time_range.type must be unix_range, hour_range, or date_range.")];
    }
    return [value, null];
  }

  const legacyKeys = [...TIME_RANGE_TYPES].filter((key) => isRecord(value[key]));
  if (legacyKeys.length !== 1) {
    return [null, badRequest("time_range must include type: unix_range, hour_range, or date_range.")];
  }
  const type = legacyKeys[0];
  return [{ type, ...(value[type] as JsonRecord) }, null];
}

function oneTimeRange(value: unknown): [string[] | null, string | null] {
  if (value === undefined || value === null) return [null, null];
  if (typeof value === "string") {
    const [parsed, error] = coerceJson(value, "time_range");
    if (error) return [null, error];
    value = parsed;
  }
  if (isRecord(value)) {
    const [normalized, error] = normalizeTimeRange(value);
    return error ? [null, error] : [[JSON.stringify(normalized)], null];
  }
  if (Array.isArray(value)) {
    if (value.length !== 1) return [null, badRequest("time_range accepts one range object.")];
    let item = value[0];
    if (typeof item === "string") {
      const [parsed, error] = coerceJson(item, "time_range");
      if (error) return [null, error];
      item = parsed;
    }
    if (!isRecord(item)) return [null, badRequest("time_range must contain an object.")];
    const [normalized, error] = normalizeTimeRange(item);
    return error ? [null, error] : [[JSON.stringify(normalized)], null];
  }
  return [null, badRequest("time_range must be an object or JSON object string.")];
}

function validateFilters(value: unknown): [string[] | null, string | null] {
  const [encoded, error] = jsonQueryList(value, "filters");
  if (error || encoded === null) return [null, error];
  for (const item of encoded) {
    const parsed = JSON.parse(item) as Record<string, unknown>;
    if (!FILTER_OPERATORS.has(String(parsed.operator))) return [null, badRequest("filter operator must be IN, GREATER_THAN, or LESS_THAN.")];
    if (!("field" in parsed) || !("value" in parsed)) return [null, badRequest("Each filter must include field, operator, and value.")];
  }
  return [encoded, null];
}

function validateSort(value: unknown): [string[] | null, string | null] {
  const [encoded, error] = jsonQueryList(value, "sort");
  if (error || encoded === null) return [null, error];
  for (const item of encoded) {
    const parsed = JSON.parse(item) as Record<string, unknown>;
    if (!["asc", "desc"].includes(String(parsed.direction))) return [null, badRequest("sort direction must be asc or desc.")];
    if (!("field" in parsed)) return [null, badRequest("Each sort entry must include field and direction.")];
  }
  return [encoded, null];
}

async function getInsights(args: ToolArgs): Promise<string> {
  const scope = String(args.scope);
  const scopeError = validateOption("scope", scope, INSIGHT_SCOPES);
  if (scopeError) return scopeError;
  const timeGranularity = String(args.time_granularity ?? "daily");
  const granularityError = validateOption("time_granularity", timeGranularity, TIME_GRANULARITIES);
  if (granularityError) return granularityError;
  const aggregationLevel = args.aggregation_level === undefined || args.aggregation_level === null ? undefined : String(args.aggregation_level);
  if (aggregationLevel) {
    const aggregationError = validateOption("aggregation_level", aggregationLevel, INSIGHT_AGGREGATION_LEVELS[scope]);
    if (aggregationError) return aggregationError;
  }
  const limit = Number(args.limit ?? 20);
  const limitError = validateIntRange("limit", limit, 1, 2000);
  if (limitError) return limitError;
  const [path, pathError] = insightsPath(scope, args.entity_id);
  if (pathError) return pathError;
  const [timeRanges, timeError] = oneTimeRange(args.time_range);
  if (timeError) return timeError;
  const [segments, segmentsError] = coerceStringList(args.segments, "segments");
  if (segmentsError) return segmentsError;
  if (segments && segments.length > 1) return badRequest("segments supports at most one value.");
  if (segments?.length) {
    const unknown = segments.filter((segment) => !SEGMENTS.has(segment));
    if (unknown.length) return badRequest(`Invalid segments: ${unknown.join(", ")}.`);
    if (timeGranularity === "hourly") return badRequest("Segmented insights support time_granularity of none, daily, or monthly.");
  }
  const [fields, fieldsError] = coerceStringList(args.fields, "fields");
  if (fieldsError) return fieldsError;
  if (segments?.[0] === "product" && !(fields?.includes("product.feed_id") || fields?.includes("product.item_id"))) {
    return badRequest("product segments require fields to include product.feed_id or product.item_id.");
  }
  const [overrideSegmentGroupOrder, overrideError] = coerceStringList(args.override_segment_group_order, "override_segment_group_order");
  if (overrideError) return overrideError;
  if (overrideSegmentGroupOrder?.length) {
    const unknown = overrideSegmentGroupOrder.filter((item) => !SEGMENT_GROUP_ORDER_VALUES.has(item));
    if (unknown.length) return badRequest(`Invalid override_segment_group_order values: ${unknown.join(", ")}.`);
    if (!aggregationLevel) return badRequest("aggregation_level is required when override_segment_group_order is provided.");
    if (!segments?.length) return badRequest("segments is required when override_segment_group_order is provided.");
    const segment = segments[0];
    if (
      overrideSegmentGroupOrder.length !== 2 ||
      overrideSegmentGroupOrder.filter((item) => item === aggregationLevel).length !== 1 ||
      overrideSegmentGroupOrder.filter((item) => item === segment).length !== 1
    ) {
      return badRequest("override_segment_group_order must include the aggregation_level and requested segment exactly once.");
    }
  }
  const [includes, includesError] = coerceStringList(args.includes, "includes");
  if (includesError) return includesError;
  if (includes?.length) {
    if (includes.length > 1) return badRequest("includes supports at most one value.");
    const unknown = includes.filter((include) => !INCLUDES.has(include));
    if (unknown.length) return badRequest(`Invalid includes: ${unknown.join(", ")}.`);
    if (includes.includes("zero_impression_items") && segments?.length) {
      return badRequest("zero_impression_items cannot be used with segments.");
    }
    if (includes.includes("zero_impression_products") && (segments?.[0] !== "product" || overrideSegmentGroupOrder?.[0] !== "product")) {
      return badRequest("zero_impression_products requires segments=product and product first in override_segment_group_order.");
    }
  }
  const [filters, filtersError] = validateFilters(args.filters);
  if (filtersError) return filtersError;
  const [sort, sortError] = validateSort(args.sort);
  if (sortError) return sortError;
  const { client, error } = getClientOrError();
  if (error) return error;
  try {
    return okSized(
      await client!.get(path!, optionalParams({
        time_granularity: timeGranularity,
        aggregation_level: aggregationLevel,
        time_ranges: timeRanges,
        segments,
        override_segment_group_order: overrideSegmentGroupOrder,
        includes,
        fields,
        filters,
        sort,
        limit,
        after: args.after,
        before: args.before,
      })),
      String(args.response_format ?? "concise"),
      "Use after or before cursors, narrow time_range, or request fewer fields.",
    );
  } catch (apiError) {
    return handleApiError(apiError);
  }
}

export const insightTools: AdsToolDefinition[] = [
  {
    name: "get_insights",
    description:
      "Get performance insights for account, campaign, ad group, or ad scope. Supports fields, filters, sort, product/country/device segments, time ranges, and cursor pagination.",
    inputSchema: {
      scope: z.enum(["account", "campaign", "ad_group", "ad"]),
      entity_id: z.string().optional(),
      time_granularity: z.enum(["hourly", "daily", "monthly", "none"]).default("daily"),
      aggregation_level: z.enum(["ad_account", "campaign", "ad_group", "ad"]).optional(),
      time_range: z.any().optional(),
      segments: z.any().optional(),
      override_segment_group_order: z.any().optional(),
      includes: z.any().optional(),
      fields: z.any().optional(),
      filters: z.any().optional(),
      sort: z.any().optional(),
      limit: z.number().int().default(20),
      after: z.string().optional(),
      before: z.string().optional(),
      response_format: z.enum(["concise", "detailed"]).default("concise"),
    },
    argNames: ["scope", "entity_id", "time_granularity", "aggregation_level", "time_range", "segments", "override_segment_group_order", "includes", "fields", "filters", "sort", "limit", "after", "before", "response_format"],
    openWorld: true,
    handler: getInsights,
  },
];
