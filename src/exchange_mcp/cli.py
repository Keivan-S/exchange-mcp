"""`exchange-mcp` command: run the server (default), store the password, or test the connection."""

import argparse
import getpass
import json
import logging
import os
import sys

from exchange_mcp.config import KEYRING_SERVICE, Settings


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="exchange-mcp", description="Local MCP server for on-premises Exchange (EWS).")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("serve", help="Run the MCP server on stdio (what Claude Desktop starts). Default.")
    for name, help_text in (
        ("set-password", "Store the Exchange password in Windows Credential Manager."),
        ("delete-password", "Remove the stored password."),
    ):
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument("--username", help="Defaults to EXCHANGE_USERNAME, then EXCHANGE_EMAIL.")
    commands.add_parser("check", help="Connect with the current EXCHANGE_* variables and print mailbox info.")
    args = parser.parse_args(argv)

    # stdout belongs to the MCP protocol; logs go to stderr (Claude Desktop keeps them in its MCP log files).
    logging.basicConfig(stream=sys.stderr, level=os.environ.get("EXCHANGE_LOG_LEVEL", "WARNING").upper())

    if args.command in (None, "serve"):
        from exchange_mcp.server import build_server

        build_server().run("stdio")
    elif args.command == "set-password":
        _set_password(_username(args.username))
    elif args.command == "delete-password":
        _delete_password(_username(args.username))
    elif args.command == "check":
        sys.exit(_check())


def _username(explicit: str | None) -> str:
    username = explicit or os.environ.get("EXCHANGE_USERNAME") or os.environ.get("EXCHANGE_EMAIL")
    if not username:
        sys.exit("Pass --username (e.g. COMPANY\\you or you@company.com) or set EXCHANGE_USERNAME.")
    return username.strip()


def _set_password(username: str) -> None:
    import keyring

    password = getpass.getpass(f"Exchange password for {username}: ")
    if not password:
        sys.exit("Empty password; nothing stored.")
    keyring.set_password(KEYRING_SERVICE, username, password)
    print(f"Stored in Windows Credential Manager (service {KEYRING_SERVICE!r}, user {username!r}).")


def _delete_password(username: str) -> None:
    import keyring
    from keyring.errors import PasswordDeleteError

    try:
        keyring.delete_password(KEYRING_SERVICE, username)
    except PasswordDeleteError:
        sys.exit(f"No stored password for {username!r}.")
    print(f"Removed the stored password for {username!r}.")


def _check() -> int:
    from exchange_mcp.client import ExchangeClient
    from exchange_mcp.server import explain

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        client = ExchangeClient(Settings.from_env())
        info = client.mailbox_info()
        info["latest_inbox"] = [
            {"received": e["received"], "from": e["from"], "subject": e["subject"]}
            for e in client.search_emails(limit=3, include_preview=False)["emails"]
        ]
    except Exception as exc:
        print(f"FAILED: {explain(exc)}", file=sys.stderr)
        return 1
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0
