"""The "Model ve skill kaydı (v0.2)" models of docs/impl/contracts.md."""

from typing import Annotated

from pydantic import StringConstraints

from ais0c_contracts.common import ContractModel

# `sha256:` and 64 lowercase hex digits.
ContentHash = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]


class ModelRelease(ContractModel):
    """The real identity of the model behind an agent run (T-24); kept in
    `agent_runs.model_release`. A change in any field is a new model release."""

    # A model alias, e.g. `soc-fast`.
    alias: str
    # The alias's target in the model registry.
    target: str
    # Name of the model artifact.
    artifact: str
    # None when unknown; required in production.
    artifact_hash: str | None = None
    quantization: str | None = None
    # Name and hash.
    tokenizer: str | None = None
    # The vLLM version.
    engine_version: str | None = None
    tool_parser: str | None = None
    max_context: int | None = None
    # temperature and the like.
    inference_params: dict[str, str | int | float | bool]


class SkillRef(ContractModel):
    """The skill version an agent run used (T-21); kept in `agent_runs.skill`."""

    skill_id: str
    version: str
    content_hash: ContentHash
