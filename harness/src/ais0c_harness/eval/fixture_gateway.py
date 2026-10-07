"""The gateway of a `fixture` run: the scenario's tool results instead of QRadar (T-030
criterion 2, decision T-64).

The agent calls it as it calls the MCP Policy Gateway, through `GatewayClient`. The fixture
gateway checks each intent as the gateway does, with the gateway's own registry profile and
checks, so a model gets the same `denied` answer it would get in production:

1. the tool is a read tool of the profile; any other tool, a write tool above all, is never
   run (`outside_profile`);
2. the intent holds no NUL character, passes the policy package's checks (non-blank `reason`
   and `expected_evidence`, time window), names the tool's schema version, and its arguments
   match the tool's JSON Schema (Draft 2020-12) and the profile's text rules
   (`schema_invalid`).

An intent that passes gets the scenario's results for its tool, one per call in order
(`scripted`). When they run out the last one repeats under a derived evidence ID,
`<evidence_id>-r<n>` for its n-th repeat (`repeated`). A profile tool the scenario has no
results for is answered from the scenario's offense and enrichment when it is one of the three
derived tools (`derived`, decision T-67 (3): list_source_addresses,
list_local_destination_addresses, get_log_source); any other gets UNSCRIPTED_RESULT and the run
goes on (`unscripted`).

Every intent and its answer is kept in `exchanges`. FakeGatewayClient (packages/agents) stays
the unit tests' gateway; this one belongs to the harness.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

from ais0c_agents import GatewayClient
from ais0c_contracts import EvidenceRef, ToolCoverage, ToolIntent, ToolResult, ToolStatus
from ais0c_harness.replay.derived import DerivedAnswers
from ais0c_mcp_gateway.pipeline import Denial, argument_problem, holds_nul, text_rule_problem
from ais0c_mcp_gateway.registry import Profile, Tool
from ais0c_policy import check_intent

ExchangeOutcome = Literal[
    "scripted",
    "repeated",
    "unscripted",
    "derived",
    "recorded",
    "replayed",
    "replay_unsupported",
    "denied",
    "schema_invalid",
    "outside_profile",
]
"""How a call was answered. `derived`: from the offense and enrichment (derived.py);
`recorded`: from a recording's read call; `replayed`: an Ariel call the replay engine ran
(an error QRadar would give included); `replay_unsupported`: a query the engine does not run;
`denied`: a policy denial after the intent checks (AQL Guard, ownership)."""

UNSCRIPTED_RESULT: Final = ToolResult(
    status=ToolStatus.ERROR,
    deny_reason="upstream_error",
    data=[],
    truncated=False,
    coverage=ToolCoverage(complete=False, gaps=[]),
)
"""What a profile tool without results in the scenario answers: a fixed error, no data."""
REPEAT_SUFFIX: Final = "-r"


class GatewayExchange(BaseModel):
    """One intent the fixture gateway received and what it answered."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: ToolIntent
    result: ToolResult
    outcome: ExchangeOutcome
    evidence: EvidenceRef | None = None
    """The evidence a replay run recorded for the call, as the gateway would have."""

    @property
    def executed(self) -> bool:
        """Whether the call ran: it got a result, not a denial."""
        return self.outcome not in ("schema_invalid", "outside_profile", "denied")


@dataclass(frozen=True)
class Answer:
    """What a gateway gives for an admitted call."""

    result: ToolResult
    outcome: ExchangeOutcome
    evidence: EvidenceRef | None = None


class FixtureGateway(GatewayClient):
    """Answers one run's tool calls from a scenario's results; one instance per run.

    A tool the scenario has no results for is answered from `derived` when it can (derived.py),
    else with UNSCRIPTED_RESULT. ReplayGateway (replay/gateway.py) answers the admitted calls
    from a recording instead (`_respond`); the checks of the intent are the same.
    """

    def __init__(
        self,
        profile: Profile,
        tool_results: Mapping[str, Sequence[ToolResult]],
        *,
        now: datetime,
        derived: DerivedAnswers | None = None,
    ) -> None:
        """`profile` is the gateway registry's profile of the agent; `now` is the moment of the
        evaluation, against which the intent's time window is checked."""
        self._profile = profile
        self._results = {tool_id: tuple(results) for tool_id, results in tool_results.items()}
        self._now = now
        self._derived = derived
        self._answered: Counter[str] = Counter()
        self.exchanges: list[GatewayExchange] = []

    async def call(self, intent: ToolIntent) -> ToolResult:
        answer = self._answer(intent)
        self.exchanges.append(
            GatewayExchange(
                intent=intent,
                result=answer.result,
                outcome=answer.outcome,
                evidence=answer.evidence,
            )
        )
        return answer.result

    def _answer(self, intent: ToolIntent) -> Answer:
        profile = self._profile
        tool = profile.tools.get(intent.tool_id)
        if tool is None or tool.risk != "read" or intent.toolset_profile != profile.name:
            denial = Denial(
                "tool_not_in_profile", f"{intent.tool_id} is not a tool of {profile.name}"
            )
            return Answer(denied(denial), "outside_profile")
        denial = self._check(intent)
        if denial is not None:
            return Answer(denied(denial), "schema_invalid")
        return self._respond(intent, tool)

    def _respond(self, intent: ToolIntent, tool: Tool) -> Answer:
        """The answer to a call that passed the intent checks."""
        results = self._results.get(tool.id, ())
        if not results:
            number = self._answered[tool.id]
            self._answered[tool.id] += 1
            if self._derived is not None:
                derived = self._derived.answer(tool.id, intent.arguments, number + 1)
                if derived is not None:
                    return Answer(derived, "derived")
            return Answer(UNSCRIPTED_RESULT, "unscripted")
        number = self._answered[tool.id]
        self._answered[tool.id] += 1
        if number < len(results):
            return Answer(results[number], "scripted")
        last = results[-1]
        if last.evidence_id is None:
            return Answer(last, "repeated")
        repeat = number - len(results) + 1
        evidence_id = f"{last.evidence_id}{REPEAT_SUFFIX}{repeat}"
        return Answer(last.model_copy(update={"evidence_id": evidence_id}), "repeated")

    def _check(self, intent: ToolIntent) -> Denial | None:
        """The gateway's intent checks (pipeline step 3), in its order."""
        profile = self._profile
        tool = profile.tools[intent.tool_id]
        if holds_nul(intent.model_dump(mode="json")):
            return Denial("invalid_intent", "the intent holds a NUL character (U+0000)")
        reasons = check_intent(intent, profile.intent_rules, self._now)
        if reasons:
            return Denial("invalid_intent", ", ".join(reasons))
        if intent.tool_schema_version != tool.entry.schema_version:
            return Denial(
                "schema_version_mismatch",
                f"{tool.id} is at schema version {tool.entry.schema_version}",
            )
        problem = argument_problem(tool, intent.arguments)
        if problem is not None:
            return Denial("invalid_arguments", problem)
        problem = text_rule_problem(profile, intent.arguments)
        if problem is not None:
            return Denial("invalid_text", problem)
        return None


def denied(denial: Denial) -> ToolResult:
    """A denial as the gateway answers it."""
    return ToolResult(
        status=ToolStatus.DENIED,
        deny_reason=denial.text(),
        data=[],
        truncated=False,
        coverage=ToolCoverage(complete=False, gaps=[]),
    )
