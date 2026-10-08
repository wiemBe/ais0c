"""The class defaults file (T-068 criterion 2, decision T-95)."""

from pathlib import Path

import pytest

from ais0c_knowledge.catalog import CLASS_DEFAULTS_FILE, TelemetryConfigError, load_class_defaults
from ais0c_storage import TelemetryClass

REPO_ROOT = Path(__file__).resolve().parents[3]


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "classes.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_repo_file_loads() -> None:
    defaults = load_class_defaults(REPO_ROOT / CLASS_DEFAULTS_FILE)

    assert defaults
    assert {name for classes in defaults.values() for name in classes} <= set(TelemetryClass)
    assert defaults["Fortinet FortiGate Security Gateway"] == {
        TelemetryClass.FIREWALL,
        TelemetryClass.VPN,
    }
    assert defaults["Microsoft Windows Security Event Log"] == {TelemetryClass.WINDOWS}
    # Universal DSM carries several products: classified per log source, never by type.
    assert "Universal LEEF" not in defaults


def test_a_valid_file_loads(tmp_path: Path) -> None:
    path = write(tmp_path, "types:\n  Some DSM: [dns, proxy, dns]\n")

    assert load_class_defaults(path) == {"Some DSM": {TelemetryClass.DNS, TelemetryClass.PROXY}}


def test_unknown_class_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path, "types:\n  Some DSM: [windwos]\n")

    with pytest.raises(TelemetryConfigError, match="unknown class 'windwos'"):
        load_class_defaults(path)


def test_duplicate_type_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path, "types:\n  Some DSM: [dns]\n  Some DSM: [proxy]\n")

    with pytest.raises(TelemetryConfigError, match="duplicate key"):
        load_class_defaults(path)


def test_empty_class_list_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path, "types:\n  Some DSM: []\n")

    with pytest.raises(TelemetryConfigError, match="non-empty list"):
        load_class_defaults(path)


@pytest.mark.parametrize(
    "text",
    [
        "types:\n  Some DSM: [dns]\nextra: 1\n",
        "classes:\n  Some DSM: [dns]\n",
        "[dns]\n",
        "",
        "types: [dns]\n",
        "types:\n  Some DSM: dns\n",
        "types:\n  Some DSM: [[dns]]\n",
    ],
)
def test_other_top_level_key_or_shape_is_rejected(tmp_path: Path, text: str) -> None:
    with pytest.raises(TelemetryConfigError):
        load_class_defaults(write(tmp_path, text))


def test_invalid_yaml_and_a_missing_file_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(TelemetryConfigError, match="invalid YAML"):
        load_class_defaults(write(tmp_path, "types: [unclosed\n"))
    with pytest.raises(TelemetryConfigError, match="cannot be read"):
        load_class_defaults(tmp_path / "missing.yaml")
