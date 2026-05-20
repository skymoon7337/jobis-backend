import hashlib

from db.repository import (
    create_analysis_job,
    get_active_analysis_job,
    get_latest_completed_analysis_job_by_input,
    load_user_session,
    save_agent_chat_message,
    update_analysis_job,
)
from services.workflow_common import WorkflowResult, default_user_key, get_llm, session_status


CONTEXT_ANALYSIS_JOB_KIND = "context_analysis"
CONTEXT_ANALYSIS_PROGRESS_TOTAL = 4


def content_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def context_analysis_input(session) -> dict[str, object]:
    return {
        "profile_hash": content_digest(session.profile),
        "resume_hash": content_digest(session.resume),
        "github_hash": content_digest(session.github_summary),
        "job_hash": content_digest(session.job_posting),
    }


def create_context_analysis_job(user_key: str | None = None, force: bool = False) -> tuple[WorkflowResult, bool]:
    resolved_user_key = user_key or default_user_key()
    session = load_user_session(resolved_user_key)
    if not session.has_context():
        return (
            WorkflowResult(
                messages=["분석할 자료가 없습니다. 프로필, 자소서, GitHub 분석, 공고 중 하나를 먼저 저장해주세요."],
                status=session_status(session, user_key=resolved_user_key),
                data={"job": None},
                ok=False,
            ),
            False,
        )

    input_data = context_analysis_input(session)
    if not force:
        cached_job = get_latest_completed_analysis_job_by_input(
            resolved_user_key,
            CONTEXT_ANALYSIS_JOB_KIND,
            input_data,
        )
        if cached_job:
            return (
                WorkflowResult(
                    messages=["같은 자료로 만든 기존 분석 결과를 불러왔습니다."],
                    status=session_status(session, user_key=resolved_user_key),
                    data={"job": cached_job, "cached": True},
                ),
                False,
            )

    active_job = get_active_analysis_job(resolved_user_key, CONTEXT_ANALYSIS_JOB_KIND)
    if active_job:
        return (
            WorkflowResult(
                messages=["이미 자료 분석이 진행 중입니다."],
                status=session_status(session, user_key=resolved_user_key),
                data={"job": active_job},
            ),
            False,
        )

    job = create_analysis_job(
        resolved_user_key,
        kind=CONTEXT_ANALYSIS_JOB_KIND,
        input_data=input_data,
        stage="queued",
        message="자료 분석을 준비하고 있습니다.",
        progress_current=0,
        progress_total=CONTEXT_ANALYSIS_PROGRESS_TOTAL,
    )
    return (
        WorkflowResult(
            messages=["자료 분석 작업을 시작했습니다."],
            status=session_status(session, user_key=resolved_user_key),
            data={"job": job},
        ),
        True,
    )


async def run_context_analysis_job(job_id: int, user_key: str) -> None:
    try:
        update_analysis_job(
            job_id,
            status="running",
            stage="collecting_context",
            message="저장된 자료를 모으는 중입니다.",
            progress_current=1,
            progress_total=CONTEXT_ANALYSIS_PROGRESS_TOTAL,
        )
        session = load_user_session(user_key)
        if not session.has_context():
            raise ValueError("분석할 자료가 없습니다.")

        update_analysis_job(
            job_id,
            status="running",
            stage="analyzing",
            message="공고, GitHub, 자소서의 연결점을 분석하는 중입니다.",
            progress_current=2,
            progress_total=CONTEXT_ANALYSIS_PROGRESS_TOTAL,
        )
        analysis = await get_llm().analyze_context(session.build_context())

        update_analysis_job(
            job_id,
            status="running",
            stage="saving",
            message="분석 결과를 저장하는 중입니다.",
            progress_current=3,
            progress_total=CONTEXT_ANALYSIS_PROGRESS_TOTAL,
        )
        update_analysis_job(
            job_id,
            status="completed",
            stage="completed",
            message="자료 분석이 완료되었습니다.",
            progress_current=CONTEXT_ANALYSIS_PROGRESS_TOTAL,
            progress_total=CONTEXT_ANALYSIS_PROGRESS_TOTAL,
            result_data={"analysis": analysis, "preview": analysis[:1600]},
            finished=True,
        )
        save_agent_chat_message(
            user_key,
            role="assistant",
            content=f"분석이 완료됐어.\n\n{analysis[:1600]}",
            action="context_analysis_completed",
        )
    except ValueError as exc:
        fail_context_analysis_job(job_id, "validation_error", str(exc))
        save_agent_chat_message(
            user_key,
            role="assistant",
            content=f"자료 분석에 실패했어.\n\n{str(exc)[:500]}",
            action="context_analysis_failed",
        )
    except Exception as exc:
        fail_context_analysis_job(job_id, classify_context_analysis_error(exc), str(exc))
        save_agent_chat_message(
            user_key,
            role="assistant",
            content=f"자료 분석에 실패했어.\n\n{str(exc)[:500]}",
            action="context_analysis_failed",
        )


def fail_context_analysis_job(job_id: int, error_type: str, error_message: str) -> None:
    update_analysis_job(
        job_id,
        status="failed",
        stage="failed",
        message="자료 분석에 실패했습니다.",
        error_type=error_type,
        error_message=error_message[:500],
        finished=True,
    )


def classify_context_analysis_error(exc: Exception) -> str:
    name = exc.__class__.__name__.lower()
    if "timeout" in name:
        return "timeout"
    return "server_error"
