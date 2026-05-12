import os
from dataclasses import asdict, dataclass, field
from typing import Any

from db.repository import get_active_interview_snapshot, load_user_session
from services.formatting import build_progress_message, next_recommendation
from services.llm import JobisLLM
from services.session import UserSession


DEFAULT_USER_KEY = os.getenv("JOBIS_DEFAULT_USER_KEY", "local")
MAX_TURNS = 5
_llm: JobisLLM | None = None


@dataclass
class WorkflowResult:
    messages: list[str]
    status: dict[str, Any]
    data: dict[str, Any] = field(default_factory=dict)
    ok: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_user_key() -> str:
    return DEFAULT_USER_KEY


def get_llm() -> JobisLLM:
    global _llm
    if _llm is None:
        _llm = JobisLLM()
    return _llm


def session_status(session: UserSession, *, user_key: str | None = None) -> dict[str, Any]:
    active_interview = get_active_interview_snapshot(user_key) if user_key is not None else None
    return {
        "profile": bool(session.profile),
        "resume": bool(session.resume),
        "github": bool(session.github_summary),
        "job": bool(session.job_posting),
        "interview": "active" if active_interview else "idle",
        "next_recommendation": next_recommendation(session),
    }


def get_status(user_key: str | None = None) -> WorkflowResult:
    resolved_user_key = user_key or default_user_key()
    session = load_user_session(resolved_user_key)
    return WorkflowResult(
        messages=[build_progress_message(session)],
        status=session_status(session, user_key=resolved_user_key),
        data={"progress_text": build_progress_message(session)},
    )


def context_change_blocked_message() -> str:
    return (
        "진행 중인 면접이 있습니다.\n\n"
        "자료를 수정하거나 다시 분석하면 현재 면접 질문/평가와 컨텍스트가 섞일 수 있어요.\n"
        "먼저 현재 면접을 종료하거나, 면접을 끝낸 뒤 다시 시도해주세요."
    )


def ensure_context_mutable(user_key: str) -> WorkflowResult | None:
    if not get_active_interview_snapshot(user_key):
        return None

    session = load_user_session(user_key)
    return WorkflowResult(
        messages=[context_change_blocked_message()],
        status=session_status(session, user_key=user_key),
        ok=False,
    )
