"""Account-level OpenAI Ads MCP tools."""

from __future__ import annotations

from ._core import *


@ads_tool(open_world=True)
async def get_account() -> str:
    """Get the authenticated OpenAI Ads account for OPENAI_ADS_API_KEY.

    Call this first and confirm the returned adacct_… id matches the Ads Manager
    account you intend. The MCP is API-key scoped to exactly one ad account;
    there is no OAuth account picker, and pasting another adacct_… in a prompt
    does not switch accounts. Returns id, name, timezone, currency, and settings.
    """
    client, client_err = _get_client_or_error()
    if client_err:
        return client_err
    try:
        return _ok(await client.get("/ad_account"))
    except OpenAIAdsAPIError as e:
        return _err(e)


__all__ = ("get_account",)
