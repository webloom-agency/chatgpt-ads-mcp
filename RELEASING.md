# Releasing `openai-ads-mcp`

Publishing happens from the dedicated public repository:

- `https://github.com/trakkr-aisearch/openai-ads-mcp`

Do not publish from this monorepo.

Current public release: `0.1.7`.

## Package Names

- PyPI: `openai-ads-mcp`
- npm: `openai-ads-mcp`

The public repo holds both runtimes:

- `python/`: Python package for `uvx openai-ads-mcp`
- `typescript/`: Node package for `npx -y openai-ads-mcp`
- `openapi.json`: shared vendored OpenAI Ads API reference
- `README.md`: shared funnel and usage docs
- `.github/workflows/`: PyPI, npm, and OpenAPI drift checks
- `server.json`: official MCP Registry metadata

## Trusted Publishing Setup

Trusted publishing should remain configured before pushing release tags.

PyPI trusted publisher:

- Repository owner: `trakkr-aisearch`
- Repository name: `openai-ads-mcp`
- Workflow file: `publish-python.yml`
- Environment: leave blank unless you add one later

npm trusted publishing:

- Package: `openai-ads-mcp`
- Repository: `trakkr-aisearch/openai-ads-mcp`
- Workflow file: `publish-npm.yml`

No PyPI or npm tokens should be stored in GitHub.

MCP Registry publishing:

- Server name: `io.github.trakkr-aisearch/openai-ads-mcp`
- Workflow file: `publish-mcp-registry.yml`
- Auth: GitHub OIDC through `mcp-publisher login github-oidc`
- Required package markers: npm `mcpName`, PyPI README `mcp-name`

## Source-of-Truth Model

1. Source code is edited here in `services/openai-ads-mcp`.
2. Releases happen from the dedicated public repo only.
3. The vendored OpenAPI reference is the shared `openapi.json` in the repo root.
4. Package and MCP Registry workflows trigger on tags matching `openai-ads-mcp-v*`.
5. A real funded OpenAI Ads account is needed to validate live writes end to end. Reads can be validated with any valid OpenAI Ads API key.
6. `server.json`, `typescript/package.json`, `python/pyproject.toml`, the Node user agent, and the hosted server card must agree on the public version.
7. MCP Registry versions are immutable after publish. For metadata-only corrections after a package release, publish a new server version rather than reusing an existing one.
8. The current registry `0.1.6` entry is already live. Any registry metadata correction to that release line must go out with the next package release version unless the registry adds an explicit update mechanism.

## Sync and Release Flow

1. Create or update the dedicated public repository locally.

```bash
PUBLIC_REPO_DIR="${PUBLIC_REPO_DIR:-../openai-ads-mcp-publish}"
SOURCE_DIR="${SOURCE_DIR:-services/openai-ads-mcp}"
mkdir -p "$PUBLIC_REPO_DIR"
rsync -av --delete \
  --exclude '.git' \
  --exclude '.pytest_cache' \
  --exclude '__pycache__' \
  --exclude 'node_modules' \
  --exclude 'dist' \
  "$SOURCE_DIR/" \
  "$PUBLIC_REPO_DIR/"
```

2. Run both test suites and the spec drift check in the dedicated repo.

```bash
cd "$PUBLIC_REPO_DIR/python"
python -m pytest -q
python -c "import openai_ads_mcp; print('ok')"

cd "../typescript"
npm install
npm run build
npm test
npm run check:openapi
npm run check:docs
```

3. Commit and push the synced public repo.

```bash
cd "$PUBLIC_REPO_DIR"
git add .
git commit -m "Release openai-ads-mcp X.Y.Z"
git push origin main
```

4. Release both packages with the shared tag.

```bash
git tag openai-ads-mcp-vX.Y.Z
git push origin openai-ads-mcp-vX.Y.Z
```

5. Confirm release.

- GitHub Actions succeeds for Python, npm, and OpenAPI drift.
- MCP Registry workflow succeeds after npm and PyPI have the matching version.
- Python package is visible at `https://pypi.org/project/openai-ads-mcp/`.
- npm package is visible at `https://www.npmjs.com/package/openai-ads-mcp`.
- Registry metadata is visible from `https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.trakkr-aisearch/openai-ads-mcp`.
- `npm pack --dry-run` from `typescript/` includes `LICENSE`.
- `python -m build` from `python/` includes `LICENSE` in the sdist and `*.dist-info/licenses/LICENSE` in the wheel.

## Manual Smoke Test

Reads can be validated with any valid OpenAI Ads API key:

```bash
export OPENAI_ADS_API_KEY="..."
export OPENAI_ADS_MCP_READONLY=1
uvx openai-ads-mcp
npx -y openai-ads-mcp
```

Live writes require a real funded OpenAI Ads account. Start with small paused objects, review the returned ids and budgets, then activate only after explicit approval.
