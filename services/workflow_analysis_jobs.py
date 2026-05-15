from db.repository import (
    get_active_analysis_job,
    get_active_analysis_jobs,
    get_analysis_job,
    load_user_session,
)
from services.workflow_common import WorkflowResult, default_user_key, session_status


def analysis_job_payload(job: dict | None) -> dict:
    return {"job": job}


def active_analysis_jobs_payload(jobs: dict[str, dict]) -> dict:
    return {"jobs": jobs}


def read_analysis_job(job_id: int, user_key: str | None = None) -> WorkflowResult:
    resolved_user_key = user_key or default_user_key()
    job = get_analysis_job(resolved_user_key, job_id)
    session = load_user_session(resolved_user_key)
    if not job:
        return WorkflowResult(
            messages=["작업을 찾지 못했습니다."],
            status=session_status(session, user_key=resolved_user_key),
            data=analysis_job_payload(None),
            ok=False,
        )

    return WorkflowResult(
        messages=[job["message"] or "작업 상태입니다."],
        status=session_status(session, user_key=resolved_user_key),
        data=analysis_job_payload(job),
    )


def read_active_analysis_job(kind: str, user_key: str | None = None) -> WorkflowResult:
    resolved_user_key = user_key or default_user_key()
    job = get_active_analysis_job(resolved_user_key, kind)
    session = load_user_session(resolved_user_key)
    message = "진행 중인 작업입니다." if job else "진행 중인 작업이 없습니다."
    return WorkflowResult(
        messages=[message],
        status=session_status(session, user_key=resolved_user_key),
        data=analysis_job_payload(job),
    )


def read_active_analysis_jobs(user_key: str | None = None) -> WorkflowResult:
    resolved_user_key = user_key or default_user_key()
    jobs = get_active_analysis_jobs(resolved_user_key)
    session = load_user_session(resolved_user_key)
    message = "진행 중인 작업 목록입니다." if jobs else "진행 중인 작업이 없습니다."
    return WorkflowResult(
        messages=[message],
        status=session_status(session, user_key=resolved_user_key),
        data=active_analysis_jobs_payload(jobs),
    )
