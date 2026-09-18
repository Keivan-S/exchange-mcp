import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from fake_mailbox import fake_mailbox  # noqa: E402


def server_command() -> list[str]:
    """The server as a process: the source by default, or a built executable via MCP_SERVER_UNDER_TEST (CI)."""
    built = os.environ.get("MCP_SERVER_UNDER_TEST")
    return [built] if built else [sys.executable, "-m", "exchange_mcp"]

BASE_ENV = {
    "EXCHANGE_EMAIL": "me@corp.local",
    "EXCHANGE_USERNAME": "CORP\\me",
    "EXCHANGE_PASSWORD": "not-a-real-password",
    "EXCHANGE_AUTH": "basic",
    "EXCHANGE_TIMEZONE": "Asia/Tehran",
}


@pytest.fixture
def ews():
    with fake_mailbox() as server:
        yield server


@pytest.fixture
def env(ews, tmp_path, monkeypatch):
    for name in list(__import__("os").environ):
        if name.startswith("EXCHANGE_"):
            monkeypatch.delenv(name)
    values = {**BASE_ENV, "EXCHANGE_EWS_URL": ews.url, "EXCHANGE_DOWNLOAD_DIR": str(tmp_path / "downloads")}
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return values


@pytest.fixture
def client(env):
    from exchange_mcp.client import ExchangeClient
    from exchange_mcp.config import Settings

    return ExchangeClient(Settings.from_env())
