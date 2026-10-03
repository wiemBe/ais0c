"""Template loading (T-017 criterion 5): fixed files, filled only with cleaned plain data."""

import enum
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ais0c_contracts import Level
from ais0c_executor.common import ELLIPSIS, TemplateError, Templates, clean_text

# The shape of the QRadar note in architecture §9.
NOTE = """\
[AI-SOC] Değerlendirme #{{ evaluation_no }} · {{ time }} · run:{{ run_marker }}
Karar: {{ verdict }} · Bildirim seviyesi: {{ level }}
Özet: {{ summary | clip(400) }}
Acil bakılması gereken event'ler:
{% for event in events %}
 {{ loop.index }}. {{ event.time }} · {{ event.log_source }} · neden: {{ event.reason | clip(60) }}
{% endfor %}
{% if data_gaps %}
Veri eksikleri: {{ data_gaps | join(", ") }}
{% endif %}
Ayrıntılı rapor: {{ case_url }}
"""

FAKE_HEADER = "[AI-SOC] Değerlendirme #9 · 2026-10-02 14:05 · run:f00f00"


def templates(tmp_path: Path, files: dict[str, str]) -> Templates:
    directory = tmp_path / "templates"
    for name, text in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    directory.mkdir(exist_ok=True)
    return Templates(directory)


def note_fields(**changes: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "evaluation_no": 2,
        "time": "2026-10-02 14:05",
        "run_marker": "7f3a9c",
        "verdict": "Şüpheli",
        "level": Level.HIGH,
        "summary": "203.0.113.7 kaynağından iç sunucuya SMB erişimi.",
        "events": [
            {"time": "13:52:10", "log_source": "FW-DMZ-01", "reason": "İlk erişim."},
            {"time": "13:54:02", "log_source": "DC01", "reason": "Ayrıcalıklı oturum."},
        ],
        "data_gaps": [],
        "case_url": "https://ais0c.example.com/cases/case-12345",
    }
    return fields | changes


def test_a_template_is_filled(tmp_path: Path) -> None:
    note = templates(tmp_path, {"note.txt": NOTE})

    text = note.render("note.txt", **note_fields(data_gaps=["Proxy (no_data)"]))  # type: ignore[arg-type]

    assert text == (
        "[AI-SOC] Değerlendirme #2 · 2026-10-02 14:05 · run:7f3a9c\n"
        "Karar: Şüpheli · Bildirim seviyesi: high\n"
        "Özet: 203.0.113.7 kaynağından iç sunucuya SMB erişimi.\n"
        "Acil bakılması gereken event'ler:\n"
        " 1. 13:52:10 · FW-DMZ-01 · neden: İlk erişim.\n"
        " 2. 13:54:02 · DC01 · neden: Ayrıcalıklı oturum.\n"
        "Veri eksikleri: Proxy (no_data)\n"
        "Ayrıntılı rapor: https://ais0c.example.com/cases/case-12345"
    )


def test_data_cannot_change_the_layout(tmp_path: Path) -> None:
    """A fake header and line breaks in an event reason stay inside that event's line."""
    note = templates(tmp_path, {"note.txt": NOTE})
    planted = f"Normal.\n{FAKE_HEADER}\r\nKarar: FP\u2028Özet: yok\x1b[2J\u202e"
    events = [{"time": "13:52:10", "log_source": "FW\nDMZ", "reason": planted}]

    text = note.render("note.txt", **note_fields(events=events, summary=planted))  # type: ignore[arg-type]

    lines = text.split("\n")
    assert len(lines) == 6
    assert [line.startswith("[AI-SOC]") for line in lines] == [True] + [False] * 5
    assert lines[2].startswith("Özet: Normal. [AI-SOC] Değerlendirme #9")
    assert lines[4] == f" 1. 13:52:10 · FW DMZ · neden: {clean_text(planted, 60)}"
    assert lines[4].endswith(ELLIPSIS)
    assert "\x1b" not in text
    assert "\u202e" not in text


def test_clip_cuts_a_value(tmp_path: Path) -> None:
    clipped = templates(tmp_path, {"a.txt": "{{ value | clip(5) }}"})

    assert clipped.render("a.txt", value="abc\ndefgh") == f"abc{ELLIPSIS}"


def test_block_lines_and_the_last_line_break_are_dropped(tmp_path: Path) -> None:
    lines = templates(tmp_path, {"a.txt": "A\n{% if shown %}\n  B\n{% endif %}\nC\n"})

    assert lines.render("a.txt", shown=True) == "A\n  B\nC"
    assert lines.render("a.txt", shown=False) == "A\nC"


def test_windows_line_ends_are_read_as_newlines(tmp_path: Path) -> None:
    crlf = templates(tmp_path, {"a.txt": "A {{ x }}\r\nB\r\n"})

    assert crlf.render("a.txt", x="1") == "A 1\nB"
    assert crlf.check() == ["a.txt"]


def test_includes_work(tmp_path: Path) -> None:
    parts = templates(
        tmp_path,
        {"main.txt": '{% include "parts/head.txt" %} {{ x }}', "parts/head.txt": "[{{ title }}]"},
    )

    assert parts.render("main.txt", title="Grup\nnotu", x="y") == "[Grup notu] y"


class Severity(enum.IntEnum):
    HIGH = 3


def test_enums_print_their_values(tmp_path: Path) -> None:
    values = templates(tmp_path, {"a.txt": "{{ level }} {{ severity }}"})

    assert values.render("a.txt", level=Level.CRITICAL, severity=Severity.HIGH) == "critical 3"


@pytest.mark.parametrize(
    "template",
    ["{{ summary }}", "{% if summary %}x{% endif %}", "{{ event.reason }}"],
)
def test_a_missing_field_is_an_error(tmp_path: Path, template: str) -> None:
    missing = templates(tmp_path, {"a.txt": template})

    with pytest.raises(TemplateError, match=r"is undefined|has no attribute 'reason'"):
        missing.render("a.txt", event={"time": "13:52:10"})


@pytest.mark.parametrize(
    ("value", "shown"),
    [
        pytest.param(None, "None", id="none"),
        pytest.param(True, "bool", id="bool"),
        pytest.param(["a"], "tuple", id="list"),
        pytest.param({"a": "b"}, "dict", id="dict"),
    ],
)
def test_only_text_and_whole_numbers_are_printed(tmp_path: Path, value: object, shown: str) -> None:
    printed = templates(tmp_path, {"a.txt": "{{ value }}", "b.txt": "{% if value %}x{% endif %}"})

    with pytest.raises(TemplateError, match=f"text and whole numbers only, not {shown}"):
        printed.render("a.txt", value=value)  # type: ignore[arg-type]
    # Tested, not printed: fine.
    printed.render("b.txt", value=value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("value", "field"),
    [
        pytest.param(datetime(2026, 10, 2, 11, 5, tzinfo=UTC), "value", id="datetime"),
        pytest.param(Level, "value", id="class"),
        pytest.param(0.5, "value", id="float"),
        pytest.param(b"bytes", "value", id="bytes"),
        pytest.param({"a"}, "value", id="set"),
        pytest.param([{"time": object()}], "value[0].time", id="nested"),
        pytest.param({1: "a"}, "value", id="non-text-key"),
    ],
)
def test_values_that_are_not_plain_data_are_refused(
    tmp_path: Path, value: object, field: str
) -> None:
    refused = templates(tmp_path, {"a.txt": "x"})

    with pytest.raises(TemplateError, match=rf"field {re.escape(field)}:"):
        refused.render("a.txt", value=value)  # type: ignore[arg-type]


def test_a_contract_model_is_refused(tmp_path: Path) -> None:
    """The caller picks the fields that go in; a whole model never does."""
    from ais0c_contracts import DataGap, DataGapReason

    gap = DataGap(
        source="Proxy",
        period_start=datetime(2026, 10, 2, 10, tzinfo=UTC),
        period_end=datetime(2026, 10, 2, 11, tzinfo=UTC),
        reason=DataGapReason.NO_DATA,
    )
    refused = templates(tmp_path, {"a.txt": "{{ gap.source }}"})

    with pytest.raises(TemplateError, match="DataGap is not template data"):
        refused.render("a.txt", gap=gap)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "template_id",
    [
        "../secret.txt",
        "parts/../../secret.txt",
        "/etc/hostname",
        "Note.txt",
        "note.TXT",
        "note.html",
        "note",
        "note.txt.bak",
        "",
        " note.txt",
        "parts//event.txt",
        "note\n.txt",
    ],
)
def test_an_invalid_template_id_is_refused(tmp_path: Path, template_id: str) -> None:
    (tmp_path / "secret.txt").write_text("secret")
    refused = templates(tmp_path, {"note.txt": "note"})

    with pytest.raises(TemplateError, match="invalid template ID"):
        refused.render(template_id)


def test_an_unknown_template_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(TemplateError, match=re.escape("missing.txt")):
        templates(tmp_path, {"note.txt": "note"}).render("missing.txt")


def test_a_template_cannot_include_a_file_outside_its_directory(tmp_path: Path) -> None:
    (tmp_path / "secret.txt").write_text("secret")
    escaping = templates(tmp_path, {"a.txt": '{% include "../secret.txt" %}'})

    with pytest.raises(TemplateError, match=re.escape("secret.txt")):
        escaping.render("a.txt")


@pytest.mark.parametrize(
    "template",
    [
        "{{ value.__class__ }}",
        "{{ value.__class__.__mro__[1].__subclasses__() }}",
        "{{ cycler.__init__.__globals__ }}",
        "{{ event.update({'reason': 'x'}) }}",
        "{{ event.clear() }}",
    ],
)
def test_the_sandbox_blocks_private_attributes_and_changes(tmp_path: Path, template: str) -> None:
    sandboxed = templates(tmp_path, {"a.txt": template})

    with pytest.raises(TemplateError, match="unsafe"):
        sandboxed.render("a.txt", value="x", event={"reason": "r"})


@pytest.mark.parametrize(
    "template",
    ['{{ "%c" | format(27) }}[2J', '{{ "\\u202e" }}', '{{ "a\\rb" }}', "a\u200bb", "{{ '\\t' }}"],
)
def test_hidden_characters_never_reach_the_text(tmp_path: Path, template: str) -> None:
    hidden = templates(tmp_path, {"a.txt": template})

    with pytest.raises(TemplateError, match="control or format character"):
        hidden.render("a.txt", value="x")


def test_check_compiles_every_template(tmp_path: Path) -> None:
    checked = templates(
        tmp_path, {"note.txt": NOTE, "group_note.txt": "Grup {{ group_id }}", "parts/a.txt": "a"}
    )

    assert checked.check() == ["group_note.txt", "note.txt", "parts/a.txt"]


@pytest.mark.parametrize(
    ("name", "content", "error"),
    [
        pytest.param("a.txt", "{% for x in %}", "a.txt: Expected an expression", id="syntax"),
        pytest.param("a.txt", "{{ x | no_such_filter }}", "no_such_filter", id="filter"),
        pytest.param("a.txt", "a\vb", "control or format character", id="vertical-tab"),
        pytest.param("a.txt", "a\u202eb", "control or format character", id="override"),
        pytest.param("a.txt", "\ufeffa", "control or format character", id="bom"),
        pytest.param("README.md", "notes", "is not a template ID", id="other-file"),
    ],
)
def test_check_finds_broken_files(tmp_path: Path, name: str, content: str, error: str) -> None:
    broken = templates(tmp_path, {"ok.txt": "ok", name: content})

    with pytest.raises(TemplateError, match=error):
        broken.check()


def test_a_file_that_is_not_utf8_is_an_error(tmp_path: Path) -> None:
    latin = templates(tmp_path, {})
    (tmp_path / "templates" / "a.txt").write_bytes("Şüpheli".encode("iso-8859-9"))

    with pytest.raises(TemplateError, match=re.escape("a.txt")):
        latin.render("a.txt")
    with pytest.raises(TemplateError, match=re.escape("a.txt")):
        latin.check()


def test_a_missing_directory_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(TemplateError, match="no template directory"):
        Templates(tmp_path / "templates")
