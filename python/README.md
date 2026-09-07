# openai-ads-mcp

<!-- mcp-name: io.github.trakkr-aisearch/openai-ads-mcp -->

Python runtime for `openai-ads-mcp`, a typed MCP server for OpenAI Ads, ChatGPT Ads MCP workflows, and OpenAI's Advertiser API.

## Quick start (local / stdio)

```bash
export OPENAI_ADS_API_KEY="..."
export OPENAI_ADS_MCP_READONLY=1
uvx openai-ads-mcp
```

Readonly mode is recommended for first connection because it hides all write tools from `tools/list`.

## Hosted / custom MCP client (Streamable HTTP)

Same pattern as `mcp-google-ads` and `link-finder-mcp`: product API key in the server env, plus a shared bearer token for the MCP transport. There is no OpenAI Ads OAuth flow yet.

```bash
cd python
python -m pip install -e .
cp .env.example .env   # fill OPENAI_ADS_API_KEY + MCP_BEARER_TOKEN

export MCP_TRANSPORT=http
export OPENAI_ADS_API_KEY="..."
export MCP_BEARER_TOKEN="long-random-secret"
export OPENAI_ADS_MCP_READONLY=1
python -m openai_ads_mcp
# listens on http://0.0.0.0:8000/mcp
```

Point your custom MCP client at:

```text
https://your-host/mcp
Authorization: Bearer <MCP_BEARER_TOKEN>
```

The Ads API key stays on the server (`OPENAI_ADS_API_KEY`). Clients do not send it.

| Variable | Role |
| --- | --- |
| `OPENAI_ADS_API_KEY` | Ads Manager API key (Settings → API keys) |
| `MCP_BEARER_TOKEN` | Protects `/mcp` (alias: `OPENAI_ADS_MCP_HTTP_TOKEN`) |
| `MCP_TRANSPORT` | `stdio` (default), `http`, or `sse` |
| `OPENAI_ADS_MCP_READONLY` | Hide write tools (`1` = default). Set `0` only when enabling edits. |
| `OPENAI_ADS_MCP_ALLOW_WRITES` | Set `1` later to register campaign edit tools (requires restart). |
| `PORT` / `HOST` | Bind address in hosted mode |

See root [`DEPLOY.md`](../DEPLOY.md) for the full Render + custom-client guide.


## Local development

```bash
cd python
python -m pip install -e .
python -m pytest -q
python -c "import openai_ads_mcp; print('ok')"
```

The full README and release notes live one directory up in the service root.
