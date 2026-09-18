"""MCP tool surface. Every tool is a thin wrapper over ExchangeClient; errors come back to the model as readable text."""

import functools
import logging
from typing import Annotated

import requests
from exchangelib.errors import (
    AutoDiscoverFailed,
    ErrorFolderNotFound,
    ErrorInvalidIdMalformed,
    ErrorItemNotFound,
    ResponseMessageError,
    TransportError,
    UnauthorizedError,
)
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from exchange_mcp import __version__
from exchange_mcp.client import ExchangeClient
from exchange_mcp.config import ConfigError, Settings

log = logging.getLogger(__name__)

READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
MAILBOX_WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
OUTBOUND = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True)

Date = Annotated[str, Field(description="YYYY-MM-DD or YYYY-MM-DDTHH:MM (Gregorian, mailbox time zone unless an offset is given).")]
OptionalDate = Annotated[str | None, Field(description="YYYY-MM-DD or YYYY-MM-DDTHH:MM (Gregorian, mailbox time zone unless an offset is given).")]
Addresses = Annotated[list[str], Field(description="Email addresses. Use find_people to turn a name into an address.")]


def explain(exc: Exception) -> str:
    """Turn an exception into something the user can act on."""
    if isinstance(exc, ConfigError):
        return f"Configuration problem: {exc}"
    if isinstance(exc, UnauthorizedError):
        return (
            "Exchange rejected the login (401). Check EXCHANGE_USERNAME (DOMAIN\\user or user@domain), "
            "the stored password (`exchange-mcp set-password`), and EXCHANGE_AUTH (ntlm or basic)."
        )
    if isinstance(exc, AutoDiscoverFailed):
        return "Autodiscover failed. Set EXCHANGE_EWS_URL, e.g. https://mail.company.local/EWS/Exchange.asmx."
    # Server-side EWS errors subclass TransportError too, so they must be matched before the network errors below.
    if isinstance(exc, (ErrorItemNotFound, ErrorInvalidIdMalformed)):
        return "No item with that id (it may have been moved or deleted). Search again for a fresh id."
    if isinstance(exc, ErrorFolderNotFound):
        return f"{exc}. Call list_folders to see the folder paths."
    if isinstance(exc, ResponseMessageError):
        return f"Exchange refused the request ({type(exc).__name__}): {exc}"
    if isinstance(exc, requests.exceptions.SSLError) or "CERTIFICATE_VERIFY_FAILED" in str(exc):
        return (
            "The Exchange server's TLS certificate is not trusted. Point EXCHANGE_CA_BUNDLE at the internal CA "
            "certificate (PEM), or as a last resort set EXCHANGE_VERIFY_SSL=false."
        )
    if isinstance(exc, (TransportError, requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
        return f"Could not reach Exchange: {exc}. Check the network/VPN and EXCHANGE_EWS_URL."
    return f"{type(exc).__name__}: {exc}"


def _instructions(settings: Settings | None, client: ExchangeClient | None) -> str:
    if settings is None or client is None:
        return "Exchange mailbox tools. The server is not configured yet; any tool call returns the configuration error."
    sending = (
        "send_email and send_draft are enabled. Before sending anything, show the user the exact recipients, subject "
        "and body and wait for an explicit yes."
        if settings.allow_send
        else "Sending is disabled on this server (EXCHANGE_ALLOW_SEND is off): write replies with create_draft / "
        "create_reply_draft and tell the user the draft is waiting in Outlook."
    )
    return (
        f"Direct EWS connection to the on-premises Exchange mailbox {settings.email}.\n"
        f"- Times are ISO 8601 in {client.tz.key}, Gregorian calendar. Pass dates as YYYY-MM-DD or YYYY-MM-DDTHH:MM.\n"
        "- Item ids are opaque strings; pass them back exactly as returned.\n"
        "- search_emails: `query` is Exchange AQS (e.g. `invoice`, `from:ali`, `subject:\"report\"`, "
        "`hasattachment:true`). Leave it empty to use the structured filters only.\n"
        "- Drafts are saved in the Drafts folder and never leave the mailbox.\n"
        f"- {sending}"
    )


def build_server() -> MCPServer:
    try:
        settings = Settings.from_env()
        client = ExchangeClient(settings)
        startup_error = None
    except Exception as exc:  # keep serving so the model can tell the user what to fix
        log.error("exchange-mcp is not configured: %s", exc)
        settings, client, startup_error = None, None, exc

    mcp = MCPServer(
        name="exchange",
        title="Exchange (on-premises)",
        version=__version__,
        instructions=_instructions(settings, client),
    )

    def tool(annotations: ToolAnnotations):
        """Register a tool whose failures reach the model as a readable ToolError instead of an opaque crash."""

        def decorate(fn):
            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                if startup_error is not None:
                    raise ToolError(explain(startup_error))
                try:
                    return fn(*args, **kwargs)
                except ToolError:
                    raise
                except Exception as exc:
                    if isinstance(exc, UnauthorizedError):
                        client.reset()  # a password fixed meanwhile is picked up on the next call
                    log.warning("%s failed: %s", fn.__name__, exc, exc_info=log.isEnabledFor(logging.DEBUG))
                    raise ToolError(explain(exc)) from exc

            return mcp.tool(annotations=annotations)(wrapper)

        return decorate

    @tool(READ)
    def mailbox_info() -> dict:
        """Connection check: the mailbox address, Exchange version, time zone, inbox counts and whether sending is enabled."""
        return client.mailbox_info()

    @tool(READ)
    def list_folders(
        mail_only: Annotated[bool, Field(description="Only mail folders (skip calendar, contacts, tasks...).")] = True,
    ) -> dict:
        """List mailbox folders as paths (e.g. "Inbox/Projects") with unread and total counts."""
        return client.list_folders(mail_only)

    @tool(READ)
    def search_emails(
        folder: Annotated[
            str,
            Field(description='"inbox", "sent", "drafts", "deleted", "junk", a path like "Inbox/Projects", or "all" for every mail folder except Deleted/Junk.'),
        ] = "inbox",
        query: Annotated[str | None, Field(description="Exchange AQS full-text query, e.g. 'contract' or 'from:ali subject:\"budget\"'.")] = None,
        subject: Annotated[str | None, Field(description="Subject contains this text.")] = None,
        from_address: Annotated[str | None, Field(description="Sender name or address.")] = None,
        unread_only: bool = False,
        has_attachments: bool | None = None,
        since: OptionalDate = None,
        until: Annotated[str | None, Field(description="Inclusive end: YYYY-MM-DD includes that whole day.")] = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
        include_preview: Annotated[bool, Field(description="Include the first ~300 characters of each body.")] = True,
    ) -> dict:
        """Find emails, newest first. Returns ids, sender, subject, date, read state and a short preview."""
        return client.search_emails(folder, query, subject, from_address, unread_only, has_attachments, since, until, limit, include_preview)

    @tool(READ)
    def get_email(
        email_id: str,
        max_body_chars: Annotated[int, Field(ge=500, le=200_000)] = 20_000,
    ) -> dict:
        """Read one email in full: headers, recipients, plain-text body and the attachment list."""
        return client.get_email(email_id, max_body_chars)

    @tool(MAILBOX_WRITE)
    def save_attachment(
        email_id: str,
        attachment_name: Annotated[str, Field(description="Exact file name as listed by get_email.")],
        occurrence: Annotated[int, Field(ge=1, description="Which one, when several attachments share the name.")] = 1,
    ) -> dict:
        """Save an attachment to the local download folder and return its path; text files are also returned inline."""
        return client.save_attachment(email_id, attachment_name, occurrence)

    @tool(MAILBOX_WRITE)
    def mark_as_read(
        email_ids: Annotated[list[str], Field(min_length=1, max_length=100)],
        is_read: bool = True,
    ) -> dict:
        """Mark emails as read (or unread with is_read=false)."""
        return client.mark_as_read(email_ids, is_read)

    @tool(MAILBOX_WRITE)
    def create_draft(
        to: Addresses,
        subject: str,
        body: Annotated[str, Field(description="Plain text (Persian is laid out right-to-left automatically) or HTML with body_is_html.")],
        cc: Addresses | None = None,
        bcc: Addresses | None = None,
        body_is_html: bool = False,
    ) -> dict:
        """Save a new email in Drafts. Nothing is sent; the user can review and send it from Outlook."""
        return client.create_draft(to, subject, body, cc, bcc, body_is_html)

    @tool(MAILBOX_WRITE)
    def create_reply_draft(
        email_id: str,
        body: Annotated[str, Field(description="The reply text; the original message is quoted below it by Exchange.")],
        reply_all: bool = False,
        body_is_html: bool = False,
    ) -> dict:
        """Save a reply (or reply-all) to an email in Drafts, threaded with the original. Nothing is sent."""
        return client.create_reply_draft(email_id, body, reply_all, body_is_html)

    @tool(READ)
    def list_events(
        start: Annotated[str | None, Field(description="Default: today 00:00.")] = None,
        end: Annotated[str | None, Field(description="Inclusive when a bare date. Default: start + 7 days.")] = None,
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
    ) -> dict:
        """Calendar events in a date range, recurring meetings expanded into occurrences."""
        return client.list_events(start, end, limit)

    @tool(READ)
    def get_event(event_id: str) -> dict:
        """One calendar event in full: attendees with their responses, and the body/agenda."""
        return client.get_event(event_id)

    @tool(READ)
    def get_availability(
        emails: Addresses,
        start: Date,
        end: Annotated[str, Field(description="Inclusive when a bare date.")],
        interval_minutes: Annotated[int, Field(ge=5, le=240)] = 30,
    ) -> dict:
        """Free/busy for colleagues (and yourself), plus the slots where everyone is free."""
        return client.get_availability(emails, start, end, interval_minutes)

    @tool(READ)
    def find_people(
        query: Annotated[str, Field(description="Part of a name or email address.")],
        limit: Annotated[int, Field(ge=1, le=50)] = 10,
    ) -> dict:
        """Look people up in the company directory (GAL) and your contacts: name, email, title, department, phones."""
        return client.find_people(query, limit)

    @tool(OUTBOUND)
    def create_event(
        subject: str,
        start: Date,
        end: OptionalDate = None,
        attendees: Annotated[
            list[str] | None,
            Field(description="Invitees' email addresses. Invitations go out immediately; only allowed when sending is enabled."),
        ] = None,
        location: str | None = None,
        body: str | None = None,
        all_day: bool = False,
        reminder_minutes: Annotated[int, Field(ge=0, le=10080)] = 15,
    ) -> dict:
        """Add an event to your calendar (default length 1 hour). With attendees it becomes a meeting and invitations are sent."""
        return client.create_event(subject, start, end, attendees, location, body, all_day, reminder_minutes)

    if settings is not None and settings.allow_send:

        @tool(OUTBOUND)
        def send_email(
            to: Addresses,
            subject: str,
            body: Annotated[str, Field(description="Plain text (Persian is laid out right-to-left automatically) or HTML with body_is_html.")],
            cc: Addresses | None = None,
            bcc: Addresses | None = None,
            body_is_html: bool = False,
        ) -> dict:
            """Send an email immediately (a copy goes to Sent Items). Confirm recipients, subject and body with the user first."""
            return client.send_email(to, subject, body, cc, bcc, body_is_html)

        @tool(OUTBOUND)
        def send_draft(draft_id: str) -> dict:
            """Send a draft previously saved with create_draft / create_reply_draft. Confirm with the user first."""
            return client.send_draft(draft_id)

    return mcp
