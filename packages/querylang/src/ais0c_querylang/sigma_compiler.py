"""Sigma to AQL compilation with IBM's pySigma QRadar backend and the bank's field mapping.

The bank's mapping (`config/sigma/qradar-pipeline.yaml`) is layered on top of the backend's
fields pipeline (`QRadarAQL_fields_pipeline`):

- A Sigma field in the bank's mapping is mapped to the bank's property name instead of the
  backend's. If the backend does not know the field, its unknown-field check and its
  free-text handling (`LIKE '%value%'`) skip it as well; otherwise the backend's value
  handling for that field still applies.
- Any other Sigma field goes through the backend unchanged.
- A Sigma field that neither mapping covers fails with `UnmappedFieldError`, which names
  every such field. The backend's payload pipeline, which turns unmapped fields into
  `UTF8(payload)` searches, is never used.
"""

import copy
import dataclasses
import re
import threading
import warnings
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Literal

import yaml
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError
from sigma.exceptions import SigmaError
from sigma.processing.conditions import IncludeFieldCondition
from sigma.processing.pipeline import ProcessingItem, ProcessingPipeline
from sigma.rule import SigmaDetection, SigmaRule
from sigma.types import SigmaFieldReference

from ais0c_querylang._backend import (
    QRadarAQL_fields_pipeline,
    QRadarAQLBackend,
    QRadarFieldMappingTransformation,
    aql_field_mapping,
)

# Identifiers of the backend's processing items that this module adjusts. If the backend
# renames or removes one, _processing_pipeline() fails instead of compiling differently.
_BACKEND_FIELD_CHECK = "QRadar_unsupported_fields"
_BACKEND_UNSTRUCTURED = "unstructured_fields"
_BACKEND_MAPPING = "aql_field_mapping"
_BANK_MAPPING = "ais0c_field_mapping"

# The backend quotes a property name only when it contains whitespace, so a name must be
# either a plain identifier or contain a space. A name such as `Object-Type` would reach the
# query unquoted and be read as `Object - Type`.
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SPACED_NAME = re.compile(r"\w[\w .\-/]*\w")

# pySigma processing pipelines keep state while they convert a rule, and warning capture is
# process-wide, so one rule is compiled at a time.
_COMPILE_LOCK = threading.Lock()


class PipelineConfigError(ValueError):
    """The pipeline file is not a field mapping this module accepts."""


class SigmaCompileError(ValueError):
    """A Sigma rule could not be compiled to AQL."""


class UnmappedFieldError(SigmaCompileError):
    """The rule uses Sigma fields that neither the bank's nor the backend's mapping covers."""

    def __init__(self, fields: Iterable[str]) -> None:
        self.fields: tuple[str, ...] = tuple(sorted(set(fields)))
        super().__init__("no QRadar mapping for Sigma field(s): " + ", ".join(self.fields))


@dataclass(frozen=True)
class AqlPipeline:
    """The bank's Sigma field name -> QRadar property name mapping."""

    name: str
    field_mapping: Mapping[str, tuple[str, ...]]


def _check_property_name(name: str) -> str:
    if _IDENTIFIER.fullmatch(name) or (" " in name and _SPACED_NAME.fullmatch(name)):
        return name
    raise ValueError(
        f"QRadar property name {name!r} must be a plain identifier or contain a space and "
        "only letters, digits, spaces and _ . - /"
    )


_PropertyName = Annotated[str, AfterValidator(_check_property_name)]


class _FieldNameMapping(BaseModel):
    """A pySigma `field_name_mapping` transformation without conditions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    type: Literal["field_name_mapping"]
    mapping: Annotated[
        dict[str, _PropertyName | Annotated[list[_PropertyName], Field(min_length=1)]],
        Field(min_length=1),
    ]


class _PipelineFile(BaseModel):
    """The subset of the pySigma processing pipeline format that the loader accepts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    priority: int
    transformations: Annotated[list[_FieldNameMapping], Field(min_length=1)]


def load_pipeline(path: Path) -> AqlPipeline:
    """Read the field mapping pipeline at `path`. No other file is read."""
    try:
        document: object = yaml.safe_load(path.read_text(encoding="utf-8"))
        parsed = _PipelineFile.model_validate(document)
    except (yaml.YAMLError, ValidationError) as error:
        raise PipelineConfigError(f"{path}: {error}") from error

    mapping: dict[str, tuple[str, ...]] = {}
    for transformation in parsed.transformations:
        for field, target in transformation.mapping.items():
            if field in mapping:
                raise PipelineConfigError(f"{path}: Sigma field {field!r} is mapped twice")
            mapping[field] = (target,) if isinstance(target, str) else tuple(target)
    return AqlPipeline(name=parsed.name, field_mapping=MappingProxyType(mapping))


def compile_sigma(rule_yaml: str, pipeline: AqlPipeline) -> str:
    """Compile one Sigma rule to an AQL query without a time window or `LIMIT`.

    Raises `UnmappedFieldError` when the rule uses a field without a mapping and
    `SigmaCompileError` for any other failure, including a warning from the backend: the
    backend warns when it drops part of a rule, which would widen the query.
    """
    try:
        rule = SigmaRule.from_yaml(rule_yaml)
    except (SigmaError, yaml.YAMLError, TypeError, AttributeError) as error:
        raise SigmaCompileError(f"invalid Sigma rule: {error}") from error

    shape = _rule_shape(rule)
    unmapped = {
        field
        for field in shape.fields
        if field not in pipeline.field_mapping and field not in aql_field_mapping
    }
    if unmapped:
        raise UnmappedFieldError(unmapped)
    if shape.has_keywords:
        raise SigmaCompileError(
            "keyword detections (values without a field) search the event payload and are "
            "not supported; match a mapped field instead"
        )
    if shape.has_field_references:
        raise SigmaCompileError("the QRadar backend does not support the fieldref modifier")

    with _COMPILE_LOCK, warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            queries: list[str] = QRadarAQLBackend(_processing_pipeline(pipeline)).convert_rule(rule)
        except SigmaError as error:
            raise SigmaCompileError(str(error)) from error
    if caught:
        raise SigmaCompileError(
            "the QRadar backend warned while compiling: "
            + "; ".join(str(warning.message) for warning in caught)
        )
    if len(queries) != 1:
        raise SigmaCompileError(f"the rule compiled to {len(queries)} queries; expected one")
    return queries[0]


@dataclass
class _RuleShape:
    fields: set[str]  # Sigma fields in detections, field references and `fields`
    has_keywords: bool = False
    has_field_references: bool = False


def _rule_shape(rule: SigmaRule) -> _RuleShape:
    shape = _RuleShape(fields=set(rule.fields))

    def visit(detection: SigmaDetection) -> None:
        for item in detection.detection_items:
            if isinstance(item, SigmaDetection):
                visit(item)
                continue
            if item.field is None:
                shape.has_keywords = True
            else:
                shape.fields.add(item.field)
            references = [v.field for v in item.value if isinstance(v, SigmaFieldReference)]
            if references:
                shape.fields.update(references)
                shape.has_field_references = True

    for detection in rule.detection.detections.values():
        visit(detection)
    return shape


def _processing_pipeline(pipeline: AqlPipeline) -> ProcessingPipeline:
    """The backend's fields pipeline with the bank's mapping applied right after its own."""
    bank_fields = list(pipeline.field_mapping)
    bank_only = [field for field in bank_fields if field not in aql_field_mapping]
    # The backend's items are module-level objects shared by every call; work on a copy.
    result: ProcessingPipeline = copy.deepcopy(QRadarAQL_fields_pipeline())
    skipped_fields = {
        # Would reject or reinterpret (as free text) a field it does not know.
        _BACKEND_FIELD_CHECK: bank_only,
        _BACKEND_UNSTRUCTURED: bank_only,
        # Would map the field to the backend's default property name first.
        _BACKEND_MAPPING: bank_fields,
    }

    identifiers = [item.identifier for item in result.items]
    missing = skipped_fields.keys() - set(identifiers)
    if missing:
        raise RuntimeError(f"pySigma-backend-QRadar-AQL no longer has the items {sorted(missing)}")
    for index, item in enumerate(result.items):
        skipped = skipped_fields.get(item.identifier or "")
        if not skipped:
            continue
        if item.field_name_conditions:
            raise RuntimeError(f"backend item {item.identifier!r} has field conditions now")
        result.items[index] = dataclasses.replace(
            item,
            field_name_conditions=[IncludeFieldCondition(fields=skipped)],
            field_name_condition_negation=True,
        )

    bank_mapping = ProcessingItem(
        identifier=_BANK_MAPPING,
        transformation=QRadarFieldMappingTransformation(
            mapping={
                field: targets[0] if len(targets) == 1 else list(targets)
                for field, targets in pipeline.field_mapping.items()
            },
            field_quote_pattern=QRadarAQLBackend.field_quote_pattern,
        ),
        field_name_conditions=[IncludeFieldCondition(fields=bank_fields)],
    )
    result.items.insert(identifiers.index(_BACKEND_MAPPING) + 1, bank_mapping)
    return result
