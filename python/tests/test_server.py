"""Tests for the OpenAI Ads MCP server."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

os.environ.setdefault("OPENAI_ADS_API_KEY", "test_ads_key")
# Full tool suite (including writes) for unit tests. Production defaults to read-only.
os.environ["OPENAI_ADS_MCP_ALLOW_WRITES"] = "1"
os.environ.pop("OPENAI_ADS_MCP_READONLY", None)

import openai_ads_mcp  # noqa: E402
from openai_ads_mcp import mcp  # noqa: E402
from openai_ads_mcp.client import OpenAIAdsAPIError, OpenAIAdsClient  # noqa: E402

_SERVICE_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST_PATH = _SERVICE_ROOT / "mcpToolManifest.json"
EXPECTED_TOOLS = [tool["name"] for tool in json.loads(_MANIFEST_PATH.read_text())["tools"]]

EXPECTED_WRITE_TOOLS = {
    "create_campaign",
    "update_campaign",
    "set_campaign_state",
    "create_ad_group",
    "update_ad_group",
    "set_ad_group_state",
    "upload_creative",
    "create_ad",
    "update_ad",
    "set_ad_state",
    "manage_audience",
    "manage_conversions",
    "send_conversions",
    "build_campaign",
    "bulk_ab_test_hints",
}
EXPECTED_DESTRUCTIVE_TOOLS = {
    "create_campaign",
    "update_campaign",
    "set_campaign_state",
    "set_ad_group_state",
    "set_ad_state",
    "manage_audience",
    "build_campaign",
}
EXPECTED_OPEN_WORLD_TOOLS = {
    "get_account",
    "list_campaigns",
    "get_campaign",
    "create_campaign",
    "update_campaign",
    "set_campaign_state",
    "list_ad_groups",
    "get_ad_group",
    "create_ad_group",
    "update_ad_group",
    "set_ad_group_state",
    "list_ads",
    "get_ad",
    "upload_creative",
    "create_ad",
    "update_ad",
    "set_ad_state",
    "get_insights",
    "list_audiences",
    "get_audience",
    "search_geo",
    "manage_audience",
    "manage_conversions",
    "send_conversions",
    "build_campaign",
    "bulk_ab_test_hints",
}
EXPECTED_READONLY_WRITE_TOOLS = {"manage_conversions"}

USED_OPENAI_ADS_PATHS = {
    "/ad_account",
    "/ad_account/insights",
    "/campaigns",
    "/campaigns/{campaign_id}",
    "/campaigns/{campaign_id}/activate",
    "/campaigns/{campaign_id}/pause",
    "/campaigns/{campaign_id}/archive",
    "/campaigns/{campaign_id}/insights",
    "/ad_groups",
    "/ad_groups/{ad_group_id}",
    "/ad_groups/{ad_group_id}/activate",
    "/ad_groups/{ad_group_id}/pause",
    "/ad_groups/{ad_group_id}/archive",
    "/ad_groups/{ad_group_id}/insights",
    "/ads",
    "/ads/{ad_id}",
    "/ads/{ad_id}/activate",
    "/ads/{ad_id}/pause",
    "/ads/{ad_id}/archive",
    "/ads/{ad_id}/insights",
    "/upload",
    "/custom_audiences",
    "/custom_audiences/{custom_audience_id}",
    "/custom_audiences/upload",
    "/custom_audiences/{custom_audience_id}/archive",
    "/conversions/pixels",
    "/conversions/api_keys",
    "/conversions/event_settings",
    "/conversions/insights",
    "/geo_lookup/search",
}


@pytest.fixture(autouse=True)
def mock_client():
    import openai_ads_mcp._core as core

    mock = AsyncMock()
    mock.get = AsyncMock(return_value={"ok": True, "data": []})
    mock.post = AsyncMock(return_value={"ok": True, "id": "created"})
    mock.upload_file = AsyncMock(return_value={"file_id": "file_123"})
    mock.post_conversions = AsyncMock(return_value={"ok": True, "received": 1})
    original = core._client
    core._client = mock
    yield mock
    core._client = original


class TestToolRegistration:
    def _tools(self):
        return mcp._tool_manager._tools

    def test_all_manifest_tools_registered(self):
        registered = set(self._tools())
        expected = set(EXPECTED_TOOLS)
        assert registered == expected

    def test_tool_count_is_curated(self):
        assert len(self._tools()) == 27

    def test_all_tools_have_descriptions_and_annotations(self):
        for name, tool in self._tools().items():
            assert tool.description and len(tool.description) > 20, name
            assert tool.annotations is not None, name
            assert tool.annotations.readOnlyHint is not None, name
            assert tool.annotations.destructiveHint is not None, name
            assert tool.annotations.idempotentHint is not None, name
            assert tool.annotations.openWorldHint is not None, name

    def test_write_annotations_match_expected_set(self):
        writes = {name for name, tool in self._tools().items() if tool.annotations.readOnlyHint is False}
        assert writes == EXPECTED_WRITE_TOOLS

    def test_destructive_annotations_match_expected_set(self):
        destructive = {name for name, tool in self._tools().items() if tool.annotations.destructiveHint is True}
        assert destructive == EXPECTED_DESTRUCTIVE_TOOLS
        assert destructive <= EXPECTED_WRITE_TOOLS

    def test_open_world_annotations_match_expected_set(self):
        open_world = {name for name, tool in self._tools().items() if tool.annotations.openWorldHint is True}
        assert open_world == EXPECTED_OPEN_WORLD_TOOLS

    def test_no_structured_output_schema(self):
        offenders = [tool.name for tool in mcp._tool_manager.list_tools() if tool.output_schema]
        assert not offenders

    def test_trakkr_resource_registered(self):
        resources = {str(resource.uri): resource for resource in mcp._resource_manager.list_resources()}
        assert "openai-ads://trakkr-visibility" in resources
        assert resources["openai-ads://trakkr-visibility"].mime_type == "text/markdown"

    @pytest.mark.asyncio
    async def test_readonly_mode_hides_writes(self):
        code = (
            "import json, openai_ads_mcp; "
            "print(json.dumps(sorted(openai_ads_mcp.mcp._tool_manager._tools)))"
        )
        env = os.environ.copy()
        env.pop("OPENAI_ADS_MCP_ALLOW_WRITES", None)
        env["OPENAI_ADS_MCP_READONLY"] = "1"
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        names = set(json.loads(proc.stdout))
        assert "get_account" in names
        assert "get_insights" in names
        assert "draft_context_hints" in names
        assert "create_campaign" not in names
        assert "send_conversions" not in names
        assert "manage_conversions" in names
        assert not (names & (EXPECTED_WRITE_TOOLS - EXPECTED_READONLY_WRITE_TOOLS))

    @pytest.mark.asyncio
    async def test_default_is_readonly_without_allow_writes(self):
        code = (
            "import json, openai_ads_mcp; "
            "print(json.dumps(sorted(openai_ads_mcp.mcp._tool_manager._tools)))"
        )
        env = os.environ.copy()
        env.pop("OPENAI_ADS_MCP_ALLOW_WRITES", None)
        env.pop("OPENAI_ADS_MCP_READONLY", None)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        names = set(json.loads(proc.stdout))
        assert "get_insights" in names
        assert "create_campaign" not in names
        assert "update_campaign" not in names
        assert "set_campaign_state" not in names


class TestReadTools:
    @pytest.mark.asyncio
    async def test_get_account(self, mock_client):
        from openai_ads_mcp.tools_account import get_account

        data = json.loads(await get_account())
        assert data["ok"] is True
        mock_client.get.assert_called_once_with("/ad_account")

    @pytest.mark.asyncio
    async def test_campaign_reads(self, mock_client):
        from openai_ads_mcp.tools_campaigns import get_campaign, list_campaigns

        await list_campaigns(limit=50, order="asc")
        mock_client.get.assert_called_with("/campaigns", params={"limit": 50, "order": "asc"})
        mock_client.get.reset_mock()
        await get_campaign("camp_1")
        mock_client.get.assert_called_once_with("/campaigns/camp_1")

    @pytest.mark.asyncio
    async def test_ad_group_reads(self, mock_client):
        from openai_ads_mcp.tools_adgroups import get_ad_group, list_ad_groups

        await list_ad_groups(campaign_id="camp_1")
        mock_client.get.assert_called_with(
            "/ad_groups",
            params={"campaign_id": "camp_1", "limit": 20, "order": "desc"},
        )
        mock_client.get.reset_mock()
        await get_ad_group("ag_1")
        mock_client.get.assert_called_once_with("/ad_groups/ag_1")

    @pytest.mark.asyncio
    async def test_ad_reads(self, mock_client):
        from openai_ads_mcp.tools_ads import get_ad, list_ads

        await list_ads(ad_group_id="ag_1")
        mock_client.get.assert_called_with(
            "/ads",
            params={"ad_group_id": "ag_1", "limit": 20, "order": "desc"},
        )
        mock_client.get.reset_mock()
        await get_ad("ad_1")
        mock_client.get.assert_called_once_with("/ads/ad_1")

    @pytest.mark.asyncio
    async def test_audience_and_geo_reads(self, mock_client):
        from openai_ads_mcp.tools_audiences import get_audience, list_audiences, search_geo

        await list_audiences(limit=10)
        mock_client.get.assert_called_with("/custom_audiences", params={"limit": 10, "order": "desc"})
        mock_client.get.reset_mock()
        await get_audience("aud_1")
        mock_client.get.assert_called_once_with("/custom_audiences/aud_1")
        mock_client.get.reset_mock()
        await search_geo("London")
        mock_client.get.assert_called_once_with("/geo_lookup/search", params={"q": "London", "limit": 20})


class TestInsights:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("scope", "entity_id", "aggregation_level", "path"),
        [
            ("account", None, "campaign", "/ad_account/insights"),
            ("campaign", "camp_1", "campaign", "/campaigns/camp_1/insights"),
            ("ad_group", "ag_1", "ad_group", "/ad_groups/ag_1/insights"),
            ("ad", "ad_1", "ad", "/ads/ad_1/insights"),
        ],
    )
    async def test_get_insights_scopes(self, mock_client, scope, entity_id, aggregation_level, path):
        from openai_ads_mcp.tools_insights import get_insights

        await get_insights(
            scope=scope,
            entity_id=entity_id,
            aggregation_level=aggregation_level,
            time_range={"type": "unix_range", "start": 1764547200, "end": 1765152000},
            segments="country",
            override_segment_group_order=[aggregation_level, "country"],
            fields=["campaign.id", "metadata.readable_time"],
            filters=[{"field": "spend", "operator": "GREATER_THAN", "value": 10}],
            sort=[{"field": "spend", "direction": "desc"}],
            limit=100,
        )
        mock_client.get.assert_called_once()
        assert mock_client.get.call_args.args[0] == path
        params = mock_client.get.call_args.kwargs["params"]
        assert params["time_granularity"] == "daily"
        assert params["aggregation_level"] == aggregation_level
        assert params["limit"] == 100
        assert params["segments"] == ["country"]
        assert params["override_segment_group_order"] == [aggregation_level, "country"]
        assert json.loads(params["time_ranges"][0]) == {
            "type": "unix_range",
            "start": 1764547200,
            "end": 1765152000,
        }
        assert json.loads(params["filters"][0])["operator"] == "GREATER_THAN"
        assert json.loads(params["sort"][0])["direction"] == "desc"
        assert params["fields"] == ["campaign.id", "metadata.readable_time"]

    @pytest.mark.asyncio
    async def test_get_insights_defaults_metric_fields(self, mock_client):
        from openai_ads_mcp.tools_insights import get_insights

        await get_insights(scope="account")
        params = mock_client.get.call_args.kwargs["params"]
        assert params["aggregation_level"] == "campaign"
        assert "impressions" in params["fields"]
        assert "clicks" in params["fields"]
        assert "spend" in params["fields"]

    @pytest.mark.asyncio
    async def test_get_insights_hour_aligns_unix_range(self, mock_client):
        from openai_ads_mcp.tools_insights import get_insights

        # Misaligned end (16:53:20) should ceil to next hour.
        await get_insights(
            scope="account",
            time_range={"type": "unix_range", "start": 1786176000, "end": 1788800000},
        )
        params = mock_client.get.call_args.kwargs["params"]
        encoded = json.loads(params["time_ranges"][0])
        assert encoded["start"] == 1786176000
        assert encoded["end"] == 1788800400
        assert encoded["end"] % 3600 == 0

    @pytest.mark.asyncio
    async def test_get_insights_product_include_rules(self, mock_client):
        from openai_ads_mcp.tools_insights import get_insights

        await get_insights(
            scope="campaign",
            entity_id="camp_1",
            aggregation_level="campaign",
            segments=["product"],
            override_segment_group_order=["product", "campaign"],
            includes=["zero_impression_products"],
            fields=["product.item_id", "campaign.id"],
        )
        params = mock_client.get.call_args.kwargs["params"]
        assert params["segments"] == ["product"]
        assert params["override_segment_group_order"] == ["product", "campaign"]
        assert params["includes"] == ["zero_impression_products"]

        mock_client.get.reset_mock()
        data = json.loads(await get_insights(
            scope="campaign",
            entity_id="camp_1",
            aggregation_level="campaign",
            segments=["product"],
            fields=["campaign.id"],
        ))
        assert data["error"] is True
        mock_client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_insights_requires_entity_id(self, mock_client):
        from openai_ads_mcp.tools_insights import get_insights

        data = json.loads(await get_insights(scope="campaign"))
        assert data["error"] is True
        mock_client.get.assert_not_called()


class TestWriteTools:
    @pytest.mark.asyncio
    async def test_create_campaign_happy_path_paused_default(self, mock_client):
        from openai_ads_mcp.tools_campaigns import create_campaign

        await create_campaign(name="Launch test", budget_usd=25, idempotency_key="campaign-create-1")
        mock_client.post.assert_called_once_with(
            "/campaigns",
            json={
                "name": "Launch test",
                "status": "paused",
                "budget": {"lifetime_spend_limit_micros": 25_000_000},
            },
            idempotency_key="campaign-create-1",
        )

    @pytest.mark.asyncio
    async def test_create_campaign_budget_guard(self, mock_client, monkeypatch):
        from openai_ads_mcp.tools_campaigns import create_campaign

        monkeypatch.setenv("OPENAI_ADS_BUDGET_CEILING_USD", "10")
        data = json.loads(await create_campaign(name="Big test", budget_usd=50))
        assert data["error"] is True
        assert "confirm_budget=True" in data["message"]
        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_campaign_rejects_active_status(self, mock_client):
        from openai_ads_mcp.tools_campaigns import create_campaign, update_campaign

        data = json.loads(await create_campaign(name="Launch test", budget_usd=25, status="active"))
        assert data["error"] is True
        mock_client.post.assert_not_called()
        data = json.loads(await update_campaign(campaign_id="camp_1", status="active"))
        assert data["error"] is True
        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_conversion_optimized_campaign(self, mock_client):
        from openai_ads_mcp.tools_campaigns import create_campaign

        await create_campaign(
            name="Conversion test",
            budget_usd=25,
            bidding_type="conversions",
            conversion_event_setting_ids=["event_setting_1"],
        )
        payload = mock_client.post.call_args.kwargs["json"]
        assert payload["bidding_type"] == "conversions"
        assert payload["conversion_event_setting_ids"] == ["event_setting_1"]

        mock_client.post.reset_mock()
        data = json.loads(await create_campaign(
            name="Broken conversion test",
            budget_usd=25,
            bidding_type="conversions",
        ))
        assert data["error"] is True
        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_set_state_tools(self, mock_client):
        from openai_ads_mcp.tools_adgroups import set_ad_group_state
        from openai_ads_mcp.tools_ads import set_ad_state
        from openai_ads_mcp.tools_campaigns import set_campaign_state

        await set_campaign_state("camp_1", "pause")
        mock_client.post.assert_called_with("/campaigns/camp_1/pause")
        await set_ad_group_state("ag_1", "archive")
        mock_client.post.assert_called_with("/ad_groups/ag_1/archive")
        await set_ad_state("ad_1", "activate")
        mock_client.post.assert_called_with("/ads/ad_1/activate")

    @pytest.mark.asyncio
    async def test_ad_group_and_ad_creation_payloads(self, mock_client):
        from openai_ads_mcp.tools_adgroups import create_ad_group
        from openai_ads_mcp.tools_ads import create_ad

        await create_ad_group(
            campaign_id="camp_1",
            name="Searchers",
            billing_event="click",
            max_bid_usd=1.25,
            context_hints=["Product: AI visibility"],
            product_set={
                "product_feed_id": "product_feed_123",
                "filters": [{"field": "brand", "operator": "in", "values": ["Trakkr"]}],
            },
            idempotency_key="ad-group-create-1",
        )
        mock_client.post.assert_called_once_with(
            "/ad_groups",
            json={
                "campaign_id": "camp_1",
                "name": "Searchers",
                "status": "paused",
                "bidding_config": {"billing_event_type": "click", "max_bid_micros": 1_250_000},
                "context_hints": ["Product: AI visibility"],
                "product_set": {
                    "product_feed_id": "product_feed_123",
                    "filters": [{"field": "brand", "operator": "in", "values": ["Trakkr"]}],
                },
            },
            idempotency_key="ad-group-create-1",
        )
        mock_client.post.reset_mock()
        await create_ad(
            ad_group_id="ag_1",
            name="Card A",
            creative_type="chat_card",
            title="Track AI visibility",
            body="See where your brand appears in AI answers.",
            target_url="https://trakkr.ai",
            file_id="file_1",
            idempotency_key="ad-create-1",
        )
        payload = mock_client.post.call_args.kwargs["json"]
        assert payload["status"] == "paused"
        assert payload["creative"]["target_url"] == "https://trakkr.ai"
        assert mock_client.post.call_args.kwargs["idempotency_key"] == "ad-create-1"

    @pytest.mark.asyncio
    async def test_update_tools_reject_active_status(self, mock_client):
        from openai_ads_mcp.tools_adgroups import update_ad_group
        from openai_ads_mcp.tools_ads import update_ad

        data = json.loads(await update_ad_group(ad_group_id="ag_1", status="active"))
        assert data["error"] is True
        data = json.loads(await update_ad(ad_id="ad_1", status="active"))
        assert data["error"] is True
        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_upload_creative_both_modes(self, mock_client, tmp_path):
        from openai_ads_mcp.tools_ads import upload_creative

        await upload_creative(image_url="https://example.com/image.png")
        mock_client.post.assert_called_once_with("/upload", json={"image_url": "https://example.com/image.png"})
        mock_client.post.reset_mock()
        image = tmp_path / "image.png"
        image.write_bytes(b"png")
        await upload_creative(file_path=str(image))
        mock_client.upload_file.assert_called_once_with("/upload", str(image))


class TestHelpers:
    @pytest.mark.asyncio
    async def test_build_campaign_orchestrates_paused_tree(self, mock_client):
        from openai_ads_mcp.helpers import build_campaign

        mock_client.post = AsyncMock(side_effect=[
            {"id": "camp_1", "status": "paused"},
            {"id": "ag_1", "status": "paused"},
            {"id": "ad_1", "status": "paused"},
            {"id": "ad_2", "status": "paused"},
        ])
        result = await build_campaign(
            name="Category test",
            budget_usd=50,
            ad_group={"name": "Buyers", "billing_event": "click", "max_bid_usd": 1.5},
            ads=[
                {
                    "name": "Variant A",
                    "creative_type": "chat_card",
                    "title": "Find your AI gaps",
                    "body": "Track your brand in AI answers.",
                    "target_url": "https://trakkr.ai",
                    "file_id": "file_1",
                },
                {
                    "name": "Variant B",
                    "creative_type": "chat_card",
                    "title": "See AI visibility",
                    "body": "Know where ChatGPT mentions you.",
                    "target_url": "https://trakkr.ai",
                    "file_id": "file_1",
                },
            ],
            idempotency_key="tree-1",
        )
        data = json.loads(result)
        assert data["created"]["campaign"]["id"] == "camp_1"
        assert data["ad_ids"] if "ad_ids" in data else True
        assert "Created paused" in data["note"]
        assert [call.args[0] for call in mock_client.post.call_args_list] == ["/campaigns", "/ad_groups", "/ads", "/ads"]
        assert [call.kwargs.get("idempotency_key") for call in mock_client.post.call_args_list] == [
            "tree-1:campaign",
            "tree-1:ad_group",
            "tree-1:ad_0",
            "tree-1:ad_1",
        ]

    @pytest.mark.asyncio
    async def test_build_campaign_product_feed_uses_product_set(self, mock_client):
        from openai_ads_mcp.helpers import build_campaign

        mock_client.post = AsyncMock(side_effect=[
            {"id": "camp_1", "status": "paused"},
            {"id": "ag_1", "status": "paused"},
            {"id": "ad_1", "status": "paused"},
        ])
        data = json.loads(await build_campaign(
            name="Feed test",
            budget_usd=50,
            ad_group={
                "name": "Feed buyers",
                "billing_event": "click",
                "max_bid_usd": 1.5,
                "product_set": {
                    "product_feed_id": "product_feed_123",
                    "filters": [{"field": "brand", "operator": "in", "values": ["Trakkr"]}],
                },
            },
            ads=[{
                "name": "Template A",
                "creative_type": "product_ad_template",
                "title": "Find your AI gaps",
                "body": "Track your brand in AI answers.",
            }],
        ))
        assert data["created"]["campaign"]["id"] == "camp_1"
        assert mock_client.post.call_args_list[0].kwargs["json"]["mode"] == "product_feed"
        assert mock_client.post.call_args_list[1].kwargs["json"]["product_set"]["product_feed_id"] == "product_feed_123"

    @pytest.mark.asyncio
    async def test_build_conversion_campaign_requires_click_billing(self, mock_client):
        from openai_ads_mcp.helpers import build_campaign

        data = json.loads(await build_campaign(
            name="Conversion tree",
            budget_usd=50,
            bidding_type="conversions",
            conversion_event_setting_id="event_setting_1",
            ad_group={"name": "Converters", "billing_event": "impression", "max_bid_usd": 5},
            ads=[{
                "name": "Card",
                "creative_type": "chat_card",
                "title": "Title",
                "body": "Body",
                "target_url": "https://trakkr.ai",
                "file_id": "file_1",
            }],
        ))

        assert data["error"] is True
        assert "billing_event='click'" in data["message"]
        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_build_campaign_returns_created_so_far_on_failure(self, mock_client):
        from openai_ads_mcp.helpers import build_campaign

        mock_client.post = AsyncMock(side_effect=[
            {"id": "camp_1", "status": "paused"},
            OpenAIAdsAPIError(403, "Access denied."),
        ])
        data = json.loads(await build_campaign(
            name="Category test",
            budget_usd=50,
            ad_group={"name": "Buyers", "billing_event": "click", "max_bid_usd": 1.5},
            ads=[{
                "name": "Variant A",
                "creative_type": "chat_card",
                "title": "Find your AI gaps",
                "body": "Track your brand in AI answers.",
                "target_url": "https://trakkr.ai",
                "file_id": "file_1",
            }],
        ))
        assert data["created"]["campaign"]["id"] == "camp_1"
        assert data["error"]["message"] == "Access denied."

    @pytest.mark.asyncio
    async def test_draft_context_hints_is_deterministic(self, mock_client):
        from openai_ads_mcp.helpers import draft_context_hints

        first = await draft_context_hints("AI visibility monitoring", audience="growth teams", keywords="ChatGPT,Perplexity")
        second = await draft_context_hints("AI visibility monitoring", audience="growth teams", keywords="ChatGPT,Perplexity")
        assert first == second
        data = json.loads(first)
        assert data["context_hints"]
        assert "AI visibility monitoring" in data["context_hints"][0]
        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_bulk_ab_test_hints_creates_paused_ads(self, mock_client):
        from openai_ads_mcp.helpers import bulk_ab_test_hints

        mock_client.post = AsyncMock(side_effect=[{"id": "ad_1"}, {"id": "ad_2"}])
        data = json.loads(await bulk_ab_test_hints("ag_1", [
            {
                "title": "Find your AI gaps",
                "body": "Track your brand in AI answers.",
                "target_url": "https://trakkr.ai",
                "file_id": "file_1",
            },
            {
                "title": "See AI visibility",
                "body": "Know where ChatGPT mentions you.",
                "target_url": "https://trakkr.ai",
                "file_id": "file_1",
            },
        ], idempotency_key="bulk-1"))
        assert data["ad_ids"] == ["ad_1", "ad_2"]
        for call in mock_client.post.call_args_list:
            assert call.kwargs["json"]["status"] == "paused"
        assert [call.kwargs.get("idempotency_key") for call in mock_client.post.call_args_list] == ["bulk-1:ad_0", "bulk-1:ad_1"]


class TestConversions:
    @pytest.mark.asyncio
    async def test_manage_conversions_actions(self, mock_client):
        from openai_ads_mcp.tools_conversions import manage_conversions

        await manage_conversions(action="create_pixel", name="Website pixel")
        mock_client.post.assert_called_with("/conversions/pixels", json={"name": "Website pixel", "client_type": "web"})
        await manage_conversions(action="create_api_key", name="Server key")
        mock_client.post.assert_called_with("/conversions/api_keys", json={"name": "Server key"})
        await manage_conversions(action="get_event_settings")
        mock_client.get.assert_called_with("/conversions/event_settings", params={"limit": 20, "order": "desc"})
        await manage_conversions(
            action="set_event_settings",
            name="Purchase",
            event_type="order_created",
            attribution_window_days=7,
            source_ids=["src_1"],
        )
        mock_client.post.assert_called_with(
            "/conversions/event_settings",
            json={
                "name": "Purchase",
                "event_type": "order_created",
                "attribution_window_days": 7,
                "source_ids": ["src_1"],
            },
        )
        await manage_conversions(
            action="get_insights",
            aggregation_level="campaign",
            time_ranges=["2026-06-01:2026-06-07"],
            entity_ids=["camp_1"],
        )
        mock_client.post.assert_called_with(
            "/conversions/insights",
            json={
                "aggregation_level": "campaign",
                "time_ranges": ["2026-06-01:2026-06-07"],
                "entity_ids": ["camp_1"],
            },
        )

    @pytest.mark.asyncio
    async def test_send_conversions_rejects_too_many(self, mock_client):
        from openai_ads_mcp.tools_conversions import send_conversions

        data = json.loads(await send_conversions("px_1", [{"id": str(i), "type": "order_created"} for i in range(1001)]))
        assert data["error"] is True
        mock_client.post_conversions.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_conversions_rejects_stale_timestamp(self, mock_client):
        from openai_ads_mcp.tools_conversions import send_conversions

        stale = 1_000
        data = json.loads(await send_conversions("px_1", [{
            "id": "evt_1",
            "type": "order_created",
            "timestamp_ms": stale,
            "data": {"type": "contents"},
            "action_source": "web",
            "source_url": "https://example.com",
        }]))
        assert data["error"] is True
        assert "older than 7 days" in data["message"]
        mock_client.post_conversions.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_conversions_happy_path(self, mock_client):
        from openai_ads_mcp._core import _now_ms
        from openai_ads_mcp.tools_conversions import send_conversions

        events = [{
            "id": "evt_1",
            "type": "order_created",
            "timestamp_ms": _now_ms(),
            "data": {
                "type": "contents",
                "contents": [{"id": "sku_1", "quantity": 1, "amount": 2500, "currency": "USD"}],
            },
            "action_source": "web",
            "source_url": "https://example.com",
            "user": {"email_sha256": "a" * 64},
        }]
        data = json.loads(await send_conversions("px_1", events, validate_only=True))
        assert data["ok"] is True
        mock_client.post_conversions.assert_called_once_with("px_1", events, True)

    @pytest.mark.asyncio
    async def test_send_app_conversion_and_obref(self, mock_client):
        from openai_ads_mcp._core import _now_ms
        from openai_ads_mcp.tools_conversions import send_conversions

        event = {
            "id": "evt_app_1",
            "type": "app_installed",
            "timestamp_ms": _now_ms(),
            "data": {"type": "customer_action"},
            "action_source": "mobile_app",
            "user": {"obref": "opaque-browser-reference"},
        }
        data = json.loads(await send_conversions("px_1", [event]))
        assert data["ok"] is True
        mock_client.post_conversions.assert_called_once_with("px_1", [event], False)

        mock_client.post_conversions.reset_mock()
        event["action_source"] = "web"
        event["source_url"] = "https://example.com"
        data = json.loads(await send_conversions("px_1", [event]))
        assert data["error"] is True
        assert "mobile_app" in data["message"]
        mock_client.post_conversions.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_conversions_rejects_unsafe_user_data(self, mock_client):
        from openai_ads_mcp._core import _now_ms
        from openai_ads_mcp.tools_conversions import send_conversions

        def event_with_user(user):
            return [{
                "id": "evt_1",
                "type": "order_created",
                "timestamp_ms": _now_ms(),
                "data": {"type": "contents"},
                "user": user,
            }]

        unsafe_users = [
            {"email": "person@example.com"},
            {"external_id": "customer_123"},
            {"phone_number": "+15551234567"},
            {"phone_sha256": "a" * 64},
            {"email_sha256": "A" * 64},
        ]
        for user in unsafe_users:
            data = json.loads(await send_conversions("px_1", event_with_user(user)))
            assert data["error"] is True
        mock_client.post_conversions.assert_not_called()

    @pytest.mark.asyncio
    async def test_custom_conversions_require_custom_event_name(self, mock_client):
        from openai_ads_mcp._core import _now_ms
        from openai_ads_mcp.tools_conversions import send_conversions

        data = json.loads(await send_conversions("px_1", [{
            "id": "evt_1",
            "type": "custom",
            "timestamp_ms": _now_ms(),
            "data": {"type": "custom"},
        }]))
        assert data["error"] is True
        assert "custom_event_name" in data["message"]
        mock_client.post_conversions.assert_not_called()

    @pytest.mark.asyncio
    async def test_manage_conversions_readonly_blocks_write_actions(self, mock_client, monkeypatch):
        from openai_ads_mcp.tools_conversions import manage_conversions

        monkeypatch.setenv("OPENAI_ADS_MCP_READONLY", "1")
        data = json.loads(await manage_conversions(action="create_pixel", name="Blocked pixel"))
        assert data["error"] is True
        mock_client.post.assert_not_called()


class TestErrorHandling:
    def test_encode_ads_query_params_uses_brackets(self):
        from openai_ads_mcp.client import encode_ads_query_params

        encoded = encode_ads_query_params(
            {"fields": ["impressions", "clicks"], "limit": 20, "time_ranges": ['{"type":"unix_range"}']}
        )
        assert encoded == [
            ("fields[]", "impressions"),
            ("fields[]", "clicks"),
            ("limit", "20"),
            ("time_ranges[]", '{"type":"unix_range"}'),
        ]

    def test_soft_statuses_return_json(self):
        from openai_ads_mcp._core import _err

        for status in (400, 403, 404, 422, 429):
            data = json.loads(_err(OpenAIAdsAPIError(status, f"msg {status}")))
            assert data["error"] is True
            assert data["message"] == f"msg {status}"

    def test_hard_statuses_raise(self):
        from openai_ads_mcp._core import _err

        for status in (0, 401, 500, 503, 504):
            with pytest.raises(OpenAIAdsAPIError):
                _err(OpenAIAdsAPIError(status, f"boom {status}"))

    @pytest.mark.asyncio
    async def test_client_friendly_error_mapping(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            assert str(request.url) == "https://ads.test/v1/ad_account"
            return httpx.Response(401, json={"message": "bad key"}, request=request)

        client = OpenAIAdsClient("test", base_url="https://ads.test/v1")
        await client._client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://ads.test/v1")
        try:
            with pytest.raises(OpenAIAdsAPIError) as excinfo:
                await client.get("/ad_account")
        finally:
            await client.close()
        assert excinfo.value.status_code == 401
        assert excinfo.value.detail == "Invalid or expired OPENAI_ADS_API_KEY."

    @pytest.mark.asyncio
    async def test_client_sends_validate_only_for_conversions(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            assert str(request.url) == "https://bzr.openai.com/v1/events?pid=px_1"
            assert json.loads(request.content) == {
                "validate_only": True,
                "events": [{"id": "evt_1"}],
            }
            return httpx.Response(200, json={"ok": True}, request=request)

        client = OpenAIAdsClient("test", base_url="https://ads.test/v1")
        await client._conversions_client.aclose()
        client._conversions_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://bzr.openai.com/v1",
        )
        try:
            result = await client.post_conversions("px_1", [{"id": "evt_1"}], True)
        finally:
            await client.close()
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_client_sends_idempotency_key_header(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            assert str(request.url) == "https://ads.test/v1/campaigns"
            assert request.headers["Idempotency-Key"] == "create-1"
            return httpx.Response(200, json={"ok": True}, request=request)

        client = OpenAIAdsClient("test", base_url="https://ads.test/v1")
        await client._client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://ads.test/v1")
        try:
            data = await client.post("/campaigns", json={"name": "x"}, idempotency_key="create-1")
        finally:
            await client.close()
        assert data == {"ok": True}


class TestOpenAPIDrift:
    def test_openapi_contains_every_ads_endpoint_used(self):
        spec_path = _SERVICE_ROOT / "openapi.json"
        assert spec_path.exists()
        paths = set(json.loads(spec_path.read_text())["paths"])
        missing = USED_OPENAI_ADS_PATHS - paths
        assert not missing, f"Paths missing from vendored OpenAPI spec: {sorted(missing)}"


class TestHostedTransport:
    def test_bearer_token_prefers_mcp_bearer_token(self, monkeypatch):
        from openai_ads_mcp.hosted import mcp_bearer_token

        monkeypatch.setenv("MCP_BEARER_TOKEN", "primary-token-value-xxxxxxxxxx")
        monkeypatch.setenv("OPENAI_ADS_MCP_HTTP_TOKEN", "alias-token-value-xxxxxxxxxxx")
        assert mcp_bearer_token() == "primary-token-value-xxxxxxxxxx"

    def test_bearer_token_falls_back_to_http_token(self, monkeypatch):
        from openai_ads_mcp.hosted import mcp_bearer_token

        monkeypatch.delenv("MCP_BEARER_TOKEN", raising=False)
        monkeypatch.setenv("OPENAI_ADS_MCP_HTTP_TOKEN", "alias-token-value-xxxxxxxxxxx")
        assert mcp_bearer_token() == "alias-token-value-xxxxxxxxxxx"

    def test_build_hosted_app_requires_bearer(self, monkeypatch):
        from openai_ads_mcp.hosted import build_hosted_app

        monkeypatch.delenv("MCP_BEARER_TOKEN", raising=False)
        monkeypatch.delenv("OPENAI_ADS_MCP_HTTP_TOKEN", raising=False)
        with pytest.raises(ValueError, match="MCP_BEARER_TOKEN"):
            build_hosted_app("http")

    def test_rejects_placeholder_bearer_token(self, monkeypatch):
        from openai_ads_mcp.hosted import build_hosted_app

        monkeypatch.setenv("MCP_BEARER_TOKEN", "change_me_to_a_long_random_string")
        with pytest.raises(ValueError, match="placeholder"):
            build_hosted_app("http")

    def test_rejects_short_bearer_token(self, monkeypatch):
        from openai_ads_mcp.hosted import build_hosted_app

        monkeypatch.setenv("MCP_BEARER_TOKEN", "too-short")
        with pytest.raises(ValueError, match="at least"):
            build_hosted_app("http")

    def test_build_hosted_app_http(self, monkeypatch):
        from openai_ads_mcp.hosted import build_hosted_app

        monkeypatch.setenv("MCP_BEARER_TOKEN", "unit-test-bearer-token-ok-xxxx")
        app = build_hosted_app("http")
        assert app is not None
        paths = {getattr(route, "path", None) for route in app.router.routes}
        assert "/healthz" in paths
        assert "/" in paths
