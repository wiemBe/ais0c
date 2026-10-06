"""T-050 criterion 2: the event-name guidance in the create_ariel_search description and the
list_offenses field guidance.

The lab runs of 2026-10-06 (offenses 30 and 33) invented fields and values: `eventname` in AQL
(QRadar: Field "eventname" does not exist in catalog "events"), `qid = 4662` (a Windows event
ID; the lab QID is 5000849), an `offense_source` filter on list_offenses (QRadar: Filtering is
unsupported) and a `name` field (none exists; `rules(id,name)` is a 422 while `log_sources
(id,name)` is accepted). The descriptions now carry the correct forms, and every example query
they show passes the investigate profile's AQL Guard.
"""

from typing import Any

import pytest
from filter_examples import query_examples
from gateway_support import CONFIG_DIR, load_config

from ais0c_mcp_gateway.registry import load_registry
from ais0c_policy import AqlProfile, check_aql

INVESTIGATE = "qradar-investigate-read"


@pytest.fixture(scope="module")
def manifest() -> dict[str, Any]:
    manifest, _ = load_config()
    return manifest


@pytest.fixture(scope="module")
def investigate_aql() -> AqlProfile:
    registry = load_registry(CONFIG_DIR, ["qradar"])
    profile = registry.profiles[INVESTIGATE]
    assert profile.aql is not None
    return profile.aql


@pytest.fixture(scope="module")
def indexed_fields() -> list[str]:
    registry = load_registry(CONFIG_DIR, ["qradar"])
    return list(registry.profiles[INVESTIGATE].connector.indexed_fields)


def test_create_ariel_search_names_the_event_name_function(manifest: dict[str, Any]) -> None:
    description = manifest["tools"]["create_ariel_search"]["description"]
    argument = manifest["tools"]["create_ariel_search"]["input_schema"]["properties"][
        "query_expression"
    ]["description"]

    for text in (description, argument):
        assert "QIDNAME(qid)" in text
        assert "no eventname field" in text
        assert "not the Windows event ID" in text
        assert "4662" in text  # the guessed value of the lab runs


def test_the_example_queries_pass_the_investigate_guard(
    manifest: dict[str, Any], investigate_aql: AqlProfile, indexed_fields: list[str]
) -> None:
    examples = query_examples(manifest)

    assert set(examples) == {"create_ariel_search"}
    assert examples["create_ariel_search"], "create_ariel_search must show an example query"
    for query in examples["create_ariel_search"]:
        guard = check_aql(query, investigate_aql, indexed_fields)
        assert guard.allowed, (query, guard.reasons)


def test_the_example_queries_use_single_quotes(manifest: dict[str, Any]) -> None:
    # T-36 (3): a double quote must be escaped in the JSON arguments of a tool call.
    for query in query_examples(manifest)["create_ariel_search"]:
        assert '"' not in query


def test_list_offenses_names_its_fields_and_its_limits(manifest: dict[str, Any]) -> None:
    entry = manifest["tools"]["list_offenses"]
    fields = entry["input_schema"]["properties"]["fields"]["description"]
    text = entry["input_schema"]["properties"]["filter"]["description"]

    # The valid-fields list the `fields` description gives; `name` is not among them and the
    # nested names are the ones the lab accepts (rules(id), log_sources(id,name)).
    assert "Valid fields:" in fields
    assert "no name field" in fields
    assert "rules(id)" in fields
    assert "log_sources(id,name)" in fields
    # A single-quoted example (T-36 (3)) and the fields QRadar refuses to filter.
    assert "description, offense_source and source_network cannot be filtered" in text
    assert "`status = 'OPEN'`" in text
