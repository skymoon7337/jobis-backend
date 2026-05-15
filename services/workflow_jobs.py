from db.repository import (
    create_analysis_job,
    create_job_posting,
    delete_job_posting,
    get_active_analysis_job,
    get_job_posting_by_index,
    get_job_postings,
    get_selected_job_posting,
    load_user_session,
    select_job_posting,
    update_analysis_job,
    update_job_posting_content,
    update_job_posting_metadata,
)
from services.formatting import parse_job_summary
from services.webpage import fetch_page_text
from services.workflow_common import (
    WorkflowResult,
    ensure_context_mutable,
    get_llm,
    default_user_key,
    session_status,
)

JOB_POSTING_JOB_KIND = "job_posting"
JOB_POSTING_PROGRESS_TOTAL = 5


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


def create_job_posting_job(
    text_or_url: str,
    user_key: str | None = None,
    replace_index: int | None = None,
) -> tuple[WorkflowResult, bool]:
    resolved_user_key = user_key or default_user_key()
    blocked = ensure_context_mutable(resolved_user_key)
    if blocked:
        blocked.data = {"job": None}
        return blocked, False

    text = text_or_url.strip()
    if not text:
        session = load_user_session(resolved_user_key)
        return (
            WorkflowResult(
                messages=["공고 URL 또는 본문을 입력해주세요."],
                status=session_status(session, user_key=resolved_user_key),
                data={"job": None},
                ok=False,
            ),
            False,
        )

    if replace_index is not None and not get_job_posting_by_index(resolved_user_key, replace_index):
        session = load_user_session(resolved_user_key)
        return (
            WorkflowResult(
                messages=["수정할 공고를 찾지 못했습니다."],
                status=session_status(session, user_key=resolved_user_key),
                data={"job": None},
                ok=False,
            ),
            False,
        )

    active_job = get_active_analysis_job(resolved_user_key, JOB_POSTING_JOB_KIND)
    if active_job:
        session = load_user_session(resolved_user_key)
        return (
            WorkflowResult(
                messages=["이미 공고 분석이 진행 중입니다."],
                status=session_status(session, user_key=resolved_user_key),
                data={"job": active_job},
            ),
            False,
        )

    job = create_analysis_job(
        resolved_user_key,
        kind=JOB_POSTING_JOB_KIND,
        input_data={"text": text, "replace_index": replace_index},
        stage="queued",
        message="공고 수정을 준비하고 있습니다." if replace_index else "공고 분석을 준비하고 있습니다.",
        progress_current=0,
        progress_total=JOB_POSTING_PROGRESS_TOTAL,
    )
    session = load_user_session(resolved_user_key)
    return (
        WorkflowResult(
            messages=["공고 분석 작업을 시작했습니다."],
            status=session_status(session, user_key=resolved_user_key),
            data={"job": job},
        ),
        True,
    )


async def run_job_posting_job(
    job_id: int,
    user_key: str,
    text_or_url: str,
    replace_index: int | None = None,
) -> None:
    text = text_or_url.strip()
    source_url = ""
    try:
        update_analysis_job(
            job_id,
            status="running",
            stage="reading",
            message="공고 내용을 확인하는 중입니다.",
            progress_current=1,
            progress_total=JOB_POSTING_PROGRESS_TOTAL,
        )
        if text.startswith(("http://", "https://")):
            source_url = text
            update_analysis_job(
                job_id,
                status="running",
                stage="fetching_page",
                message="공고 페이지를 읽는 중입니다.",
                progress_current=1,
                progress_total=JOB_POSTING_PROGRESS_TOTAL,
            )
            text = await fetch_page_text(text)

        update_analysis_job(
            job_id,
            status="running",
            stage="summarizing",
            message="주요 업무를 정리하는 중입니다.",
            progress_current=2,
            progress_total=JOB_POSTING_PROGRESS_TOTAL,
        )
        job_result = await get_llm().summarize_job_posting(text)

        update_analysis_job(
            job_id,
            status="running",
            stage="parsing",
            message="필수 역량과 우대사항을 나누는 중입니다.",
            progress_current=3,
            progress_total=JOB_POSTING_PROGRESS_TOTAL,
        )
        title, summary = parse_job_summary(job_result)

        update_analysis_job(
            job_id,
            status="running",
            stage="saving",
            message="공고 요약을 저장하는 중입니다.",
            progress_current=4,
            progress_total=JOB_POSTING_PROGRESS_TOTAL,
        )
        if replace_index is None:
            create_job_posting(
                user_key,
                title=title,
                source_url=source_url,
                raw_text=text,
                summary=summary,
            )
        elif not update_job_posting_content(
            user_key,
            replace_index,
            title=title,
            source_url=source_url,
            raw_text=text,
            summary=summary,
        ):
            raise ValueError("수정할 공고를 찾지 못했습니다.")
        update_analysis_job(
            job_id,
            status="completed",
            stage="completed",
            message="공고 수정이 완료되었습니다." if replace_index else "공고 분석이 완료되었습니다.",
            progress_current=JOB_POSTING_PROGRESS_TOTAL,
            progress_total=JOB_POSTING_PROGRESS_TOTAL,
            result_data=jobs_payload(user_key),
            finished=True,
        )
    except ValueError as exc:
        fail_job_posting_job(job_id, "parse_error", str(exc))
    except Exception as exc:
        fail_job_posting_job(job_id, classify_job_posting_error(exc), str(exc))


def fail_job_posting_job(job_id: int, error_type: str, error_message: str) -> None:
    update_analysis_job(
        job_id,
        status="failed",
        stage="failed",
        message="공고 분석에 실패했습니다.",
        error_type=error_type,
        error_message=error_message[:500],
        finished=True,
    )


def classify_job_posting_error(exc: Exception) -> str:
    name = exc.__class__.__name__.lower()
    if "timeout" in name:
        return "timeout"
    if "http" in name or "network" in name or "connect" in name:
        return "job_page_fetch_error"
    return "server_error"


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
            f"[{selected['index']}] {selected.get('display_name') or selected['title']} 공고를 현재 면접 기준으로 선택했습니다.\n\n"
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


def rename_job(index: int, alias: str, user_key: str | None = None) -> WorkflowResult:
    return update_job_meta(index, alias=alias, user_key=user_key)


def update_job_meta(
    index: int,
    *,
    alias: str | None = None,
    source_url: str | None = None,
    user_key: str | None = None,
) -> WorkflowResult:
    resolved_user_key = user_key or default_user_key()
    blocked = ensure_context_mutable(resolved_user_key)
    if blocked:
        blocked.data = jobs_payload(resolved_user_key)
        return blocked

    updated = update_job_posting_metadata(
        resolved_user_key,
        index,
        alias=alias,
        source_url=source_url,
    )
    session = load_user_session(resolved_user_key)
    if not updated:
        return WorkflowResult(
            messages=["해당 번호의 공고를 찾지 못했습니다."],
            status=session_status(session, user_key=resolved_user_key),
            data=jobs_payload(resolved_user_key),
            ok=False,
        )

    display_name = updated.get("display_name") or updated.get("title") or "공고"
    changed = []
    if alias is not None:
        changed.append("별명")
    if source_url is not None:
        changed.append("링크")
    changed_label = "/".join(changed) or "기본"
    return WorkflowResult(
        messages=[f"[{index}] {display_name} 공고 {changed_label} 정보를 저장했습니다."],
        status=session_status(session, user_key=resolved_user_key),
        data=jobs_payload(resolved_user_key),
    )
