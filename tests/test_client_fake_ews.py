"""ExchangeClient against the fake EWS endpoint: real exchangelib requests, canned server answers."""

import json
import subprocess

import pytest
from conftest import server_command
from exchangelib.errors import ErrorItemNotFound, UnauthorizedError

from exchange_mcp.server import explain


def test_mailbox_info(client):
    info = client.mailbox_info()
    assert info["email"] == "me@corp.local"
    assert info["exchange_build"].startswith("15.2")
    assert info["inbox"] == {"unread": 2, "total": 3}
    assert info["sending_enabled"] is False


def test_list_folders_shows_mail_folder_paths(client):
    paths = [f["path"] for f in client.list_folders()["folders"]]
    assert "Inbox/Projects" in paths
    assert "Calendar" not in paths


def test_structured_search_sends_restrictions_and_fetches_previews(client, ews):
    result = client.search_emails(subject="Invoice", unread_only=True, since="2026-09-01", until="2026-09-18")

    request = ews.last("FindItem")
    assert "<m:Restriction>" in request
    assert 'FieldURI="item:Subject"' in request and "<t:Contains" in request
    assert 'FieldURI="message:IsRead"' in request
    assert "<t:IsGreaterThanOrEqualTo>" in request and "<t:IsLessThan>" in request
    assert 'Order="Descending"' in request and 'FieldURI="item:DateTimeReceived"' in request

    assert result["mode"] == "restriction"
    assert [e["id"] for e in result["emails"]] == ["m1", "m2", "m3", "m4"]  # the fake does not filter; the order is kept
    assert result["emails"][0]["preview"] == "سلام، گزارش پیوست است."
    assert result["emails"][0]["received"] == "2026-09-17T11:30+03:30"
    assert result["emails"][0]["folder"] == "Inbox"


def test_aqs_search_uses_query_string_and_filters_client_side(client, ews):
    result = client.search_emails(query="فروش", from_address="Ali", unread_only=True, since="2026-09-12", include_preview=False)

    request = ews.last("FindItem")
    assert '>فروش from:"Ali"</m:QueryString>' in request
    assert "<m:Restriction>" not in request
    assert result["mode"] == "aqs"
    assert [e["id"] for e in result["emails"]] == ["m1"]  # m2 is read, m3 is older than since
    assert "preview" not in result["emails"][0]


def test_search_all_queries_each_mail_folder_and_merges_newest_first(client, ews):
    before = len(ews.calls)
    result = client.search_emails(folder="all", limit=2, include_preview=False)

    find_calls = [raw for op, raw in ews.calls[before:] if op == "FindItem"]
    assert len(find_calls) == 4  # Inbox, Inbox/Projects, Sent Items, Drafts; Deleted/Junk/Outbox are skipped
    assert [e["id"] for e in result["emails"]] == ["m1", "m2"]
    assert result["more_may_exist"] is True


def test_folder_paths_resolve_from_well_known_names(client):
    assert client.search_emails(folder="inbox/projects", include_preview=False)["count"] == 0


def test_get_email_converts_html_body_and_lists_attachments(client):
    email = client.get_email("m1")
    assert email["from"] == "Ali Ahmadi <ali@corp.local>"
    assert email["body"] == "سلام،\n\nگزارش پیوست است."
    assert email["attachments"] == [{"name": "report.csv", "content_type": "text/csv", "size": 20, "is_inline": False, "kind": "file"}]


def test_meeting_requests_are_searchable_and_readable_but_not_replyable(client):
    rows = {e["id"]: e for e in client.search_emails()["emails"]}
    assert rows["m4"]["kind"] == "meeting_request"
    assert rows["m4"]["preview"] == "Let's review Q3."
    assert rows["m1"]["kind"] == "email"

    invite = client.get_email("m4")
    assert invite["kind"] == "meeting_request"
    assert invite["meeting"] == {"start": "2026-09-25T09:30+03:30", "end": "2026-09-25T11:00+03:30", "location": "Room 1"}
    assert "meeting" not in client.get_email("m1")

    with pytest.raises(ValueError, match="meeting request, not an email"):
        client.create_reply_draft("m4", "OK")


def test_get_email_unknown_id_raises_exchange_error(client):
    with pytest.raises(ErrorItemNotFound):
        client.get_email("missing")


def test_save_attachment_writes_file_and_returns_text(client, env):
    saved = client.save_attachment("m1", "REPORT.CSV")
    assert saved["text"] == "month,total\n06,1200\n"
    assert saved["saved_to"].startswith(env["EXCHANGE_DOWNLOAD_DIR"])

    with pytest.raises(LookupError, match="report.csv"):
        client.save_attachment("m1", "missing.pdf")


def test_mark_as_read_updates_the_flag(client, ews):
    assert client.mark_as_read(["m1"]) == {"updated": 1, "failed": []}
    request = ews.last("UpdateItem")
    assert 'FieldURI="message:IsRead"' in request and "<t:IsRead>1</t:IsRead>" in request  # xs:boolean


def test_create_draft_saves_rtl_html_in_drafts(client, ews):
    result = client.create_draft(["ali@corp.local"], "پاسخ", "سلام\nممنون")
    request = ews.last("CreateItem")
    assert 'MessageDisposition="SaveOnly"' in request
    assert '<t:DistinguishedFolderId Id="drafts">' in request
    assert "dir=&quot;rtl&quot;" in request or 'dir="rtl"' in request
    assert result == {"id": "new-message", "subject": "پاسخ", "saved_in": "Drafts", "sent": False}

    with pytest.raises(ValueError, match="find_people"):
        client.create_draft(["Ali Ahmadi"], "x", "y")


def test_create_reply_all_draft(client, ews):
    result = client.create_reply_draft("m1", "Thanks", reply_all=True)
    request = ews.last("CreateItem")
    assert "<t:ReplyAllToItem>" in request and 'MessageDisposition="SaveOnly"' in request
    assert '<t:ReferenceItemId Id="m1"' in request
    assert result["subject"] == "RE: گزارش فروش شهریور"


def test_sending_is_off_by_default(client):
    with pytest.raises(PermissionError, match="EXCHANGE_ALLOW_SEND"):
        client.send_email(["ali@corp.local"], "Hi", "Body")
    with pytest.raises(PermissionError):
        client.create_event("Sync", "2026-09-21T09:00", attendees=["ali@corp.local"])


def test_send_email_when_enabled(env, ews, monkeypatch):
    from exchange_mcp.client import ExchangeClient
    from exchange_mcp.config import Settings

    monkeypatch.setenv("EXCHANGE_ALLOW_SEND", "true")
    client = ExchangeClient(Settings.from_env())
    assert client.send_email(["ali@corp.local"], "Hi", "Body")["sent"] is True
    assert 'MessageDisposition="SendAndSaveCopy"' in ews.last("CreateItem")


def test_create_event_without_attendees_sends_nothing(client, ews):
    result = client.create_event("Focus", "2026-09-21T09:00")
    request = ews.last("CreateItem")
    assert 'SendMeetingInvitations="SendToNone"' in request
    assert result["start"] == "2026-09-21T09:00+03:30" and result["end"] == "2026-09-21T10:00+03:30"

    all_day = client.create_event("Off", "2026-09-22", all_day=True)
    assert (all_day["start"], all_day["end"]) == ("2026-09-22", "2026-09-23")


def test_list_events_uses_calendar_view(client, ews):
    result = client.list_events("2026-09-18", "2026-09-25")
    request = ews.last("FindItem")
    assert "<m:CalendarView" in request
    assert result["end"] == "2026-09-26T00:00+03:30"
    assert result["events"][0]["start"] == "2026-09-20T09:30+03:30"
    assert result["events"][0]["organizer"] == "Sara Karimi <sara@corp.local>"


def test_get_event_has_attendees_and_body(client):
    event = client.get_event("e1")
    assert event["required_attendees"] == [{"who": "Me <me@corp.local>", "response": "Accept"}]
    assert event["body"] == "Agenda: numbers"


def test_find_people_works_as_the_first_call(client):
    people = client.find_people("ali")["people"]
    assert people[0]["email"] == "ali@corp.local"
    assert people[0]["job_title"] == "Sales Manager"
    assert people[0]["phones"] == [{"label": "BusinessPhone", "number": "021-5550"}]
    assert client.find_people("nobody") == {"count": 0, "people": []}


def test_rejected_login_is_explained(client, ews):
    ews.reject_logins = True
    with pytest.raises(UnauthorizedError) as caught:
        client.mailbox_info()
    assert "rejected the login" in explain(caught.value)


def test_check_command_prints_mailbox_and_latest_mail(env):
    done = subprocess.run([*server_command(), "check"], capture_output=True, timeout=120)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    info = json.loads(done.stdout.decode("utf-8"))
    assert info["email"] == "me@corp.local"
    assert info["latest_inbox"][0]["subject"] == "گزارش فروش شهریور"