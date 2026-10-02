"""Sigma to AQL compilation with the bank's field mapping pipeline."""

import warnings
from pathlib import Path

import pytest
from sigma.exceptions import SigmaTransformationError
from sigma.processing.pipeline import ProcessingPipeline
from sigma.rule import SigmaRule

from ais0c_querylang import (
    AqlPipeline,
    PipelineConfigError,
    SigmaCompileError,
    UnmappedFieldError,
    compile_sigma,
    load_pipeline,
)
from ais0c_querylang._backend import QRadarAQL_fields_pipeline, QRadarAQLBackend

TESTS = Path(__file__).resolve().parent
REPO_ROOT = TESTS.parents[2]
PIPELINE_PATH = REPO_ROOT / "config/sigma/qradar-pipeline.yaml"
H2_RULE = TESTS / "rules/h2_dcsync.yml"
H2_SNAPSHOT = TESTS / "snapshots/h2_dcsync.aql"


@pytest.fixture(scope="module")
def pipeline() -> AqlPipeline:
    return load_pipeline(PIPELINE_PATH)


def rule(detection: str, *, fields: str = "") -> str:
    """A Windows Security Sigma rule with the given (indented) detection block."""
    header = "title: test rule\nlogsource:\n  product: windows\n  service: security\n"
    return header + (f"fields: {fields}\n" if fields else "") + "detection:\n" + detection


def write_pipeline(directory: Path, mapping: str) -> Path:
    path = directory / "pipeline.yaml"
    path.write_text(
        "name: test\npriority: 10\ntransformations:\n"
        "  - id: test_mapping\n    type: field_name_mapping\n    mapping:\n" + mapping,
        encoding="utf-8",
    )
    return path


# --- acceptance criterion 1: H2 snapshot ----------------------------------------------------


def test_h2_dcsync_rule_compiles_to_the_snapshot(pipeline: AqlPipeline) -> None:
    query = compile_sigma(H2_RULE.read_text(encoding="utf-8"), pipeline)
    expected = H2_SNAPSHOT.read_text(encoding="utf-8").rstrip("\n")
    assert query == expected, (
        "compiled AQL changed; review it and update tests/snapshots/h2_dcsync.aql:\n" + query
    )


def test_h2_query_uses_the_bank_property_names_and_no_payload(pipeline: AqlPipeline) -> None:
    query = compile_sigma(H2_RULE.read_text(encoding="utf-8"), pipeline)
    assert query.startswith("SELECT * FROM events WHERE devicetype=12 AND ")
    assert '"Event ID"=4662' in query
    assert "LOWER(\"Object Properties\") LIKE '%1131f6ad-9c07-11d1-f79f-00c04fc2dcd2%'" in query
    assert "NOT(LOWER(username) LIKE '%$')" in query
    assert "payload" not in query.lower()


# --- acceptance criterion 2: unmapped fields ------------------------------------------------


def test_unmapped_fields_are_all_listed(pipeline: AqlPipeline) -> None:
    detection = (
        "  selection:\n"
        "    EventID: 4662\n"
        "    NoSuchField: value\n"
        "    AnotherMissing|contains: other\n"
        "  condition: selection\n"
    )
    with pytest.raises(UnmappedFieldError) as error:
        compile_sigma(rule(detection), pipeline)
    assert error.value.fields == ("AnotherMissing", "NoSuchField")
    assert "AnotherMissing" in str(error.value)
    assert "NoSuchField" in str(error.value)


def test_unmapped_fields_in_nested_detections_and_fields_list_are_listed(
    pipeline: AqlPipeline,
) -> None:
    detection = (
        "  selection:\n    - EventID: 4662\n    - MissingInList: value\n  condition: selection\n"
    )
    with pytest.raises(UnmappedFieldError) as error:
        compile_sigma(rule(detection, fields="[MissingOutput, SubjectUserName]"), pipeline)
    assert error.value.fields == ("MissingInList", "MissingOutput")


def test_unmapped_field_error_is_a_compile_error(pipeline: AqlPipeline) -> None:
    with pytest.raises(SigmaCompileError):
        compile_sigma(rule("  sel:\n    NoSuchField: x\n  condition: sel\n"), pipeline)


def test_the_backend_alone_reports_only_the_first_unmapped_field() -> None:
    # Why compile_sigma checks fields itself: the backend's fields pipeline raises on the
    # first field it does not know and does not list the others.
    sigma_rule = SigmaRule.from_yaml(H2_RULE.read_text(encoding="utf-8"))
    backend = QRadarAQLBackend(QRadarAQL_fields_pipeline())
    with pytest.raises(SigmaTransformationError, match="field 'Properties' is not supported"):
        backend.convert_rule(sigma_rule)


def test_keyword_detections_are_rejected_instead_of_searching_the_payload(
    pipeline: AqlPipeline,
) -> None:
    detection = "  keywords:\n    - mimikatz\n  condition: keywords\n"
    with pytest.raises(SigmaCompileError, match="keyword"):
        compile_sigma(rule(detection), pipeline)


def test_field_references_are_rejected(pipeline: AqlPipeline) -> None:
    detection = "  sel:\n    TargetUserName|fieldref: SubjectUserName\n  condition: sel\n"
    with pytest.raises(SigmaCompileError, match="fieldref"):
        compile_sigma(rule(detection), pipeline)


# --- how the bank's mapping and the backend's mapping work together ----------------------------


def test_bank_mapping_overrides_the_backend_default(tmp_path: Path) -> None:
    pipeline = load_pipeline(write_pipeline(tmp_path, "      EventID: BankEventCode\n"))
    query = compile_sigma(rule("  sel:\n    EventID: 4662\n  condition: sel\n"), pipeline)
    assert "BankEventCode=4662" in query
    assert "Event ID" not in query


def test_bank_only_field_keeps_exact_match_semantics(pipeline: AqlPipeline) -> None:
    # The backend treats fields it does not know as free text (LIKE '%value%'). A field the
    # bank maps is matched exactly.
    query = compile_sigma(rule("  sel:\n    SubjectUserName: admin\n  condition: sel\n"), pipeline)
    assert query.endswith("WHERE devicetype=12 AND username='admin'")


def test_backend_mapping_applies_to_fields_the_bank_does_not_map(pipeline: AqlPipeline) -> None:
    detection = "  sel:\n    Image|endswith: '\\cmd.exe'\n  condition: sel\n"
    query = compile_sigma(rule(detection), pipeline)
    assert '"Process Path"' in query
    assert '"Process Name"' in query


def test_fields_list_is_mapped_in_the_select_clause(pipeline: AqlPipeline) -> None:
    detection = "  sel:\n    EventID: 4624\n  condition: sel\n"
    query = compile_sigma(rule(detection, fields="[TargetUserName, EventID]"), pipeline)
    assert query.startswith('SELECT *, "Target Username", "Event ID" FROM events WHERE ')


def test_mapping_to_several_properties_matches_any_of_them(tmp_path: Path) -> None:
    pipeline = load_pipeline(write_pipeline(tmp_path, '      Account: ["Account Name", acct]\n'))
    query = compile_sigma(rule("  sel:\n    Account: bob\n  condition: sel\n"), pipeline)
    assert "\"Account Name\"='bob' OR acct='bob'" in query


def test_backend_warning_fails_compilation(pipeline: AqlPipeline) -> None:
    # The backend warns and drops a log source type it does not know, widening the query.
    detection = (
        "  sel:\n    eventSource: no-such-log-source-type\n    EventID: 4624\n  condition: sel\n"
    )
    with pytest.raises(SigmaCompileError, match="warned"):
        compile_sigma(rule(detection), pipeline)


@pytest.mark.parametrize(
    "rule_yaml",
    [
        "",
        "title: [unclosed",
        "- a\n- b\n",
        "just a string",
        "title: no log source or detection\n",
    ],
)
def test_invalid_rule_yaml_is_a_compile_error(pipeline: AqlPipeline, rule_yaml: str) -> None:
    with pytest.raises(SigmaCompileError):
        compile_sigma(rule_yaml, pipeline)


def test_compiling_does_not_change_the_backend_pipeline(pipeline: AqlPipeline) -> None:
    compile_sigma(H2_RULE.read_text(encoding="utf-8"), pipeline)
    items = QRadarAQL_fields_pipeline().items
    assert [item.identifier for item in items] == [
        "QRadar_unsupported_fields",
        "host_fields_value",
        "unstructured_fields",
        "aql_field_mapping",
        "set_log_source_type",
        "drop_field_log_source_type",
        "qradar_table",
    ]
    assert not any(item.field_name_condition_negation for item in items)


def test_compile_works_when_warnings_are_errors(pipeline: AqlPipeline) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert compile_sigma(H2_RULE.read_text(encoding="utf-8"), pipeline)


# --- pipeline file --------------------------------------------------------------------------


def test_pipeline_file_maps_the_sample_fields(pipeline: AqlPipeline) -> None:
    assert pipeline.field_mapping["EventID"] == ("Event ID",)
    assert pipeline.field_mapping["SubjectUserName"] == ("username",)
    assert pipeline.field_mapping["Properties"] == ("Object Properties",)


def test_pipeline_file_is_a_valid_pysigma_pipeline() -> None:
    parsed = ProcessingPipeline.from_yaml(PIPELINE_PATH.read_text(encoding="utf-8"))
    assert [item.identifier for item in parsed.items] == ["ais0c_field_mapping"]


@pytest.mark.parametrize(
    "target",
    [
        '"Event\\"ID"',  # quote
        '"Event\'ID"',  # quote
        "UTF8(payload)",  # expression, payload search
        "Object-Type",  # no space, so the backend would not quote it
        "a=b",
        '" Event ID"',  # surrounding space
        '"Event ID;"',
        "[]",
    ],
)
def test_pipeline_rejects_unsafe_property_names(tmp_path: Path, target: str) -> None:
    with pytest.raises(PipelineConfigError):
        load_pipeline(write_pipeline(tmp_path, f"      EventID: {target}\n"))


@pytest.mark.parametrize(
    "text",
    [
        "not: [valid",
        "- just\n- a list\n",
        # unknown top-level key
        "name: t\npriority: 1\nvars: {}\ntransformations:\n"
        "  - {id: m, type: field_name_mapping, mapping: {EventID: x}}\n",
        # another transformation type
        "name: t\npriority: 1\ntransformations:\n"
        "  - {id: m, type: add_condition, conditions: {EventID: 1}}\n",
        # a condition on the mapping
        "name: t\npriority: 1\ntransformations:\n"
        "  - id: m\n    type: field_name_mapping\n    mapping: {EventID: x}\n"
        "    rule_conditions: [{type: logsource, product: windows}]\n",
        # empty mapping
        "name: t\npriority: 1\ntransformations:\n"
        "  - {id: m, type: field_name_mapping, mapping: {}}\n",
        # no transformations
        "name: t\npriority: 1\ntransformations: []\n",
        # the same Sigma field mapped twice
        "name: t\npriority: 1\ntransformations:\n"
        "  - {id: a, type: field_name_mapping, mapping: {EventID: x}}\n"
        "  - {id: b, type: field_name_mapping, mapping: {EventID: y}}\n",
    ],
)
def test_pipeline_rejects_anything_but_plain_field_mappings(tmp_path: Path, text: str) -> None:
    path = tmp_path / "pipeline.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(PipelineConfigError):
        load_pipeline(path)
