"""The default telemetry classes of QRadar log source types (decision T-95).

`config/telemetry/log-source-classes.yaml` maps a QRadar log source type name to the classes
its log sources carry: `types: {<type name>: [<class>, ...]}`. KnowledgeSync writes the
classes of the log source's type to each catalog row (`default_telemetry_classes`), so the API
and the activities never read the file. A type the file does not name has no default; an
installation's own types are classified per log source in the Analysis Catalog.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Final

import yaml

from ais0c_knowledge._yaml import load_yaml_text
from ais0c_storage import TelemetryClass

ClassDefaults = Mapping[str, frozenset[TelemetryClass]]
CLASS_DEFAULTS_FILE: Final = Path("config/telemetry/log-source-classes.yaml")


class TelemetryConfigError(ValueError):
    """The class defaults file cannot be used."""


def load_class_defaults(path: Path) -> ClassDefaults:
    """The type name -> classes mapping. Raises TelemetryConfigError on invalid YAML, a
    duplicate type, an unknown class, an empty class list or a key other than `types`."""
    try:
        document = load_yaml_text(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        raise TelemetryConfigError(f"{path}: cannot be read: {type(error).__name__}") from None
    except yaml.YAMLError as error:
        raise TelemetryConfigError(f"{path}: invalid YAML: {error}") from None
    if not isinstance(document, dict) or set(document) != {"types"}:
        raise TelemetryConfigError(f"{path}: the only top-level key is `types`")
    types = document["types"]
    if not isinstance(types, dict):
        raise TelemetryConfigError(f"{path}: `types` must map type names to class lists")
    defaults: dict[str, frozenset[TelemetryClass]] = {}
    for name, classes in types.items():
        if not isinstance(name, str) or not name.strip():
            raise TelemetryConfigError(f"{path}: a type name must be a non-empty string")
        if not isinstance(classes, list) or not classes:
            raise TelemetryConfigError(f"{path}: {name!r} needs a non-empty list of classes")
        defaults[name] = _classes(path, name, classes)
    return defaults


def _classes(path: Path, name: str, values: list[object]) -> frozenset[TelemetryClass]:
    found: set[TelemetryClass] = set()
    for value in values:
        try:
            found.add(TelemetryClass(value))
        except ValueError:
            raise TelemetryConfigError(
                f"{path}: {name!r} has the unknown class {value!r}"
            ) from None
    return frozenset(found)
