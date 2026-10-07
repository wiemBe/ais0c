"""T-062 criterion 5 (T-80 (2)): the lab tests report the run's output corrections.

No lab and no stack is needed: the report line of each lab test counts the run's
`RetryPromptPart`s (the model's corrections) with the helper this test imports from the two
lab test modules, so the count the lab reports is the one checked here.
"""

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
)
from test_lab_investigation import _output_retries as investigation_retries
from test_lab_verification import _output_retries as verification_retries

from ais0c_agents.builder import OUTPUT_TOOL


def _request(*parts: object) -> ModelRequest:
    return ModelRequest(parts=parts)  # type: ignore[arg-type]


def _response_with_tool_call(tool_name: str) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(tool_name=tool_name)])


def _messages(corrections: int, *, tool_name: str | None) -> list[ModelMessage]:
    """A minimal run: one tool call, then `corrections` correction requests, then the answer."""
    messages: list[ModelMessage] = [_response_with_tool_call("get_ariel_search_results")]
    for _ in range(corrections):
        messages.append(
            _request(
                RetryPromptPart(
                    content="The result is not schema-valid.",
                    tool_name=tool_name,
                )
            )
        )
    messages.append(_response_with_tool_call(OUTPUT_TOOL))
    return messages


def test_the_count_is_the_run_s_output_corrections() -> None:
    assert investigation_retries(_messages(2, tool_name=OUTPUT_TOOL)) == 2
    assert verification_retries(_messages(1, tool_name=None)) == 1
    # A correction of a tool call is not an output correction.
    assert investigation_retries(_messages(3, tool_name="get_ariel_search_results")) == 0


def test_a_run_without_corrections_counts_none() -> None:
    messages: list[ModelMessage] = [
        _response_with_tool_call("create_ariel_search"),
        _request(),  # the tool result, no retry
        _response_with_tool_call(OUTPUT_TOOL),
    ]

    assert verification_retries(messages) == 0
    assert investigation_retries(messages) == 0
