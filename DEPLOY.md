# Deploy ChatGPT / OpenAI Ads MCP (Render + any client)

OpenAI Ads has **no OAuth** yet. Remote clients always use:

```json
{
  "url": "https://YOUR-SERVICE.onrender.com/mcp",
  "headers": {
    "Authorization": "Bearer YOUR_MCP_BEARER_TOKEN"
  }
}
```

Two secrets:

| Secret | Where it lives | Who sends it |
| --- | --- | --- |
| `OPENAI_ADS_API_KEY` | Render env only | Nobody — server calls Ads API |
| `MCP_BEARER_TOKEN` | Render env + your client headers | Your MCP client |

---

## 1. Get an OpenAI Ads API key

1. Open [ads.openai.com](https://ads.openai.com) with an approved advertiser account.
2. Go to **Settings → API keys** (not the campaign list, and not platform.openai.com model keys).
3. Create a key, copy it once.

Quick check:

```bash
curl -sS -H "Authorization: Bearer $OPENAI_ADS_API_KEY" \
  https://api.ads.openai.com/v1/ad_account
```

---

## 2. Deploy on Render

### Option A — Blueprint (`render.yaml`)

1. Push this repo to GitHub (or connect your fork).
2. In Render: **New → Blueprint** → select the repo.
3. Render reads `render.yaml` and creates a Python web service with `rootDir: python`.
4. Set **`OPENAI_ADS_API_KEY`** in the dashboard (secret).
5. Copy the generated **`MCP_BEARER_TOKEN`** (or set your own long random string).
6. Deploy. Health check is `GET /healthz` (no auth).

### Option B — Manual web service

1. **New → Web Service** → connect the repo.
2. Settings:
   - **Root Directory:** `python`
   - **Runtime:** Python
   - **Build Command:** `pip install -e .`
   - **Start Command:** `python -m openai_ads_mcp`
   - **Health Check Path:** `/healthz`
3. Environment variables:

| Key | Value |
| --- | --- |
| `MCP_TRANSPORT` | `http` |
| `MCP_STATELESS_HTTP` | `true` |
| `OPENAI_ADS_MCP_READONLY` | `1` (default anyway — keeps write tools hidden) |
| `OPENAI_ADS_API_KEY` | your Ads API key |
| `MCP_BEARER_TOKEN` | long random secret — `openssl rand -hex 32` (Render Blueprint can auto-generate) |

> **Read-only by default.** Write tools (`create_campaign`, `update_campaign`, `set_campaign_state`, …) are not registered. When you want MCP to edit campaigns later, add `OPENAI_ADS_MCP_ALLOW_WRITES=1` on Render and **redeploy** (restart required so tools re-register).

4. Deploy. Endpoint: `https://<service-name>.onrender.com/mcp`

Verify:

```bash
curl -sS https://YOUR-SERVICE.onrender.com/
curl -sS https://YOUR-SERVICE.onrender.com/healthz
# should be 401 without token:
curl -sS -o /dev/null -w "%{http_code}\n" https://YOUR-SERVICE.onrender.com/mcp
```

---

## 3. Connect any custom MCP client

Use Streamable HTTP at **`/mcp`** plus bearer auth.

```json
{
  "url": "https://YOUR-SERVICE.onrender.com/mcp",
  "headers": {
    "Authorization": "Bearer YOUR_MCP_BEARER_TOKEN"
  }
}
```

Do **not** put `OPENAI_ADS_API_KEY` in client headers. The server already has it.

### Cursor (remote)

In `~/.cursor/mcp.json` or project `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "openai-ads": {
      "url": "https://YOUR-SERVICE.onrender.com/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_MCP_BEARER_TOKEN"
      }
    }
  }
}
```

### ChatGPT custom connector / Developer mode

- Server URL: `https://YOUR-SERVICE.onrender.com/mcp`
- Auth: header `Authorization` = `Bearer YOUR_MCP_BEARER_TOKEN`

### Generic / SDK / n8n / in-house

Same shape: `url` + `headers.Authorization`. Prefer Streamable HTTP (`/mcp`). Only use `/sse` if a legacy client forces SSE (`MCP_TRANSPORT=sse`).

---

## 4. First prompts (stats)

With read-only (default):

1. Call `get_account`
2. Call `list_campaigns`
3. Call `get_insights` for the last 7 days with impressions, clicks, spend, ctr, cpc, cpm

### Enable campaign editing later

Write tools already exist in the codebase (`create_campaign`, `update_campaign`, `set_campaign_state`, `build_campaign`, …). They stay hidden until you opt in:

1. In Render → Environment, set **`OPENAI_ADS_MCP_ALLOW_WRITES=1`**
   (or set `OPENAI_ADS_MCP_READONLY=0`).
2. Optionally raise `OPENAI_ADS_BUDGET_CEILING_USD` (default `100`).
3. **Redeploy / restart** the service (tool list is fixed at process start).
4. Confirm `create_campaign` appears in `tools/list`, then edit only with paused defaults first.

Until then, leave writes off.

---

## 5. Troubleshooting

| Symptom | Fix |
| --- | --- |
| `401` on `/mcp` | Wrong/missing `Authorization: Bearer …` (must match `MCP_BEARER_TOKEN`) |
| `421` / Invalid Host | Leave `MCP_ALLOWED_HOSTS` empty (default). Bearer already protects the endpoint. |
| Init handshake stalls / proxy buffering | Keep `MCP_TRANSPORT=http` and `MCP_STATELESS_HTTP=true` (do not use SSE on Render) |
| Free Render sleeps | First request after idle can take ~30s; use a paid instance for always-on |
| Ads API 401 | Bad `OPENAI_ADS_API_KEY` — recreate under Ads Manager → Settings → API keys |

---

## Why not OAuth?

OpenAI Ads / ChatGPT Ads expose an **API key** today, not an OAuth authorization server for advertisers. So:

- **MCP transport** → shared bearer (`MCP_BEARER_TOKEN`) — required for every remote client
- **Ads API** → server-side key (`OPENAI_ADS_API_KEY`) — never sent by the client

When/if OpenAI ships Ads OAuth, this repo can grow a google-ads-style optional OAuth 2.1 layer; until then, URL + Bearer is the correct contract.
