from db.repository import (
    create_analysis_job,
    delete_github_repository,
    get_active_analysis_job,
    get_github_repository_by_repo_key,
    get_github_repositories,
    get_latest_completed_analysis_job_by_input,
    load_user_session,
    save_agent_chat_message,
    update_github_repository_alias,
    update_analysis_job,
    upsert_github_repository_snapshot,
)
from services.formatting import split_tagged_sections
from services.github import fetch_repo_context, parse_github_repo_url
from services.workflow_common import (
    WorkflowResult,
    ensure_context_mutable,
    get_llm,
    default_user_key,
    session_status,
)

GITHUB_ANALYSIS_JOB_KIND = "github_analysis"
GITHUB_ANALYSIS_PROGRESS_TOTAL = 7


def github_payload(user_key: str) -> dict:
    session = load_user_session(user_key)
    return {
        "github_url": session.github_url,
        "github_summary": session.github_summary,
        "repositories": get_github_repositories(user_key),
    }


def get_github_analysis(user_key: str | None = None) -> WorkflowResult:
    resolved_user_key = user_key or default_user_key()
    session = load_user_session(resolved_user_key)
    message = "저장된 GitHub 분석이 없습니다." if not session.github_summary else "저장된 GitHub 분석입니다."
    return WorkflowResult(
        messages=[message],
        status=session_status(session, user_key=resolved_user_key),
        data=github_payload(resolved_user_key),
    )


def list_github_repositories(user_key: str | None = None) -> WorkflowResult:
    resolved_user_key = user_key or default_user_key()
    session = load_user_session(resolved_user_key)
    repositories = get_github_repositories(resolved_user_key)
    message = "저장된 GitHub 저장소가 없습니다." if not repositories else "저장된 GitHub 저장소 목록입니다."
    return WorkflowResult(
        messages=[message],
        status=session_status(session, user_key=resolved_user_key),
        data=github_payload(resolved_user_key),
    )


def create_github_analysis_job(url: str, user_key: str | None = None, force: bool = False) -> tuple[WorkflowResult, bool]:
    resolved_user_key = user_key or default_user_key()
    blocked = ensure_context_mutable(resolved_user_key)
    if blocked:
        blocked.data = {"job": None}
        return blocked, False

    github_url = url.strip()
    try:
        parse_github_repo_url(github_url)
    except Exception as exc:
        session = load_user_session(resolved_user_key)
        return (
            WorkflowResult(
                messages=[str(exc)],
                status=session_status(session, user_key=resolved_user_key),
                data={"job": None},
                ok=False,
            ),
            False,
        )

    input_data = {"url": github_url}
    if not force:
        cached_job = get_latest_completed_analysis_job_by_input(
            resolved_user_key,
            GITHUB_ANALYSIS_JOB_KIND,
            input_data,
        )
        if cached_job:
            session = load_user_session(resolved_user_key)
            return (
                WorkflowResult(
                    messages=["같은 GitHub URL로 만든 기존 분석 결과를 불러왔습니다."],
                    status=session_status(session, user_key=resolved_user_key),
                    data={"job": cached_job, "cached": True},
                ),
                False,
            )

    active_job = get_active_analysis_job(resolved_user_key, GITHUB_ANALYSIS_JOB_KIND)
    if active_job:
        session = load_user_session(resolved_user_key)
        return (
            WorkflowResult(
                messages=["이미 GitHub 분석이 진행 중입니다."],
                status=session_status(session, user_key=resolved_user_key),
                data={"job": active_job},
            ),
            False,
        )

    job = create_analysis_job(
        resolved_user_key,
        kind=GITHUB_ANALYSIS_JOB_KIND,
        input_data=input_data,
        stage="queued",
        message="GitHub 분석을 준비하고 있습니다.",
        progress_current=0,
        progress_total=GITHUB_ANALYSIS_PROGRESS_TOTAL,
    )
    session = load_user_session(resolved_user_key)
    return (
        WorkflowResult(
            messages=["GitHub 분석 작업을 시작했습니다."],
            status=session_status(session, user_key=resolved_user_key),
            data={"job": job},
        ),
        True,
    )


async def run_github_analysis_job(job_id: int, user_key: str, url: str) -> None:
    async def progress(stage: str, message: str, current: int, total: int) -> None:
        update_analysis_job(
            job_id,
            status="running",
            stage=stage,
            message=message,
            progress_current=current,
            progress_total=total,
        )

    try:
        update_analysis_job(
            job_id,
            status="running",
            stage="fetching_repo",
            message="저장소 정보를 확인하는 중입니다.",
            progress_current=1,
            progress_total=GITHUB_ANALYSIS_PROGRESS_TOTAL,
        )
        repo_context = await fetch_repo_context(url, progress=progress)
        metadata = repo_context.metadata

        update_analysis_job(
            job_id,
            status="running",
            stage="summarizing",
            message="면접 질문으로 이어질 소재를 뽑는 중입니다.",
            progress_current=6,
            progress_total=GITHUB_ANALYSIS_PROGRESS_TOTAL,
        )
        previous_repository = get_github_repository_by_repo_key(user_key, metadata.repo_key)
        github_result = await get_llm().summarize_github(repo_context.text)
        user_summary, detail_summary = split_tagged_sections(github_result, "USER_SUMMARY", "DETAIL")
        github_summary = detail_summary or github_result
        change_summary = "첫 분석 스냅샷입니다."
        if previous_repository and previous_repository.get("summary"):
            change_summary = await get_llm().summarize_github_changes(
                previous_repository.get("summary", ""),
                github_summary,
            )
        if not user_summary:
            user_summary = (
                "요약\n"
                "- GitHub 분석을 저장했습니다.\n"
                "- 상세 내용을 화면에서 확인할 수 있습니다.\n\n"
                "다음 단계: 공고 추가 후 면접 탭에서 질문 후보 만들기"
            )

        update_analysis_job(
            job_id,
            status="running",
            stage="saving",
            message="분석 결과를 저장하는 중입니다.",
            progress_current=7,
            progress_total=GITHUB_ANALYSIS_PROGRESS_TOTAL,
        )
        upsert_github_repository_snapshot(
            user_key,
            url=metadata.url,
            title=metadata.title,
            repo_key=metadata.repo_key,
            summary=github_summary,
            change_summary=change_summary,
            default_branch=metadata.default_branch,
            commit_sha=metadata.commit_sha,
            commit_date=metadata.commit_date,
        )
        update_analysis_job(
            job_id,
            status="completed",
            stage="completed",
            message="GitHub 분석이 완료되었습니다.",
            progress_current=GITHUB_ANALYSIS_PROGRESS_TOTAL,
            progress_total=GITHUB_ANALYSIS_PROGRESS_TOTAL,
            result_data=github_payload(user_key),
            finished=True,
        )
        save_agent_chat_message(
            user_key,
            role="assistant",
            content="GitHub 분석이 완료됐어.",
            action="github_analysis_completed",
        )
        from services.agent import run_pending_agent_commands_for_job

        await run_pending_agent_commands_for_job(user_key, job_id)
    except ValueError as exc:
        fail_github_analysis_job(job_id, "validation_error", str(exc))
        save_agent_chat_message(
            user_key,
            role="assistant",
            content=f"GitHub 분석에 실패했어.\n\n{str(exc)[:500]}",
            action="github_analysis_failed",
        )
        from services.agent import fail_pending_agent_commands_for_job

        fail_pending_agent_commands_for_job(user_key, job_id, str(exc))
    except Exception as exc:
        fail_github_analysis_job(job_id, classify_github_analysis_error(exc), str(exc))
        save_agent_chat_message(
            user_key,
            role="assistant",
            content=f"GitHub 분석에 실패했어.\n\n{str(exc)[:500]}",
            action="github_analysis_failed",
        )
        from services.agent import fail_pending_agent_commands_for_job

        fail_pending_agent_commands_for_job(user_key, job_id, str(exc))


def fail_github_analysis_job(job_id: int, error_type: str, error_message: str) -> None:
    update_analysis_job(
        job_id,
        status="failed",
        stage="failed",
        message="GitHub 분석에 실패했습니다.",
        error_type=error_type,
        error_message=error_message[:500],
        finished=True,
    )


def classify_github_analysis_error(exc: Exception) -> str:
    name = exc.__class__.__name__.lower()
    if "timeout" in name:
        return "timeout"
    if "http" in name or "network" in name or "connect" in name:
        return "github_fetch_error"
    return "server_error"


def remove_github_repository(index: int, user_key: str | None = None) -> WorkflowResult:
    resolved_user_key = user_key or default_user_key()
    blocked = ensure_context_mutable(resolved_user_key)
    if blocked:
        blocked.data = github_payload(resolved_user_key)
        return blocked

    deleted = delete_github_repository(resolved_user_key, index)
    session = load_user_session(resolved_user_key)
    if not deleted:
        return WorkflowResult(
            messages=["해당 번호의 GitHub 저장소를 찾지 못했습니다."],
            status=session_status(session, user_key=resolved_user_key),
            data=github_payload(resolved_user_key),
            ok=False,
        )

    return WorkflowResult(
        messages=[f"[{deleted['index']}] {deleted['title']} GitHub 저장소를 삭제했습니다."],
        status=session_status(session, user_key=resolved_user_key),
        data=github_payload(resolved_user_key),
    )


def rename_github_repository(index: int, alias: str, user_key: str | None = None) -> WorkflowResult:
    resolved_user_key = user_key or default_user_key()
    blocked = ensure_context_mutable(resolved_user_key)
    if blocked:
        blocked.data = github_payload(resolved_user_key)
        return blocked

    repository = update_github_repository_alias(resolved_user_key, index, alias)
    session = load_user_session(resolved_user_key)
    if not repository:
        return WorkflowResult(
            messages=["해당 번호의 GitHub 저장소를 찾지 못했습니다."],
            status=session_status(session, user_key=resolved_user_key),
            data=github_payload(resolved_user_key),
            ok=False,
        )

    display_name = repository["display_name"]
    return WorkflowResult(
        messages=[f"[{repository['index']}] {display_name} 별명을 저장했습니다."],
        status=session_status(session, user_key=resolved_user_key),
        data=github_payload(resolved_user_key),
    )


def github_title(url: str) -> str:
    cleaned = url.strip().rstrip("/")
    if not cleaned:
        return "GitHub 저장소"
    parts = [part for part in cleaned.split("/") if part]
    if len(parts) >= 2:
        return "/".join(parts[-2:])
    return cleaned
