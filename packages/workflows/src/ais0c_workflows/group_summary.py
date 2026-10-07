"""The summary of an offense group as the workflows see it (T-027).

The workflows package may not import the agents package (docs/impl/repo-structure.md), so these
models mirror `ais0c_agents.group` field for field: `group_case_state` returns the agents'
models as JSON, the workflow reads them as these, and the Triage run of a group case reads them
back as the agents' (`ais0c_activities.TriageRuntime.run`). A worker test keeps the two sides
together (services/worker/tests/test_group_mirrors.py).

Also here, pure: the values the group case compares between evaluations (T-22): the most
frequent source and destination, when one leads.
"""

from pydantic import AwareDatetime, BaseModel, ConfigDict


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GroupValueCount(_Model):
    value: str
    offenses: int


class GroupValues(_Model):
    distinct: int
    top: list[GroupValueCount]

    def leader(self) -> str | None:
        """The value more of the group's offenses carry than any other; None when none does,
        such as when every value is carried once, as the sources of a spray are."""
        if not self.top:
            return None
        if len(self.top) > 1 and self.top[1].offenses >= self.top[0].offenses:
            return None
        return self.top[0].value


class GroupRule(_Model):
    rule_id: int
    name: str | None


class GroupSummary(_Model):
    offense_count: int
    first_seen_at: AwareDatetime
    last_seen_at: AwareDatetime
    example_offense_id: int
    rules: list[GroupRule]
    source_ips: GroupValues
    destination_ips: GroupValues
    usernames: GroupValues
    log_sources: GroupValues
    categories: GroupValues
