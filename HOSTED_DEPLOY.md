# Hosted OpenAI Ads MCP Endpoint

This runbook is for the public read-only endpoint:

```text
https://openai-ads-mcp.trakkr.ai/mcp
```

The hosted service is for discovery, Smithery compatibility, and safe usage learning. It must not become a shared Trakkr Ads API proxy.

## Safety Defaults

- `OPENAI_ADS_MCP_HOSTED_PUBLIC=1`
- No `OPENAI_ADS_API_KEY` in the service environment.
- `OPENAI_ADS_MCP_READONLY=1` forced by the server.
- `OPENAI_ADS_MCP_HTTP_ALLOW_WRITES=1` is rejected at startup.
- `X-OpenAI-Ads-API-Base-Url` is rejected in hosted public mode.
- Tool calls that need Ads data require `X-OpenAI-Ads-API-Key` per request.
- Anonymous clients can still initialize and call `tools/list`.
- Telemetry stores summaries, hashes, counts, status, latency, and client metadata only.
- No plaintext campaign names, ad names, ad text, API keys, auth headers, raw request bodies, or raw OpenAI responses are logged.

## Required Secrets

Set a long random salt before deploy:

```bash
export OPENAI_ADS_MCP_TELEMETRY_SALT="$(openssl rand -hex 32)"
```

Do not set `OPENAI_ADS_API_KEY`.

Optional but recommended when a Cloudflare Worker is placed in front:

```bash
export OPENAI_ADS_MCP_EDGE_SECRET="$(openssl rand -hex 32)"
```

If `OPENAI_ADS_MCP_EDGE_SECRET` is set on Cloud Run, Cloud Run rejects requests without `X-Trakkr-Edge-Secret` except `/healthz`, `/health`, and `/ready`. The Cloudflare Worker reads the same `OPENAI_ADS_MCP_EDGE_SECRET` binding and forwards it to Cloud Run as `X-Trakkr-Edge-Secret`.

## Deploy

From the monorepo:

```bash
cd services/openai-ads-mcp
chmod +x scripts/deploy-hosted-cloud-run.sh
OPENAI_ADS_MCP_TELEMETRY_SALT="..." ./scripts/deploy-hosted-cloud-run.sh
```

The script deploys with these spend-protection defaults:

- min instances: `0`
- max instances: `1`
- concurrency: `1`
- memory: `256Mi`
- CPU: `0.25`
- timeout: `15s`
- global tool-call cap: `1,000/day`

Cloud Run requires concurrency `1` when CPU is below `1`. This endpoint deliberately keeps `0.25 CPU` and lets availability degrade under pressure instead of increasing active compute.

### Add the Edge Secret to Cloud Run

The deploy script always manages `OPENAI_ADS_MCP_TELEMETRY_SALT`. If you are using the Cloudflare Worker origin guard, add the edge secret after deploy:

```bash
PROJECT_ID="${PROJECT_ID:-trakkr-ai}"
REGION="${REGION:-us-east1}"
SERVICE="${SERVICE:-openai-ads-mcp}"
EDGE_SECRET_NAME="${EDGE_SECRET_NAME:-openai-ads-mcp-edge-secret}"

printf '%s' "$OPENAI_ADS_MCP_EDGE_SECRET" | gcloud secrets create "$EDGE_SECRET_NAME" \
  --project "$PROJECT_ID" \
  --replication-policy=automatic \
  --data-file=- 2>/dev/null || \
printf '%s' "$OPENAI_ADS_MCP_EDGE_SECRET" | gcloud secrets versions add "$EDGE_SECRET_NAME" \
  --project "$PROJECT_ID" \
  --data-file=-

gcloud run services update "$SERVICE" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --update-secrets "OPENAI_ADS_MCP_EDGE_SECRET=${EDGE_SECRET_NAME}:latest"
```

Use `--update-secrets`, not `--set-env-vars`, so existing Cloud Run configuration is preserved.

## Cloudflare

Use a proxied DNS record for:

```text
openai-ads-mcp.trakkr.ai
```

Deploy the Worker from `services/openai-ads-mcp`:

```bash
cd services/openai-ads-mcp
printf '%s' "$OPENAI_ADS_MCP_EDGE_SECRET" | npx wrangler secret put OPENAI_ADS_MCP_EDGE_SECRET
npx wrangler deploy
```

The Worker allows only `/mcp`, health paths, and `/.well-known/mcp/server-card.json`, caps request bodies at 256 KiB, forwards `X-Forwarded-Host`, and forwards the shared secret as `X-Trakkr-Edge-Secret` when the binding is present.

Recommended rules:

- Block obvious bad bots.
- Block oversized requests to `/mcp`.
- Rate limit `/mcp` by IP.
- Do not challenge known MCP clients, Smithery, MCP Registry crawlers, or directory validators.

Start without Google Load Balancer and Cloud Armor to avoid baseline cost. If direct Cloud Run origin bypass becomes a real issue, move to:

- External HTTPS Load Balancer
- Serverless NEG to Cloud Run
- Cloud Armor rate limiting
- Cloud Run ingress: internal-and-load-balancer only

## Billing And Monitoring

Create Cloud Billing budget alerts:

- warning: `$10`
- urgent: `$25`
- critical: `$50`

Create Cloud Monitoring alerts:

- request count spike
- 429 spike
- 5xx spike
- continuous instance activity over 30 minutes
- log ingestion spike

## Verification

Local:

```bash
cd services/openai-ads-mcp/typescript
npm run build
npm test
docker build -t openai-ads-mcp-hosted .
docker run --rm -p 8080:8080 \
  -e OPENAI_ADS_MCP_HOSTED_PUBLIC=1 \
  -e OPENAI_ADS_MCP_TELEMETRY_SALT="local-test-salt" \
  openai-ads-mcp-hosted
```

Production smoke:

```bash
curl -fsS https://openai-ads-mcp.trakkr.ai/health
curl -fsS https://openai-ads-mcp.trakkr.ai/.well-known/mcp/server-card.json
```

`/healthz` is still supported by the app, but Cloud Run's default `run.app` frontend can intercept it before the container. Use `/health` or `/ready` for external Cloud Run checks.

Check an anonymous discovery request:

```bash
curl -fsS https://openai-ads-mcp.trakkr.ai/mcp \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -H 'Mcp-Protocol-Version: 2025-06-18' \
  --data '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

Check missing-key behavior:

```bash
curl -fsS https://openai-ads-mcp.trakkr.ai/mcp \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -H 'Mcp-Protocol-Version: 2025-06-18' \
  --data '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_account","arguments":{}}}'
```

Expected result: a normal MCP tool-result error asking for `X-OpenAI-Ads-API-Key`, not an upstream OpenAI request.

If the edge secret is enabled, direct Cloud Run origin calls to `/mcp` should return `403` without `X-Trakkr-Edge-Secret`, while the Cloudflare hostname should continue to work.

## Release And Discovery

After staging or production verifies:

1. Keep `server.json` remote URL set to `https://openai-ads-mcp.trakkr.ai/mcp`.
2. Sync to the public repo.
3. Tag the next release, for example `openai-ads-mcp-v0.1.7`.
4. Confirm npm, PyPI, and Official MCP Registry workflows pass.
5. Submit the remote URL to Smithery.

## Kill Switch

To pause the hosted endpoint without breaking npm, PyPI, or local installs:

```bash
gcloud run services update openai-ads-mcp \
  --project trakkr-ai \
  --region us-east1 \
  --update-env-vars OPENAI_ADS_MCP_HOSTED_DISABLED=1
```

Re-enable:

```bash
gcloud run services update openai-ads-mcp \
  --project trakkr-ai \
  --region us-east1 \
  --remove-env-vars OPENAI_ADS_MCP_HOSTED_DISABLED
```
