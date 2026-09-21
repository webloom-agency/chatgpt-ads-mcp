import { z } from "zod";

import {
  badRequest,
  coerceJson,
  coerceStringList,
  getClientOrError,
  handleApiError,
  isRecord,
  okSized,
  optionalParams,
  type AdsClientLike,
  type AdsToolDefinition,
  type JsonRecord,
  type ToolArgs,
} from "../core.js";

const PERF_LEVELS = new Set(["campaign", "ad_account"]);
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

function parseDate(name: string, value: unknown): [string | null, string | null] {
  if (value === undefined || value === null) return [null, null];
  if (typeof value !== "string" || !DATE_RE.test(value.trim())) {
    return [null, badRequest(`${name} must be YYYY-MM-DD.`)];
  }
  const trimmed = value.trim();
  const parsed = new Date(`${trimmed}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== trimmed) {
    return [null, badRequest(`${name} must be a valid calendar date.`)];
  }
  return [trimmed, null];
}

function asFloat(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function safeDiv(numerator: number | null | undefined, denominator: number | null | undefined): number | null {
  if (numerator === null || numerator === undefined || denominator === null || denominator === undefined || denominator === 0) {
    return null;
  }
  return numerator / denominator;
}

function nestedMetric(row: JsonRecord, entity: string, key: string): number | null {
  const direct = asFloat(row[key]);
  if (direct !== null) return direct;
  const dotted = asFloat(row[`${entity}.${key}`]);
  if (dotted !== null) return dotted;
  const nested = row[entity];
  if (isRecord(nested)) {
    const nestedValue = asFloat(nested[key]);
    if (nestedValue !== null) return nestedValue;
  }
  return asFloat(row[`${entity}_${key}`]);
}

function entityIdFromRow(row: JsonRecord, entity: string): string | null {
  const nested = row[entity];
  if (isRecord(nested) && typeof nested.id === "string") return nested.id;
  for (const key of [`${entity}.id`, `${entity}_id`, "entity_id", "id"]) {
    const value = row[key];
    if (typeof value === "string" && value) return value;
  }
  return null;
}

function entityNameFromRow(row: JsonRecord, entity: string): string | null {
  const nested = row[entity];
  if (isRecord(nested) && typeof nested.name === "string") return nested.name;
  for (const key of [`${entity}.name`, `${entity}_name`, "name"]) {
    const value = row[key];
    if (typeof value === "string" && value) return value;
  }
  return null;
}

function conversionValueMap(value: unknown): [Record<string, number> | null, string | null] {
  if (value === undefined || value === null) return [{}, null];
  const [parsed, error] = coerceJson(value, "conversion_value_by_entity");
  if (error) return [null, error];
  if (isRecord(parsed)) {
    const out: Record<string, number> = {};
    for (const [key, amount] of Object.entries(parsed)) {
      const amountValue = asFloat(amount);
      if (amountValue === null) return [null, badRequest("conversion_value_by_entity values must be numbers.")];
      out[String(key)] = amountValue;
    }
    return [out, null];
  }
  if (Array.isArray(parsed)) {
    const out: Record<string, number> = {};
    for (const [index, item] of parsed.entries()) {
      if (!isRecord(item)) return [null, badRequest(`conversion_value_by_entity[${index}] must be an object.`)];
      const entityId = item.entity_id ?? item.id;
      const amountValue = asFloat(item.value ?? item.conversion_value);
      if (typeof entityId !== "string" || !entityId) {
        return [null, badRequest(`conversion_value_by_entity[${index}].entity_id is required.`)];
      }
      if (amountValue === null) return [null, badRequest(`conversion_value_by_entity[${index}].value must be a number.`)];
      out[entityId] = amountValue;
    }
    return [out, null];
  }
  return [null, badRequest("conversion_value_by_entity must be an object or list of {entity_id, value}.")];
}

function efficiencyRow(input: {
  entityId: string;
  entityName: string | null;
  impressions: number;
  clicks: number;
  spend: number;
  conversions: number;
  conversionValue: number | null;
}): JsonRecord {
  return {
    entity_id: input.entityId,
    entity_name: input.entityName,
    impressions: input.impressions,
    clicks: input.clicks,
    spend: input.spend,
    conversions: input.conversions,
    conversion_value: input.conversionValue,
    ctr: safeDiv(input.clicks, input.impressions),
    cpc: safeDiv(input.spend, input.clicks),
    cpm: safeDiv(input.spend * 1000, input.impressions),
    conversion_rate: safeDiv(input.conversions, input.clicks),
    cpa: safeDiv(input.spend, input.conversions),
    roas: safeDiv(input.conversionValue, input.spend),
    revenue_per_click: safeDiv(input.conversionValue, input.clicks),
  };
}

function sumRows(rows: JsonRecord[]): JsonRecord {
  const impressions = rows.reduce((sum, row) => sum + Number(row.impressions || 0), 0);
  const clicks = rows.reduce((sum, row) => sum + Number(row.clicks || 0), 0);
  const spend = rows.reduce((sum, row) => sum + Number(row.spend || 0), 0);
  const conversions = rows.reduce((sum, row) => sum + Number(row.conversions || 0), 0);
  const values = rows.map((row) => asFloat(row.conversion_value)).filter((value): value is number => value !== null);
  return efficiencyRow({
    entityId: "totals",
    entityName: "totals",
    impressions,
    clicks,
    spend,
    conversions,
    conversionValue: values.length ? values.reduce((sum, value) => sum + value, 0) : null,
  });
}

function addDays(isoDate: string, days: number): string {
  const date = new Date(`${isoDate}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function defaultWindow(timeZone: string): [string, string] {
  const formatter = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  const today = formatter.format(new Date());
  return [addDays(today, -6), today];
}

function zonedMidnightUnix(isoDate: string, timeZone: string): number {
  const probe = new Date(`${isoDate}T12:00:00Z`);
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    timeZoneName: "longOffset",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).formatToParts(probe);
  const get = (type: string) => parts.find((part) => part.type === type)?.value;
  const offset = get("timeZoneName") ?? "GMT";
  const match = offset.match(/GMT([+-])(\d{1,2})(?::?(\d{2}))?/);
  let offsetMinutes = 0;
  if (match) {
    const sign = match[1] === "-" ? -1 : 1;
    offsetMinutes = sign * (Number(match[2]) * 60 + Number(match[3] ?? "0"));
  }
  const utcGuess = Date.UTC(Number(isoDate.slice(0, 4)), Number(isoDate.slice(5, 7)) - 1, Number(isoDate.slice(8, 10)));
  return Math.floor((utcGuess - offsetMinutes * 60_000) / 1000);
}

async function listCampaignIds(client: AdsClientLike): Promise<[string[], Record<string, string>]> {
  const names: Record<string, string> = {};
  const ids: string[] = [];
  let after: string | undefined;
  for (let page = 0; page < 20; page += 1) {
    const payload = await client.get("/campaigns", optionalParams({ limit: 200, order: "desc", after }));
    const rows = isRecord(payload) && Array.isArray(payload.data) ? payload.data : [];
    for (const row of rows) {
      if (!isRecord(row) || typeof row.id !== "string") continue;
      ids.push(row.id);
      if (typeof row.name === "string") names[row.id] = row.name;
    }
    if (!isRecord(payload) || !payload.has_more || typeof payload.last_id !== "string") break;
    after = payload.last_id;
  }
  return [ids, names];
}

async function getPerformance(args: ToolArgs): Promise<string> {
  const aggregationLevel = String(args.aggregation_level ?? "campaign");
  if (!PERF_LEVELS.has(aggregationLevel)) {
    return badRequest("aggregation_level must be campaign or ad_account.");
  }
  if (args.average_order_value !== undefined && args.average_order_value !== null && Number(args.average_order_value) < 0) {
    return badRequest("average_order_value must be >= 0.");
  }
  const [valueMap, valueError] = conversionValueMap(args.conversion_value_by_entity);
  if (valueError || !valueMap) return valueError ?? badRequest("conversion_value_by_entity is invalid.");
  const [startDate, startError] = parseDate("start_date", args.start_date);
  if (startError) return startError;
  const [endDate, endError] = parseDate("end_date", args.end_date);
  if (endError) return endError;
  if ((startDate === null) !== (endDate === null)) {
    return badRequest("Provide both start_date and end_date, or omit both for the last 7 days.");
  }
  if (startDate && endDate && endDate < startDate) {
    return badRequest("end_date must be on or after start_date.");
  }
  const [entityIdsInput, entityIdsError] = coerceStringList(args.entity_ids, "entity_ids");
  if (entityIdsError) return entityIdsError;

  const { client, error } = getClientOrError();
  if (error) return error;

  try {
    const account = await client!.get("/ad_account");
    const timezoneName = isRecord(account) && typeof account.timezone === "string" ? account.timezone : "UTC";
    const accountId = isRecord(account) && typeof account.id === "string" ? account.id : null;
    let start = startDate;
    let end = endDate;
    if (!start || !end) {
      [start, end] = defaultWindow(timezoneName);
    }

    let startUnix = zonedMidnightUnix(start, timezoneName);
    let endUnix = zonedMidnightUnix(addDays(end, 1), timezoneName);
    const nowUnix = Math.floor(Date.now() / 1000);
    if (endUnix > nowUnix + 3600) {
      endUnix = nowUnix - (nowUnix % 3600) + 3600;
    }
    if (endUnix <= startUnix) {
      return badRequest("Resolved time window is empty. Check start_date/end_date.");
    }

    let campaignNames: Record<string, string> = {};
    let ids = entityIdsInput ?? [];
    if (!ids.length) {
      if (aggregationLevel === "ad_account") {
        if (!accountId) return badRequest("Could not resolve ad account id from get_account.");
        ids = [accountId];
      } else {
        [ids, campaignNames] = await listCampaignIds(client!);
        if (!ids.length) {
          return okSized({
            aggregation_level: aggregationLevel,
            start_date: start,
            end_date: end,
            timezone: timezoneName,
            rows: [],
            totals: efficiencyRow({
              entityId: "totals",
              entityName: "totals",
              impressions: 0,
              clicks: 0,
              spend: 0,
              conversions: 0,
              conversionValue: null,
            }),
            notes: ["No campaigns found. Create or activate campaigns before reading performance."],
          }, String(args.response_format ?? "concise"));
        }
      }
    }

    const entity = aggregationLevel;
    const deliveryFields = [
      `${entity}.id`,
      `${entity}.name`,
      `${entity}.impressions`,
      `${entity}.clicks`,
      `${entity}.spend`,
      `${entity}.ctr`,
      `${entity}.cpc`,
      `${entity}.cpm`,
    ];
    const delivery = await client!.get("/ad_account/insights", optionalParams({
      time_granularity: "none",
      aggregation_level: aggregationLevel,
      time_ranges: [JSON.stringify({ type: "unix_range", start: startUnix, end: endUnix })],
      fields: deliveryFields,
      limit: Math.min(Math.max(ids.length, 20), 2000),
    }));
    const conversions = await client!.post("/conversions/insights", {
      aggregation_level: aggregationLevel,
      time_ranges: [`${start}:${end}`],
      entity_ids: ids,
    });

    const deliveryRows = isRecord(delivery) && Array.isArray(delivery.data) ? delivery.data : [];
    const conversionRows = isRecord(conversions) && Array.isArray(conversions.data) ? conversions.data : [];
    const byId = new Map<string, JsonRecord>();
    for (const entityId of ids) {
      byId.set(entityId, {
        entity_id: entityId,
        entity_name: campaignNames[entityId] ?? null,
        impressions: 0,
        clicks: 0,
        spend: 0,
        conversions: 0,
      });
    }

    for (const row of deliveryRows) {
      if (!isRecord(row)) continue;
      const entityId = entityIdFromRow(row, entity);
      if (!entityId) continue;
      const bucket = byId.get(entityId) ?? {
        entity_id: entityId,
        entity_name: null,
        impressions: 0,
        clicks: 0,
        spend: 0,
        conversions: 0,
      };
      const name = entityNameFromRow(row, entity);
      if (name) bucket.entity_name = name;
      for (const key of ["impressions", "clicks", "spend"] as const) {
        const metric = nestedMetric(row, entity, key);
        if (metric !== null) bucket[key] = Number(bucket[key] || 0) + metric;
      }
      byId.set(entityId, bucket);
    }

    for (const row of conversionRows) {
      if (!isRecord(row) || typeof row.entity_id !== "string") continue;
      const bucket = byId.get(row.entity_id) ?? {
        entity_id: row.entity_id,
        entity_name: campaignNames[row.entity_id] ?? null,
        impressions: 0,
        clicks: 0,
        spend: 0,
        conversions: 0,
      };
      const count = asFloat(row.conversions);
      if (count !== null) bucket.conversions = Number(bucket.conversions || 0) + count;
      byId.set(row.entity_id, bucket);
    }

    const averageOrderValue = args.average_order_value === undefined || args.average_order_value === null
      ? null
      : Number(args.average_order_value);
    const rows = [...byId.values()].map((bucket) => {
      const conversionsCount = Number(bucket.conversions || 0);
      let conversionValue = valueMap[String(bucket.entity_id)] ?? null;
      if (conversionValue === null && averageOrderValue !== null) {
        conversionValue = conversionsCount * averageOrderValue;
      }
      return efficiencyRow({
        entityId: String(bucket.entity_id),
        entityName: typeof bucket.entity_name === "string" ? bucket.entity_name : null,
        impressions: Number(bucket.impressions || 0),
        clicks: Number(bucket.clicks || 0),
        spend: Number(bucket.spend || 0),
        conversions: conversionsCount,
        conversionValue,
      });
    }).sort((left, right) => Number(right.spend || 0) - Number(left.spend || 0));

    const totals = sumRows(rows);
    const notes = [
      "Delivery metrics come from /ad_account/insights; conversion counts come from /conversions/insights.",
      "CPA = spend / conversions. conversion_rate = conversions / clicks.",
    ];
    if (totals.conversion_value === null) {
      notes.push(
        "ROAS is null because Ads conversion insights return counts only. Pass average_order_value or conversion_value_by_entity to estimate revenue.",
      );
    }
    if (totals.conversions === 0) {
      notes.push(
        "No attributed conversions in this window. Confirm manage_conversions event settings and that send_conversions (or the pixel) is receiving events.",
      );
    }

    return okSized({
      aggregation_level: aggregationLevel,
      start_date: start,
      end_date: end,
      timezone: timezoneName,
      applied_time_range: {
        type: "unix_range",
        start: startUnix,
        end: endUnix,
        duration_hours: (endUnix - startUnix) / 3600,
      },
      entity_ids: ids,
      rows,
      totals,
      notes,
    }, String(args.response_format ?? "concise"), "Pass average_order_value for ROAS, or narrow entity_ids / dates. Ingest events with send_conversions.");
  } catch (apiError) {
    return handleApiError(apiError);
  }
}

export const performanceTools: AdsToolDefinition[] = [
  {
    name: "get_performance",
    description:
      "Join ChatGPT Ads delivery spend with attributed conversion counts and compute CPA, conversion_rate, and ROAS. Delivery insights have no conversions; this tool calls both APIs. Pass average_order_value or conversion_value_by_entity for ROAS. Dates are inclusive YYYY-MM-DD in the account timezone (default last 7 days).",
    inputSchema: {
      aggregation_level: z.enum(["campaign", "ad_account"]).default("campaign"),
      start_date: z.string().optional(),
      end_date: z.string().optional(),
      entity_ids: z.any().optional(),
      average_order_value: z.number().optional(),
      conversion_value_by_entity: z.any().optional(),
      response_format: z.enum(["concise", "detailed"]).default("concise"),
    },
    argNames: [
      "aggregation_level",
      "start_date",
      "end_date",
      "entity_ids",
      "average_order_value",
      "conversion_value_by_entity",
      "response_format",
    ],
    openWorld: true,
    handler: getPerformance,
  },
];
