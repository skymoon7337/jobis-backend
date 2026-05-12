import json
from typing import Any

from sqlalchemy import desc, func, select

from db.models import (
    GithubRepositoryRecord,
    InterviewQuestionRecord,
    InterviewSession,
    InterviewTurnRecord,
    JobPostingRecord,
    User,
)
from db.session import SessionLocal
from services.session import InterviewTurn, UserSession


def get_or_create_user(user_key: str) -> User:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.user_key == user_key))
        if user:
            return user

        user = User(user_key=user_key)
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def load_user_session(user_key: str) -> UserSession:
    user = get_or_create_user(user_key)
    return UserSession(
        profile=user.profile or "",
        resume=user.resume or "",
        github_url=user.github_url or "",
        github_summary=user.github_summary or "",
        job_posting=user.job_posting or "",
    )


def update_user_fields(user_key: str, **fields: Any) -> None:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.user_key == user_key))
        if not user:
            user = User(user_key=user_key)
            db.add(user)

        for key, value in fields.items():
            setattr(user, key, value)

        db.commit()


def create_job_posting(
    user_key: str,
    *,
    title: str,
    source_url: str,
    raw_text: str,
    summary: str,
) -> dict[str, Any]:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.user_key == user_key))
        if not user:
            user = User(user_key=user_key)
            db.add(user)
            db.flush()

        old_selected = db.scalars(
            select(JobPostingRecord)
            .where(JobPostingRecord.user_id == user.id)
            .where(JobPostingRecord.is_selected.is_(True))
        ).all()
        for posting in old_selected:
            posting.is_selected = False

        posting = JobPostingRecord(
            user_id=user.id,
            title=title,
            source_url=source_url,
            raw_text=raw_text,
            summary=summary,
            is_selected=True,
        )
        user.job_posting = raw_text
        db.add(posting)
        db.commit()
        db.refresh(posting)
        postings = db.scalars(
            select(JobPostingRecord)
            .where(JobPostingRecord.user_id == user.id)
            .order_by(JobPostingRecord.id)
        ).all()
        index = next((position for position, item in enumerate(postings, start=1) if item.id == posting.id), None)
        return serialize_job_posting(posting, index=index)


def get_job_postings(user_key: str) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        postings = db.scalars(
            select(JobPostingRecord)
            .join(User, JobPostingRecord.user_id == User.id)
            .where(User.user_key == user_key)
            .order_by(JobPostingRecord.id)
        ).all()
        return [serialize_job_posting(posting, index=index) for index, posting in enumerate(postings, start=1)]


def get_selected_job_posting(user_key: str) -> dict[str, Any] | None:
    postings = get_job_postings(user_key)
    for posting in postings:
        if posting["is_selected"]:
            return posting
    return None


def get_job_posting_by_index(user_key: str, list_index: int) -> dict[str, Any] | None:
    postings = get_job_postings(user_key)
    if list_index < 1 or list_index > len(postings):
        return None
    return postings[list_index - 1]


def select_job_posting(user_key: str, list_index: int) -> dict[str, Any] | None:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.user_key == user_key))
        if not user:
            return None

        postings = db.scalars(
            select(JobPostingRecord)
            .where(JobPostingRecord.user_id == user.id)
            .order_by(JobPostingRecord.id)
        ).all()
        if list_index < 1 or list_index > len(postings):
            return None

        selected = postings[list_index - 1]
        for posting in postings:
            posting.is_selected = posting.id == selected.id

        user.job_posting = selected.raw_text
        db.commit()
        db.refresh(selected)
        return serialize_job_posting(selected, index=list_index)


def delete_job_posting(user_key: str, list_index: int) -> tuple[dict[str, Any] | None, bool]:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.user_key == user_key))
        if not user:
            return None, False

        postings = db.scalars(
            select(JobPostingRecord)
            .where(JobPostingRecord.user_id == user.id)
            .order_by(JobPostingRecord.id)
        ).all()
        if list_index < 1 or list_index > len(postings):
            return None, False

        posting = postings[list_index - 1]
        was_selected = bool(posting.is_selected)
        deleted = serialize_job_posting(posting, index=list_index)
        db.delete(posting)
        if was_selected:
            user.job_posting = ""
        db.commit()
        return deleted, was_selected


def serialize_job_posting(posting: JobPostingRecord, index: int | None = None) -> dict[str, Any]:
    return {
        "id": posting.id,
        "index": index,
        "title": posting.title or f"공고 {posting.id}",
        "source_url": posting.source_url or "",
        "raw_text": posting.raw_text or "",
        "summary": posting.summary or "",
        "is_selected": bool(posting.is_selected),
        "created_at": posting.created_at,
    }


def create_github_repository(
    user_key: str,
    *,
    url: str,
    title: str,
    summary: str,
) -> dict[str, Any]:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.user_key == user_key))
        if not user:
            user = User(user_key=user_key)
            db.add(user)
            db.flush()

        repository = GithubRepositoryRecord(
            user_id=user.id,
            url=url,
            title=title,
            summary=summary,
        )
        user.github_url = url
        user.github_summary = summary
        db.add(repository)
        db.commit()
        db.refresh(repository)
        repositories = db.scalars(
            select(GithubRepositoryRecord)
            .where(GithubRepositoryRecord.user_id == user.id)
            .order_by(GithubRepositoryRecord.id)
        ).all()
        index = next((position for position, item in enumerate(repositories, start=1) if item.id == repository.id), None)
        return serialize_github_repository(repository, index=index)


def get_github_repositories(user_key: str) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.user_key == user_key))
        if not user:
            return []

        repositories = db.scalars(
            select(GithubRepositoryRecord)
            .where(GithubRepositoryRecord.user_id == user.id)
            .order_by(GithubRepositoryRecord.id)
        ).all()

        if not repositories and user.github_summary:
            repository = GithubRepositoryRecord(
                user_id=user.id,
                url=user.github_url or "",
                title=github_repository_title(user.github_url or ""),
                summary=user.github_summary or "",
            )
            db.add(repository)
            db.commit()
            repositories = [repository]

        return [serialize_github_repository(repository, index=index) for index, repository in enumerate(repositories, start=1)]


def get_github_repositories_by_indices(user_key: str, list_indices: list[int]) -> list[dict[str, Any]]:
    repositories = get_github_repositories(user_key)
    selected: list[dict[str, Any]] = []
    for list_index in list_indices:
        if list_index < 1 or list_index > len(repositories):
            continue
        selected.append(repositories[list_index - 1])
    return selected


def delete_github_repository(user_key: str, list_index: int) -> dict[str, Any] | None:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.user_key == user_key))
        if not user:
            return None

        repositories = db.scalars(
            select(GithubRepositoryRecord)
            .where(GithubRepositoryRecord.user_id == user.id)
            .order_by(GithubRepositoryRecord.id)
        ).all()
        if list_index < 1 or list_index > len(repositories):
            return None

        repository = repositories[list_index - 1]
        deleted = serialize_github_repository(repository, index=list_index)
        db.delete(repository)
        remaining = [item for item in repositories if item.id != repository.id]
        if user.github_url == repository.url and user.github_summary == repository.summary:
            latest = remaining[-1] if remaining else None
            user.github_url = latest.url if latest else ""
            user.github_summary = latest.summary if latest else ""
        db.commit()
        return deleted


def serialize_github_repository(repository: GithubRepositoryRecord, index: int | None = None) -> dict[str, Any]:
    return {
        "id": repository.id,
        "index": index,
        "url": repository.url or "",
        "title": repository.title or f"GitHub 저장소 {repository.id}",
        "summary": repository.summary or "",
        "created_at": repository.created_at,
    }


def github_repository_title(url: str) -> str:
    cleaned = url.strip().rstrip("/")
    parts = [part for part in cleaned.split("/") if part]
    if len(parts) >= 2:
        return "/".join(parts[-2:])
    return cleaned or "GitHub 저장소"


def reset_user_context(user_key: str) -> None:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.user_key == user_key))
        if not user:
            return

        user.profile = ""
        user.resume = ""
        user.github_url = ""
        user.github_summary = ""
        user.job_posting = ""

        postings = db.scalars(select(JobPostingRecord).where(JobPostingRecord.user_id == user.id)).all()
        for posting in postings:
            db.delete(posting)

        repositories = db.scalars(select(GithubRepositoryRecord).where(GithubRepositoryRecord.user_id == user.id)).all()
        for repository in repositories:
            db.delete(repository)

        db.commit()


def create_interview_session(user_key: str, context_snapshot: dict[str, Any] | None = None) -> int:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.user_key == user_key))
        if not user:
            user = User(user_key=user_key)
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

        snapshot = context_snapshot or {}
        interview_session = InterviewSession(
            user_id=user.id,
            status="active",
            context_profile=snapshot.get("profile", ""),
            context_resume=snapshot.get("resume", ""),
            context_job_title=snapshot.get("job_title", ""),
            context_job_summary=snapshot.get("job_summary", ""),
            context_github_repositories=json.dumps(
                snapshot.get("github_repositories", []),
                ensure_ascii=False,
            ),
        )
        db.add(interview_session)
        db.commit()
        db.refresh(interview_session)
        return interview_session.id


def parse_json_list(value: str) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def serialize_interview_question(question: InterviewQuestionRecord) -> dict[str, Any]:
    return {
        "display_id": question.display_id or "",
        "question_type": question.question_type or "",
        "question": question.question or "",
        "is_bonus": bool(question.is_bonus),
        "bonus_type": question.bonus_type or "",
    }


def serialize_interview_turn(turn: InterviewTurnRecord) -> dict[str, Any]:
    return {
        "display_id": turn.display_id or "",
        "question_type": turn.question_type or "",
        "question": turn.question or "",
        "answer": turn.answer or "",
        "feedback": turn.feedback or "",
        "is_bonus": bool(turn.is_bonus),
        "bonus_type": turn.bonus_type or "",
    }


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


def get_active_interview_snapshot(user_key: str) -> dict[str, Any] | None:
    with SessionLocal() as db:
        interview_session = db.scalar(
            select(InterviewSession)
            .join(User, InterviewSession.user_id == User.id)
            .where(User.user_key == user_key)
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
            "questions": [serialize_interview_question(question) for question in questions],
            "turns": [serialize_interview_turn(turn) for turn in turns],
        }


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


def get_recent_sessions(user_key: str, limit: int = 5) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        sessions = db.scalars(
            select(InterviewSession)
            .join(User, InterviewSession.user_id == User.id)
            .where(User.user_key == user_key)
            .order_by(desc(InterviewSession.created_at))
            .limit(limit)
        ).all()

        result: list[dict[str, Any]] = []
        for session in sessions:
            questions = db.scalars(
                select(InterviewQuestionRecord)
                .where(InterviewQuestionRecord.session_id == session.id)
                .order_by(InterviewQuestionRecord.id)
            ).all()
            turns = db.scalars(
                select(InterviewTurnRecord)
                .where(InterviewTurnRecord.session_id == session.id)
                .order_by(InterviewTurnRecord.id)
            ).all()
            regular_questions = [question for question in questions if not question.is_bonus]
            result.append(
                {
                    "id": session.id,
                    "status": session.status,
                    "summary": session.summary or "",
                    "weakness_summary": session.weakness_summary or "",
                    "created_at": session.created_at,
                    "turn_count": sum(1 for turn in turns if not turn.is_bonus),
                    "question_count": len(regular_questions),
                    "job_title": session.context_job_title or "",
                    "job_summary": session.context_job_summary or "",
                    "github_repositories": parse_json_list(session.context_github_repositories),
                    "questions": [serialize_interview_question(question) for question in questions],
                    "turns": [serialize_interview_turn(turn) for turn in turns],
                }
            )

        return result


def get_latest_weakness_summary(user_key: str) -> str:
    with SessionLocal() as db:
        row = db.execute(
            select(InterviewSession.weakness_summary)
            .join(User, InterviewSession.user_id == User.id)
            .where(User.user_key == user_key)
            .where(InterviewSession.weakness_summary != "")
            .order_by(desc(InterviewSession.created_at))
            .limit(1)
        ).scalar_one_or_none()

    return row or ""


def get_latest_feedback_summary(user_key: str) -> str:
    with SessionLocal() as db:
        row = db.execute(
            select(InterviewSession.summary)
            .join(User, InterviewSession.user_id == User.id)
            .where(User.user_key == user_key)
            .where(InterviewSession.status == "completed")
            .where(InterviewSession.summary != "")
            .order_by(desc(InterviewSession.created_at))
            .limit(1)
        ).scalar_one_or_none()

    return row or ""


def get_recent_turns(user_key: str, limit: int = 20) -> list[InterviewTurn]:
    with SessionLocal() as db:
        rows = db.execute(
            select(InterviewTurnRecord)
            .join(InterviewSession, InterviewTurnRecord.session_id == InterviewSession.id)
            .join(User, InterviewSession.user_id == User.id)
            .where(User.user_key == user_key)
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
