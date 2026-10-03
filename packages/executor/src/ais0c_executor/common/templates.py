"""The fixed templates notes and e-mails are built from (architecture §9).

Each executor module keeps its templates in a directory of its own and renders them through a
`Templates` object:

    TEMPLATES = Templates(Path(__file__).parent / "templates")
    text = TEMPLATES.render("offense_note.txt", summary=summary, events=events)

Templates are plain-text Jinja2 files in UTF-8; "\\r\\n" line ends are read as "\\n". A template
ID is the file's path in the directory: lowercase letters, digits, `_` and `-`, ending in `.txt`.
Templates run in Jinja2's immutable sandbox: no access to private attributes, no changes to the
data. There are no HTML templates yet; they need escaping on top of what is here (the hunt PDF,
phase 3).

What a template prints is limited, so only the template decides the layout:

- Field values are plain data: text, whole numbers, booleans and None, in lists, tuples and
  dicts. Every text in them is cleaned with `clean_text` first, so a value is one line without
  hidden characters. Anything else is refused, contract models and datetimes included: the
  caller picks the fields that go in and formats them.
- Printing a missing field, None, a boolean, a list or a dict is an error, never "None", "True"
  or an empty string.
- The `clip` filter cuts a value: `{{ event.reason | clip(200) }}`.
- The rendered text has no control or format character except "\\n".
"""

import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from jinja2 import FileSystemLoader, StrictUndefined, Undefined
from jinja2.sandbox import ImmutableSandboxedEnvironment

from ais0c_executor.common.errors import TemplateError
from ais0c_executor.common.text import clean_text, is_clean

type FieldValue = str | int | bool | Sequence[FieldValue] | Mapping[str, FieldValue] | None

_TEMPLATE_ID = re.compile(r"[a-z0-9][a-z0-9_-]*(?:/[a-z0-9][a-z0-9_-]*)*\.txt")


class Templates:
    """The templates in one directory."""

    def __init__(self, directory: Path) -> None:
        if not directory.is_dir():
            raise TemplateError(f"no template directory at {directory}")
        self._loader = FileSystemLoader(directory, encoding="utf-8")
        self._environment = ImmutableSandboxedEnvironment(
            loader=self._loader,
            autoescape=False,
            undefined=StrictUndefined,
            finalize=_printable,
            trim_blocks=True,
            lstrip_blocks=True,
            auto_reload=False,
        )
        self._environment.filters["clip"] = _clip

    def render(self, template_id: str, /, **fields: FieldValue) -> str:
        """The template filled with `fields`. Raises `TemplateError` if it cannot be."""
        if not _TEMPLATE_ID.fullmatch(template_id):
            raise TemplateError(f"invalid template ID {template_id!r}")
        try:
            data = {name: _plain(value, name) for name, value in fields.items()}
            text = self._environment.get_template(template_id).render(data)
        except Exception as error:
            # Whatever fails inside a template is a defect of the template or of its data.
            raise TemplateError(f"{template_id}: {error}") from error
        if not is_clean(text, multiline=True):
            raise TemplateError(f"{template_id}: the text has a control or format character")
        return text

    def check(self) -> list[str]:
        """Compile every template and check its file; return the template IDs, sorted.

        Raises `TemplateError` for a file whose name is not a template ID, a syntax error, or
        a control or format character in the file other than "\\n". A module calls it at
        start-up or in a test, so a broken template is found before the first write.
        """
        template_ids = sorted(self._environment.list_templates())
        for template_id in template_ids:
            if not _TEMPLATE_ID.fullmatch(template_id):
                raise TemplateError(f"{template_id!r} is not a template ID")
            try:
                source, _, _ = self._loader.get_source(self._environment, template_id)
                self._environment.get_template(template_id)
            except Exception as error:
                raise TemplateError(f"{template_id}: {error}") from error
            if not is_clean(source, multiline=True):
                raise TemplateError(f"{template_id}: the file has a control or format character")
        return template_ids


def _plain(value: object, path: str) -> object:
    """`value` as template data: texts cleaned, lists made tuples, anything else refused."""
    if isinstance(value, str):
        return clean_text(value)
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, list | tuple):
        return tuple(_plain(item, f"{path}[{index}]") for index, item in enumerate(value))
    if isinstance(value, Mapping):
        data: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TemplateError(f"field {path}: keys must be text")
            data[clean_text(key)] = _plain(item, f"{path}.{key}")
        return data
    raise TemplateError(
        f"field {path}: {type(value).__name__} is not template data; pass text, whole numbers,"
        " booleans or None, in lists and dicts"
    )


def _printable(value: object) -> object:
    """Jinja2's `finalize`: every value a template prints passes through it."""
    if isinstance(value, Undefined):
        # Printing it raises the strict undefined error that names the field.
        return value
    if isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool)):
        return value
    shown = "None" if value is None else type(value).__name__
    raise TemplateError(f"a template prints text and whole numbers only, not {shown}")


def _clip(value: str, max_length: int) -> str:
    return clean_text(value, max_length)
