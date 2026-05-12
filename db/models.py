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
    github_repositories: Mapped[list["GithubRepositoryRecord"]] = relationship(back_populates="user")


class JobPostingRecord(Base):
    __tablename__ = "job_postings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    source_url: Mapped[str] = mapped_column(Text, default="")
    raw_text: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    is_selected: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    user: Mapped[User] = relationship(back_populates="job_postings")


class GithubRepositoryRecord(Base):
    __tablename__ = "github_repositories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    url: Mapped[str] = mapped_column(Text, default="")
    title: Mapped[str] = mapped_column(String(200), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    user: Mapped[User] = relationship(back_populates="github_repositories")


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
