"""JSON Schema export and the snapshot in packages/contracts/schemas/."""

import json
from pathlib import Path

from ais0c_contracts import ContractModel, Level
from ais0c_contracts.export import SCHEMA_DIR, exported_types, render_schemas, write_schemas

from .payloads import VALID

REGENERATE = "run `uv run python -m ais0c_contracts.export` and commit packages/contracts/schemas/"


def test_schema_snapshot_matches_models() -> None:
    on_disk = {path.name: path.read_text(encoding="utf-8") for path in SCHEMA_DIR.glob("*.json")}
    rendered = render_schemas()
    assert sorted(on_disk) == sorted(rendered), f"schema files are stale; {REGENERATE}"
    changed = [name for name, text in rendered.items() if on_disk[name] != text]
    assert changed == [], f"schemas changed; {REGENERATE}"


def test_every_model_and_enum_is_exported() -> None:
    exported = set(exported_types())
    assert set(VALID) <= exported
    assert Level in exported
    assert ContractModel not in exported


def test_model_schemas_forbid_unknown_fields() -> None:
    rendered = render_schemas()
    for model in VALID:
        schema = json.loads(rendered[f"{model.__name__}.json"])
        assert schema["additionalProperties"] is False, model.__name__


def test_write_schemas_writes_files_and_removes_stale_ones(tmp_path: Path) -> None:
    stale = tmp_path / "RemovedModel.json"
    stale.write_text("{}\n", encoding="utf-8")
    unrelated = tmp_path / "README.md"
    unrelated.write_text("kept\n", encoding="utf-8")

    write_schemas(tmp_path)

    written = {path.name: path.read_text(encoding="utf-8") for path in tmp_path.glob("*.json")}
    assert written == render_schemas()
    assert not stale.exists()
    assert unrelated.exists()
