from enum import Enum
from typing import Optional

from sqlalchemy import Integer, String, Float, Text, ForeignKey, Index, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass

class LeadStatus(str, Enum):
    RAW = "RAW"
    RESEARCHED = "RESEARCHED"
    DRAFTED = "DRAFTED"
    SENT = "SENT"
    FU1_SENT = "FU1_SENT"
    FU2_SENT = "FU2_SENT"
    FU3_SENT = "FU3_SENT"
    GHOSTED = "GHOSTED"
    LOW_FIT = "LOW_FIT"
    PROFILE_FAILED = "PROFILE_FAILED"
    PAUSED = "PAUSED"
    REPLIED = "REPLIED"

class PipelineStage(str, Enum):
    REPLIED = "REPLIED"
    MEETING_BOOKED = "MEETING_BOOKED"
    PROPOSAL_SENT = "PROPOSAL_SENT"
    NEGOTIATING = "NEGOTIATING"
    WON = "WON"
    LOST = "LOST"
    PAUSED = "PAUSED"

class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_name: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    website: Mapped[Optional[str]] = mapped_column(Text)
    email: Mapped[Optional[str]] = mapped_column(Text)
    phone: Mapped[Optional[str]] = mapped_column(Text)
    industry: Mapped[Optional[str]] = mapped_column(Text)
    location: Mapped[Optional[str]] = mapped_column(Text)
    size_range: Mapped[Optional[str]] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default=LeadStatus.RAW.value)
    profile_json: Mapped[Optional[str]] = mapped_column(Text)
    decision_maker_name: Mapped[Optional[str]] = mapped_column(Text)
    decision_maker_email: Mapped[Optional[str]] = mapped_column(Text)
    decision_maker_title: Mapped[Optional[str]] = mapped_column(Text)
    fit_score: Mapped[Optional[float]] = mapped_column(Float)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=func.datetime('now'))
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, default=func.datetime('now'), onupdate=func.datetime('now'))

    __table_args__ = (
        Index("idx_leads_status", "status"),
        Index("idx_leads_domain", "domain"),
    )

class OutreachSequence(Base):
    __tablename__ = "outreach_sequences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[int] = mapped_column(Integer, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
    sequence_type: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft")
    scheduled_at: Mapped[Optional[str]] = mapped_column(Text)
    sent_at: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=func.datetime('now'))

    __table_args__ = (
        UniqueConstraint("lead_id", "sequence_type"),
        Index("idx_seq_lead", "lead_id"),
        Index("idx_seq_status_scheduled", "status", "scheduled_at"),
    )

class EmailEvent(Base):
    __tablename__ = "email_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[int] = mapped_column(Integer, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
    sequence_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("outreach_sequences.id"))
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    sentiment: Mapped[Optional[str]] = mapped_column(Text)
    raw_snippet: Mapped[Optional[str]] = mapped_column(Text)
    timestamp: Mapped[str] = mapped_column(Text, nullable=False, default=func.datetime('now'))

    __table_args__ = (
        Index("idx_events_lead", "lead_id"),
    )

class Pipeline(Base):
    __tablename__ = "pipeline"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[int] = mapped_column(Integer, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, unique=True)
    stage: Mapped[str] = mapped_column(Text, nullable=False, default=PipelineStage.REPLIED.value)
    contract_value: Mapped[Optional[float]] = mapped_column(Float)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    next_action_date: Mapped[Optional[str]] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, default=func.datetime('now'), onupdate=func.datetime('now'))

class ActivityLog(Base):
    __tablename__ = "activity_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("leads.id", ondelete="SET NULL"))
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[Optional[str]] = mapped_column(Text)
    timestamp: Mapped[str] = mapped_column(Text, nullable=False, default=func.datetime('now'))

    __table_args__ = (
        Index("idx_activity_lead", "lead_id"),
        Index("idx_activity_time", "timestamp"),
    )

class LLMCall(Base):
    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    task_type: Mapped[str] = mapped_column(Text, nullable=False)
    success: Mapped[int] = mapped_column(Integer, nullable=False)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer)
    tokens_in: Mapped[Optional[int]] = mapped_column(Integer)
    tokens_out: Mapped[Optional[int]] = mapped_column(Integer)
    error: Mapped[Optional[str]] = mapped_column(Text)
    timestamp: Mapped[str] = mapped_column(Text, nullable=False, default=func.datetime('now'))

class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[str] = mapped_column(Text, nullable=False, default=func.datetime('now'))
    completed_at: Mapped[Optional[str]] = mapped_column(Text)
    leads_scraped: Mapped[int] = mapped_column(Integer, default=0)
    leads_researched: Mapped[int] = mapped_column(Integer, default=0)
    emails_sent: Mapped[int] = mapped_column(Integer, default=0)
    replies_received: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
