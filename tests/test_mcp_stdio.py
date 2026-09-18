"""The real server process over stdio, the way Claude Desktop runs it."""

import json
import os
import socket

import anyio
from conftest import server_command
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

READ_TOOLS = {
    "mailbox_info",
    "list_folders",
    "search_emails",
    "get_email",
    "list_events",
    "get_event",
    "get_availability",
    "find_people",
}
WRITE_TOOLS = {"save_attachment", "mark_as_read", "create_draft", "create_reply_draft", "create_event"}


def _session(env: dict, action):
    command = server_command()
    params = StdioServerParameters(
        command=command[0],
        args=command[1:],
        env={**{k: v for k, v in os.environ.items() if not k.startswith("EXCHANGE_")}, **env},
    )

    async def run():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await action(session)

    return anyio.run(run)


def _text(result) -> str:
    return "".join(getattr(block, "text", "") for block in result.content)


def test_tool_list_hides_send_tools_until_enabled(env):
    async def names(session):
        return {tool.name: tool for tool in (await session.list_tools()).tools}

    tools = _session(env, names)
    assert set(tools) == READ_TOOLS | WRITE_TOOLS
    assert tools["search_emails"].annotations.read_only_hint is True
    assert tools["create_draft"].annotations.read_only_hint is False

    tools = _session({**env, "EXCHANGE_ALLOW_SEND": "true"}, names)
    assert {"send_email", "send_draft"} <= set(tools)


def test_search_over_stdio_returns_structured_json(env):
    async def call(session):
        return await session.call_tool("search_emails", {"limit": 2})

    result = _session(env, call)
    assert not result.is_error
    payload = result.structured_content or json.loads(_text(result))
    assert [e["subject"] for e in payload["emails"]] == ["گزارش فروش شهریور", "Meeting notes"]


def test_unknown_id_comes_back_as_readable_error(env):
    async def call(session):
        return await session.call_tool("get_email", {"email_id": "missing"})

    result = _session(env, call)
    assert result.is_error
    assert "No item with that id" in _text(result)


def test_missing_configuration_is_reported_not_crashed(env):
    broken = {k: v for k, v in env.items() if k != "EXCHANGE_EMAIL"}

    async def call(session):
        return await session.call_tool("mailbox_info", {})

    result = _session(broken, call)
    assert result.is_error
    assert "EXCHANGE_EMAIL is not set" in _text(result)


def test_unreachable_server_is_explained(env):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        dead_port = s.getsockname()[1]
    dead = {**env, "EXCHANGE_EWS_URL": f"http://127.0.0.1:{dead_port}/EWS/Exchange.asmx"}

    async def call(session):
        return await session.call_tool("mailbox_info", {})

    result = _session(dead, call)
    assert result.is_error
    assert "Could not reach Exchange" in _text(result)
