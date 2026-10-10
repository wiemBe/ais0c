# SPDX-License-Identifier: Apache-2.0
"""add_offense_note sends the note text in a form body, not in the URL (T-040).

QRadar reads note_text from the query string or from an application/x-www-form-urlencoded body.
In the URL, a note of 2000 Turkish letters is 12,000 characters once percent-encoded, and
QRadar's web server refuses the request line with 414; the lab QRadar did so in T-019. The fake
QRadar refuses long request lines the same way, so a note moved back into the URL fails here.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from fastmcp import Client

from qradar_mcp.fork.settings import Settings
from qradar_mcp.fork.tool_profiles import NOTE_PROFILE

from .conftest import make_server
from .fake_qradar import (
    CONSOLE_HOST,
    MAX_REQUEST_LINE,
    OFFENSE_ID,
    QRADAR_TOKEN,
    FakeQRadar,
    form_fields,
)

FORM_CONTENT_TYPE = "application/x-www-form-urlencoded; charset=UTF-8"
TURKISH_LETTERS = "çğıöşüÇĞİÖŞÜ"
# Every letter takes two bytes in UTF-8, so six characters once percent-encoded.
LONG_NOTE = (TURKISH_LETTERS * 167)[:2000]
# What a form encoding must escape, line feeds, and markup a note may hold as plain text.
SPECIAL_NOTE = (
    "[AI-SOC] Değerlendirme #1 · 2026-10-05 14:05 · run:7f3a9c\n"
    "Özet: a&b=c+d; 100% %41 <b>güçlü</b> \"çift\" 'tek' ?#/\n"
    "Önerilen: investigate_further"
)
SHORT_NOTE = "Özet notu."
NOTES_URL = f"https://{CONSOLE_HOST}/api/siem/offenses/{OFFENSE_ID}/notes"


async def add_note(
    fake: FakeQRadar, settings: Settings, note_text: str, fields: str | None = None
) -> dict[str, Any]:
    arguments: dict[str, Any] = {"offense_id": OFFENSE_ID, "note_text": note_text}
    if fields is not None:
        arguments["fields"] = fields
    async with Client(make_server(NOTE_PROFILE, fake, settings)) as client:
        result = await client.call_tool("add_offense_note", arguments)
    return result.structured_content


def note_request(fake: FakeQRadar) -> httpx.Request:
    [request] = [request for request in fake.api_requests() if request.method == "POST"]
    return request


def sent_fields(request: httpx.Request) -> dict[str, list[str]]:
    """The form body as QRadar reads it; strict, so a malformed body fails the test."""
    return parse_qs(request.content.decode("ascii"), encoding="utf-8", strict_parsing=True)


@pytest.mark.asyncio
async def test_a_long_turkish_note_goes_in_the_body_and_the_url_stays_short(
    settings: Settings, fake_qradar: FakeQRadar
) -> None:
    note = await add_note(fake_qradar, settings, note_text=LONG_NOTE)

    request = note_request(fake_qradar)
    assert request.url.path == f"/api/siem/offenses/{OFFENSE_ID}/notes"
    assert "note_text" not in request.url.params
    assert len(str(request.url)) < 500
    assert request.headers["Content-Type"] == FORM_CONTENT_TYPE
    assert sent_fields(request) == {"note_text": [LONG_NOTE]}
    assert note["note_text"] == LONG_NOTE


@pytest.mark.asyncio
async def test_form_characters_line_feeds_and_markup_arrive_unchanged(
    settings: Settings, fake_qradar: FakeQRadar
) -> None:
    note = await add_note(fake_qradar, settings, note_text=SPECIAL_NOTE)

    assert sent_fields(note_request(fake_qradar)) == {"note_text": [SPECIAL_NOTE]}
    assert note["note_text"] == SPECIAL_NOTE
    assert fake_qradar.notes[-1]["note_text"] == SPECIAL_NOTE


@pytest.mark.asyncio
async def test_only_the_fields_parameter_stays_in_the_url(
    settings: Settings, fake_qradar: FakeQRadar
) -> None:
    await add_note(fake_qradar, settings, note_text=SHORT_NOTE, fields="id,note_text")

    request = note_request(fake_qradar)
    assert dict(request.url.params) == {"fields": "id,note_text"}
    assert sent_fields(request) == {"note_text": [SHORT_NOTE]}


async def post_to_fake(
    fake: FakeQRadar,
    *,
    params: dict[str, str] | None = None,
    content_type: str | None = None,
    content: str | None = None,
) -> httpx.Response:
    """A POST to the offense's notes, straight to the fake QRadar."""
    headers = {"SEC": QRADAR_TOKEN}
    if content_type is not None:
        headers["Content-Type"] = content_type
    async with httpx.AsyncClient(transport=fake.transport()) as client:
        return await client.post(NOTES_URL, headers=headers, params=params, content=content)


@pytest.mark.asyncio
async def test_the_long_note_in_the_url_would_get_414_and_write_nothing(
    fake_qradar: FakeQRadar,
) -> None:
    # The fork before T-040: note_text in the query string.
    refused = await post_to_fake(fake_qradar, params={"note_text": LONG_NOTE})
    short = await post_to_fake(fake_qradar, params={"note_text": SHORT_NOTE})

    assert len(str(refused.request.url)) > MAX_REQUEST_LINE
    assert refused.status_code == 414
    assert short.status_code == 201
    assert [note["note_text"] for note in fake_qradar.notes[1:]] == [SHORT_NOTE]


@pytest.mark.asyncio
async def test_a_form_body_without_a_charset_garbles_turkish_letters(
    fake_qradar: FakeQRadar,
) -> None:
    # Why the fork names the charset: without it the percent-encoded UTF-8 bytes are read as
    # ISO-8859-1, as a servlet container reads them.
    response = await post_to_fake(
        fake_qradar,
        content_type="application/x-www-form-urlencoded",
        content="note_text=%C3%A7%C4%9F",
    )
    garbled = "çğ".encode().decode("iso-8859-1")

    assert response.status_code == 201
    assert response.json()["note_text"] == garbled != "çğ"
    assert form_fields(response.request) == {"note_text": [garbled]}
