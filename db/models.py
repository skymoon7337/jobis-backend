from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.session import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_key: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    profile: Mapped[str] = mapped_column(Text, default="")
    resume: Mapped[str] = mapped_column(Text, default="")
    profile_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resume_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    github_url: Mapped[str] = mapped_column(Text, default="")
    github_summary: Mapped[str] = mapped_column(Text, default="")
    job_posting: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    sessions: Mapped[list["InterviewSession"]] = relationship(back_populates="user")
    job_postings: Mapped[list["JobPostingRecord"]] = relationship(back_populates="user")
    github_projects: Mapped[list["GithubProjectRecord"]] = relationship(back_populates="user")
    analysis_jobs: Mapped[list["AnalysisJobRecord"]] = relationship(back_populates="user")
    agent_chat_messages: Mapped[list["AgentChatMessageRecord"]] = relationship(back_populates="user")
    agent_actions: Mapped[list["AgentActionRecord"]] = relationship(back_populates="user")
    agent_pending_commands: Mapped[list["AgentPendingCommandRecord"]] = relationship(back_populates="user")
    memory_items: Mapped[list["MemoryItemRecord"]] = relationship(back_populates="user")
    weakness_items: Mapped[list["WeaknessItemRecord"]] = relationship(back_populates="user")


class JobPostingRecord(Base):
    __tablename__ = "job_postings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    alias: Mapped[str] = mapped_column(String(200), default="")
    source_url: Mapped[str] = mapped_column(Text, default="")
    raw_text: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    is_selected: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    user: Mapped[User] = relationship(back_populates="job_postings")


class GithubProjectRecord(Base):
    __tablename__ = "github_projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    repo_key: Mapped[str] = mapped_column(String(300), index=True)
    url: Mapped[str] = mapped_column(Text, default="")
    alias: Mapped[str] = mapped_column(String(200), default="")
    title: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    user: Mapped[User] = relationship(back_populates="github_projects")
    snapshots: Mapped[list["GithubSnapshotRecord"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
    )


class GithubSnapshotRecord(Base):
    __tablename__ = "github_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("github_projects.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    summary: Mapped[str] = mapped_column(Text, default="")
    change_summary: Mapped[str] = mapped_column(Text, default="")
    default_branch: Mapped[str] = mapped_column(String(200), default="")
    commit_sha: Mapped[str] = mapped_column(String(80), default="")
    commit_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    analyzed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    is_latest: Mapped[bool] = mapped_column(default=True)

    project: Mapped[GithubProjectRecord] = relationship(back_populates="snapshots")


class AnalysisJobRecord(Base):
    __tablename__ = "analysis_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(50), index=True)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(80), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    progress_current: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    input_json: Mapped[str] = mapped_column(Text, default="{}")
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    error_type: Mapped[str] = mapped_column(String(80), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="analysis_jobs")


class AgentChatMessageRecord(Base):
    __tablename__ = "agent_chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(30), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    action: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    user: Mapped[User] = relationship(back_populates="agent_chat_messages")


class AgentActionRecord(Base):
    __tablename__ = "agent_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(80), default="", index=True)
    status: Mapped[str] = mapped_column(String(30), default="", index=True)
    result_summary: Mapped[str] = mapped_column(Text, default="")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    user: Mapped[User] = relationship(back_populates="agent_actions")


class AgentPendingCommandRecord(Base):
    __tablename__ = "agent_pending_commands"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    wait_job_id: Mapped[int] = mapped_column(Integer, index=True)
    command: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    result_summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="agent_pending_commands")


class WeaknessItemRecord(Base):
    __tablename__ = "weakness_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    topic: Mapped[str] = mapped_column(String(200), default="", index=True)
    normalized_topic: Mapped[str] = mapped_column(String(220), default="", index=True)
    category: Mapped[str] = mapped_column(String(80), default="", index=True)
    weakness_type: Mapped[str] = mapped_column(String(80), default="")
    severity: Mapped[int] = mapped_column(Integer, default=3)
    confidence: Mapped[int] = mapped_column(Integer, default=1)
    evidence: Mapped[str] = mapped_column(Text, default="")
    suggested_training: Mapped[str] = mapped_column(Text, default="")
    source_session_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    source_analysis_job_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    user: Mapped[User] = relationship(back_populates="weakness_items")


class MemoryItemRecord(Base):
    __tablename__ = "memory_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    source_type: Mapped[str] = mapped_column(String(80), default="", index=True)
    source_id: Mapped[int] = mapped_column(Integer, default=0, index=True)
    title: Mapped[str] = mapped_column(String(240), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    tags_json: Mapped[str] = mapped_column(Text, default="[]")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    user: Mapped[User] = relationship(back_populates="memory_items")


class InterviewSession(Base):
    __tablename__ = "interview_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="active")
    current_display_id: Mapped[str] = mapped_column(String(30), default="")
    awaiting_choice: Mapped[bool] = mapped_column(default=False)
    context_profile: Mapped[str] = mapped_column(Text, default="")
    context_resume: Mapped[str] = mapped_column(Text, default="")
    context_job_title: Mapped[str] = mapped_column(String(200), default="")
    context_job_summary: Mapped[str] = mapped_column(Text, default="")
    context_github_repositories: Mapped[str] = mapped_column(Text, default="[]")
    summary: Mapped[str] = mapped_column(Text, default="")
    weakness_summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    user: Mapped[User] = relationship(back_populates="sessions")
    questions: Mapped[list["InterviewQuestionRecord"]] = relationship(back_populates="session")
    turns: Mapped[list["InterviewTurnRecord"]] = relationship(back_populates="session")


class InterviewQuestionRecord(Base):
    __tablename__ = "interview_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("interview_sessions.id"), index=True)
    display_id: Mapped[str] = mapped_column(String(30), default="")
    question_type: Mapped[str] = mapped_column(String(50), default="")
    question: Mapped[str] = mapped_column(Text)
    is_bonus: Mapped[bool] = mapped_column(default=False)
    bonus_type: Mapped[str] = mapped_column(String(30), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    session: Mapped[InterviewSession] = relationship(back_populates="questions")


class InterviewTurnRecord(Base):
    __tablename__ = "interview_turns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("interview_sessions.id"), index=True)
    display_id: Mapped[str] = mapped_column(String(30), default="")
    question_type: Mapped[str] = mapped_column(String(50), default="")
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    feedback: Mapped[str] = mapped_column(Text, default="")
    is_bonus: Mapped[bool] = mapped_column(default=False)
    bonus_type: Mapped[str] = mapped_column(String(30), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    session: Mapped[InterviewSession] = relationship(back_populates="turns")
