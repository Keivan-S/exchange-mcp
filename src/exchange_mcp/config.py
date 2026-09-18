"""Settings read from environment variables (set them in claude_desktop_config.json -> mcpServers.<name>.env)."""

import os
from dataclasses import dataclass
from pathlib import Path

KEYRING_SERVICE = "exchange-mcp"

AUTH_TYPES = ("ntlm", "basic", "digest", "sspi")


class ConfigError(Exception):
    """The server is misconfigured; the message tells the user which variable to fix."""


def _flag(name: str, default: bool) -> bool:
    raw = (_text(name) or "").lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _text(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    if value.startswith("${"):
        return None  # an optional Claude Desktop extension setting left blank may arrive as its literal placeholder
    return value or None


@dataclass(frozen=True)
class Settings:
    email: str
    username: str
    ews_url: str | None
    auth: str
    verify_ssl: bool
    ca_bundle: str | None
    use_system_certs: bool
    timezone: str | None
    allow_send: bool
    download_dir: Path

    @classmethod
    def from_env(cls) -> "Settings":
        email = _text("EXCHANGE_EMAIL")
        if not email:
            raise ConfigError("EXCHANGE_EMAIL is not set (the mailbox's primary SMTP address, e.g. you@company.com).")

        auth = (_text("EXCHANGE_AUTH") or "ntlm").lower()
        if auth not in AUTH_TYPES:
            raise ConfigError(f"EXCHANGE_AUTH must be one of {', '.join(AUTH_TYPES)} (got {auth!r}).")

        ews_url = _text("EXCHANGE_EWS_URL")
        if ews_url and not ews_url.lower().startswith(("https://", "http://")):
            raise ConfigError(f"EXCHANGE_EWS_URL must be a full URL such as https://mail.company.local/EWS/Exchange.asmx (got {ews_url!r}).")

        ca_bundle = _text("EXCHANGE_CA_BUNDLE")
        if ca_bundle and not Path(ca_bundle).is_file():
            raise ConfigError(f"EXCHANGE_CA_BUNDLE points to a missing file: {ca_bundle}")

        download_dir = _text("EXCHANGE_DOWNLOAD_DIR")

        return cls(
            email=email,
            username=_text("EXCHANGE_USERNAME") or email,
            ews_url=ews_url,
            auth=auth,
            verify_ssl=_flag("EXCHANGE_VERIFY_SSL", True),
            ca_bundle=ca_bundle,
            use_system_certs=_flag("EXCHANGE_USE_SYSTEM_CERTS", True),
            timezone=_text("EXCHANGE_TIMEZONE"),
            allow_send=_flag("EXCHANGE_ALLOW_SEND", False),
            download_dir=Path(download_dir) if download_dir else Path.home() / "Downloads" / "exchange-mcp",
        )


def resolve_password(username: str) -> str:
    """EXCHANGE_PASSWORD wins; otherwise the Windows Credential Manager entry written by `exchange-mcp set-password`."""
    password = os.environ.get("EXCHANGE_PASSWORD")
    if password and not password.startswith("${user_config."):
        return password

    import keyring

    stored = keyring.get_password(KEYRING_SERVICE, username)
    if stored:
        return stored

    raise ConfigError(
        f"No password for {username!r}. Run `exchange-mcp set-password --username \"{username}\"` once "
        "(stores it in Windows Credential Manager), or set EXCHANGE_PASSWORD."
    )
