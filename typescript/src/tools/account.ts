import { z } from "zod";

import { getClientOrError, handleApiError, ok, type AdsToolDefinition } from "../core.js";

export async function getAccount(): Promise<string> {
  const { client, error } = getClientOrError();
  if (error) return error;
  try {
    return ok(await client!.get("/ad_account"));
  } catch (apiError) {
    return handleApiError(apiError);
  }
}

export const accountTools: AdsToolDefinition[] = [
  {
    name: "get_account",
    description:
      "Get the authenticated OpenAI Ads account for the server's OPENAI_ADS_API_KEY (or per-request Ads key). Call this first and confirm the returned adacct_… id matches the Ads Manager account you intend — the MCP is API-key scoped to exactly one ad account; there is no OAuth account picker, and pasting another adacct_… in a prompt does not switch accounts. Returns id, name, timezone, currency, and settings.",
    inputSchema: {},
    argNames: [],
    openWorld: true,
    handler: () => getAccount(),
  },
];

void z;
