import { z } from "zod";
import { isIP } from "node:net";

import {
  badRequest,
  coerceList,
  coerceStringList,
  conversionTimeBoundsMs,
  getClientOrError,
  handleApiError,
  isRecord,
  isReadonlyMode,
  ok,
  optionalParams,
  validateIntRange,
  validateNonEmpty,
  type AdsToolDefinition,
  type JsonRecord,
  type ToolArgs,
} from "../core.js";

const ACTION_SOURCES = new Set(["web", "mobile_app", "offline", "physical_store", "phone_call", "email", "other"]);
const CONVERSION_READ_ACTIONS = new Set(["get_event_settings", "get_insights"]);
const SUPPORTED_EVENT_DATA_TYPES: Record<string, string> = {
  app_installed: "customer_action",
  app_opened: "customer_action",
  appointment_scheduled: "customer_action",
  checkout_started: "contents",
  contents_viewed: "contents",
  custom: "custom",
  items_added: "contents",
  lead_created: "customer_action",
  order_created: "contents",
  page_viewed: "contents",
  registration_completed: "customer_action",
  subscription_created: "plan_enrollment",
  trial_started: "plan_enrollment",
};
const BUILT_IN_EVENT_NAMES = new Set(Object.keys(SUPPORTED_EVENT_DATA_TYPES).filter((name) => name !== "custom"));
const USER_FIELDS = new Set(["email_sha256", "external_id_sha256", "country", "city", "zip_code", "ip_address", "user_agent", "obref"]);
const EVENT_DATA_FIELDS: Record<string, Set<string>> = {
  contents: new Set(["type", "amount", "currency", "contents"]),
  customer_action: new Set(["type", "amount", "currency"]),
  plan_enrollment: new Set(["type", "plan_id", "amount", "currency", "contents"]),
  custom: new Set(["type", "plan_id", "amount", "currency", "contents"]),
};
const CONTENT_FIELDS = new Set(["id", "name", "content_type", "quantity", "amount", "currency"]);
const HASH_RE = /^[a-f0-9]{64}$/;
const CUSTOM_EVENT_NAME_RE = /^[a-z0-9_-]{1,64}$/;
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function sourceIdsPayload(sourceIds: unknown): [string[] | null, string | null] {
  const [ids, error] = coerceStringList(sourceIds, "source_ids");
  if (error) return [null, error];
  if (!ids?.length) return [null, badRequest("source_ids must include at least one id.")];
  return [ids, null];
}

async function manageConversions(args: ToolArgs): Promise<string> {
  if (isReadonlyMode() && !CONVERSION_READ_ACTIONS.has(String(args.action))) {
    return badRequest("OPENAI_ADS_MCP_READONLY=1 permits only get_event_settings and get_insights for manage_conversions.");
  }
  const { client, error } = getClientOrError();
  if (error) return error;
  try {
    if (args.action === "create_pixel") {
      const nameError = validateNonEmpty("name", args.name, 3, 1000);
      if (nameError) return nameError;
      return ok(await client!.post("/conversions/pixels", { name: args.name, client_type: args.client_type ?? "web" }));
    }
    if (args.action === "create_api_key") {
      const nameError = validateNonEmpty("name", args.name, 3, 1000);
      if (nameError) return nameError;
      return ok(await client!.post("/conversions/api_keys", { name: args.name }));
    }
    if (args.action === "get_event_settings") {
      const limit = Number(args.limit ?? 20);
      const limitError = validateIntRange("limit", limit, 1, 500);
      if (limitError) return limitError;
      return ok(await client!.get("/conversions/event_settings", optionalParams({
        limit,
        after: args.after,
        before: args.before,
        order: args.order ?? "desc",
      })));
    }
    if (args.action === "set_event_settings") {
      const nameError = validateNonEmpty("name", args.name, 1, 1000);
      if (nameError) return nameError;
      const eventError = validateNonEmpty("event_type", args.event_type, 1, 100);
      if (eventError) return eventError;
      const eventType = String(args.event_type);
      if (!(eventType in SUPPORTED_EVENT_DATA_TYPES)) {
        return badRequest(`event_type must be one of ${Object.keys(SUPPORTED_EVENT_DATA_TYPES).sort().join(", ")}.`);
      }
      if (eventType === "custom") {
        const customNameError = validateCustomEventName(args.custom_event_name, "custom_event_name");
        if (customNameError) return customNameError;
      } else if (args.custom_event_name !== undefined && args.custom_event_name !== null) {
        const customNameError = validateCustomEventName(args.custom_event_name, "custom_event_name");
        if (customNameError) return customNameError;
      }
      const attributionWindowDays = Number(args.attribution_window_days);
      if (!Number.isFinite(attributionWindowDays) || attributionWindowDays < 1) {
        return badRequest("attribution_window_days must be at least 1.");
      }
      const [sourceIds, sourceIdsError] = sourceIdsPayload(args.source_ids);
      if (sourceIdsError) return sourceIdsError;
      const body: JsonRecord = {
        name: args.name,
        event_type: args.event_type,
        attribution_window_days: attributionWindowDays,
        source_ids: sourceIds,
      };
      if (args.custom_event_name) body.custom_event_name = args.custom_event_name;
      return ok(await client!.post("/conversions/event_settings", body));
    }
    if (args.action === "get_insights") {
      const levelError = validateNonEmpty("aggregation_level", args.aggregation_level, 1, 100);
      if (levelError) return levelError;
      const [timeRanges, timeRangesError] = coerceStringList(args.time_ranges, "time_ranges");
      if (timeRangesError) return timeRangesError;
      const [entityIds, entityIdsError] = coerceStringList(args.entity_ids, "entity_ids");
      if (entityIdsError) return entityIdsError;
      if (!timeRanges?.length || !entityIds?.length) {
        return badRequest("time_ranges and entity_ids are required for get_insights.");
      }
      return ok(await client!.post("/conversions/insights", {
        aggregation_level: args.aggregation_level,
        time_ranges: timeRanges,
        entity_ids: entityIds,
      }));
    }
    return badRequest(`Unknown action: ${String(args.action)}`);
  } catch (apiError) {
    return handleApiError(apiError);
  }
}

function validateCustomEventName(value: unknown, name: string): string | null {
  if (typeof value !== "string" || !value.trim()) {
    return badRequest(`${name} is required when event type is custom.`);
  }
  const normalized = value.trim();
  if (!CUSTOM_EVENT_NAME_RE.test(normalized)) {
    return badRequest(`${name} must use lowercase letters, numbers, underscores, or dashes and be 1-64 characters.`);
  }
  if (BUILT_IN_EVENT_NAMES.has(normalized)) {
    return badRequest(`${name} must not reuse a built-in event name.`);
  }
  return null;
}

function validateUserData(user: unknown, index: number): string | null {
  if (user === undefined || user === null) {
    return null;
  }
  if (!isRecord(user)) {
    return badRequest(`events[${index}].user must be an object.`);
  }
  for (const [key, value] of Object.entries(user)) {
    if (key.toLowerCase().includes("phone")) {
      return badRequest(`events[${index}].user must not include phone numbers or phone hashes.`);
    }
    if (key === "email") {
      return badRequest(`events[${index}].user must not include raw email addresses. Send email_sha256.`);
    }
    if (key !== "external_id_sha256" && key.toLowerCase().includes("external")) {
      return badRequest(`events[${index}].user must not include raw external IDs. Send external_id_sha256.`);
    }
    if (!USER_FIELDS.has(key)) {
      return badRequest(`events[${index}].user contains unsupported field '${key}'.`);
    }
    if (key === "email_sha256" || key === "external_id_sha256") {
      if (typeof value !== "string" || EMAIL_RE.test(value) || !HASH_RE.test(value)) {
        return badRequest(`events[${index}].user.${key} must be a lowercase 64-character SHA-256 hex hash.`);
      }
    } else if (key === "country") {
      if (typeof value !== "string" || !/^[A-Za-z]{2}$/.test(value)) {
        return badRequest(`events[${index}].user.country must be a two-letter ISO country code.`);
      }
    } else if (key === "city") {
      if (typeof value !== "string" || !value.trim() || value.length > 128) {
        return badRequest(`events[${index}].user.city must be a non-empty string up to 128 characters.`);
      }
    } else if (key === "zip_code") {
      if (typeof value !== "string" || !/^[A-Za-z0-9 -]{1,32}$/.test(value)) {
        return badRequest(`events[${index}].user.zip_code must be 1-32 letters, numbers, spaces, or hyphens.`);
      }
    } else if (key === "ip_address") {
      if (typeof value !== "string" || isIP(value) === 0) {
        return badRequest(`events[${index}].user.ip_address must be a valid IPv4 or IPv6 address.`);
      }
    } else if (key === "user_agent" && (typeof value !== "string" || !value.trim())) {
      return badRequest(`events[${index}].user.user_agent must be a non-empty string.`);
    } else if (key === "obref" && (typeof value !== "string" || !value.trim())) {
      return badRequest(`events[${index}].user.obref must be a non-empty opaque browser reference.`);
    }
  }
  return null;
}

function validateEventData(eventType: string, data: unknown, index: number): string | null {
  if (!isRecord(data)) {
    return badRequest(`events[${index}].data is required and must be an object.`);
  }
  const expectedType = SUPPORTED_EVENT_DATA_TYPES[eventType];
  if (!expectedType) {
    return badRequest(`events[${index}].type must be one of ${Object.keys(SUPPORTED_EVENT_DATA_TYPES).sort().join(", ")}.`);
  }
  if (data.type !== expectedType) {
    return badRequest(`events[${index}].data.type must be ${expectedType} for ${eventType}.`);
  }
  const allowedDataFields = EVENT_DATA_FIELDS[expectedType];
  for (const key of Object.keys(data)) {
    if (!allowedDataFields.has(key)) {
      return badRequest(`events[${index}].data contains unsupported field '${key}'.`);
    }
  }
  if (data.amount !== undefined && !Number.isInteger(data.amount)) {
    return badRequest(`events[${index}].data.amount must be an integer minor-unit value.`);
  }
  if (data.amount !== undefined && (typeof data.currency !== "string" || !/^[A-Z]{3}$/.test(data.currency))) {
    return badRequest(`events[${index}].data.currency is required as a 3-letter code when amount is present.`);
  }
  if (data.contents !== undefined) {
    if (!Array.isArray(data.contents)) {
      return badRequest(`events[${index}].data.contents must be a list.`);
    }
    for (const [contentIndex, content] of data.contents.entries()) {
      if (!isRecord(content)) return badRequest(`events[${index}].data.contents[${contentIndex}] must be an object.`);
      for (const key of Object.keys(content)) {
        if (!CONTENT_FIELDS.has(key)) {
          return badRequest(`events[${index}].data.contents[${contentIndex}] contains unsupported field '${key}'.`);
        }
      }
      if (content.quantity !== undefined && !Number.isInteger(content.quantity)) {
        return badRequest(`events[${index}].data.contents[${contentIndex}].quantity must be an integer.`);
      }
      if (content.amount !== undefined && !Number.isInteger(content.amount)) {
        return badRequest(`events[${index}].data.contents[${contentIndex}].amount must be an integer minor-unit value.`);
      }
      if (content.amount !== undefined && data.currency === undefined && (typeof content.currency !== "string" || !/^[A-Z]{3}$/.test(content.currency))) {
        return badRequest(`events[${index}].data.contents[${contentIndex}].currency is required when item amount has no event-level currency.`);
      }
    }
  }
  return null;
}

export function validateConversionEvents(events: unknown): [JsonRecord[] | null, string | null] {
  const [parsed, error] = coerceList(events, "events");
  if (error) return [null, error];
  if (!parsed?.length) return [null, badRequest("events must include at least one event.")];
  if (parsed.length > 1000) return [null, badRequest("send_conversions accepts at most 1000 events per call.")];
  const [earliest, latest] = conversionTimeBoundsMs();
  const out: JsonRecord[] = [];
  for (const [index, event] of parsed.entries()) {
    if (!isRecord(event)) return [null, badRequest(`events[${index}] must be an object.`)];
    if (typeof event.id !== "string" || !event.id.trim()) return [null, badRequest(`events[${index}].id is required.`)];
    if (typeof event.type !== "string" || !event.type.trim()) return [null, badRequest(`events[${index}].type is required.`)];
    const eventType = event.type.trim();
    if (!(eventType in SUPPORTED_EVENT_DATA_TYPES)) {
      return [null, badRequest(`events[${index}].type must be one of ${Object.keys(SUPPORTED_EVENT_DATA_TYPES).sort().join(", ")}.`)];
    }
    if (eventType === "custom") {
      const customNameError = validateCustomEventName(event.custom_event_name, `events[${index}].custom_event_name`);
      if (customNameError) return [null, customNameError];
    }
    const dataError = validateEventData(eventType, event.data, index);
    if (dataError) return [null, dataError];
    const userError = validateUserData(event.user, index);
    if (userError) return [null, userError];
    if (!Number.isInteger(event.timestamp_ms)) {
      return [null, badRequest(`events[${index}].timestamp_ms must be an integer.`)];
    }
    const timestampMs = event.timestamp_ms as number;
    if (timestampMs < earliest) return [null, badRequest("events include a timestamp older than 7 days.")];
    if (timestampMs > latest) return [null, badRequest("events include a timestamp more than 10 minutes in the future.")];
    const actionSource = event.action_source === undefined || event.action_source === null ? undefined : String(event.action_source);
    if (actionSource !== undefined && !ACTION_SOURCES.has(actionSource)) {
      return [null, badRequest(`events[${index}].action_source must be one of ${[...ACTION_SOURCES].sort().join(", ")}.`)];
    }
    if (actionSource === "web" && !event.source_url) {
      return [null, badRequest("source_url is required for web conversion events.")];
    }
    if ((eventType === "app_installed" || eventType === "app_opened") && actionSource !== "mobile_app") {
      return [null, badRequest(`${eventType} events require action_source='mobile_app'.`)];
    }
    out.push(event);
  }
  return [out, null];
}

async function sendConversions(args: ToolArgs): Promise<string> {
  const pixelError = validateNonEmpty("pixel_id", args.pixel_id);
  if (pixelError) return pixelError;
  const [events, eventsError] = validateConversionEvents(args.events);
  if (eventsError) return eventsError;
  const { client, error } = getClientOrError();
  if (error) return error;
  try {
    return ok(await client!.postConversions(String(args.pixel_id), events ?? [], args.validate_only === true));
  } catch (apiError) {
    return handleApiError(apiError);
  }
}

export const conversionTools: AdsToolDefinition[] = [
  {
    name: "manage_conversions",
    description: "Manage conversion pixels, API keys, event settings, and conversion reporting.",
    inputSchema: {
      action: z.enum(["create_pixel", "create_api_key", "get_event_settings", "set_event_settings", "get_insights"]),
      name: z.string().optional(),
      client_type: z.enum(["web"]).default("web"),
      event_type: z.string().optional(),
      custom_event_name: z.string().optional(),
      attribution_window_days: z.number().int().optional(),
      source_ids: z.any().optional(),
      aggregation_level: z.string().optional(),
      time_ranges: z.any().optional(),
      entity_ids: z.any().optional(),
      limit: z.number().int().default(20),
      after: z.string().optional(),
      before: z.string().optional(),
      order: z.enum(["asc", "desc"]).default("desc"),
    },
    argNames: [
      "action",
      "name",
      "client_type",
      "event_type",
      "custom_event_name",
      "attribution_window_days",
      "source_ids",
      "aggregation_level",
      "time_ranges",
      "entity_ids",
      "limit",
      "after",
      "before",
      "order",
    ],
    writes: true,
    openWorld: true,
    readonlyActions: [...CONVERSION_READ_ACTIONS],
    handler: manageConversions,
  },
  {
    name: "send_conversions",
    description: "Send conversion events to the OpenAI conversion ingest host after local privacy-safe validation. Use validate_only=true to test a batch without ingesting it.",
    inputSchema: {
      pixel_id: z.string(),
      events: z.any(),
      validate_only: z.boolean().default(false),
    },
    argNames: ["pixel_id", "events", "validate_only"],
    writes: true,
    openWorld: true,
    handler: sendConversions,
  },
];
