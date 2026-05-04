from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.session import Base


class TelegramUser(Base):
    __tablename__ = "telegram_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    profile: Mapped[str] = mapped_column(Text, default="")
    resume: Mapped[str] = mapped_column(Text, default="")
    github_url: Mapped[str] = mapped_column(Text, default="")
    github_summary: Mapped[str] = mapped_column(Text, default="")
    job_posting: Mapped[str] = mapped_column(Text, default="")
    analysis_summary: Mapped[str] = mapped_column(Text, default="")
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
    current_display_id: Mapped[str] = mapped_column(String(30), default="")
    awaiting_choice: Mapped[bool] = mapped_column(default=False)
    summary: Mapped[str] = mapped_column(Text, default="")
    weakness_summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    user: Mapped[TelegramUser] = relationship(back_populates="sessions")
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
