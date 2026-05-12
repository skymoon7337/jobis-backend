from db.repository import (
    create_job_posting,
    delete_job_posting,
    get_job_postings,
    get_selected_job_posting,
    load_user_session,
    select_job_posting,
)
from services.formatting import build_progress_message, parse_job_summary
from services.webpage import fetch_page_text
from services.workflow_common import (
    WorkflowResult,
    ensure_context_mutable,
    get_llm,
    default_user_key,
    session_status,
)


def jobs_payload(user_key: str) -> dict:
    return {
        "jobs": get_job_postings(user_key),
        "selected_job": get_selected_job_posting(user_key),
    }


def list_jobs(user_key: str | None = None) -> WorkflowResult:
    resolved_user_key = user_key or default_user_key()
    session = load_user_session(resolved_user_key)
    postings = get_job_postings(resolved_user_key)
    message = "저장된 공고가 없습니다." if not postings else "저장된 공고 목록입니다."
    return WorkflowResult(
        messages=[message],
        status=session_status(session, user_key=resolved_user_key),
        data=jobs_payload(resolved_user_key),
    )


async def save_job(text_or_url: str, user_key: str | None = None) -> WorkflowResult:
    resolved_user_key = user_key or default_user_key()
    blocked = ensure_context_mutable(resolved_user_key)
    if blocked:
        blocked.data = jobs_payload(resolved_user_key)
        return blocked

    text = text_or_url.strip()
    source_url = ""
    if text.startswith(("http://", "https://")):
        source_url = text
        try:
            text = await fetch_page_text(text)
        except Exception as exc:
            session = load_user_session(resolved_user_key)
            return WorkflowResult(
                messages=[
                    "이 URL은 자동으로 읽지 못했습니다.\n\n"
                    f"원인: {exc}\n\n"
                    "공고 본문을 복사해서 다시 추가해주세요."
                ],
                status=session_status(session, user_key=resolved_user_key),
                data=jobs_payload(resolved_user_key),
                ok=False,
            )

    try:
        job_result = await get_llm().summarize_job_posting(text)
        title, summary = parse_job_summary(job_result)
    except Exception as exc:
        session = load_user_session(resolved_user_key)
        return WorkflowResult(
            messages=[
                "공고 요약에 실패했습니다.\n\n"
                "잠시 후 같은 공고로 다시 시도해주세요.\n\n"
                f"원인: {str(exc)[:300]}"
            ],
            status=session_status(session, user_key=resolved_user_key),
            data=jobs_payload(resolved_user_key),
            ok=False,
        )

    posting = create_job_posting(
        resolved_user_key,
        title=title,
        source_url=source_url,
        raw_text=text,
        summary=summary,
    )
    session = load_user_session(resolved_user_key)
    return WorkflowResult(
        messages=[
            "공고를 저장하고 현재 면접 기준으로 선택했습니다.\n\n"
            f"[{posting['index']}] {posting['title']}\n\n"
            f"{posting['summary']}\n\n"
            f"{build_progress_message(session)}"
        ],
        status=session_status(session, user_key=resolved_user_key),
        data=jobs_payload(resolved_user_key),
    )


def select_job(index: int, user_key: str | None = None) -> WorkflowResult:
    resolved_user_key = user_key or default_user_key()
    blocked = ensure_context_mutable(resolved_user_key)
    if blocked:
        blocked.data = jobs_payload(resolved_user_key)
        return blocked

    selected = select_job_posting(resolved_user_key, index)
    session = load_user_session(resolved_user_key)
    if not selected:
        return WorkflowResult(
            messages=["해당 번호의 공고를 찾지 못했습니다."],
            status=session_status(session, user_key=resolved_user_key),
            data=jobs_payload(resolved_user_key),
            ok=False,
        )

    return WorkflowResult(
        messages=[
            f"[{selected['index']}] {selected['title']} 공고를 현재 면접 기준으로 선택했습니다.\n\n"
            "다음 추천: 면접 탭에서 질문 후보 만들기"
        ],
        status=session_status(session, user_key=resolved_user_key),
        data=jobs_payload(resolved_user_key),
    )


def remove_job(index: int, user_key: str | None = None) -> WorkflowResult:
    resolved_user_key = user_key or default_user_key()
    blocked = ensure_context_mutable(resolved_user_key)
    if blocked:
        blocked.data = jobs_payload(resolved_user_key)
        return blocked

    deleted, was_selected = delete_job_posting(resolved_user_key, index)
    session = load_user_session(resolved_user_key)
    if not deleted:
        return WorkflowResult(
            messages=["해당 번호의 공고를 찾지 못했습니다."],
            status=session_status(session, user_key=resolved_user_key),
            data=jobs_payload(resolved_user_key),
            ok=False,
        )

    message = f"[{deleted['index']}] {deleted['title']} 공고를 삭제했습니다."
    if was_selected:
        message += "\n\n삭제한 공고가 현재 선택된 공고였습니다. 다른 공고를 선택하거나 새 공고를 추가해주세요."

    return WorkflowResult(
        messages=[message],
        status=session_status(session, user_key=resolved_user_key),
        data=jobs_payload(resolved_user_key),
    )
