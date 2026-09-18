"""Canned mailbox served by FakeEWS: a folder tree, three inbox messages, one calendar event, one GAL entry."""

import base64
import re

from fake_ews import FakeEWS

FOLDERS = [
    # id, distinguished id, parent, name, class, unread, total
    ("root", "root", None, "root", None, 0, 0),
    ("msgroot", "msgfolderroot", "root", "Top of Information Store", None, 0, 0),
    ("inbox", "inbox", "msgroot", "Inbox", "IPF.Note", 2, 3),
    ("projects", None, "inbox", "Projects", "IPF.Note", 0, 0),
    ("sent", "sentitems", "msgroot", "Sent Items", "IPF.Note", 0, 0),
    ("drafts", "drafts", "msgroot", "Drafts", "IPF.Note", 0, 0),
    ("trash", "deleteditems", "msgroot", "Deleted Items", "IPF.Note", 0, 0),
    ("junk", "junkemail", "msgroot", "Junk Email", "IPF.Note", 0, 0),
    ("outbox", "outbox", "msgroot", "Outbox", "IPF.Note", 0, 0),
    ("calendar", "calendar", "msgroot", "Calendar", "IPF.Appointment", 0, 1),
    ("contacts", "contacts", "msgroot", "Contacts", "IPF.Contact", 0, 0),
]

CSV = "month,total\n06,1200\n".encode()

MESSAGES = [
    {
        "id": "m1",
        "subject": "گزارش فروش شهریور",
        "from": ("Ali Ahmadi", "ali@corp.local"),
        "received": "2026-09-17T08:00:00Z",
        "is_read": False,
        "attachments": [("a1", "report.csv", "text/csv", CSV)],
        "html": "<html><head><style>p{}</style></head><body><p>سلام،</p><p>گزارش پیوست است.</p></body></html>",
        "text": "سلام، گزارش پیوست است.",
    },
    {
        "id": "m2",
        "subject": "Meeting notes",
        "from": ("Sara Karimi", "sara@corp.local"),
        "received": "2026-09-16T10:00:00Z",
        "is_read": True,
        "attachments": [],
        "html": "<div>Notes<br>line two</div>",
        "text": "Notes line two",
    },
    {
        "id": "m3",
        "subject": "Invoice 1024",
        "from": ("Billing", "billing@vendor.com"),
        "received": "2026-09-10T06:30:00Z",
        "is_read": False,
        "attachments": [],
        "html": "<p>Please pay invoice 1024.</p>",
        "text": "Please pay invoice 1024.",
    },
    {
        # Mail folders also hold meeting requests; they must not vanish from search.
        "id": "m4",
        "tag": "MeetingRequest",
        "subject": "Budget review",
        "from": ("Sara Karimi", "sara@corp.local"),
        "received": "2026-09-05T09:00:00Z",
        "is_read": True,
        "attachments": [],
        "html": "<p>Let's review Q3.</p>",
        "text": "Let's review Q3.",
        "meeting": ("2026-09-25T06:00:00Z", "2026-09-25T07:30:00Z", "Room 1"),
    },
]

EVENT = {
    "id": "e1",
    "subject": "Weekly sync",
    "start": "2026-09-20T06:00:00Z",
    "end": "2026-09-20T07:00:00Z",
    "location": "Room 2",
}


def _ok(op: str, inner: str, klass: str = "Success", code: str = "NoError") -> str:
    return (
        f"<m:{op}Response><m:ResponseMessages><m:{op}ResponseMessage ResponseClass=\"{klass}\">"
        f"<m:ResponseCode>{code}</m:ResponseCode>{inner}</m:{op}ResponseMessage></m:ResponseMessages></m:{op}Response>"
    )


def _error(op: str, code: str, text: str) -> str:
    return (
        f"<m:{op}ResponseMessage ResponseClass=\"Error\"><m:MessageText>{text}</m:MessageText>"
        f"<m:ResponseCode>{code}</m:ResponseCode><m:DescriptiveLinkKey>0</m:DescriptiveLinkKey></m:{op}ResponseMessage>"
    )


def _folder_xml(folder) -> str:
    fid, distinguished, parent, name, klass, unread, total = folder
    tag = {"IPF.Appointment": "CalendarFolder", "IPF.Contact": "ContactsFolder"}.get(klass, "Folder")
    children = sum(1 for f in FOLDERS if f[2] == fid)
    return (
        f"<t:{tag}><t:FolderId Id=\"{fid}\" ChangeKey=\"ck-{fid}\"/>"
        + (f"<t:ParentFolderId Id=\"{parent}\" ChangeKey=\"ck-{parent}\"/>" if parent else "")
        + (f"<t:FolderClass>{klass}</t:FolderClass>" if klass else "")
        + f"<t:DisplayName>{name}</t:DisplayName><t:TotalCount>{total}</t:TotalCount>"
        + f"<t:ChildFolderCount>{children}</t:ChildFolderCount>"
        + (f"<t:DistinguishedFolderId>{distinguished}</t:DistinguishedFolderId>" if distinguished else "")
        + (f"<t:UnreadCount>{unread}</t:UnreadCount>" if tag == "Folder" else "")
        + f"</t:{tag}>"
    )


def _descendants(parent: str, deep: bool) -> list:
    found = []
    for f in FOLDERS:
        if f[2] == parent:
            found.append(f)
            if deep:
                found.extend(_descendants(f[0], True))
    return found


def get_folder(raw: str) -> str:
    wanted = re.findall(r'<t:(?:Distinguished)?FolderId Id="([^"]+)"', raw)
    parts = []
    for key in wanted:
        folder = next((f for f in FOLDERS if key in (f[0], f[1])), None)
        if folder is None:
            parts.append(_error("GetFolder", "ErrorFolderNotFound", "folder not found"))
        else:
            parts.append(
                "<m:GetFolderResponseMessage ResponseClass=\"Success\"><m:ResponseCode>NoError</m:ResponseCode>"
                f"<m:Folders>{_folder_xml(folder)}</m:Folders></m:GetFolderResponseMessage>"
            )
    return f"<m:GetFolderResponse><m:ResponseMessages>{''.join(parts)}</m:ResponseMessages></m:GetFolderResponse>"


def find_folder(raw: str) -> str:
    deep = 'Traversal="Deep"' in raw
    parent = re.search(r'<m:ParentFolderIds><t:(?:Distinguished)?FolderId Id="([^"]+)"', raw).group(1)
    parent_id = next(f[0] for f in FOLDERS if parent in (f[0], f[1]))
    found = _descendants(parent_id, deep)
    return _ok(
        "FindFolder",
        f"<m:RootFolder IndexedPagingOffset=\"{len(found)}\" TotalItemsInView=\"{len(found)}\" IncludesLastItemInRange=\"true\">"
        f"<t:Folders>{''.join(_folder_xml(f) for f in found)}</t:Folders></m:RootFolder>",
    )


def _message_xml(m: dict, full: bool) -> str:
    name, email = m["from"]
    tag = m.get("tag", "Message")
    xml = (
        f"<t:{tag}><t:ItemId Id=\"{m['id']}\" ChangeKey=\"ck-{m['id']}\"/>"
        f"<t:Subject>{m['subject']}</t:Subject>"
        f"<t:DateTimeReceived>{m['received']}</t:DateTimeReceived><t:DateTimeSent>{m['received']}</t:DateTimeSent>"
        f"<t:HasAttachments>{str(bool(m['attachments'])).lower()}</t:HasAttachments>"
        "<t:Importance>Normal</t:Importance><t:DisplayTo>Me</t:DisplayTo>"
        f"<t:Sender><t:Mailbox><t:Name>{name}</t:Name><t:EmailAddress>{email}</t:EmailAddress></t:Mailbox></t:Sender>"
        f"<t:IsRead>{str(m['is_read']).lower()}</t:IsRead>"
    )
    if full:
        xml += (
            f"<t:From><t:Mailbox><t:Name>{name}</t:Name><t:EmailAddress>{email}</t:EmailAddress></t:Mailbox></t:From>"
            "<t:ToRecipients><t:Mailbox><t:Name>Me</t:Name><t:EmailAddress>me@corp.local</t:EmailAddress></t:Mailbox></t:ToRecipients>"
            "<t:IsDraft>false</t:IsDraft>"
            f"<t:Body BodyType=\"HTML\">{_escape(m['html'])}</t:Body>"
        )
        if m["attachments"]:
            xml += "<t:Attachments>" + "".join(
                f"<t:FileAttachment><t:AttachmentId Id=\"{aid}\"/><t:Name>{aname}</t:Name><t:ContentType>{ctype}</t:ContentType>"
                f"<t:Size>{len(data)}</t:Size><t:IsInline>false</t:IsInline></t:FileAttachment>"
                for aid, aname, ctype, data in m["attachments"]
            ) + "</t:Attachments>"
        if "meeting" in m:
            start, end, location = m["meeting"]
            xml += f"<t:Start>{start}</t:Start><t:End>{end}</t:End><t:Location>{location}</t:Location>"
    return xml + f"</t:{tag}>"


def _event_xml(full: bool) -> str:
    e = EVENT
    xml = (
        f"<t:CalendarItem><t:ItemId Id=\"{e['id']}\" ChangeKey=\"ck-{e['id']}\"/><t:Subject>{e['subject']}</t:Subject>"
        f"<t:Start>{e['start']}</t:Start><t:End>{e['end']}</t:End><t:IsAllDayEvent>false</t:IsAllDayEvent>"
        f"<t:LegacyFreeBusyStatus>Busy</t:LegacyFreeBusyStatus><t:Location>{e['location']}</t:Location>"
        "<t:IsMeeting>true</t:IsMeeting><t:IsCancelled>false</t:IsCancelled><t:IsRecurring>false</t:IsRecurring>"
        "<t:MyResponseType>Accept</t:MyResponseType><t:DisplayTo>Sara Karimi</t:DisplayTo>"
        "<t:Organizer><t:Mailbox><t:Name>Sara Karimi</t:Name><t:EmailAddress>sara@corp.local</t:EmailAddress></t:Mailbox></t:Organizer>"
    )
    if full:
        xml += (
            "<t:RequiredAttendees><t:Attendee><t:Mailbox><t:Name>Me</t:Name><t:EmailAddress>me@corp.local</t:EmailAddress></t:Mailbox>"
            "<t:ResponseType>Accept</t:ResponseType></t:Attendee></t:RequiredAttendees>"
            "<t:Body BodyType=\"Text\">Agenda: numbers</t:Body>"
        )
    return xml + "</t:CalendarItem>"


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def find_item(raw: str) -> str:
    # One response message per parent folder, like the real server. Mail lives in the inbox, the event in the calendar.
    parents = re.search(r"<m:ParentFolderIds>(.*?)</m:ParentFolderIds>", raw).group(1)
    parts = []
    for key in re.findall(r'FolderId Id="([^"]+)"', parents):
        if key in ("calendar",):
            items, count = _event_xml(False), 1
        elif key in ("inbox",):
            items, count = "".join(_message_xml(m, False) for m in MESSAGES), len(MESSAGES)
        else:
            items, count = "", 0
        parts.append(
            "<m:FindItemResponseMessage ResponseClass=\"Success\"><m:ResponseCode>NoError</m:ResponseCode>"
            f"<m:RootFolder IndexedPagingOffset=\"{count}\" TotalItemsInView=\"{count}\" IncludesLastItemInRange=\"true\">"
            f"<t:Items>{items}</t:Items></m:RootFolder></m:FindItemResponseMessage>"
        )
    return f"<m:FindItemResponse><m:ResponseMessages>{''.join(parts)}</m:ResponseMessages></m:FindItemResponse>"


def get_item(raw: str) -> str:
    parts = []
    for item_id in re.findall(r'<t:ItemId Id="([^"]+)"', raw):
        message = next((m for m in MESSAGES if m["id"] == item_id), None)
        if message and 'FieldURI="item:TextBody"' in raw and 'FieldURI="item:Body"' not in raw:
            # Preview fetch: Exchange 2013+ returns a server-rendered plain-text body.
            tag = message.get("tag", "Message")
            body = (
                f"<t:{tag}><t:ItemId Id=\"{item_id}\" ChangeKey=\"ck-{item_id}\"/>"
                f"<t:TextBody BodyType=\"Text\">{_escape(message['text'])}</t:TextBody></t:{tag}>"
            )
        elif message:
            body = _message_xml(message, True)
        elif item_id == EVENT["id"]:
            body = _event_xml(True)
        else:
            parts.append(_error("GetItem", "ErrorItemNotFound", "The specified object was not found in the store."))
            continue
        parts.append(
            "<m:GetItemResponseMessage ResponseClass=\"Success\"><m:ResponseCode>NoError</m:ResponseCode>"
            f"<m:Items>{body}</m:Items></m:GetItemResponseMessage>"
        )
    return f"<m:GetItemResponse><m:ResponseMessages>{''.join(parts)}</m:ResponseMessages></m:GetItemResponse>"


def get_attachment(raw: str) -> str:
    wanted = re.search(r'<t:AttachmentId Id="([^"]+)"', raw).group(1)
    for m in MESSAGES:
        for aid, name, ctype, data in m["attachments"]:
            if aid == wanted:
                return _ok(
                    "GetAttachment",
                    f"<m:Attachments><t:FileAttachment><t:AttachmentId Id=\"{aid}\"/><t:Name>{name}</t:Name>"
                    f"<t:ContentType>{ctype}</t:ContentType><t:Content>{base64.b64encode(data).decode()}</t:Content>"
                    "</t:FileAttachment></m:Attachments>",
                )
    raise KeyError(wanted)


def create_item(raw: str) -> str:
    tag = "CalendarItem" if "<t:CalendarItem" in raw else "Message"
    return _ok("CreateItem", f"<m:Items><t:{tag}><t:ItemId Id=\"new-{tag.lower()}\" ChangeKey=\"ck-new\"/></t:{tag}></m:Items>")


def update_item(raw: str) -> str:
    ids = re.findall(r'<t:ItemId Id="([^"]+)"', raw)
    return _ok(
        "UpdateItem",
        "<m:Items>" + "".join(f"<t:Message><t:ItemId Id=\"{i}\" ChangeKey=\"ck-{i}-2\"/></t:Message>" for i in ids) + "</m:Items>"
        "<m:ConflictResults><t:Count>0</t:Count></m:ConflictResults>",
    )


def resolve_names(raw: str) -> str:
    if "DUMMY" in raw or "nobody" in raw:
        return (
            "<m:ResolveNamesResponse><m:ResponseMessages>"
            + _error("ResolveNames", "ErrorNameResolutionNoResults", "No results were found.")
            + "</m:ResponseMessages></m:ResolveNamesResponse>"
        )
    return _ok(
        "ResolveNames",
        "<m:ResolutionSet TotalItemsInView=\"1\" IncludesLastItemInRange=\"true\"><t:Resolution>"
        "<t:Mailbox><t:Name>Ali Ahmadi</t:Name><t:EmailAddress>ali@corp.local</t:EmailAddress>"
        "<t:RoutingType>SMTP</t:RoutingType><t:MailboxType>Mailbox</t:MailboxType></t:Mailbox>"
        "<t:Contact><t:DisplayName>Ali Ahmadi</t:DisplayName><t:JobTitle>Sales Manager</t:JobTitle>"
        "<t:Department>Sales</t:Department><t:PhoneNumbers><t:Entry Key=\"BusinessPhone\">021-5550</t:Entry></t:PhoneNumbers>"
        "</t:Contact></t:Resolution></m:ResolutionSet>",
    )


CONVERT_ID = (
    "<m:ConvertIdResponse><m:ResponseMessages>"
    + _error("ConvertId", "ErrorInvalidIdMalformed", "Id is malformed.")
    + "</m:ResponseMessages></m:ConvertIdResponse>"
)


def fake_mailbox() -> FakeEWS:
    ews = FakeEWS()
    ews.handlers.update(
        ConvertId=lambda raw: CONVERT_ID,
        GetFolder=get_folder,
        FindFolder=find_folder,
        FindItem=find_item,
        GetItem=get_item,
        GetAttachment=get_attachment,
        CreateItem=create_item,
        UpdateItem=update_item,
        ResolveNames=resolve_names,
    )
    return ews
