from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.session import Base


class TelegramUser(Base):
    __tablename__ = "telegram_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
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


class InterviewSession(Base):
    __tablename__ = "interview_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("telegram_users.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="active")
    summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    user: Mapped[TelegramUser] = relationship(back_populates="sessions")
    turns: Mapped[list["InterviewTurnRecord"]] = relationship(back_populates="session")


class InterviewTurnRecord(Base):
    __tablename__ = "interview_turns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("interview_sessions.id"), index=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    feedback: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    session: Mapped[InterviewSession] = relationship(back_populates="turns")
