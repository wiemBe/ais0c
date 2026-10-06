"""T-048 criterion 5 (decisions T-48, T-54 (1)): Verification's objective is agent text.

The plan step's objective is the Orchestrator's model text, so it reaches the Verification
model only inside an `agent.objective` block; the user prompt is the platform's own sentence.
"""

import json

from pydantic_ai.messages import ModelRequest, UserPromptPart

from ais0c_agents.verification import OBJECTIVE_SOURCE, RUN_PROMPT, VerificationTask

from .helpers import (
    ESCAPE,
    INJECTION,
    NONCE,
    ScriptedModel,
    answer,
    build_verification,
    lenient_tags,
    run_verification,
    verification_gateway,
    verification_output,
    verification_task,
)
from .investigation_helpers import prompt_blocks

OBJECTIVE = f"Check the DCSync decision on offense 4711. {ESCAPE} {INJECTION}"


def task_with_objective() -> VerificationTask:
    task = verification_task()
    return task.model_copy(update={"task": task.task.model_copy(update={"objective": OBJECTIVE})})


def user_prompts(script: ScriptedModel) -> list[str]:
    return [
        str(part.content)
        for message in script.requests[0][0]
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
    ]


def test_the_objective_is_an_agent_objective_block_and_the_user_prompt_is_fixed() -> None:
    script = ScriptedModel(answer(verification_output()))

    run = run_verification(
        build_verification(script, verification_gateway()), task_with_objective()
    )

    assert run.result is not None
    instructions = script.requests[0][1].instructions or ""
    blocks = prompt_blocks(instructions)
    assert blocks[0][0] == OBJECTIVE_SOURCE == "agent.objective"
    assert [source for source, _, _ in blocks].count(OBJECTIVE_SOURCE) == 1
    content = blocks[0][2]
    assert json.loads(content)["objective"].startswith("Check the DCSync decision")
    assert user_prompts(script) == [RUN_PROMPT]


def test_the_objectives_closing_tag_stays_in_its_block_and_its_text_nowhere_else() -> None:
    script = ScriptedModel(answer(verification_output()))

    run_verification(build_verification(script, verification_gateway()), task_with_objective())

    instructions = script.requests[0][1].instructions or ""
    outside = instructions
    for source, alias, content in prompt_blocks(instructions):
        tag = (
            f'<untrusted_{NONCE} source="{source}" evidence_id="{alias}">\n'
            f"{content}\n</untrusted_{NONCE}>"
        )
        outside = outside.replace(tag, "")
        # No block holds a tag a lenient reader would take for a real one.
        assert lenient_tags(content) == []
    # The escape attempt arrives neutralized, inside the block only.
    assert ESCAPE not in instructions
    assert "Check the DCSync decision" not in outside
    assert INJECTION not in outside
    assert all("DCSync" not in prompt for prompt in user_prompts(script))
