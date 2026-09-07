# MCP ChatGPT Ads (OpenAI Ads)

*[🇬🇧 English version](./README.md)*

Serveur [MCP](https://modelcontextprotocol.io) pour **OpenAI Ads / ChatGPT Ads** — consultez le compte annonceur, listez les campagnes et récupérez les stats (impressions, clics, dépenses, CTR, CPC, CPM) depuis Claude, ChatGPT, Cursor ou tout client MCP.

Les pubs ChatGPT se gèrent dans [Ads Manager](https://ads.openai.com). Ce serveur parle à l’**Advertiser API** OpenAI avec votre clé Ads.

**Lecture seule par défaut** (idéal pour le reporting). Les outils de création / modification / activation existent mais restent masqués tant que vous n’activez pas les écritures.

Les secrets restent dans des variables d’environnement — jamais en argument d’outil.

---

## Ce que vous pouvez faire

| Objectif | Outil |
| --- | --- |
| Vérifier la clé / le compte | `get_account` |
| Lister campagnes, ad groups, ads | `list_campaigns`, `list_ad_groups`, `list_ads` |
| Performances | `get_insights` (compte / campagne / ad group / ad) |
| Audiences & geo | `list_audiences`, `search_geo`, … |
| Conversions (lecture) | `manage_conversions` (`get_event_settings`, …) |

Exemples de prompts :

> Quelles sont les perfs des campagnes de mon compte ces derniers jours ?

> Liste mes campagnes ChatGPT Ads et montre impressions, clics et spend.

---

## Prérequis

1. Un compte annonceur OpenAI Ads / ChatGPT Ads approuvé — [ads.openai.com](https://ads.openai.com)
2. Une **clé API Ads** dans **Settings → API keys**  
   (pas une clé modèle de platform.openai.com)
3. Python 3.10+ en local, **ou** un hébergeur type Render pour une URL HTTPS

Test rapide de la clé :

```bash
curl -sS -H "Authorization: Bearer $OPENAI_ADS_API_KEY" \
  https://api.ads.openai.com/v1/ad_account
```

---

## Brancher votre IA — choisissez le mode

| | **A. Local (sur votre machine)** | **B. Hébergé (URL en ligne)** |
| --- | --- | --- |
| **Idéal pour** | Claude Desktop, Cursor, Claude Code | ChatGPT, Claude (web), clients MCP custom, n8n |
| **Fonctionnement** | L’app lance le serveur | Vous déployez une fois, puis collez URL + token |
| **Transport** | `stdio` | Streamable HTTP sur `/mcp` |
| **Auth** | Clé Ads dans l’env | Clé Ads sur le serveur + `Authorization: Bearer …` côté client |

> Règle simple : **app bureau → A**, **chat web / cloud → B**.

OpenAI Ads n’a **pas d’OAuth** pour l’instant. Les clients distants utilisent toujours URL + bearer.

---

## Configuration

| Variable | Requis | Défaut | Description |
| --- | --- | --- | --- |
| `OPENAI_ADS_API_KEY` | oui | — | Clé API Ads Manager |
| `MCP_TRANSPORT` | non | `stdio` | `stdio` (local), `http` (hébergé, recommandé), ou `sse` (legacy) |
| `MCP_BEARER_TOKEN` | hébergé | — | Secret partagé : `Authorization: Bearer <token>` (min. 24 car. ; `openssl rand -hex 32`) |
| `OPENAI_ADS_MCP_READONLY` | non | `1` (actif) | Masque les outils d’écriture. Lecture seule par défaut. |
| `OPENAI_ADS_MCP_ALLOW_WRITES` | non | off | Mettre `1` plus tard pour éditer les campagnes (redémarrage requis) |
| `OPENAI_ADS_BUDGET_CEILING_USD` | non | `100` | Garde-fou budget quand les écritures sont actives |
| `PORT` / `HOST` | hébergé | injecté / `0.0.0.0` | Bind (Render injecte `PORT`) |
| `MCP_STATELESS_HTTP` | non | `true` | Idéal derrière Render / proxies |

Copiez `python/.env.example` si vous lancez depuis les sources.

---

## A. Local — Claude Desktop

1. Clone et install :

```bash
git clone https://github.com/webloom-agency/chatgpt-ads-mcp.git
cd chatgpt-ads-mcp/python
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

2. Config Claude :

- macOS : `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows : `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "chatgpt-ads": {
      "command": "/chemin/absolu/vers/chatgpt-ads-mcp/python/.venv/bin/python",
      "args": ["-m", "openai_ads_mcp"],
      "env": {
        "MCP_TRANSPORT": "stdio",
        "OPENAI_ADS_API_KEY": "votre_cle_ads_ici",
        "OPENAI_ADS_MCP_READONLY": "1"
      }
    }
  }
}
```

3. Redémarrez Claude Desktop. Essayez : *« Appelle get_account, puis list_campaigns, puis get_insights sur le compte. »*

### Install rapide sans clone (PyPI / uvx)

```bash
claude mcp add chatgpt-ads \
  -e OPENAI_ADS_API_KEY=votre_cle_ads_ici \
  -e OPENAI_ADS_MCP_READONLY=1 \
  -- uvx openai-ads-mcp
```

---

## A. Local — Cursor

`~/.cursor/mcp.json` (ou `.cursor/mcp.json` du projet) :

```json
{
  "mcpServers": {
    "chatgpt-ads": {
      "command": "uvx",
      "args": ["openai-ads-mcp"],
      "env": {
        "OPENAI_ADS_API_KEY": "votre_cle_ads_ici",
        "OPENAI_ADS_MCP_READONLY": "1"
      }
    }
  }
}
```

Ou pointez `command` vers le Python du venv et `args` vers `["-m", "openai_ads_mcp"]` comme pour Claude Desktop.

---

## B. Hébergé — déployer sur Render (ou toute VM)

Recommandé pour ChatGPT et tout client MCP distant.

### Interface Render (Docker)

1. Poussez ce dépôt sur GitHub.
2. **New → Web Service** → connectez le dépôt.
3. Réglages :

| Champ | Valeur |
| --- | --- |
| **Language** | **Docker** |
| **Root Directory** | **laisser vide** |
| **Branch** | `main` |
| **Health Check Path** | `/healthz` |

4. Variables d’environnement :

| Clé | Valeur |
| --- | --- |
| `OPENAI_ADS_API_KEY` | votre clé Ads |
| `MCP_BEARER_TOKEN` | `openssl rand -hex 32` |
| `MCP_TRANSPORT` | `http` |
| `MCP_STATELESS_HTTP` | `true` |
| `OPENAI_ADS_MCP_READONLY` | `1` |
| `HOST` | `0.0.0.0` |

5. Déployez. URL MCP : `https://<service>.onrender.com/mcp`

Ou **New → Blueprint** avec [`render.yaml`](./render.yaml).

Plus de détail : [DEPLOY.md](./DEPLOY.md) (EN).

Vérification :

```bash
curl -sS https://VOTRE-SERVICE.onrender.com/healthz
curl -sS -o /dev/null -w "%{http_code}\n" https://VOTRE-SERVICE.onrender.com/mcp
# → 401 sans bearer est normal
```

---

## B. Hébergé — ChatGPT

1. Déployez comme ci-dessus.
2. ChatGPT → **Settings → Connectors** (mode développeur / connecteur custom).
3. URL du serveur : `https://VOTRE-SERVICE.onrender.com/mcp`
4. En-tête : `Authorization` = `Bearer VOTRE_MCP_BEARER_TOKEN`
5. Activez le connecteur dans un chat et demandez les perfs des campagnes.

Via l’API Responses :

```python
from openai import OpenAI

client = OpenAI()
resp = client.responses.create(
    model="gpt-4.1",
    tools=[{
        "type": "mcp",
        "server_label": "chatgpt-ads",
        "server_url": "https://VOTRE-SERVICE.onrender.com/mcp",
        "headers": {"Authorization": "Bearer VOTRE_MCP_BEARER_TOKEN"},
        "require_approval": "never",
    }],
    input="Utilise get_account, list_campaigns, puis get_insights pour les perfs.",
)
print(resp.output_text)
```

---

## B. Hébergé — tout autre client MCP

Claude (web), Cursor (remote), n8n, apps custom, SDK — même format :

```json
{
  "url": "https://VOTRE-SERVICE.onrender.com/mcp",
  "headers": {
    "Authorization": "Bearer VOTRE_MCP_BEARER_TOKEN"
  }
}
```

Ne mettez **pas** `OPENAI_ADS_API_KEY` dans les headers du client. Elle reste sur le serveur.

---

## Astuces reporting

- Préférez **omettre** `time_range` : l’API utilise alors sa fenêtre récente par défaut.
- Bon premier appel : `get_insights` avec `scope=account`, `aggregation_level=campaign`, `time_granularity=daily`.
- Les réponses incluent un `summary` (totaux + par campagne).
- Si vous passez un `unix_range`, les timestamps doivent être alignés à l’**heure pleine** (le serveur les aligne).

---

## Activer l’édition de campagnes plus tard

Les outils d’écriture (`create_campaign`, `update_campaign`, `set_campaign_state`, `build_campaign`, …) sont dans le code mais **masqués** jusqu’à activation :

1. Définir `OPENAI_ADS_MCP_ALLOW_WRITES=1` (ou `OPENAI_ADS_MCP_READONLY=0`)
2. Redémarrer / redéployer
3. Les créations restent **en pause** par défaut ; l’activation est une étape séparée
4. Les budgets respectent `OPENAI_ADS_BUDGET_CEILING_USD` (défaut 100 $) sauf confirmation

---

## Dépannage

| Symptôme | Solution |
| --- | --- |
| `401` sur `/mcp` | Le bearer doit correspondre exactement à `MCP_BEARER_TOKEN` |
| Ads API `401` | Mauvaise clé — recréez dans Ads Manager → Settings → API keys |
| `duplicate_parameter` / `fields` | Redéployez une build qui encode `fields[]` (main actuel) |
| Handshake MCP qui bloque | Garder `MCP_TRANSPORT=http` + `MCP_STATELESS_HTTP=true` (éviter SSE sur Render) |
| Instance Render gratuite endormie | Premier appel après inactivité ~30 s |
| Root Directory = `python` en Docker | Le laisser **vide** ; le `Dockerfile` racine copie déjà `python/` |

---

## Développement

```bash
cd python
pip install -e .
pytest -q
python -m openai_ads_mcp          # stdio
MCP_TRANSPORT=http MCP_BEARER_TOKEN=$(openssl rand -hex 32) \
  OPENAI_ADS_API_KEY=... python -m openai_ads_mcp
```

Docker :

```bash
docker build -t chatgpt-ads-mcp .
docker run --rm -p 8000:8000 \
  -e OPENAI_ADS_API_KEY=... \
  -e MCP_BEARER_TOKEN="$(openssl rand -hex 32)" \
  -e PORT=8000 \
  chatgpt-ads-mcp
```

Un runtime Node existe aussi dans `typescript/` (`npx -y openai-ads-mcp`) avec les mêmes outils. Pour ce fork (client custom / Render), préférez l’image **Python** ci-dessus.

---

## Licence

MIT — voir [LICENSE](./LICENSE).
