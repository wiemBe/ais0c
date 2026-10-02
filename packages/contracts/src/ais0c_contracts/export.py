"""Write the JSON Schema of every exported model and enum to `packages/contracts/schemas/`.

Usage, from the repository root: `uv run python -m ais0c_contracts.export`

A snapshot test fails when the files there no longer match the models.
"""

import json
from enum import StrEnum
from pathlib import Path

from pydantic import TypeAdapter

import ais0c_contracts
from ais0c_contracts.common import ContractModel

# packages/contracts/schemas/; the package is installed in editable mode by the workspace.
SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"


def exported_types() -> list[type[ContractModel] | type[StrEnum]]:
    """Every model and enum exported from the package root, except `ContractModel`."""
    found: list[type[ContractModel] | type[StrEnum]] = []
    for name in ais0c_contracts.__all__:
        value: object = getattr(ais0c_contracts, name)
        if not isinstance(value, type) or value is ContractModel:
            continue
        if issubclass(value, ContractModel | StrEnum):
            found.append(value)
    return found


def render_schemas() -> dict[str, str]:
    """File name -> JSON Schema text, one file per exported type."""
    rendered: dict[str, str] = {}
    for exported in exported_types():
        schema = TypeAdapter(exported).json_schema()
        text = json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False)
        rendered[f"{exported.__name__}.json"] = text + "\n"
    return rendered


def write_schemas(directory: Path = SCHEMA_DIR) -> None:
    """Write every schema to `directory` and delete schema files no type produces."""
    directory.mkdir(parents=True, exist_ok=True)
    rendered = render_schemas()
    for path in directory.glob("*.json"):
        if path.name not in rendered:
            path.unlink()
    for name, text in rendered.items():
        (directory / name).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    write_schemas()
