from db.repository import load_user_session, reset_user_context, update_user_fields
from services.formatting import build_progress_message
from services.workflow_common import (
    WorkflowResult,
    ensure_context_mutable,
    default_user_key,
    session_status,
)


def context_payload(user_key: str) -> dict:
    session = load_user_session(user_key)
    return {
        "profile": session.profile,
        "resume": session.resume,
        "github_url": session.github_url,
        "github_summary": session.github_summary,
        "job_posting": session.job_posting,
    }


def get_context(user_key: str | None = None) -> WorkflowResult:
    resolved_user_key = user_key or default_user_key()
    session = load_user_session(resolved_user_key)
    return WorkflowResult(
        messages=["저장된 입력 자료입니다."],
        status=session_status(session, user_key=resolved_user_key),
        data=context_payload(resolved_user_key),
    )


def reset_context(user_key: str | None = None) -> WorkflowResult:
    resolved_user_key = user_key or default_user_key()
    blocked = ensure_context_mutable(resolved_user_key)
    if blocked:
        blocked.data = context_payload(resolved_user_key)
        return blocked

    reset_user_context(resolved_user_key)
    session = load_user_session(resolved_user_key)
    return WorkflowResult(
        messages=[
            "입력 자료를 초기화했습니다.\n\n"
            "삭제된 것\n"
            "- 프로필\n"
            "- 자소서\n"
            "- GitHub 분석\n"
            "- 공고\n\n"
            "남아있는 것\n"
            "- 지난 면접 기록\n"
            "- 전체 피드백\n"
            "- 약점 요약"
        ],
        status=session_status(session, user_key=resolved_user_key),
        data=context_payload(resolved_user_key),
    )


def save_profile(profile: str, user_key: str | None = None) -> WorkflowResult:
    resolved_user_key = user_key or default_user_key()
    blocked = ensure_context_mutable(resolved_user_key)
    if blocked:
        return blocked

    cleaned_profile = profile.strip()
    update_user_fields(resolved_user_key, profile=cleaned_profile)
    session = load_user_session(resolved_user_key)
    message = (
        "프로필을 저장했습니다.\n\n"
        "프로필이 변경되었습니다. 면접 탭에서 질문 후보를 새로 만들면 변경된 자료가 반영됩니다.\n\n"
        f"{build_progress_message(session)}"
    )
    return WorkflowResult(
        messages=[message],
        status=session_status(session, user_key=resolved_user_key),
        data={"profile": cleaned_profile},
    )


def save_resume(resume: str, user_key: str | None = None) -> WorkflowResult:
    resolved_user_key = user_key or default_user_key()
    blocked = ensure_context_mutable(resolved_user_key)
    if blocked:
        return blocked

    cleaned_resume = resume.strip()
    update_user_fields(resolved_user_key, resume=cleaned_resume)
    session = load_user_session(resolved_user_key)
    message = (
        "자소서를 저장했습니다.\n\n"
        "자소서가 변경되었습니다. 면접 탭에서 질문 후보를 새로 만들면 변경된 자료가 반영됩니다.\n\n"
        f"{build_progress_message(session)}"
    )
    return WorkflowResult(
        messages=[message],
        status=session_status(session, user_key=resolved_user_key),
        data={"resume": cleaned_resume},
    )
