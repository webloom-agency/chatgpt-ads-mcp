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
const INSIGHT_METRICS = ["impressions", "clicks", "spend", "ctr", "cpc", "cpm"];
const INSIGHT_METRIC_ENTITIES = ["ad_account", "campaign", "ad_group", "ad", "product", "country", "device"];
const INSIGHT_FIELDS = new Set([
  ...INSIGHT_METRIC_ENTITIES.flatMap((entity) => INSIGHT_METRICS.map((metric) => `${entity}.${metric}`)),
  "ad_account.id",
  "ad_account.name",
  "ad_account.url",
  "ad_account.budget.daily",
  "ad_account.budget.lifetime",
  "campaign.id",
  "campaign.name",
  "campaign.description",
  "campaign.status",
  "campaign.start_time",
  "campaign.end_time",
  "campaign.budget.daily",
  "campaign.budget.lifetime",
  "ad_group.id",
  "ad_group.name",
  "ad_group.description",
  "ad_group.status",
  "ad.id",
  "ad.name",
  "ad.title",
  "ad.copy",
  "ad.link",
  "ad.status",
  "ad.review_status",
  "product.feed_id",
  "product.item_id",
  "product.title",
  "product.description",
  "product.body",
  "product.target_url",
  "product.image_url",
  "product.brand",
  "product.seller_name",
  "product.price",
  "product.availability",
  "country.name",
  "device.type",
  "metadata.readable_time",
  "metadata.timezone",
]);
const SNAKE_FIELD_PREFIXES = ["ad_account", "ad_group", "campaign", "product", "country", "device", "metadata", "ad"];
const FIELD_ENTITY_BY_SCOPE: Record<string, string> = {
  account: "campaign",
  campaign: "campaign",
  ad_group: "ad_group",
  ad: "ad",
};
const TIME_BUCKET_FIELDS = new Set(["metadata.readable_time", "metadata.timezone"]);
const AGGREGATION_ENTITIES = new Set(["ad_account", "campaign", "ad_group", "ad"]);

function fieldEntity(scope: string, aggregationLevel: string | undefined): string {
  return aggregationLevel ?? FIELD_ENTITY_BY_SCOPE[scope];
}

function defaultInsightFields(entity: string, includeTime = true): string[] {
  const fields = [
    `${entity}.id`,
    `${entity}.name`,
    ...INSIGHT_METRICS.map((metric) => `${entity}.${metric}`),
  ];
  if (includeTime) {
    fields.push("metadata.readable_time", "metadata.timezone");
  }
  return fields.filter((field) => INSIGHT_FIELDS.has(field));
}

function metricEntities(fields: string[]): Set<string> {
  const found = new Set<string>();
  for (const field of fields) {
    const [entity, rest] = field.split(".");
    if (entity && rest && AGGREGATION_ENTITIES.has(entity) && INSIGHT_METRICS.includes(rest)) {
      found.add(entity);
    }
  }
  return found;
}

function resolveAggregation(
  scope: string,
  aggregationLevel: string | undefined,
  fields: string[],
  hasSegments: boolean,
): [string | undefined, string | null] {
  const entities = metricEntities(fields);
  if (entities.size > 1) {
    return [undefined, badRequest(
      "Metric fields must use one grain. Use ad_account.* with aggregation_level=ad_account " +
      "for an account total, or campaign.* with aggregation_level=campaign for a campaign breakdown.",
    )];
  }
  const metricEntity = entities.values().next().value as string | undefined;
  if (aggregationLevel && metricEntity && metricEntity !== aggregationLevel) {
    return [undefined, badRequest(
      `fields use ${metricEntity}.* metrics but aggregation_level is ${aggregationLevel}. ` +
      `Use ${aggregationLevel}.impressions or set aggregation_level=${metricEntity}.`,
    )];
  }
  if (!aggregationLevel && scope === "account" && !hasSegments) {
    return [metricEntity ?? "campaign", null];
  }
  return [aggregationLevel, null];
}

function withoutTimeBucketFields(fields: string[], timeGranularity: string): [string[], boolean] {
  if (timeGranularity !== "none") return [fields, false];
  const kept = fields.filter((field) => !TIME_BUCKET_FIELDS.has(field));
  return [kept, kept.length !== fields.length];
}

function canonicalInsightField(field: string, entity: string): string | null {
  if (INSIGHT_FIELDS.has(field)) return field;
  if (INSIGHT_METRICS.includes(field)) {
    const candidate = `${entity}.${field}`;
    return INSIGHT_FIELDS.has(candidate) ? candidate : null;
  }
  if (field === "readable_time" || field === "timezone") return `metadata.${field}`;
  if (field === "item_id") return "product.item_id";
  if (field === "feed_id") return "product.feed_id";
  for (const prefix of SNAKE_FIELD_PREFIXES) {
    const head = `${prefix}_`;
    if (field.startsWith(head)) {
      const rest = field.slice(head.length).replace("budget_daily", "budget.daily").replace("budget_lifetime", "budget.lifetime");
      const candidate = `${prefix}.${rest}`;
      return INSIGHT_FIELDS.has(candidate) ? candidate : null;
    }
  }
  return null;
}

function normalizeInsightFields(fields: string[], entity: string): [string[] | null, string | null] {
  const canonical: string[] = [];
  const unknown: string[] = [];
  for (const field of fields) {
    const mapped = canonicalInsightField(field, entity);
    if (!mapped) unknown.push(field);
    else if (!canonical.includes(mapped)) canonical.push(mapped);
  }
  if (unknown.length) {
    return [null, badRequest(
      "Unknown ChatGPT Ads insight fields: " +
      unknown.join(", ") +
      ". Use dotted fields such as campaign.id, campaign.name, campaign.impressions, " +
      "campaign.clicks, campaign.spend, campaign.ctr, campaign.cpc, campaign.cpm, " +
      "metadata.readable_time, and metadata.timezone. Shorthand impressions, clicks, spend, " +
      "ctr, cpc, and cpm are rewritten using the aggregation entity. " +
      "get_insights does not return conversions, conversion_rate, conversion_value, or roas.",
    )];
  }
  return [canonical, null];
}

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
  const entity = fieldEntity(scope, aggregationLevel);
  let normalizedFields = fields;
  if (!normalizedFields) {
    normalizedFields = defaultInsightFields(entity, timeGranularity !== "none");
  } else {
    const [mapped, normalizeError] = normalizeInsightFields(normalizedFields, entity);
    if (normalizeError || !mapped) return normalizeError ?? badRequest("fields could not be normalized.");
    normalizedFields = mapped;
  }
  const [fieldsWithoutTime] = withoutTimeBucketFields(normalizedFields, timeGranularity);
  normalizedFields = fieldsWithoutTime;
  const [resolvedAggregation, aggregationResolveError] = resolveAggregation(
    scope,
    aggregationLevel,
    normalizedFields,
    Boolean(segments?.length),
  );
  if (aggregationResolveError) return aggregationResolveError;
  const requestAggregation = resolvedAggregation;
  if (segments?.[0] === "product" && !(normalizedFields?.includes("product.feed_id") || normalizedFields?.includes("product.item_id"))) {
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
        aggregation_level: requestAggregation,
        time_ranges: timeRanges,
        segments,
        override_segment_group_order: overrideSegmentGroupOrder,
        includes,
        fields: normalizedFields,
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
      "Get ChatGPT Ads performance insights for an account, campaign, ad group, or ad. This is OpenAI Ads, not Google Ads. Use dotted fields such as campaign.impressions, campaign.clicks, campaign.spend, campaign.ctr, campaign.cpc, campaign.cpm, campaign.id, and campaign.name. Account totals use aggregation_level=ad_account with ad_account.* metrics. Campaign breakdowns use aggregation_level=campaign with campaign.* metrics. Do not mix those grains. time_granularity=none is one total and cannot include metadata.readable_time. A calendar week starts Monday 00:00 in the account timezone and ends at the current hour; on Monday that window is only the current day. Use time_granularity=daily for a multi-day week. Shorthand impressions, clicks, spend, ctr, cpc, and cpm are rewritten to the aggregation entity. conversions, conversion_rate, conversion_value, and roas are not insight fields.",
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
