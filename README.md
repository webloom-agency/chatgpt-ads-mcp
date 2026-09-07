# ChatGPT Ads MCP (OpenAI Ads)

*🇫🇷 [Version française](./README.fr.md)*

An [MCP](https://modelcontextprotocol.io) server for **OpenAI Ads / ChatGPT Ads** — inspect your advertiser account, list campaigns, and pull performance stats (impressions, clicks, spend, CTR, CPC, CPM) from Claude, ChatGPT, Cursor, or any MCP client.

ChatGPT Ads are managed in [Ads Manager](https://ads.openai.com). This server talks to the **OpenAI Advertiser API** with your Ads API key.

**Read-only by default** (safe for reporting). Campaign create/update/activate tools exist but stay hidden until you explicitly enable writes.

Secrets stay in environment variables only — never as tool arguments.

---

## What you can do

| Goal | Tool |
| --- | --- |
| Confirm the API key / account | `get_account` |
| List campaigns, ad groups, ads | `list_campaigns`, `list_ad_groups`, `list_ads` |
| Performance stats | `get_insights` (account / campaign / ad group / ad) |
| Audiences & geo | `list_audiences`, `search_geo`, … |
| Conversions (read) | `manage_conversions` (`get_event_settings`, …) |

Example prompts:

> What are the campaign performances for my account over the last few days?

> List my ChatGPT Ads campaigns and show impressions, clicks, and spend.

---

## Requirements

1. An approved OpenAI Ads / ChatGPT Ads advertiser account — [ads.openai.com](https://ads.openai.com)
2. An **Ads API key** from **Settings → API keys**  
   (not a model key from platform.openai.com)
3. Python 3.10+ for local use, **or** a host like Render for a public HTTPS URL

Quick key check:

```bash
curl -sS -H "Authorization: Bearer $OPENAI_ADS_API_KEY" \
  https://api.ads.openai.com/v1/ad_account
```

---

## Connect it to your AI — pick your setup

| | **A. Local (on your computer)** | **B. Hosted (online URL)** |
| --- | --- | --- |
| **Best for** | Claude Desktop, Cursor, Claude Code | ChatGPT, Claude (web), custom MCP clients, n8n |
| **How it runs** | The app starts the server for you | You deploy once, then paste a URL + token |
| **Transport** | `stdio` | Streamable HTTP at `/mcp` |
| **Auth** | Ads key in env | Ads key on the server + `Authorization: Bearer …` for the client |

> Rule of thumb: **desktop app → A**, **web / cloud chat → B**.

OpenAI Ads has **no OAuth** yet. Hosted clients always use URL + bearer token.

---

## Configuration

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `OPENAI_ADS_API_KEY` | yes | — | Ads Manager API key |
| `MCP_TRANSPORT` | no | `stdio` | `stdio` (local), `http` (hosted, recommended), or `sse` (legacy) |
| `MCP_BEARER_TOKEN` | hosted | — | Shared secret: `Authorization: Bearer <token>` (min. 24 chars; generate with `openssl rand -hex 32`) |
| `OPENAI_ADS_MCP_READONLY` | no | `1` (on) | Hide write tools. Default is read-only. |
| `OPENAI_ADS_MCP_ALLOW_WRITES` | no | off | Set `1` later to enable campaign editing (restart required) |
| `OPENAI_ADS_BUDGET_CEILING_USD` | no | `100` | Guardrail when writes are enabled |
| `PORT` / `HOST` | hosted | from host / `0.0.0.0` | Bind address (Render injects `PORT`) |
| `MCP_STATELESS_HTTP` | no | `true` | Best behind Render / proxies |

Copy `python/.env.example` when running from source.

---

## A. Local — Claude Desktop

1. Clone and install:

```bash
git clone https://github.com/webloom-agency/chatgpt-ads-mcp.git
cd chatgpt-ads-mcp/python
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

2. Edit Claude’s config:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "chatgpt-ads": {
      "command": "/absolute/path/to/chatgpt-ads-mcp/python/.venv/bin/python",
      "args": ["-m", "openai_ads_mcp"],
      "env": {
        "MCP_TRANSPORT": "stdio",
        "OPENAI_ADS_API_KEY": "your_ads_api_key_here",
        "OPENAI_ADS_MCP_READONLY": "1"
      }
    }
  }
}
```

3. Restart Claude Desktop. Try: *“Call get_account, then list_campaigns, then get_insights for the account.”*

### Quick install without cloning (PyPI / uvx)

```bash
# Claude Code example
claude mcp add chatgpt-ads \
  -e OPENAI_ADS_API_KEY=your_ads_api_key_here \
  -e OPENAI_ADS_MCP_READONLY=1 \
  -- uvx openai-ads-mcp
```

---

## A. Local — Cursor

`~/.cursor/mcp.json` (or project `.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "chatgpt-ads": {
      "command": "uvx",
      "args": ["openai-ads-mcp"],
      "env": {
        "OPENAI_ADS_API_KEY": "your_ads_api_key_here",
        "OPENAI_ADS_MCP_READONLY": "1"
      }
    }
  }
}
```

Or point `command` at your venv Python and `args` at `["-m", "openai_ads_mcp"]` like Claude Desktop.

---

## B. Hosted — deploy on Render (or any VM)

Recommended for ChatGPT and any remote MCP client.

### Render UI (Docker)

1. Push this repo to GitHub.
2. **New → Web Service** → connect the repo.
3. Settings:

| Field | Value |
| --- | --- |
| **Language** | **Docker** |
| **Root Directory** | **leave empty** |
| **Branch** | `main` |
| **Health Check Path** | `/healthz` |

4. Environment:

| Key | Value |
| --- | --- |
| `OPENAI_ADS_API_KEY` | your Ads API key |
| `MCP_BEARER_TOKEN` | `openssl rand -hex 32` |
| `MCP_TRANSPORT` | `http` |
| `MCP_STATELESS_HTTP` | `true` |
| `OPENAI_ADS_MCP_READONLY` | `1` |
| `HOST` | `0.0.0.0` |

5. Deploy. MCP URL: `https://<service>.onrender.com/mcp`

Or use **New → Blueprint** with [`render.yaml`](./render.yaml).

More detail: [DEPLOY.md](./DEPLOY.md).

Verify:

```bash
curl -sS https://YOUR-SERVICE.onrender.com/healthz
curl -sS -o /dev/null -w "%{http_code}\n" https://YOUR-SERVICE.onrender.com/mcp
# → 401 without bearer is expected
```

---

## B. Hosted — ChatGPT

1. Deploy as above.
2. ChatGPT → **Settings → Connectors** (Developer mode / custom connector).
3. Server URL: `https://YOUR-SERVICE.onrender.com/mcp`
4. Header: `Authorization` = `Bearer YOUR_MCP_BEARER_TOKEN`
5. Enable the connector in a chat and ask for campaign performance.

Programmatic (Responses API):

```python
from openai import OpenAI

client = OpenAI()
resp = client.responses.create(
    model="gpt-4.1",
    tools=[{
        "type": "mcp",
        "server_label": "chatgpt-ads",
        "server_url": "https://YOUR-SERVICE.onrender.com/mcp",
        "headers": {"Authorization": "Bearer YOUR_MCP_BEARER_TOKEN"},
        "require_approval": "never",
    }],
    input="Use get_account, list_campaigns, then get_insights for campaign performance.",
)
print(resp.output_text)
```

---

## B. Hosted — any other MCP client

Claude (web), Cursor (remote), n8n, custom apps, SDKs — same shape:

```json
{
  "url": "https://YOUR-SERVICE.onrender.com/mcp",
  "headers": {
    "Authorization": "Bearer YOUR_MCP_BEARER_TOKEN"
  }
}
```

Do **not** put `OPENAI_ADS_API_KEY` in client headers. It stays on the server.

---

## Reporting tips

- Prefer **omitting** `time_range` so the API uses its recent default window.
- Good first call: `get_insights` with `scope=account`, `aggregation_level=campaign`, `time_granularity=daily`.
- Responses include a `summary` (totals + by campaign) for easy reading.
- If you pass `unix_range`, timestamps must land on **full-hour** boundaries (the server hour-aligns them).

---

## Enable campaign editing later

Write tools (`create_campaign`, `update_campaign`, `set_campaign_state`, `build_campaign`, …) are built in but **hidden** until you opt in:

1. Set `OPENAI_ADS_MCP_ALLOW_WRITES=1` (or `OPENAI_ADS_MCP_READONLY=0`)
2. Restart / redeploy
3. Creates still default to **paused**; activation is a separate explicit step
4. Budget changes respect `OPENAI_ADS_BUDGET_CEILING_USD` (default $100) unless confirmed

---

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `401` on `/mcp` | Bearer must match `MCP_BEARER_TOKEN` exactly |
| Ads API `401` | Wrong key — recreate under Ads Manager → Settings → API keys |
| `duplicate_parameter` / `fields` | Redeploy a build that encodes `fields[]` (current main) |
| Init handshake stalls | Keep `MCP_TRANSPORT=http` + `MCP_STATELESS_HTTP=true` (avoid SSE on Render) |
| Free Render sleep | First request after idle can take ~30s |
| Root Directory = `python` on Docker | Leave it **empty**; the root `Dockerfile` already copies `python/` |

---

## Development

```bash
cd python
pip install -e .
pytest -q
python -m openai_ads_mcp          # stdio
MCP_TRANSPORT=http MCP_BEARER_TOKEN=$(openssl rand -hex 32) \
  OPENAI_ADS_API_KEY=... python -m openai_ads_mcp
```

Docker:

```bash
docker build -t chatgpt-ads-mcp .
docker run --rm -p 8000:8000 \
  -e OPENAI_ADS_API_KEY=... \
  -e MCP_BEARER_TOKEN="$(openssl rand -hex 32)" \
  -e PORT=8000 \
  chatgpt-ads-mcp
```

A Node runtime also exists under `typescript/` (`npx -y openai-ads-mcp`) with the same tool names. For this fork’s hosted/custom-client path, prefer the **Python** Docker image above.

---

## License

MIT — see [LICENSE](./LICENSE).
