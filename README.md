# exchange-mcp

[فارسی](README.fa.md)

A local MCP server that connects **Claude Desktop** (and Claude Code) directly to an **on-premises Microsoft Exchange**
server (2016 / 2019 / Subscription Edition) over **EWS**.

It runs on your own machine and talks straight to your Exchange server. There is no cloud relay and no third-party
service. If your PC can open OWA, this server can reach Exchange too.

## Tools

| Tool | What it does | Writes? |
|---|---|---|
| `mailbox_info` | Connection check: address, Exchange build, time zone, inbox counts | no |
| `list_folders` | Folder paths with unread/total counts | no |
| `search_emails` | Newest-first search; AQS full-text (`query`) or structured filters | no |
| `get_email` | Full email: recipients, plain-text body, attachment list; meeting time for invitations | no |
| `save_attachment` | Saves an attachment to `EXCHANGE_DOWNLOAD_DIR`; text files also returned inline | local file |
| `mark_as_read` | Read/unread flag | mailbox |
| `create_draft` | New email saved in Drafts, never sent | mailbox |
| `create_reply_draft` | Reply / reply-all saved in Drafts, threaded | mailbox |
| `list_events` | Calendar range, recurring meetings expanded | no |
| `get_event` | Event with attendees' responses and agenda | no |
| `get_availability` | Free/busy for colleagues + slots where everyone is free | no |
| `find_people` | GAL + contacts lookup (name, email, title, department, phones) | no |
| `create_event` | Calendar entry; with attendees it sends invitations (requires `EXCHANGE_ALLOW_SEND`) | mailbox / outbound |
| `send_email`, `send_draft` | Only registered when `EXCHANGE_ALLOW_SEND=true` | outbound |

Search results carry a `kind` (`email`, `meeting_request`, `meeting_cancellation`, ...), since the inbox holds
meeting traffic as well as mail. Plain-text bodies are sent as HTML, right-to-left when they contain Persian/Arabic
script, so they display correctly in Outlook.

## Install from a release (recommended)

Download the file for your machine from [Releases](../../releases):

| Machine | File |
|---|---|
| Windows | `exchange-mcp-<version>-windows-x64.mcpb` |
| Mac, Apple silicon (M1 and later) | `exchange-mcp-<version>-macos-arm64.mcpb` |
| Mac, Intel | `exchange-mcp-<version>-macos-x64.mcpb` |

1. Double-click the `.mcpb` file (or install it from Claude Desktop → Settings → Extensions).
2. Fill in the form: email address, EWS address and password. The EWS address is your OWA address with
   `/EWS/Exchange.asmx` instead of `/owa`, e.g. `https://mail.company.com/EWS/Exchange.asmx`.
3. Claude Desktop keeps the password in the operating system's secure storage, not in a config file.

No Python needed; the executable is self-contained. The standalone executables are attached to the release as well.

The binaries are not code-signed. Windows may show a SmartScreen warning on download. On macOS the `.mcpb` clears the
Gatekeeper quarantine flag itself; for the standalone macOS executable run this once:

```bash
xattr -d com.apple.quarantine exchange-mcp-*-macos-* && chmod +x exchange-mcp-*-macos-*
```

## Install from source

```powershell
cd D:\Projects\Packages\exchange-mcp
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

Store the password once, in **Windows Credential Manager** (macOS: Keychain):

```powershell
.\.venv\Scripts\exchange-mcp.exe set-password --username "you@company.com"
```

Test the connection. It prints the mailbox info and the three newest inbox items, or a clear reason why it failed:

```powershell
$env:EXCHANGE_EMAIL = "you@company.com"
$env:EXCHANGE_EWS_URL = "https://mail.company.com/EWS/Exchange.asmx"
.\.venv\Scripts\exchange-mcp.exe check
```

Add it to `%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "exchange": {
      "command": "D:\\Projects\\Packages\\exchange-mcp\\.venv\\Scripts\\exchange-mcp.exe",
      "env": {
        "EXCHANGE_EMAIL": "you@company.com",
        "EXCHANGE_EWS_URL": "https://mail.company.com/EWS/Exchange.asmx"
      }
    }
  }
}
```

Restart Claude Desktop (quit it from the tray icon; closing the window is not enough).

Claude Code: `claude mcp add exchange -s user -e EXCHANGE_EMAIL=... -e EXCHANGE_EWS_URL=... -- D:\Projects\Packages\exchange-mcp\.venv\Scripts\exchange-mcp.exe`

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `EXCHANGE_EMAIL` | (required) | Primary SMTP address of the mailbox |
| `EXCHANGE_USERNAME` | `EXCHANGE_EMAIL` | Login: `DOMAIN\user` or `user@domain` |
| `EXCHANGE_EWS_URL` | autodiscover | e.g. `https://mail.company.com/EWS/Exchange.asmx` (recommended) |
| `EXCHANGE_AUTH` | `ntlm` | `ntlm`, `basic`, `digest`, or `sspi` (current Windows login; domain-joined PCs, needs `pip install requests_negotiate_sspi`) |
| `EXCHANGE_PASSWORD` | Credential Manager | Plain-text fallback |
| `EXCHANGE_USE_SYSTEM_CERTS` | `true` | Trust the OS certificate store (internal/domain CAs) |
| `EXCHANGE_CA_BUNDLE` | none | PEM file of the internal CA, if the OS does not trust it |
| `EXCHANGE_VERIFY_SSL` | `true` | `false` disables TLS verification (last resort) |
| `EXCHANGE_TIMEZONE` | system zone | IANA name, e.g. `Asia/Tehran` |
| `EXCHANGE_ALLOW_SEND` | `false` | Registers `send_email` / `send_draft` and allows meeting invitations |
| `EXCHANGE_DOWNLOAD_DIR` | `~/Downloads/exchange-mcp` | Where `save_attachment` writes |
| `EXCHANGE_LOG_LEVEL` | `WARNING` | stderr log level (Claude Desktop: `mcp-server-exchange.log` in its logs folder) |

## Requirements on the Exchange side

- EWS enabled for the user (`Get-CASMailbox you | fl EwsEnabled`). It is enabled by default on-premises.
- NTLM or Basic auth allowed on the `/EWS` virtual directory. This is the default.
- Full-text `query` search needs Exchange Search (the content index) to be healthy. Structured filters work without it.

## Limitations

- Responding to meeting invitations (accept / decline / tentative) is not supported yet. Invitations can be read and
  show up in the calendar as tentative.
- `folder: "all"` sends one search per mail folder, so it is slower on mailboxes with many folders.

## Development

```bash
pip install -e ".[dev,build]"
pytest                        # runs against a fake EWS endpoint (tests/fake_mailbox.py)
python packaging/build.py     # standalone executable + .mcpb for this platform, in dist/
```

Pushing a `v*` tag makes GitHub Actions build Windows x64, macOS arm64 and macOS x64, run the MCP tests against each
built executable, and attach everything to a GitHub release.
