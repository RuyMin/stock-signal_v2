"""BaseTask — Vibe Framework Task의 부모."""
from typing import Optional, Sequence

from crewai import Agent, Task


class BaseTask:
    description: str = NotImplemented
    expected_output: str = NotImplemented

    def build(
        self,
        agent: Agent,
        context: Optional[Sequence[Task]] = None,
    ) -> Task:
        if self.description is NotImplemented or self.expected_output is NotImplemented:
            raise NotImplementedError("Task must define description/expected_output")
        return Task(
            description=self.description,
            expected_output=self.expected_output,
            agent=agent,
            context=list(context) if context else [],
        )
