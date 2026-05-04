from typing import Any

from sqlalchemy import desc, func, or_, select

from db.models import InterviewQuestionRecord, InterviewSession, InterviewTurnRecord, TelegramUser
from db.session import SessionLocal
from services.session import InterviewTurn, UserSession


def get_or_create_user(chat_id: int) -> TelegramUser:
    with SessionLocal() as db:
        user = db.scalar(select(TelegramUser).where(TelegramUser.chat_id == chat_id))
        if user:
            return user

        user = TelegramUser(chat_id=chat_id)
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def load_user_session(chat_id: int) -> UserSession:
    user = get_or_create_user(chat_id)
    return UserSession(
        profile=user.profile or "",
        resume=user.resume or "",
        github_url=user.github_url or "",
        github_summary=user.github_summary or "",
        job_posting=user.job_posting or "",
        analysis_summary=user.analysis_summary or "",
    )


def update_user_fields(chat_id: int, **fields: Any) -> None:
    with SessionLocal() as db:
        user = db.scalar(select(TelegramUser).where(TelegramUser.chat_id == chat_id))
        if not user:
            user = TelegramUser(chat_id=chat_id)
            db.add(user)

        for key, value in fields.items():
            setattr(user, key, value)

        db.commit()


def reset_user_context(chat_id: int) -> None:
    update_user_fields(
        chat_id,
        profile="",
        resume="",
        github_url="",
        github_summary="",
        job_posting="",
        analysis_summary="",
    )


def create_interview_session(chat_id: int) -> int:
    with SessionLocal() as db:
        user = db.scalar(select(TelegramUser).where(TelegramUser.chat_id == chat_id))
        if not user:
            user = TelegramUser(chat_id=chat_id)
            db.add(user)
            db.flush()

        old_active_sessions = db.scalars(
            select(InterviewSession)
            .where(InterviewSession.user_id == user.id)
            .where(InterviewSession.status == "active")
        ).all()
        for old_session in old_active_sessions:
            old_session.status = "stopped"
            old_session.current_display_id = ""
            old_session.awaiting_choice = False

        interview_session = InterviewSession(user_id=user.id, status="active")
        db.add(interview_session)
        db.commit()
        db.refresh(interview_session)
        return interview_session.id


def update_interview_session(
    session_id: int,
    *,
    status: str | None = None,
    current_display_id: str | None = None,
    awaiting_choice: bool | None = None,
    summary: str | None = None,
    weakness_summary: str | None = None,
) -> None:
    with SessionLocal() as db:
        interview_session = db.get(InterviewSession, session_id)
        if not interview_session:
            return

        if status is not None:
            interview_session.status = status
        if current_display_id is not None:
            interview_session.current_display_id = current_display_id
        if awaiting_choice is not None:
            interview_session.awaiting_choice = awaiting_choice
        if summary is not None:
            interview_session.summary = summary
        if weakness_summary is not None:
            interview_session.weakness_summary = weakness_summary

        db.commit()


def get_active_interview_snapshot(chat_id: int) -> dict[str, Any] | None:
    with SessionLocal() as db:
        interview_session = db.scalar(
            select(InterviewSession)
            .join(TelegramUser, InterviewSession.user_id == TelegramUser.id)
            .where(TelegramUser.chat_id == chat_id)
            .where(InterviewSession.status == "active")
            .order_by(desc(InterviewSession.created_at))
            .limit(1)
        )
        if not interview_session:
            return None

        questions = db.scalars(
            select(InterviewQuestionRecord)
            .where(InterviewQuestionRecord.session_id == interview_session.id)
            .order_by(InterviewQuestionRecord.id)
        ).all()
        turns = db.scalars(
            select(InterviewTurnRecord)
            .where(InterviewTurnRecord.session_id == interview_session.id)
            .order_by(InterviewTurnRecord.id)
        ).all()

        return {
            "id": interview_session.id,
            "current_display_id": interview_session.current_display_id or "",
            "awaiting_choice": bool(interview_session.awaiting_choice),
            "questions": [
                {
                    "display_id": question.display_id or "",
                    "question_type": question.question_type or "",
                    "question": question.question or "",
                    "is_bonus": bool(question.is_bonus),
                    "bonus_type": question.bonus_type or "",
                }
                for question in questions
            ],
            "turns": [
                {
                    "display_id": turn.display_id or "",
                    "question_type": turn.question_type or "",
                    "question": turn.question or "",
                    "answer": turn.answer or "",
                    "feedback": turn.feedback or "",
                    "is_bonus": bool(turn.is_bonus),
                    "bonus_type": turn.bonus_type or "",
                }
                for turn in turns
            ],
        }


def get_active_interview_notices() -> list[dict[str, Any]]:
    with SessionLocal() as db:
        rows = db.execute(
            select(
                TelegramUser.chat_id,
                InterviewSession.id,
                InterviewSession.current_display_id,
                InterviewSession.awaiting_choice,
                func.count(InterviewTurnRecord.id).label("turn_count"),
            )
            .join(InterviewSession, InterviewSession.user_id == TelegramUser.id)
            .outerjoin(InterviewTurnRecord, InterviewTurnRecord.session_id == InterviewSession.id)
            .where(InterviewSession.status == "active")
            .group_by(InterviewSession.id, TelegramUser.chat_id)
            .order_by(desc(InterviewSession.created_at))
        ).all()

    notices: list[dict[str, Any]] = []
    seen_chat_ids: set[int] = set()
    for row in rows:
        if row.chat_id in seen_chat_ids:
            continue
        seen_chat_ids.add(row.chat_id)
        latest_turn = db.scalar(
            select(InterviewTurnRecord)
            .where(InterviewTurnRecord.session_id == row.id)
            .order_by(desc(InterviewTurnRecord.id))
            .limit(1)
        )
        notices.append(
            {
                "chat_id": row.chat_id,
                "session_id": row.id,
                "current_display_id": row.current_display_id or "",
                "awaiting_choice": bool(row.awaiting_choice),
                "turn_count": row.turn_count,
                "last_display_id": latest_turn.display_id if latest_turn else "",
                "last_question": latest_turn.question if latest_turn else "",
            }
        )

    return notices


def get_progress_notices() -> list[dict[str, Any]]:
    active_user_ids = select(InterviewSession.user_id).where(InterviewSession.status == "active")

    with SessionLocal() as db:
        users = db.scalars(
            select(TelegramUser)
            .where(TelegramUser.id.not_in(active_user_ids))
            .where(
                or_(
                    TelegramUser.profile != "",
                    TelegramUser.resume != "",
                    TelegramUser.github_summary != "",
                    TelegramUser.job_posting != "",
                    TelegramUser.analysis_summary != "",
                )
            )
            .order_by(desc(TelegramUser.updated_at))
        ).all()

        return [
            {
                "chat_id": user.chat_id,
                "profile": user.profile or "",
                "resume": user.resume or "",
                "github_url": user.github_url or "",
                "github_summary": user.github_summary or "",
                "job_posting": user.job_posting or "",
                "analysis_summary": user.analysis_summary or "",
            }
            for user in users
        ]


def save_interview_turn(
    session_id: int,
    *,
    question_type: str,
    question: str,
    answer: str,
    feedback: str,
    display_id: str = "",
    is_bonus: bool = False,
    bonus_type: str = "",
) -> None:
    with SessionLocal() as db:
        db.add(
            InterviewTurnRecord(
                session_id=session_id,
                display_id=display_id,
                question_type=question_type,
                question=question,
                answer=answer,
                feedback=feedback,
                is_bonus=is_bonus,
                bonus_type=bonus_type,
            )
        )
        db.commit()


def save_interview_question(
    session_id: int,
    *,
    display_id: str,
    question_type: str,
    question: str,
    is_bonus: bool = False,
    bonus_type: str = "",
) -> None:
    with SessionLocal() as db:
        db.add(
            InterviewQuestionRecord(
                session_id=session_id,
                display_id=display_id,
                question_type=question_type,
                question=question,
                is_bonus=is_bonus,
                bonus_type=bonus_type,
            )
        )
        db.commit()


def save_interview_questions(session_id: int, questions: list[dict[str, Any]]) -> None:
    with SessionLocal() as db:
        db.add_all(
            InterviewQuestionRecord(
                session_id=session_id,
                display_id=item.get("display_id", ""),
                question_type=item.get("question_type", ""),
                question=item.get("question", ""),
                is_bonus=item.get("is_bonus", False),
                bonus_type=item.get("bonus_type", ""),
            )
            for item in questions
        )
        db.commit()


def get_recent_sessions(chat_id: int, limit: int = 5) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        rows = db.execute(
            select(
                InterviewSession.id,
                InterviewSession.status,
                InterviewSession.summary,
                InterviewSession.weakness_summary,
                InterviewSession.created_at,
                func.count(InterviewTurnRecord.id).label("turn_count"),
            )
            .join(TelegramUser, InterviewSession.user_id == TelegramUser.id)
            .outerjoin(InterviewTurnRecord, InterviewTurnRecord.session_id == InterviewSession.id)
            .where(TelegramUser.chat_id == chat_id)
            .group_by(InterviewSession.id)
            .order_by(desc(InterviewSession.created_at))
            .limit(limit)
        ).all()

    return [
        {
            "id": row.id,
            "status": row.status,
            "summary": row.summary or "",
            "weakness_summary": row.weakness_summary or "",
            "created_at": row.created_at,
            "turn_count": row.turn_count,
        }
        for row in rows
    ]


def get_latest_weakness_summary(chat_id: int) -> str:
    with SessionLocal() as db:
        row = db.execute(
            select(InterviewSession.weakness_summary)
            .join(TelegramUser, InterviewSession.user_id == TelegramUser.id)
            .where(TelegramUser.chat_id == chat_id)
            .where(InterviewSession.weakness_summary != "")
            .order_by(desc(InterviewSession.created_at))
            .limit(1)
        ).scalar_one_or_none()

    return row or ""


def get_latest_feedback_summary(chat_id: int) -> str:
    with SessionLocal() as db:
        row = db.execute(
            select(InterviewSession.summary)
            .join(TelegramUser, InterviewSession.user_id == TelegramUser.id)
            .where(TelegramUser.chat_id == chat_id)
            .where(InterviewSession.status == "completed")
            .where(InterviewSession.summary != "")
            .order_by(desc(InterviewSession.created_at))
            .limit(1)
        ).scalar_one_or_none()

    return row or ""


def get_recent_turns(chat_id: int, limit: int = 20) -> list[InterviewTurn]:
    with SessionLocal() as db:
        rows = db.execute(
            select(InterviewTurnRecord)
            .join(InterviewSession, InterviewTurnRecord.session_id == InterviewSession.id)
            .join(TelegramUser, InterviewSession.user_id == TelegramUser.id)
            .where(TelegramUser.chat_id == chat_id)
            .order_by(desc(InterviewTurnRecord.created_at))
            .limit(limit)
        ).scalars()

        return [
            InterviewTurn(
                question=turn.question,
                answer=turn.answer,
                feedback=turn.feedback,
                question_type=turn.question_type,
                display_id=turn.display_id,
                is_bonus=turn.is_bonus,
                bonus_type=turn.bonus_type,
            )
            for turn in rows
        ]
