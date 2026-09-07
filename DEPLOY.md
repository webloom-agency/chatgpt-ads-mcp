# Deploy ChatGPT / OpenAI Ads MCP (Render + any client)

*Full product docs: [README.md](./README.md) (EN) · [README.fr.md](./README.fr.md)*

OpenAI Ads has **no OAuth**. Remote clients use:

```json
{
  "url": "https://YOUR-SERVICE.onrender.com/mcp",
  "headers": {
    "Authorization": "Bearer YOUR_MCP_BEARER_TOKEN"
  }
}
```

| Secret | Lives on | Sent by client? |
| --- | --- | --- |
| `OPENAI_ADS_API_KEY` | Render env | No |
| `MCP_BEARER_TOKEN` | Render env + client header | Yes (`Authorization: Bearer …`) |

---

## 1. Ads API key

1. [ads.openai.com](https://ads.openai.com) → **Settings → API keys**
2. Create a key (not a platform.openai.com model key)
3. Optional check:

```bash
curl -sS -H "Authorization: Bearer $OPENAI_ADS_API_KEY" \
  https://api.ads.openai.com/v1/ad_account
```

---

## 2. Push this repo to GitHub

Commit and push `main` (including the root `Dockerfile`).

---

## 3. Create the Render web service

**New → Web Service** → connect `chatgpt-ads-mcp`.

Fill the form like this (matches the Render UI):

| Field | Value |
| --- | --- |
| **Name** | `chatgpt-ads-mcp` |
| **Language** | **Docker** |
| **Branch** | `main` |
| **Region** | wherever you want (e.g. Frankfurt) |
| **Root Directory** | **leave empty** |
| **Dockerfile Path** | `./Dockerfile` (default) |
| **Instance** | Starter ($7) or free if you accept cold starts |

> Do **not** set Root Directory to `python`. The Docker build already copies `python/` from the repo root. An empty Root Directory is correct.

Then open **Environment** and add:

| Key | Value |
| --- | --- |
| `OPENAI_ADS_API_KEY` | your Ads API key (secret) |
| `MCP_BEARER_TOKEN` | run locally: `openssl rand -hex 32` — paste the result (secret) |
| `MCP_TRANSPORT` | `http` |
| `MCP_STATELESS_HTTP` | `true` |
| `OPENAI_ADS_MCP_READONLY` | `1` |
| `HOST` | `0.0.0.0` |

Optional: set **Health Check Path** to `/healthz`.

Click **Deploy web service**.

### Or use Blueprint

**New → Blueprint** → this repo. `render.yaml` creates the same Docker service. Then set `OPENAI_ADS_API_KEY` and copy the generated `MCP_BEARER_TOKEN`.

---

## 4. Verify

After deploy, URL looks like `https://chatgpt-ads-mcp-xxxx.onrender.com`.

```bash
curl -sS https://YOUR-SERVICE.onrender.com/healthz
# → ok

curl -sS https://YOUR-SERVICE.onrender.com/
# → {"name":"openai-ads-mcp","readonly":true,"auth":"bearer",...}

curl -sS -o /dev/null -w "%{http_code}\n" https://YOUR-SERVICE.onrender.com/mcp
# → 401  (expected without bearer)
```

MCP endpoint: **`https://YOUR-SERVICE.onrender.com/mcp`**

---

## 5. Custom MCP client config

```json
{
  "url": "https://YOUR-SERVICE.onrender.com/mcp",
  "headers": {
    "Authorization": "Bearer YOUR_MCP_BEARER_TOKEN"
  }
}
```

Use the same `MCP_BEARER_TOKEN` you set on Render. Never put `OPENAI_ADS_API_KEY` in client headers.

### Cursor example

`~/.cursor/mcp.json`:

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

---

## 6. First prompts

1. `get_account`
2. `list_campaigns`
3. `get_insights` (impressions, clicks, spend, ctr, cpc, cpm)

### Enable campaign editing later

1. Render → Environment → `OPENAI_ADS_MCP_ALLOW_WRITES=1`
2. Redeploy
3. Confirm write tools appear in `tools/list`

---

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Build uses Node / wrong image | You need the **Python** root `Dockerfile`. Push latest `main`. Old Node image is now `Dockerfile.node`. |
| Root Directory = `python` | Clear it. Empty is correct for Docker. |
| `401` on `/mcp` | Bearer must match `MCP_BEARER_TOKEN` exactly |
| Free tier sleep | First request after idle ~30s; Starter stays warmer |
| Ads API 401 | Bad `OPENAI_ADS_API_KEY` |

---

## Why not OAuth?

OpenAI Ads only offers API keys today. Transport auth = shared bearer. Product auth = server-side Ads key.
