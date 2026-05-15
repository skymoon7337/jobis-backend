from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends

from api.dependencies import request_user_key
from api.schemas import (
    GithubAliasRequest,
    GithubAnalyzeRequest,
    InterviewAnswerRequest,
    InterviewQuestionPlanRequest,
    InterviewStartRequest,
    JobMetaRequest,
    JobRequest,
    ProfileRequest,
    ResumeRequest,
)
from services.workflow import (
    create_bonus_question_job,
    create_github_analysis_job,
    create_final_review_job,
    create_job_posting_job,
    create_question_plan_job,
    end_interview,
    get_context,
    get_github_analysis,
    get_interview,
    get_interview_review,
    get_status,
    list_jobs,
    list_github_repositories,
    next_interview_question,
    read_active_analysis_job,
    read_active_analysis_jobs,
    read_analysis_job,
    remove_job,
    remove_github_repository,
    rename_job,
    rename_github_repository,
    reset_context,
    run_bonus_question_job,
    save_profile,
    save_resume,
    select_job,
    start_interview,
    run_github_analysis_job,
    run_final_review_job,
    run_job_posting_job,
    run_question_plan_job,
    skip_interview_question,
    submit_interview_answer,
    update_job_meta,
)


router = APIRouter(prefix="/api")
UserKey = Annotated[str, Depends(request_user_key)]


@router.get("/status")
def status(user_key: UserKey) -> dict:
    return get_status(user_key).to_dict()


@router.get("/context")
def context_latest(user_key: UserKey) -> dict:
    return get_context(user_key).to_dict()


@router.post("/context/reset")
def context_reset(user_key: UserKey) -> dict:
    return reset_context(user_key).to_dict()


@router.post("/profile")
def profile(payload: ProfileRequest, user_key: UserKey) -> dict:
    return save_profile(payload.profile, user_key).to_dict()


@router.post("/resume")
def resume(payload: ResumeRequest, user_key: UserKey) -> dict:
    return save_resume(payload.resume, user_key).to_dict()


@router.get("/jobs")
def jobs(user_key: UserKey) -> dict:
    return list_jobs(user_key).to_dict()


@router.post("/jobs/analyze-jobs")
async def create_job_analysis_job(
    payload: JobRequest,
    background_tasks: BackgroundTasks,
    user_key: UserKey,
) -> dict:
    result, should_start = create_job_posting_job(payload.text, user_key)
    if should_start and result.data.get("job"):
        background_tasks.add_task(
            run_job_posting_job,
            result.data["job"]["id"],
            user_key,
            payload.text.strip(),
        )
    return result.to_dict()


@router.post("/jobs/{index}/analyze-jobs")
async def update_job_analysis_job(
    index: int,
    payload: JobRequest,
    background_tasks: BackgroundTasks,
    user_key: UserKey,
) -> dict:
    result, should_start = create_job_posting_job(payload.text, user_key, replace_index=index)
    if should_start and result.data.get("job"):
        background_tasks.add_task(
            run_job_posting_job,
            result.data["job"]["id"],
            user_key,
            payload.text.strip(),
            index,
        )
    return result.to_dict()


@router.post("/jobs/{index}/select")
def select_job_route(index: int, user_key: UserKey) -> dict:
    return select_job(index, user_key).to_dict()


@router.delete("/jobs/{index}")
def delete_job_route(index: int, user_key: UserKey) -> dict:
    return remove_job(index, user_key).to_dict()


@router.patch("/jobs/{index}")
def update_job_meta_route(index: int, payload: JobMetaRequest, user_key: UserKey) -> dict:
    return update_job_meta(
        index,
        alias=payload.alias,
        source_url=payload.source_url,
        user_key=user_key,
    ).to_dict()


@router.get("/github/latest")
def github_latest(user_key: UserKey) -> dict:
    return get_github_analysis(user_key).to_dict()


@router.get("/github")
def github_list(user_key: UserKey) -> dict:
    return list_github_repositories(user_key).to_dict()


@router.post("/github/analyze-jobs")
async def github_analyze_job(
    payload: GithubAnalyzeRequest,
    background_tasks: BackgroundTasks,
    user_key: UserKey,
) -> dict:
    result, should_start = create_github_analysis_job(payload.url, user_key)
    if should_start and result.data.get("job"):
        background_tasks.add_task(
            run_github_analysis_job,
            result.data["job"]["id"],
            user_key,
            payload.url.strip(),
        )
    return result.to_dict()


@router.delete("/github/{index}")
def github_delete(index: int, user_key: UserKey) -> dict:
    return remove_github_repository(index, user_key).to_dict()


@router.patch("/github/{index}")
def github_rename(index: int, payload: GithubAliasRequest, user_key: UserKey) -> dict:
    return rename_github_repository(index, payload.alias, user_key).to_dict()


@router.get("/analysis-jobs/active")
def analysis_job_active(user_key: UserKey, kind: str | None = None) -> dict:
    if kind:
        return read_active_analysis_job(kind, user_key).to_dict()
    return read_active_analysis_jobs(user_key).to_dict()


@router.get("/analysis-jobs/{job_id}")
def analysis_job_latest(job_id: int, user_key: UserKey) -> dict:
    return read_analysis_job(job_id, user_key).to_dict()


@router.get("/interview")
def interview_latest(user_key: UserKey) -> dict:
    return get_interview(user_key).to_dict()


@router.get("/interview/review")
def interview_review(user_key: UserKey) -> dict:
    return get_interview_review(user_key).to_dict()


@router.post("/interview/plan-jobs")
async def interview_plan_job(
    payload: InterviewQuestionPlanRequest,
    background_tasks: BackgroundTasks,
    user_key: UserKey,
) -> dict:
    result, should_start = create_question_plan_job(
        job_index=payload.job_index,
        github_indices=payload.github_indices,
        question_counts=payload.question_counts,
        user_key=user_key,
    )
    if should_start and result.data.get("job"):
        background_tasks.add_task(
            run_question_plan_job,
            result.data["job"]["id"],
            user_key,
            question_counts=payload.question_counts,
        )
    return result.to_dict()


@router.post("/interview/start")
async def interview_start(user_key: UserKey, payload: InterviewStartRequest | None = None) -> dict:
    return (
        await start_interview(
            payload.job_index if payload else None,
            payload.github_indices if payload else None,
            [question.model_dump() for question in payload.questions] if payload else None,
            user_key,
        )
    ).to_dict()


@router.post("/interview/answer")
def interview_answer(payload: InterviewAnswerRequest, user_key: UserKey) -> dict:
    return submit_interview_answer(payload.answer, user_key).to_dict()


@router.post("/interview/next")
async def interview_next(user_key: UserKey) -> dict:
    return (await next_interview_question(user_key)).to_dict()


@router.post("/interview/skip")
def interview_skip(user_key: UserKey) -> dict:
    return skip_interview_question(user_key).to_dict()


@router.post("/interview/review-jobs")
async def interview_review_job(background_tasks: BackgroundTasks, user_key: UserKey) -> dict:
    result, should_start = create_final_review_job(user_key)
    if should_start and result.data.get("job"):
        background_tasks.add_task(
            run_final_review_job,
            result.data["job"]["id"],
            user_key,
    )
    return result.to_dict()


@router.post("/interview/followup-jobs")
async def interview_followup_job(background_tasks: BackgroundTasks, user_key: UserKey) -> dict:
    result, should_start = create_bonus_question_job("followup", user_key)
    if should_start and result.data.get("job"):
        background_tasks.add_task(
            run_bonus_question_job,
            result.data["job"]["id"],
            user_key,
            "followup",
    )
    return result.to_dict()


@router.post("/interview/another-jobs")
async def interview_another_job(background_tasks: BackgroundTasks, user_key: UserKey) -> dict:
    result, should_start = create_bonus_question_job("another", user_key)
    if should_start and result.data.get("job"):
        background_tasks.add_task(
            run_bonus_question_job,
            result.data["job"]["id"],
            user_key,
            "another",
        )
    return result.to_dict()


@router.post("/interview/end")
def interview_end(user_key: UserKey) -> dict:
    return end_interview(user_key).to_dict()
