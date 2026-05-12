from typing import Annotated

from fastapi import APIRouter, Depends

from api.dependencies import request_user_key
from api.schemas import (
    GithubAnalyzeRequest,
    InterviewAnswerRequest,
    InterviewQuestionPlanRequest,
    InterviewStartRequest,
    JobRequest,
    ProfileRequest,
    ResumeRequest,
)
from services.workflow import (
    analyze_github,
    end_interview,
    generate_bonus_question,
    get_context,
    get_github_analysis,
    get_interview,
    generate_interview_question_candidates,
    get_interview_review,
    get_status,
    list_jobs,
    list_github_repositories,
    next_interview_question,
    remove_job,
    remove_github_repository,
    reset_context,
    save_job,
    save_profile,
    save_resume,
    select_job,
    start_interview,
    submit_interview_answer,
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


@router.post("/jobs")
async def create_job(payload: JobRequest, user_key: UserKey) -> dict:
    return (await save_job(payload.text, user_key)).to_dict()


@router.post("/jobs/{index}/select")
def select_job_route(index: int, user_key: UserKey) -> dict:
    return select_job(index, user_key).to_dict()


@router.delete("/jobs/{index}")
def delete_job_route(index: int, user_key: UserKey) -> dict:
    return remove_job(index, user_key).to_dict()


@router.get("/github/latest")
def github_latest(user_key: UserKey) -> dict:
    return get_github_analysis(user_key).to_dict()


@router.get("/github")
def github_list(user_key: UserKey) -> dict:
    return list_github_repositories(user_key).to_dict()


@router.post("/github/analyze")
async def github_analyze(payload: GithubAnalyzeRequest, user_key: UserKey) -> dict:
    return (await analyze_github(payload.url, user_key)).to_dict()


@router.delete("/github/{index}")
def github_delete(index: int, user_key: UserKey) -> dict:
    return remove_github_repository(index, user_key).to_dict()


@router.get("/interview")
def interview_latest(user_key: UserKey) -> dict:
    return get_interview(user_key).to_dict()


@router.get("/interview/review")
def interview_review(user_key: UserKey) -> dict:
    return get_interview_review(user_key).to_dict()


@router.post("/interview/plan")
async def interview_plan(payload: InterviewQuestionPlanRequest, user_key: UserKey) -> dict:
    return (
        await generate_interview_question_candidates(
            job_index=payload.job_index,
            github_indices=payload.github_indices,
            question_counts=payload.question_counts,
            user_key=user_key,
        )
    ).to_dict()


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


@router.post("/interview/followup")
async def interview_followup(user_key: UserKey) -> dict:
    return (await generate_bonus_question("followup", user_key)).to_dict()


@router.post("/interview/another")
async def interview_another(user_key: UserKey) -> dict:
    return (await generate_bonus_question("another", user_key)).to_dict()


@router.post("/interview/end")
def interview_end(user_key: UserKey) -> dict:
    return end_interview(user_key).to_dict()
