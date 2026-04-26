from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


AgentStatus = str


@dataclass(frozen=True)
class AgentCapability:
    key: str
    label: str
    lane: str
    description: str
    safety_mode: str
    actions: list[str] = field(default_factory=list)
    pauses_for: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "lane": self.lane,
            "description": self.description,
            "safety_mode": self.safety_mode,
            "actions": self.actions,
            "pauses_for": self.pauses_for,
            "auto_submit": False,
        }


@dataclass
class AgentContext:
    user_id: int | None = None
    job_id: int | None = None
    task_id: int | None = None
    resume_version_id: int | None = None
    instructions: str | None = None
    action: str | None = None
    force: bool = False
    start_browser: bool = False
    wait_seconds: int = 90
    max_items: int = 25
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentResult:
    agent_key: str
    agent_label: str
    status: AgentStatus
    message: str
    trace: list[dict[str, Any]] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)
    next_actions: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    run_id: int | None = None
    auto_submit: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "agent_key": self.agent_key,
            "agent_label": self.agent_label,
            "status": self.status,
            "message": self.message,
            "trace": self.trace,
            "artifacts": self.artifacts,
            "next_actions": self.next_actions,
            "errors": self.errors,
            "auto_submit": False,
        }


class BaseAgent:
    capability: AgentCapability

    def __init__(self, capability: AgentCapability):
        self.capability = capability

    @property
    def key(self) -> str:
        return self.capability.key

    @property
    def label(self) -> str:
        return self.capability.label

    def step(self, name: str, status: AgentStatus, message: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "name": name,
            "status": status,
            "message": message,
            "data": data or {},
            "at": datetime.now(UTC).isoformat(),
        }

    def result(
        self,
        status: AgentStatus,
        message: str,
        *,
        trace: list[dict[str, Any]] | None = None,
        artifacts: dict[str, Any] | None = None,
        next_actions: list[str] | None = None,
        errors: list[str] | None = None,
    ) -> AgentResult:
        return AgentResult(
            agent_key=self.key,
            agent_label=self.label,
            status=status,
            message=message,
            trace=trace or [],
            artifacts=artifacts or {},
            next_actions=next_actions or [],
            errors=errors or [],
            auto_submit=False,
        )

    def run(self, context: AgentContext) -> AgentResult:
        raise NotImplementedError
