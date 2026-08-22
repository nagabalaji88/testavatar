"""A single-model agent executor built on agent-llm.

Run:
    export AGENT_LLM_MODEL=anthropic/claude-sonnet-5
    export AGENT_LLM_API_KEY_ENV=ANTHROPIC_API_KEY
    export ANTHROPIC_API_KEY=sk-ant-...
    python examples/agent_loop.py "Summarise the risks in this brief: ..."

The loop is the part you own; the client is the part you do not want to write
again. Note what the executor gets for free: a transient provider failure is
retried without the task knowing, a missing credential is caught before any
work starts, and every step reports what it cost.
"""

from __future__ import annotations

import asyncio
import sys

from agent_llm import LLMClient, LLMError, from_env


class Agent:
    """Runs a fixed sequence of steps against one model, tracking spend."""

    def __init__(self, llm: LLMClient, budget_usd: float = 0.50) -> None:
        self.llm = llm
        self.budget_usd = budget_usd
        self.spent_usd = 0.0
        self.steps: list[tuple[str, float]] = []

    async def step(self, name: str, system: str, user: str) -> str:
        # Checked before the call, not after: the point of a budget is to stop
        # spending, and a check that runs afterwards has already spent.
        if self.spent_usd >= self.budget_usd:
            raise RuntimeError(
                f"budget exhausted before '{name}': "
                f"${self.spent_usd:.4f} of ${self.budget_usd:.2f}"
            )

        result = await self.llm.complete(system=system, user=user)
        self.spent_usd += result.cost_usd
        self.steps.append((name, result.cost_usd))

        if result.truncated:
            print(f"  ! {name} hit the output ceiling; the reply is incomplete")
        print(
            f"  {name:12} {result.total_tokens:5} tok  "
            f"${result.cost_usd:.5f}  {result.latency_ms:5}ms  "
            f"attempt {result.attempts}"
        )
        return result.content

    async def run(self, task: str) -> str:
        plan = await self.step(
            "plan",
            "You are a planner. Reply with 2-4 numbered steps, nothing else.",
            f"Task: {task}",
        )
        draft = await self.step(
            "execute",
            "You are an execution agent. Follow the plan exactly.",
            f"Task: {task}\n\nPlan:\n{plan}",
        )
        return await self.step(
            "review",
            "You are a reviewer. Return the corrected final answer only.",
            f"Task: {task}\n\nDraft:\n{draft}",
        )


async def main() -> int:
    task = " ".join(sys.argv[1:]) or "Explain what a dead letter queue is for."

    try:
        llm = from_env()
    except ValueError as exc:
        print(f"configuration error: {exc}")
        return 2

    # Fails fast and legibly. Without it the first call would burn the whole
    # retry ladder against a 401 and report a provider failure.
    if not llm.is_ready():
        print(
            f"model {llm.model.label} needs {llm.model.api_key_env}, which is unset"
        )
        return 2

    agent = Agent(llm)
    print(f"model: {llm.model.label}\ntask : {task}\n")
    try:
        answer = await agent.run(task)
    except LLMError as exc:
        print(f"\nprovider failed ({'retryable' if exc.retryable else 'fatal'}): {exc}")
        return 1
    except RuntimeError as exc:
        print(f"\n{exc}")
        return 1

    print(f"\ntotal: ${agent.spent_usd:.5f} across {len(agent.steps)} steps\n")
    print(answer)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
