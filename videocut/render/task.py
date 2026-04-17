from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

TaskStatus = Literal["pending", "rendering", "completed", "failed"]
TASK_ID_HEX_LENGTH = 16


def generate_task_id(*, prefix: str = "") -> str:
    return f"{prefix}{uuid4().hex[:TASK_ID_HEX_LENGTH]}"


@dataclass(slots=True)
class RenderTask:
    id: str
    template_id: str
    status: TaskStatus
    progress: int
    attempt: int
    created_at: datetime
    variables: dict[str, Any] = field(default_factory=dict)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    output_path: str | None = None
    error: str | None = None


def create_task(template_id: str, variables: dict[str, Any]) -> RenderTask:
    return RenderTask(
        id=generate_task_id(),
        template_id=template_id,
        status="pending",
        progress=0,
        attempt=0,
        created_at=datetime.utcnow(),
        variables=dict(variables),
    )


def start_task(task: RenderTask) -> None:
    task.status = "rendering"
    task.started_at = datetime.utcnow()


def update_progress(task: RenderTask, progress: float) -> None:
    task.progress = round(max(0.0, min(100.0, progress * 100)))


def complete_task(task: RenderTask, output_path: str) -> None:
    task.status = "completed"
    task.completed_at = datetime.utcnow()
    task.output_path = output_path
    task.progress = 100


def fail_task(task: RenderTask, error: str) -> None:
    task.status = "failed"
    task.completed_at = datetime.utcnow()
    task.error = error
