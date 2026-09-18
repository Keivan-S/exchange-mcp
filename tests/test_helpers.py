from datetime import datetime, timedelta
from pathlib import Path

import pytest

from exchange_mcp.client import ExchangeClient, _safe_filename, _unique_path, html_to_text, text_to_html
from exchange_mcp.config import ConfigError, Settings


def _settings(**overrides) -> Settings:
    values = dict(
        email="me@corp.local",
        username="CORP\\me",
        ews_url=None,
        auth="ntlm",
        verify_ssl=True,
        ca_bundle=None,
        use_system_certs=True,
        timezone="Asia/Tehran",
        allow_send=False,
        download_dir=Path("."),
    )
    values.update(overrides)
    return Settings(**values)


def test_persian_plain_text_becomes_rtl_html():
    markup = text_to_html("سلام\nدنیا <ok>")
    assert 'dir="rtl"' in markup
    assert "سلام<br>" in markup
    assert "&lt;ok&gt;" in markup


def test_latin_plain_text_stays_ltr():
    assert 'dir="ltr"' in text_to_html("Hello")


def test_html_to_text_drops_styles_and_keeps_paragraphs():
    text = html_to_text("<html><head><style>p{color:red}</style></head><body><p>One &amp; two</p><div>Three<br>Four</div></body></html>")
    assert text == "One & two\n\nThree\nFour"


def test_parse_when_handles_dates_datetimes_and_offsets():
    client = ExchangeClient(_settings())
    midnight = client._parse_when("2026-09-18")
    assert midnight.isoformat() == "2026-09-18T00:00:00+03:30"
    assert client._parse_when("2026-09-18", end_of_day=True) - midnight == timedelta(days=1)
    assert client._parse_when("2026-09-18T09:15").isoformat() == "2026-09-18T09:15:00+03:30"
    assert client._parse_when("2026-09-18T05:45:00+00:00").isoformat() == "2026-09-18T09:15:00+03:30"
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        client._parse_when("1405/06/27")


def test_common_free_slots_needs_everyone_free():
    client = ExchangeClient(_settings())
    start = client._parse_when("2026-09-20T08:00")
    slots = client._common_free_slots(["00200", "02000"], start, 30)
    assert slots == [
        {"start": "2026-09-20T08:00+03:30", "end": "2026-09-20T08:30+03:30"},
        {"start": "2026-09-20T09:30+03:30", "end": "2026-09-20T10:30+03:30"},
    ]


def test_iso_output_is_in_mailbox_time_zone():
    client = ExchangeClient(_settings())
    assert client._iso(datetime.fromisoformat("2026-09-17T08:00:00+00:00")) == "2026-09-17T11:30+03:30"


def test_settings_validation(monkeypatch):
    for name in ("EXCHANGE_EMAIL", "EXCHANGE_AUTH", "EXCHANGE_EWS_URL"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ConfigError, match="EXCHANGE_EMAIL"):
        Settings.from_env()

    monkeypatch.setenv("EXCHANGE_EMAIL", "me@corp.local")
    monkeypatch.setenv("EXCHANGE_AUTH", "kerberos")
    with pytest.raises(ConfigError, match="EXCHANGE_AUTH"):
        Settings.from_env()

    monkeypatch.setenv("EXCHANGE_AUTH", "ntlm")
    monkeypatch.setenv("EXCHANGE_EWS_URL", "mail.corp.local")
    with pytest.raises(ConfigError, match="full URL"):
        Settings.from_env()

    monkeypatch.delenv("EXCHANGE_EWS_URL")
    settings = Settings.from_env()
    assert settings.username == "me@corp.local"
    assert settings.allow_send is False


def test_attachment_file_names_are_safe_and_never_overwrite(tmp_path):
    assert _safe_filename('a<b>:"c".pdf') == "a_b___c_.pdf"
    first = tmp_path / "report.csv"
    first.write_text("x")
    assert _unique_path(first).name == "report (1).csv"
