from db.repository import (
    create_github_repository,
    delete_github_repository,
    get_github_repositories,
    load_user_session,
    update_user_fields,
)
from services.formatting import build_progress_message, split_tagged_sections
from services.github import fetch_repo_context
from services.workflow_common import (
    WorkflowResult,
    ensure_context_mutable,
    get_llm,
    default_user_key,
    session_status,
)


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


async def analyze_github(url: str, user_key: str | None = None) -> WorkflowResult:
    resolved_user_key = user_key or default_user_key()
    blocked = ensure_context_mutable(resolved_user_key)
    if blocked:
        blocked.data = github_payload(resolved_user_key)
        return blocked

    github_url = url.strip()
    try:
        repo_context = await fetch_repo_context(github_url)
        github_result = await get_llm().summarize_github(repo_context)
        user_summary, detail_summary = split_tagged_sections(github_result, "USER_SUMMARY", "DETAIL")
        github_summary = detail_summary or github_result
        if not user_summary:
            user_summary = (
                "요약\n"
                "- GitHub 분석을 저장했습니다.\n"
                "- 상세 내용을 화면에서 확인할 수 있습니다.\n\n"
                "다음 단계: 공고 추가 후 면접 탭에서 질문 후보 만들기"
            )
    except Exception as exc:
        session = load_user_session(resolved_user_key)
        return WorkflowResult(
            messages=[
                "GitHub 분석에 실패했습니다.\n\n"
                "잠시 후 같은 GitHub URL로 다시 시도해주세요.\n\n"
                f"원인: {str(exc)[:300]}"
            ],
            status=session_status(session, user_key=resolved_user_key),
            data=github_payload(resolved_user_key),
            ok=False,
        )

    update_user_fields(
        resolved_user_key,
        github_url=github_url,
        github_summary=github_summary,
    )
    repository = create_github_repository(
        resolved_user_key,
        url=github_url,
        title=github_title(github_url),
        summary=github_summary,
    )
    session = load_user_session(resolved_user_key)
    return WorkflowResult(
        messages=[
            f"GitHub 저장소를 분석하고 보관했습니다.\n\n[{repository['index']}] {repository['title']}\n\n"
            f"{user_summary}\n\n"
            "GitHub 자료가 변경되었습니다.\n"
            "면접 탭에서 질문 후보를 새로 만들면 변경된 GitHub 자료가 반영됩니다.\n\n"
            f"{build_progress_message(session)}"
        ],
        status=session_status(session, user_key=resolved_user_key),
        data=github_payload(resolved_user_key),
    )


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


def github_title(url: str) -> str:
    cleaned = url.strip().rstrip("/")
    if not cleaned:
        return "GitHub 저장소"
    parts = [part for part in cleaned.split("/") if part]
    if len(parts) >= 2:
        return "/".join(parts[-2:])
    return cleaned
