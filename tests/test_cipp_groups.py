"""Tests for CIPP group write tools (create / add member / remove member / delete)."""
import os
import sys
from typing import Callable

import httpx
import pytest

sys.path.append(os.getcwd())

import cipp_tools  # noqa: E402


class _FakeConfig:
    """Stub CIPPConfig that skips real auth and reports as configured."""

    api_url = "https://cipp.example.test"
    is_configured = True

    async def get_access_token(self) -> str:
        return "test-token"


def _patch_async_client(monkeypatch, handler: Callable[[httpx.Request], httpx.Response]):
    """Force every httpx.AsyncClient() in cipp_tools to use a MockTransport."""
    real_async_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(cipp_tools.httpx, "AsyncClient", factory)


@pytest.mark.asyncio
async def test_create_distribution_list(monkeypatch):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = httpx.Response(200, content=request.content).json()
        return httpx.Response(200, json={"Results": ["Successfully created group Sales DL for client.onmicrosoft.com"]})

    _patch_async_client(monkeypatch, handler)
    config = _FakeConfig()

    response = await cipp_tools._cipp_post(config, "/api/AddGroup", {
        "tenantFilter": "client.onmicrosoft.com",
        "displayName": "Sales DL",
        "description": "Sales team",
        "username": "salesdl",
        "groupType": "Distribution",
        "allowExternal": False,
        "membershipRules": "",
        "owners": [],
        "members": ["alice@client.onmicrosoft.com"],
    })

    assert response.status_code == 200
    assert "/api/AddGroup" in captured["url"]
    assert captured["body"]["groupType"] == "Distribution"
    assert captured["body"]["members"] == ["alice@client.onmicrosoft.com"]
    payload = response.json()
    assert "Successfully created" in cipp_tools._format_results(payload)


@pytest.mark.asyncio
async def test_create_dynamic_group_sends_membership_rule(monkeypatch):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = httpx.Response(200, content=request.content).json()
        return httpx.Response(200, json={"Results": "Successfully created group SalesDynamic"})

    _patch_async_client(monkeypatch, handler)
    config = _FakeConfig()

    body = {
        "tenantFilter": "client.onmicrosoft.com",
        "displayName": "Sales Dynamic",
        "description": "",
        "username": "salesdynamic",
        "groupType": "Dynamic",
        "allowExternal": False,
        "membershipRules": '(user.department -eq "Sales")',
        "owners": [],
        "members": [],
    }
    await cipp_tools._cipp_post(config, "/api/AddGroup", body)

    assert captured["body"]["membershipRules"] == '(user.department -eq "Sales")'
    assert captured["body"]["groupType"] == "Dynamic"


@pytest.mark.asyncio
async def test_add_member_payload_format(monkeypatch):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = httpx.Response(200, content=request.content).json()
        return httpx.Response(200, json={"Results": ["Success - Added member alice to Sales DL group"]})

    _patch_async_client(monkeypatch, handler)
    config = _FakeConfig()

    body = {
        "tenantFilter": "client.onmicrosoft.com",
        "groupId": "abc-123-def",
        "groupType": cipp_tools._to_edit_group_type("Distribution"),
        "AddMember": [
            {"value": "alice@client.onmicrosoft.com",
             "addedFields": {"userPrincipalName": "alice@client.onmicrosoft.com"}}
        ],
    }
    await cipp_tools._cipp_post(config, "/api/EditGroup", body)

    assert "/api/EditGroup" in captured["url"]
    assert captured["body"]["groupType"] == "Distribution List"
    assert captured["body"]["AddMember"][0]["addedFields"]["userPrincipalName"] == "alice@client.onmicrosoft.com"


@pytest.mark.asyncio
async def test_remove_member_uses_remove_key(monkeypatch):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = httpx.Response(200, content=request.content).json()
        return httpx.Response(200, json={"Results": ["Success - Removed member bob from Sales DL group"]})

    _patch_async_client(monkeypatch, handler)
    config = _FakeConfig()

    body = {
        "tenantFilter": "client.onmicrosoft.com",
        "groupId": "abc-123-def",
        "groupType": cipp_tools._to_edit_group_type("Distribution"),
        "RemoveMember": [
            {"value": "bob@client.onmicrosoft.com",
             "addedFields": {"userPrincipalName": "bob@client.onmicrosoft.com"}}
        ],
    }
    await cipp_tools._cipp_post(config, "/api/EditGroup", body)

    assert "RemoveMember" in captured["body"]
    assert "AddMember" not in captured["body"]


@pytest.mark.asyncio
async def test_delete_group_payload(monkeypatch):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = httpx.Response(200, content=request.content).json()
        return httpx.Response(200, json={"Results": "Successfully Deleted Distribution List group Sales DL"})

    _patch_async_client(monkeypatch, handler)
    config = _FakeConfig()

    body = {
        "tenantFilter": "client.onmicrosoft.com",
        "id": "abc-123-def",
        "GroupType": cipp_tools._to_edit_group_type("Distribution"),
        "displayName": "Sales DL",
    }
    await cipp_tools._cipp_post(config, "/api/ExecGroupsDelete", body)

    assert "/api/ExecGroupsDelete" in captured["url"]
    assert captured["body"]["GroupType"] == "Distribution List"
    assert captured["body"]["id"] == "abc-123-def"


@pytest.mark.asyncio
async def test_post_propagates_4xx(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "missing displayName"})

    _patch_async_client(monkeypatch, handler)
    config = _FakeConfig()

    response = await cipp_tools._cipp_post(config, "/api/AddGroup", {})
    assert response.status_code == 400
    with pytest.raises(httpx.HTTPStatusError):
        response.raise_for_status()


def test_derive_mail_nickname_strips_specials():
    assert cipp_tools._derive_mail_nickname("Sales & Marketing — DL!") == "salesmarketingdl"
    assert cipp_tools._derive_mail_nickname("###") == "group"
    assert len(cipp_tools._derive_mail_nickname("a" * 200)) == 64


def test_to_edit_group_type_mapping():
    assert cipp_tools._to_edit_group_type("Distribution") == "Distribution List"
    assert cipp_tools._to_edit_group_type("Security") == "Mail-Enabled Security"
    assert cipp_tools._to_edit_group_type("M365") == "Microsoft 365"
    assert cipp_tools._to_edit_group_type("Generic") == "Security"
    assert cipp_tools._to_edit_group_type("DynamicDistribution") == "Distribution List"


def test_format_results_handles_list_and_string():
    assert cipp_tools._format_results({"Results": ["a", "b"]}) == "a\nb"
    assert cipp_tools._format_results({"Results": "ok"}) == "ok"
    assert cipp_tools._format_results({"results": "ok"}) == "ok"
    assert cipp_tools._format_results("plain") == "plain"


def test_looks_like_failure():
    assert cipp_tools._looks_like_failure("Failed to create group")
    assert cipp_tools._looks_like_failure("Error - whatever")
    assert not cipp_tools._looks_like_failure("Successfully created group")


@pytest.mark.asyncio
async def test_get_group_single_lookup_query_params(monkeypatch):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={
            "groupInfo": {
                "id": "abc-123-def",
                "displayName": "Sales DL",
                "mail": "salesdl@client.onmicrosoft.com",
                "mailNickname": "salesdl",
                "groupType": "Distribution List",
                "calculatedGroupType": "distributionList",
                "dynamicGroupBool": False,
                "teamsEnabled": False,
            },
            "members": [],
            "owners": [],
        })

    _patch_async_client(monkeypatch, handler)
    config = _FakeConfig()

    response = await cipp_tools._cipp_get(
        config,
        "/api/ListGroups",
        params={"tenantFilter": "client.onmicrosoft.com", "groupID": "abc-123-def"},
    )
    assert response.status_code == 200
    assert "groupID=abc-123-def" in captured["url"]
    assert "tenantFilter=client.onmicrosoft.com" in captured["url"]


@pytest.mark.asyncio
async def test_get_group_with_members_expansion(monkeypatch):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={
            "groupInfo": {"id": "g1", "displayName": "DL", "groupType": "Distribution List"},
            "members": [{"displayName": "Alice", "userPrincipalName": "alice@x.com"}],
            "owners": [],
        })

    _patch_async_client(monkeypatch, handler)
    config = _FakeConfig()

    response = await cipp_tools._cipp_get(
        config,
        "/api/ListGroups",
        params={
            "tenantFilter": "x.onmicrosoft.com",
            "groupID": "g1",
            "members": "true",
            "groupType": "Distribution List",
        },
    )
    assert response.status_code == 200
    assert "members=true" in captured["url"]
    assert "groupType=Distribution+List" in captured["url"] or "groupType=Distribution%20List" in captured["url"]
    body = response.json()
    assert body["members"][0]["userPrincipalName"] == "alice@x.com"
