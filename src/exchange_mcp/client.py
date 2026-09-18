"""Everything that talks to Exchange. Each public method returns plain JSON-able data for the MCP layer."""

import html
import logging
import re
import threading
from datetime import date, datetime, time, timedelta
from html.parser import HTMLParser
from pathlib import Path

import requests
from exchangelib import (
    BASIC,
    DELEGATE,
    DIGEST,
    NTLM,
    SSPI,
    Account,
    CalendarItem,
    Configuration,
    Credentials,
    EWSDate,
    EWSDateTime,
    EWSTimeZone,
    FileAttachment,
    HTMLBody,
    ItemId,
    Message,
)
from exchangelib.items import MeetingRequest
from exchangelib.items import SEND_TO_ALL_AND_SAVE_COPY, SEND_TO_NONE
from exchangelib.protocol import BaseProtocol, NoVerifyHTTPAdapter

from exchange_mcp.config import Settings, resolve_password

log = logging.getLogger(__name__)

_AUTH = {"ntlm": NTLM, "basic": BASIC, "digest": DIGEST, "sspi": SSPI}

# Aliases that map to the account's well-known folders. Well-known folders work whatever the mailbox language is
# (a Persian mailbox calls its Inbox "صندوق ورودی"), so paths may start with one of these.
_WELL_KNOWN = {
    "inbox": "inbox",
    "sent": "sent",
    "sent items": "sent",
    "drafts": "drafts",
    "deleted": "trash",
    "deleted items": "trash",
    "trash": "trash",
    "junk": "junk",
    "junk email": "junk",
    "outbox": "outbox",
}

MESSAGE_LIST_FIELDS = ["subject", "sender", "display_to", "datetime_received", "is_read", "has_attachments", "importance"]
MESSAGE_FIELDS = [
    "subject",
    "author",
    "sender",
    "to_recipients",
    "cc_recipients",
    "bcc_recipients",
    "datetime_sent",
    "datetime_received",
    "is_read",
    "is_draft",
    "importance",
    "categories",
    "has_attachments",
    "attachments",
    "text_body",
    "body",
    "conversation_id",
]
EVENT_LIST_FIELDS = [
    "subject",
    "start",
    "end",
    "is_all_day",
    "location",
    "organizer",
    "display_to",
    "my_response_type",
    "is_cancelled",
    "is_recurring",
    "is_meeting",
    "legacy_free_busy_status",
]
EVENT_FIELDS = EVENT_LIST_FIELDS + ["required_attendees", "optional_attendees", "text_body", "body", "is_online_meeting"]

_TEXT_ATTACHMENT_TYPES = ("application/json", "application/xml", "application/csv")
_INLINE_TEXT_LIMIT = 200_000
_RTL_CHARS = re.compile(r"[֐-ࣿיִ-﷿ﹰ-﻿]")


class ExchangeClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._account: Account | None = None
        self._lock = threading.Lock()
        self.tz = EWSTimeZone(settings.timezone) if settings.timezone else EWSTimeZone.localzone()

    # ------------------------------------------------------------------ connection

    @property
    def account(self) -> Account:
        """Connects on first use, so the MCP server starts even while Exchange is unreachable."""
        with self._lock:
            if self._account is None:
                self._account = self._connect()
            return self._account

    def reset(self) -> None:
        """Forget the connection, so the next call re-reads the password (e.g. after `exchange-mcp set-password`)."""
        with self._lock:
            self._account = None

    def _connect(self) -> Account:
        s = self.settings
        _configure_tls(s)

        credentials = None if s.auth == "sspi" else Credentials(username=s.username, password=resolve_password(s.username))

        if s.ews_url:
            config = Configuration(service_endpoint=s.ews_url, credentials=credentials, auth_type=_AUTH[s.auth])
        else:
            config = Configuration(credentials=credentials, auth_type=_AUTH[s.auth])

        return Account(
            primary_smtp_address=s.email,
            config=config,
            autodiscover=not s.ews_url,
            access_type=DELEGATE,
            default_timezone=self.tz,
        )

    def mailbox_info(self) -> dict:
        a = self.account
        inbox = a.inbox.refresh()  # the folder objects are cached for the life of the process; counts are not
        return {
            "email": a.primary_smtp_address,
            "ews_url": a.protocol.service_endpoint,
            "exchange_build": str(a.version.build),
            "api_version": a.version.api_version,
            "timezone": self.tz.key,
            "sending_enabled": self.settings.allow_send,
            "inbox": {"unread": inbox.unread_count, "total": inbox.total_count},
        }

    # ------------------------------------------------------------------ folders

    def list_folders(self, mail_only: bool = True) -> dict:
        self.account.root.clear_cache()  # pick up new folders and current counts; the hierarchy is otherwise cached
        root = self.account.msg_folder_root
        prefix = root.absolute + "/"
        folders = []
        for folder in root.walk():
            folder_class = folder.folder_class or ""
            if mail_only and not folder_class.startswith("IPF.Note"):
                continue
            folders.append(
                {
                    "path": folder.absolute.removeprefix(prefix),
                    "unread": folder.unread_count,
                    "total": folder.total_count,
                    "class": folder_class,
                }
            )
        return {"count": len(folders), "folders": folders}

    def _folder(self, name: str):
        parts = [p for p in (name or "inbox").replace("\\", "/").split("/") if p.strip()]
        if not parts:
            return self.account.inbox

        first = _WELL_KNOWN.get(parts[0].strip().lower())
        folder = getattr(self.account, first) if first else self.account.msg_folder_root / parts[0]
        for part in parts[1:]:
            folder = folder / part
        return folder

    def _search_folders(self, name: str) -> list:
        if (name or "").strip().lower() != "all":
            return [self._folder(name)]

        a = self.account
        skip = {a.trash.id, a.junk.id, a.outbox.id}
        return [f for f in a.msg_folder_root.walk() if (f.folder_class or "").startswith("IPF.Note") and f.id not in skip]

    def _folder_path(self, folder) -> str:
        return folder.absolute.removeprefix(self.account.msg_folder_root.absolute + "/")

    # ------------------------------------------------------------------ mail: read

    def search_emails(
        self,
        folder: str = "inbox",
        query: str | None = None,
        subject: str | None = None,
        from_address: str | None = None,
        unread_only: bool = False,
        has_attachments: bool | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 20,
        include_preview: bool = True,
    ) -> dict:
        limit = max(1, min(limit, 100))
        since_dt = self._parse_when(since) if since else None
        until_dt = self._parse_when(until, end_of_day=True) if until else None

        # EWS takes either an AQS query string or a restriction, never both. Free text and the sender go through AQS
        # (the search index); the remaining filters are then applied to the newest-first results as they stream in.
        aqs = None
        restrictions = {}
        if query or from_address:
            aqs = " ".join(
                part
                for part in (query, f'from:"{from_address}"' if from_address else None, f'subject:"{subject}"' if subject else None)
                if part
            )
        else:
            if subject:
                restrictions["subject__contains"] = subject
            if unread_only:
                restrictions["is_read"] = False
            if has_attachments is not None:
                restrictions["has_attachments"] = has_attachments
            if since_dt:
                restrictions["datetime_received__gte"] = since_dt
            if until_dt:
                restrictions["datetime_received__lt"] = until_dt

        # One query per folder: a multi-folder FindItem pages folder by folder, so it is neither newest-first overall
        # nor safe to cut at `limit`. Each folder yields its newest `limit` matches; the merge keeps the newest overall.
        found = []
        for target in self._search_folders(folder):
            if aqs is not None:
                qs = target.filter(aqs).only(*MESSAGE_LIST_FIELDS).order_by("-datetime_received")
                items = self._post_filter(qs, limit, unread_only, has_attachments, since_dt, until_dt)
            else:
                qs = (target.filter(**restrictions) if restrictions else target.all()).only(*MESSAGE_LIST_FIELDS).order_by("-datetime_received")
                items = [m for m in qs[:limit] if not isinstance(m, Exception)]
            path = self._folder_path(target)
            found.extend((m, path) for m in items)

        found.sort(key=lambda pair: pair[0].datetime_received.timestamp() if pair[0].datetime_received else 0, reverse=True)
        found = found[:limit]
        previews = self._previews([m for m, _ in found]) if include_preview else {}

        rows = [self._message_summary(m, path, previews.get(m.id) if include_preview else None) for m, path in found]
        return {"mode": "aqs" if aqs is not None else "restriction", "count": len(rows), "more_may_exist": len(rows) == limit, "emails": rows}

    def _previews(self, messages: list) -> dict[str, str]:
        """Body previews in one GetItem round trip, only for the messages that made the cut."""
        if not messages:
            return {}
        fetched = self.account.fetch(ids=[ItemId(id=m.id, changekey=m.changekey) for m in messages], only_fields=["text_body"])
        return {item.id: _collapse(item.text_body or "")[:300] for item in fetched if not isinstance(item, Exception)}

    def _post_filter(self, qs, limit, unread_only, has_attachments, since_dt, until_dt) -> list:
        qs.page_size = 50
        matched = []
        for scanned, item in enumerate(qs, start=1):
            if scanned > 1000 or len(matched) >= limit:
                break
            if isinstance(item, Exception):
                continue
            received = item.datetime_received
            if since_dt and received and received < since_dt:
                break  # newest first: everything after this is older still
            if until_dt and received and received >= until_dt:
                continue
            if unread_only and item.is_read:
                continue
            if has_attachments is not None and bool(item.has_attachments) != has_attachments:
                continue
            matched.append(item)
        return matched

    def get_email(self, email_id: str, max_body_chars: int = 20_000) -> dict:
        m = self._get_item(email_id, MESSAGE_FIELDS)
        body = self._body_text(m)
        truncated = len(body) > max_body_chars
        result = {
            "id": m.id,
            "kind": _kind(m),
            "subject": m.subject,
            "from": _address(m.author or m.sender),
            "to": [_address(r) for r in (m.to_recipients or [])],
            "cc": [_address(r) for r in (m.cc_recipients or [])],
            "bcc": [_address(r) for r in (m.bcc_recipients or [])],
            "sent": self._iso(m.datetime_sent),
            "received": self._iso(m.datetime_received),
            "is_read": m.is_read,
            "is_draft": m.is_draft,
            "importance": m.importance,
            "categories": m.categories or [],
            "attachments": [_attachment_summary(a) for a in (m.attachments or [])],
            "conversation_id": m.conversation_id.id if m.conversation_id else None,
            "body": body[:max_body_chars],
            "body_truncated": truncated,
        }
        if isinstance(m, MeetingRequest):
            # Only invitations carry the meeting's time; fetched separately so plain emails never ask for calendar fields.
            meeting = self._get_item(email_id, ["start", "end", "location"])
            result["meeting"] = {"start": self._iso(meeting.start), "end": self._iso(meeting.end), "location": meeting.location}
        return result

    def save_attachment(self, email_id: str, attachment_name: str, occurrence: int = 1) -> dict:
        m = self._get_item(email_id, ["subject", "attachments"])
        matches = [a for a in (m.attachments or []) if (a.name or "").lower() == attachment_name.strip().lower()]
        if not matches:
            names = ", ".join(repr(a.name) for a in (m.attachments or [])) or "none"
            raise LookupError(f"No attachment named {attachment_name!r} on this email. Attachments: {names}.")
        if not 1 <= occurrence <= len(matches):
            raise ValueError(f"occurrence must be between 1 and {len(matches)}.")

        attachment = matches[occurrence - 1]
        if not isinstance(attachment, FileAttachment):
            raise ValueError(f"{attachment.name!r} is an attached Outlook item (e.g. a forwarded email), not a file; open it with get_email on the original instead.")

        content = attachment.content
        target_dir = self.settings.download_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        path = _unique_path(target_dir / _safe_filename(attachment.name or "attachment"))
        path.write_bytes(content)

        result = {"saved_to": str(path), "name": attachment.name, "content_type": attachment.content_type, "size": len(content)}
        content_type = (attachment.content_type or "").lower()
        if (content_type.startswith("text/") or content_type in _TEXT_ATTACHMENT_TYPES) and len(content) <= _INLINE_TEXT_LIMIT:
            result["text"] = content.decode("utf-8", errors="replace")
        return result

    # ------------------------------------------------------------------ mail: write

    def mark_as_read(self, email_ids: list[str], is_read: bool = True) -> dict:
        updated, failed = [], []
        for item_id, item in zip(email_ids, self.account.fetch(ids=[ItemId(id=i) for i in email_ids], only_fields=["is_read"])):
            if isinstance(item, Exception):
                failed.append({"id": item_id, "error": str(item)})
                continue
            item.is_read = is_read
            item.save(update_fields=["is_read"])
            updated.append(item_id)
        return {"updated": len(updated), "failed": failed}

    def create_draft(self, to: list[str], subject: str, body: str, cc: list[str] | None = None, bcc: list[str] | None = None, body_is_html: bool = False) -> dict:
        message = self._new_message(to, subject, body, cc, bcc, body_is_html, folder=self.account.drafts)
        message.save()
        return {"id": message.id, "subject": subject, "saved_in": "Drafts", "sent": False}

    def create_reply_draft(self, email_id: str, body: str, reply_all: bool = False, body_is_html: bool = False) -> dict:
        original = self._get_item(email_id, ["subject", "author", "to_recipients", "cc_recipients", "bcc_recipients"])
        if not isinstance(original, Message):
            raise ValueError(f"That item is a {_kind(original).replace('_', ' ')}, not an email, so it cannot be replied to here. Use create_draft to write to the organizer instead.")
        subject = original.subject or ""
        if not re.match(r"^\s*(re|پاسخ)\s*:", subject, re.IGNORECASE):
            subject = f"RE: {subject}"
        reply_body = _body(body, body_is_html)
        reply = original.create_reply_all(subject=subject, body=reply_body) if reply_all else original.create_reply(subject=subject, body=reply_body)
        created = reply.save(self.account.drafts)
        return {"id": getattr(created, "id", None), "subject": subject, "reply_all": reply_all, "saved_in": "Drafts", "sent": False}

    def send_email(self, to: list[str], subject: str, body: str, cc: list[str] | None = None, bcc: list[str] | None = None, body_is_html: bool = False) -> dict:
        self._require_send("send_email")
        message = self._new_message(to, subject, body, cc, bcc, body_is_html, folder=self.account.sent)
        message.send_and_save()
        return {"sent": True, "subject": subject, "to": to, "cc": cc or [], "bcc": bcc or []}

    def send_draft(self, draft_id: str) -> dict:
        self._require_send("send_draft")
        draft = self._get_item(draft_id, ["subject", "is_draft", "to_recipients"])
        if not draft.is_draft:
            raise ValueError("That item is not a draft; only drafts can be sent with send_draft.")
        if not draft.to_recipients:
            raise ValueError("The draft has no To recipients.")
        draft.send()
        return {"sent": True, "subject": draft.subject, "to": [_address(r) for r in draft.to_recipients]}

    def _new_message(self, to, subject, body, cc, bcc, body_is_html, folder) -> Message:
        for address in [*(to or []), *(cc or []), *(bcc or [])]:
            if "@" not in address:
                raise ValueError(f"{address!r} is not an email address. Use find_people to look it up first.")
        if not to:
            raise ValueError("At least one To recipient is required.")
        return Message(
            account=self.account,
            folder=folder,
            subject=subject,
            body=_body(body, body_is_html),
            to_recipients=list(to),
            cc_recipients=list(cc) if cc else None,
            bcc_recipients=list(bcc) if bcc else None,
        )

    def _require_send(self, tool: str) -> None:
        if not self.settings.allow_send:
            raise PermissionError(f"{tool} is disabled. Set EXCHANGE_ALLOW_SEND=true in the server's env to allow sending; create_draft works without it.")

    # ------------------------------------------------------------------ calendar

    def list_events(self, start: str | None = None, end: str | None = None, limit: int = 100) -> dict:
        start_dt = self._parse_when(start) if start else EWSDateTime.from_datetime(datetime.combine(date.today(), time.min, tzinfo=self.tz))
        end_dt = self._parse_when(end, end_of_day=True) if end else start_dt + timedelta(days=7)
        if end_dt <= start_dt:
            raise ValueError("end must be after start.")
        limit = max(1, min(limit, 500))

        events = self.account.calendar.view(start=start_dt, end=end_dt, max_items=limit).only(*EVENT_LIST_FIELDS)
        rows = [self._event_summary(e) for e in events if isinstance(e, CalendarItem)]
        return {"start": self._iso(start_dt), "end": self._iso(end_dt), "count": len(rows), "events": rows}

    def get_event(self, event_id: str, max_body_chars: int = 10_000) -> dict:
        e = self._get_item(event_id, EVENT_FIELDS)
        row = self._event_summary(e)
        row.update(
            {
                "required_attendees": [_attendee(a) for a in (e.required_attendees or [])],
                "optional_attendees": [_attendee(a) for a in (e.optional_attendees or [])],
                "is_online_meeting": e.is_online_meeting,
                "body": self._body_text(e)[:max_body_chars],
            }
        )
        return row

    def create_event(
        self,
        subject: str,
        start: str,
        end: str | None = None,
        attendees: list[str] | None = None,
        location: str | None = None,
        body: str | None = None,
        all_day: bool = False,
        reminder_minutes: int = 15,
    ) -> dict:
        if attendees:
            self._require_send("create_event with attendees (it sends invitations)")
            for address in attendees:
                if "@" not in address:
                    raise ValueError(f"{address!r} is not an email address. Use find_people to look it up first.")

        if all_day:
            first = _parse_date(start)
            last = _parse_date(end) if end else first
            start_value, end_value = EWSDate.from_date(first), EWSDate.from_date(last + timedelta(days=1))
        else:
            start_value = self._parse_when(start)
            end_value = self._parse_when(end) if end else start_value + timedelta(hours=1)
            if end_value <= start_value:
                raise ValueError("end must be after start.")

        item = CalendarItem(
            account=self.account,
            folder=self.account.calendar,
            subject=subject,
            start=start_value,
            end=end_value,
            is_all_day=all_day,
            location=location,
            body=_body(body, False) if body else None,
            required_attendees=list(attendees) if attendees else None,
            reminder_is_set=reminder_minutes > 0,
            reminder_minutes_before_start=max(reminder_minutes, 0),
        )
        item.save(send_meeting_invitations=SEND_TO_ALL_AND_SAVE_COPY if attendees else SEND_TO_NONE)
        return {"id": item.id, "subject": subject, "start": self._iso(start_value), "end": self._iso(end_value), "invitations_sent": bool(attendees)}

    def get_availability(self, emails: list[str], start: str, end: str, interval_minutes: int = 30) -> dict:
        if not emails:
            raise ValueError("Give at least one email address.")
        start_dt = self._parse_when(start)
        end_dt = self._parse_when(end, end_of_day=True)
        if end_dt <= start_dt:
            raise ValueError("end must be after start.")
        interval = max(5, min(interval_minutes, 240))

        views = self._protocol().get_free_busy_info(
            accounts=[(email, "Required", False) for email in emails],
            start=start_dt,
            end=end_dt,
            merged_free_busy_interval=interval,
        )

        people, merged = [], []
        for email, view in zip(emails, views):
            if isinstance(view, Exception):
                people.append({"email": email, "error": str(view)})
                continue
            people.append(
                {
                    "email": email,
                    "busy": [
                        {
                            "start": self._iso(event.start),
                            "end": self._iso(event.end),
                            "status": event.busy_type,
                            "subject": getattr(event.details, "subject", None) if event.details else None,
                        }
                        for event in (view.calendar_events or [])
                    ],
                }
            )
            if view.merged:
                merged.append(view.merged)

        return {
            "interval_minutes": interval,
            "people": people,
            "free_for_everyone": self._common_free_slots(merged, start_dt, interval) if len(merged) == len(emails) else None,
        }

    def _common_free_slots(self, merged: list[str], start_dt, interval: int) -> list[dict]:
        # Each merged string has one digit per interval: 0 free, 1 tentative, 2 busy, 3 away, 4 elsewhere.
        slots, run_start = [], None
        length = min(len(m) for m in merged) if merged else 0
        for i in range(length + 1):
            free = i < length and all(m[i] == "0" for m in merged)
            if free and run_start is None:
                run_start = i
            elif not free and run_start is not None:
                slots.append(
                    {
                        "start": self._iso(start_dt + timedelta(minutes=run_start * interval)),
                        "end": self._iso(start_dt + timedelta(minutes=i * interval)),
                    }
                )
                run_start = None
        return slots

    # ------------------------------------------------------------------ people

    def _protocol(self):
        """Protocol-level services (ResolveNames, availability) need the server version, which the account resolves."""
        account = self.account
        account.version
        return account.protocol

    def find_people(self, query: str, limit: int = 10) -> dict:
        results = self._protocol().resolve_names(
            names=[query],
            return_full_contact_data=True,
            search_scope="ActiveDirectoryContacts",
        )
        people = []
        for result in results:
            if isinstance(result, Exception):
                continue
            mailbox, contact = result if isinstance(result, tuple) else (result, None)
            people.append(
                {
                    "name": mailbox.name or getattr(contact, "display_name", None),
                    "email": mailbox.email_address,
                    "job_title": getattr(contact, "job_title", None),
                    "department": getattr(contact, "department", None),
                    "company": getattr(contact, "company_name", None),
                    "phones": [
                        {"label": p.label, "number": p.phone_number}
                        for p in (getattr(contact, "phone_numbers", None) or [])
                        if getattr(p, "phone_number", None)
                    ],
                }
            )
            if len(people) >= limit:
                break
        return {"count": len(people), "people": people}

    # ------------------------------------------------------------------ helpers

    def _get_item(self, item_id: str, fields: list[str]):
        results = list(self.account.fetch(ids=[ItemId(id=item_id)], only_fields=fields))
        item = results[0] if results else None
        if isinstance(item, Exception):
            raise item
        if item is None:
            raise LookupError("Item not found or not accessible.")
        return item

    def _message_summary(self, m, folder: str, preview: str | None) -> dict:
        """Mail folders also hold meeting requests, responses and cancellations; they share these fields."""
        row = {
            "id": m.id,
            "kind": _kind(m),
            "folder": folder,
            "subject": m.subject,
            "from": _address(m.sender),
            "to": m.display_to,
            "received": self._iso(m.datetime_received),
            "is_read": m.is_read,
            "has_attachments": m.has_attachments,
            "importance": m.importance,
        }
        if preview is not None:
            row["preview"] = preview
        return row

    def _event_summary(self, e: CalendarItem) -> dict:
        return {
            "id": e.id,
            "subject": e.subject,
            "start": self._iso(e.start),
            "end": self._iso(e.end),
            "all_day": e.is_all_day,
            "location": e.location,
            "organizer": _address(e.organizer),
            "attendees": e.display_to,
            "my_response": e.my_response_type,
            "busy_status": e.legacy_free_busy_status,
            "is_meeting": e.is_meeting,
            "is_recurring": e.is_recurring,
            "is_cancelled": e.is_cancelled,
        }

    def _body_text(self, item) -> str:
        if item.text_body:
            return item.text_body.strip()
        if item.body is None:
            return ""
        return html_to_text(str(item.body)) if isinstance(item.body, HTMLBody) else str(item.body).strip()

    def _iso(self, value) -> str | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=self.tz)
            return value.astimezone(self.tz).isoformat(timespec="minutes")
        if isinstance(value, date):
            return value.isoformat()
        return str(value)

    def _parse_when(self, value: str, end_of_day: bool = False) -> EWSDateTime:
        text = value.strip()
        try:
            if len(text) == 10:
                parsed = datetime.combine(date.fromisoformat(text), time.min)
                if end_of_day:
                    parsed += timedelta(days=1)
            else:
                parsed = datetime.fromisoformat(text)
        except ValueError:
            raise ValueError(f"Not an ISO date or datetime: {value!r}. Use YYYY-MM-DD or YYYY-MM-DDTHH:MM (Gregorian).") from None
        parsed = parsed.replace(tzinfo=self.tz) if parsed.tzinfo is None else parsed.astimezone(self.tz)
        return parsed if isinstance(parsed, EWSDateTime) else EWSDateTime.from_datetime(parsed)


# ---------------------------------------------------------------------- module helpers


def _configure_tls(settings: Settings) -> None:
    if not settings.verify_ssl:
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        BaseProtocol.HTTP_ADAPTER_CLS = NoVerifyHTTPAdapter
        return

    if settings.ca_bundle:
        bundle = settings.ca_bundle

        class _CABundleAdapter(requests.adapters.HTTPAdapter):
            def cert_verify(self, conn, url, verify, cert):
                super().cert_verify(conn=conn, url=url, verify=bundle, cert=cert)

        BaseProtocol.HTTP_ADAPTER_CLS = _CABundleAdapter
        return

    if settings.use_system_certs:
        # Trust whatever Windows trusts, so an internal/domain CA that already works in the browser works here too.
        import truststore

        truststore.inject_into_ssl()


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        raise ValueError(f"Not an ISO date: {value!r}. Use YYYY-MM-DD (Gregorian).") from None


def _body(text: str, is_html: bool) -> HTMLBody:
    if is_html:
        return HTMLBody(text)
    return HTMLBody(text_to_html(text))


def text_to_html(text: str) -> str:
    """Plain text -> minimal HTML, right-to-left when the text contains Persian/Arabic script."""
    direction = "rtl" if _RTL_CHARS.search(text) else "ltr"
    escaped = html.escape(text.replace("\r\n", "\n")).replace("\n", "<br>\n")
    return f'<div dir="{direction}" style="font-family: Tahoma, Arial, sans-serif;">{escaped}</div>'


class _TextExtractor(HTMLParser):
    _BLOCK = {"p", "div", "br", "li", "tr", "table", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "hr"}
    _SKIP = {"script", "style", "head", "title"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skipping += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP:
            self._skipping = max(0, self._skipping - 1)
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skipping:
            self.parts.append(data)


def html_to_text(markup: str) -> str:
    parser = _TextExtractor()
    parser.feed(markup)
    parser.close()
    text = re.sub(r"[ \t\r\f\v ]+", " ", "".join(parser.parts))
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _address(mailbox) -> str | None:
    if mailbox is None:
        return None
    name, email = getattr(mailbox, "name", None), getattr(mailbox, "email_address", None)
    if name and email and name != email:
        return f"{name} <{email}>"
    return email or name


_KINDS = {
    "Message": "email",
    "MeetingRequest": "meeting_request",
    "MeetingResponse": "meeting_response",
    "MeetingCancellation": "meeting_cancellation",
}


def _kind(item) -> str:
    name = type(item).__name__
    return _KINDS.get(name) or re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _attendee(attendee) -> dict:
    return {"who": _address(attendee.mailbox), "response": attendee.response_type}


def _attachment_summary(attachment) -> dict:
    return {
        "name": attachment.name,
        "content_type": getattr(attachment, "content_type", None),
        "size": getattr(attachment, "size", None),
        "is_inline": getattr(attachment, "is_inline", None),
        "kind": "file" if isinstance(attachment, FileAttachment) else "item",
    }


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return cleaned or "attachment"


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for n in range(1, 1000):
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Too many files named like {path.name} in {path.parent}")
