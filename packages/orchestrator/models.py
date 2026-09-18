from __future__ import annotations
import uuid
from datetime import datetime
from sqlalchemy import (BigInteger, DateTime, ForeignKey, Index, Integer, String,
                        UniqueConstraint, func, text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """오케스트레이터 소유 스키마(public). SDK 테이블은 스키마 a2a에 별도."""


def _uuid() -> str:
    return str(uuid.uuid4())


class WorkflowRequirement(Base):
    __tablename__ = "workflow_requirements"
    requirement_id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    max_revisions: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    run_id: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class WorkflowTask(Base):
    __tablename__ = "workflow_tasks"
    task_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    requirement_id: Mapped[str] = mapped_column(ForeignKey("workflow_requirements.requirement_id"))
    agent: Mapped[str] = mapped_column(String, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    a2a_task_id: Mapped[str | None] = mapped_column(String)
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    verdict: Mapped[str | None] = mapped_column(String)
    failure_class: Mapped[str | None] = mapped_column(String)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    revision_of: Mapped[str | None] = mapped_column(ForeignKey("workflow_tasks.task_id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_workflow_tasks_idem"),
        Index("ix_workflow_tasks_req_rev", "requirement_id", "revision"),
        Index("ix_workflow_tasks_open", "state",
              postgresql_where=text("state IN ('submitted','working')")),
    )


class Artifact(Base):
    __tablename__ = "artifacts"
    artifact_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    requirement_id: Mapped[str] = mapped_column(ForeignKey("workflow_requirements.requirement_id"))
    producer_task: Mapped[str] = mapped_column(ForeignKey("workflow_tasks.task_id"))
    kind: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    sha256: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("requirement_id", "kind", "version", name="uq_artifact_version"),)


class OutboxEvent(Base):
    __tablename__ = "events"
    event_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    aggregate: Mapped[str] = mapped_column(String, nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String, nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentRegistryEntry(Base):
    __tablename__ = "agent_registry"
    agent: Mapped[str] = mapped_column(String, primary_key=True)
    base_url: Mapped[str] = mapped_column(String, nullable=False)
    card: Mapped[dict] = mapped_column(JSONB, nullable=False)
    healthy: Mapped[bool] = mapped_column(default=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
