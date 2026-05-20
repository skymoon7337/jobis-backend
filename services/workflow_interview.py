import re
from datetime import datetime, timedelta
from typing import Any

from db.repository import (
    create_analysis_job,
    create_interview_session,
    get_active_analysis_job,
    get_active_interview_snapshot,
    get_github_repositories_by_indices,
    get_latest_completed_analysis_job_by_input,
    get_job_posting_by_index,
    get_latest_feedback_summary,
    get_latest_weakness_summary,
    get_memory_items,
    get_recent_sessions,
    get_recent_turns,
    get_weakness_items,
    load_user_session,
    save_agent_chat_message,
    save_interview_question,
    save_interview_questions,
    save_interview_turn,
    update_analysis_job,
    update_interview_session,
    update_user_fields,
)
from services.formatting import format_interview_feedback, split_final_feedback
from services.session import InterviewTurn, PlannedQuestion, UserSession
from services.workflow_common import (
    MAX_TURNS,
    WorkflowResult,
    get_llm,
    default_user_key,
    session_status,
)
from services.workflow_weakness import (
    record_final_review_memory,
    record_interview_turn_memory,
    record_weakness_learning_from_review,
)

QUESTION_TYPES = ("CS 기본기", "언어", "기술스택", "프로젝트/GitHub", "프로젝트/자소서")
QUESTION_PLAN_JOB_KIND = "question_plan"
QUESTION_PLAN_PROGRESS_TOTAL = 6
FINAL_REVIEW_JOB_KIND = "final_review"
FINAL_REVIEW_PROGRESS_TOTAL = 7
BONUS_QUESTION_JOB_KIND = "bonus_question"
BONUS_QUESTION_PROGRESS_TOTAL = 4


def question_label(question_type: str) -> str:
    labels = {
        "CS 기본기": "CS",
        "언어": "언어",
        "기술스택": "기술 스택",
        "프로젝트/GitHub": "프로젝트/GitHub",
        "프로젝트/자소서": "프로젝트/자소서",
    }
    return labels.get(question_type, question_type or "질문")


def question_section_number(question_type: str) -> int:
    section_numbers = {
        "CS 기본기": 1,
        "언어": 2,
        "기술스택": 3,
        "프로젝트/GitHub": 4,
        "프로젝트/자소서": 5,
    }
    return section_numbers.get(question_type, 0)


def base_display_id(display_id: str) -> str:
    return display_id.split("-f", maxsplit=1)[0] if display_id else ""


def strip_question_label(question: str) -> str:
    return re.sub(r"^\s*\[[^\]]+\]\s*", "", question).strip()


def format_question(question_type: str, question: str, display_id: str) -> str:
    return f"{display_id} [{question_label(question_type)}] {strip_question_label(question)}"


def format_planned_question(question: PlannedQuestion, display_id: str) -> str:
    return format_question(question.question_type, question.question, display_id)


def format_bonus_question(question_type: str, question: str, bonus_type: str, display_id: str) -> str:
    label = "꼬리질문" if bonus_type == "followup" else "추가질문"
    return f"{display_id} [{question_label(question_type)}/{label}] {strip_question_label(question)}"


def next_section_display_id(session: UserSession, question_type: str) -> str:
    section_number = question_section_number(question_type)
    next_count = session.section_question_counts.get(question_type, 0) + 1
    session.section_question_counts[question_type] = next_count
    return f"{section_number}-{next_count}" if section_number else str(next_count)


def next_followup_display_id(session: UserSession, parent_display_id: str) -> str:
    root_id = base_display_id(parent_display_id)
    next_count = session.followup_counts.get(root_id, 0) + 1
    session.followup_counts[root_id] = next_count
    return f"{root_id}-f{next_count}" if root_id else f"f{next_count}"


def parse_planned_questions(text: str, expected_types: list[str] | tuple[str, ...] | None = None) -> list[PlannedQuestion]:
    expected_types = tuple(expected_types or QUESTION_TYPES)
    type_aliases = {
        "cs기본기": "CS 기본기",
        "언어": "언어",
        "기술스택": "기술스택",
        "프로젝트/github": "프로젝트/GitHub",
        "프로젝트/자소서": "프로젝트/자소서",
    }
    questions: list[PlannedQuestion] = []
    for line in text.splitlines():
        match = re.match(r"^\s*\d+\.\s*(.+?)\s*[:：\-]\s*(.+?)\s*$", line)
        if not match:
            continue
        raw_type = re.sub(r"\s+", "", match.group(1)).lower()
        question_type = type_aliases.get(raw_type)
        if not question_type:
            continue
        questions.append(PlannedQuestion(question_type=question_type, question=strip_question_label(match.group(2))))

    if len(questions) != len(expected_types):
        return []

    for question, expected_type in zip(questions, expected_types, strict=True):
        if question.question_type != expected_type or not question.question:
            return []

    return questions


def question_plan_from_counts(question_counts: dict[str, int] | None) -> list[str]:
    if not question_counts:
        return list(QUESTION_TYPES)

    question_plan: list[str] = []
    for question_type in QUESTION_TYPES:
        question_plan.extend([question_type] * max(0, question_counts.get(question_type, 0)))

    return question_plan or list(QUESTION_TYPES)


def question_candidate_payload(questions: list[PlannedQuestion]) -> dict[str, Any]:
    return {
        "questions": [
            {
                "id": index,
                "question_type": question.question_type,
                "question": question.question,
            }
            for index, question in enumerate(questions, start=1)
        ]
    }


def create_question_plan_job(
    *,
    job_index: int | None = None,
    github_indices: list[int] | None = None,
    question_counts: dict[str, int] | None = None,
    user_key: str | None = None,
    force: bool = False,
) -> tuple[WorkflowResult, bool]:
    resolved_user_key = user_key or default_user_key()
    selected_github_indices = github_indices or []
    question_plan = question_plan_from_counts(question_counts)
    selected_question_count = sum(max(0, count) for count in (question_counts or {}).values())
    if job_index is None or not selected_github_indices or selected_question_count < 1 or not question_plan:
        session = load_user_session(resolved_user_key)
        return (
            WorkflowResult(
                messages=["질문 후보를 만들 공고, GitHub 프로젝트, 질문 구성을 선택해주세요."],
                status=session_status(session, user_key=resolved_user_key),
                data={"job": None},
                ok=False,
            ),
            False,
        )

    selection_error = apply_interview_context_selection(
        resolved_user_key,
        job_index=job_index,
        github_indices=selected_github_indices,
    )
    session = load_user_session(resolved_user_key)
    if selection_error:
        return (
            WorkflowResult(
                messages=[selection_error],
                status=session_status(session, user_key=resolved_user_key),
                data={"job": None},
                ok=False,
            ),
            False,
        )

    if not session.has_context():
        return (
            WorkflowResult(
                messages=["아직 면접 컨텍스트가 없습니다. 자소서 또는 GitHub 분석 중 하나는 먼저 넣어주세요."],
                status=session_status(session, user_key=resolved_user_key),
                data={"job": None},
                ok=False,
            ),
            False,
        )

    selected_job = get_job_posting_by_index(resolved_user_key, job_index)
    selected_repositories = get_github_repositories_by_indices(resolved_user_key, selected_github_indices)
    input_data = {
        "job_index": job_index,
        "job_id": selected_job["id"] if selected_job else None,
        "github_indices": selected_github_indices,
        "github_snapshot_ids": [repository.get("snapshot_id") for repository in selected_repositories],
        "question_counts": question_counts or {},
        "question_plan": question_plan,
    }
    if not force:
        cached_job = get_latest_completed_analysis_job_by_input(
            resolved_user_key,
            QUESTION_PLAN_JOB_KIND,
            input_data,
        )
        if cached_job:
            return (
                WorkflowResult(
                    messages=["같은 공고와 GitHub 구성으로 만든 기존 질문 후보를 불러왔습니다."],
                    status=session_status(session, user_key=resolved_user_key),
                    data={"job": cached_job, "cached": True},
                ),
                False,
            )

    active_job = get_active_analysis_job(resolved_user_key, QUESTION_PLAN_JOB_KIND)
    if active_job:
        return (
            WorkflowResult(
                messages=["이미 질문 후보 생성이 진행 중입니다."],
                status=session_status(session, user_key=resolved_user_key),
                data={"job": active_job},
            ),
            False,
        )

    job = create_analysis_job(
        resolved_user_key,
        kind=QUESTION_PLAN_JOB_KIND,
        input_data=input_data,
        stage="queued",
        message="질문 후보 생성을 준비하고 있습니다.",
        progress_current=0,
        progress_total=QUESTION_PLAN_PROGRESS_TOTAL,
    )
    return (
        WorkflowResult(
            messages=["질문 후보 생성 작업을 시작했습니다."],
            status=session_status(session, user_key=resolved_user_key),
            data={"job": job},
        ),
        True,
    )


async def run_question_plan_job(
    job_id: int,
    user_key: str,
    *,
    question_counts: dict[str, int] | None = None,
) -> None:
    try:
        update_analysis_job(
            job_id,
            status="running",
            stage="collecting_context",
            message="선택한 공고를 확인하는 중입니다.",
            progress_current=1,
            progress_total=QUESTION_PLAN_PROGRESS_TOTAL,
        )
        session = load_user_session(user_key)

        update_analysis_job(
            job_id,
            status="running",
            stage="matching_materials",
            message="선택한 GitHub 프로젝트를 반영하는 중입니다.",
            progress_current=2,
            progress_total=QUESTION_PLAN_PROGRESS_TOTAL,
        )
        question_plan = question_plan_from_counts(question_counts)

        update_analysis_job(
            job_id,
            status="running",
            stage="planning_types",
            message="질문 유형 구성을 정리하는 중입니다.",
            progress_current=3,
            progress_total=QUESTION_PLAN_PROGRESS_TOTAL,
        )
        context = session.build_interview_context()

        update_analysis_job(
            job_id,
            status="running",
            stage="generating",
            message="면접에서 물어볼 질문 후보를 생성하는 중입니다.",
            progress_current=4,
            progress_total=QUESTION_PLAN_PROGRESS_TOTAL,
        )
        question_result = await get_llm().generate_interview_questions(context, question_plan)

        update_analysis_job(
            job_id,
            status="running",
            stage="parsing",
            message="중복되거나 약한 질문을 정리하는 중입니다.",
            progress_current=5,
            progress_total=QUESTION_PLAN_PROGRESS_TOTAL,
        )
        questions = parse_planned_questions(question_result, question_plan)
        if not questions:
            raise ValueError("질문 후보를 정해진 형식으로 파싱하지 못했습니다.")

        update_analysis_job(
            job_id,
            status="completed",
            stage="completed",
            message="질문 후보 생성이 완료되었습니다.",
            progress_current=QUESTION_PLAN_PROGRESS_TOTAL,
            progress_total=QUESTION_PLAN_PROGRESS_TOTAL,
            result_data=question_candidate_payload(questions),
            finished=True,
        )
        save_agent_chat_message(
            user_key,
            role="assistant",
            content=f"질문 후보 {len(questions)}개를 만들었어.",
            action="question_plan_completed",
        )
        from services.agent import run_pending_agent_commands_for_job

        await run_pending_agent_commands_for_job(user_key, job_id)
    except ValueError as exc:
        fail_question_plan_job(job_id, "parse_error", str(exc))
        save_agent_chat_message(
            user_key,
            role="assistant",
            content=f"질문 후보 생성에 실패했어.\n\n{str(exc)[:500]}",
            action="question_plan_failed",
        )
        from services.agent import fail_pending_agent_commands_for_job

        fail_pending_agent_commands_for_job(user_key, job_id, str(exc))
    except Exception as exc:
        fail_question_plan_job(job_id, classify_question_plan_error(exc), str(exc))
        save_agent_chat_message(
            user_key,
            role="assistant",
            content=f"질문 후보 생성에 실패했어.\n\n{str(exc)[:500]}",
            action="question_plan_failed",
        )
        from services.agent import fail_pending_agent_commands_for_job

        fail_pending_agent_commands_for_job(user_key, job_id, str(exc))


def fail_question_plan_job(job_id: int, error_type: str, error_message: str) -> None:
    update_analysis_job(
        job_id,
        status="failed",
        stage="failed",
        message="질문 후보 생성에 실패했습니다.",
        error_type=error_type,
        error_message=error_message[:500],
        finished=True,
    )


def classify_question_plan_error(exc: Exception) -> str:
    name = exc.__class__.__name__.lower()
    if "timeout" in name:
        return "timeout"
    return "server_error"


def create_final_review_job(user_key: str | None = None) -> tuple[WorkflowResult, bool]:
    resolved_user_key = user_key or default_user_key()
    snapshot = get_active_interview_snapshot(resolved_user_key)
    session = load_user_session(resolved_user_key)
    if not snapshot:
        return (
            WorkflowResult(
                messages=["진행 중인 면접이 없습니다."],
                status=session_status(session, user_key=resolved_user_key),
                data={"job": None},
                ok=False,
            ),
            False,
        )

    restore_interview_session(session, snapshot)
    max_turns = len(session.planned_questions) or MAX_TURNS
    if not session.awaiting_choice:
        return (
            WorkflowResult(
                messages=["먼저 현재 질문에 답변해주세요."],
                status=session_status(session, user_key=resolved_user_key),
                data={"job": None},
                ok=False,
            ),
            False,
        )
    if session.turn_count < max_turns:
        return (
            WorkflowResult(
                messages=["아직 최종 리뷰를 만들 차례가 아닙니다. 다음 질문을 먼저 진행해주세요."],
                status=session_status(session, user_key=resolved_user_key),
                data={"job": None},
                ok=False,
            ),
            False,
        )
    if not session.history:
        return (
            WorkflowResult(
                messages=["평가할 답변이 없습니다."],
                status=session_status(session, user_key=resolved_user_key),
                data={"job": None},
                ok=False,
            ),
            False,
        )

    active_job = get_active_analysis_job(resolved_user_key, FINAL_REVIEW_JOB_KIND)
    if active_job:
        return (
            WorkflowResult(
                messages=["이미 리뷰 생성이 진행 중입니다."],
                status=session_status(session, user_key=resolved_user_key),
                data={"job": active_job},
            ),
            False,
        )

    job = create_analysis_job(
        resolved_user_key,
        kind=FINAL_REVIEW_JOB_KIND,
        input_data={"session_id": snapshot["id"], "turn_count": session.turn_count},
        stage="queued",
        message="면접 리뷰 생성을 준비하고 있습니다.",
        progress_current=0,
        progress_total=FINAL_REVIEW_PROGRESS_TOTAL,
    )
    return (
        WorkflowResult(
            messages=["면접 리뷰 생성 작업을 시작했습니다."],
            status=session_status(session, user_key=resolved_user_key),
            data={"job": job},
        ),
        True,
    )


async def run_final_review_job(job_id: int, user_key: str) -> None:
    try:
        update_analysis_job(
            job_id,
            status="running",
            stage="collecting_history",
            message="이번 세션의 질문과 답변을 모으는 중입니다.",
            progress_current=1,
            progress_total=FINAL_REVIEW_PROGRESS_TOTAL,
        )
        snapshot = get_active_interview_snapshot(user_key)
        session = load_user_session(user_key)
        if not snapshot:
            raise ValueError("진행 중인 면접이 없습니다.")
        restore_interview_session(session, snapshot)
        if not session.history:
            raise ValueError("평가할 답변이 없습니다.")

        update_analysis_job(
            job_id,
            status="running",
            stage="checking_answers",
            message="문항별 답변 흐름을 확인하는 중입니다.",
            progress_current=2,
            progress_total=FINAL_REVIEW_PROGRESS_TOTAL,
        )
        context = session.build_interview_context()
        history = session.build_history()

        update_analysis_job(
            job_id,
            status="running",
            stage="reviewing_answers",
            message="좋았던 답변과 부족한 답변을 나누는 중입니다.",
            progress_current=3,
            progress_total=FINAL_REVIEW_PROGRESS_TOTAL,
        )
        result = await get_llm().evaluate_full_interview(context=context, history=history)

        update_analysis_job(
            job_id,
            status="running",
            stage="finding_weaknesses",
            message="반복된 약점을 찾는 중입니다.",
            progress_current=4,
            progress_total=FINAL_REVIEW_PROGRESS_TOTAL,
        )
        answer_feedback, overall_feedback = split_final_feedback(result)
        weakness_summary = extract_weakness_summary(overall_feedback or result)

        update_analysis_job(
            job_id,
            status="running",
            stage="saving",
            message="리뷰를 저장하는 중입니다.",
            progress_current=6,
            progress_total=FINAL_REVIEW_PROGRESS_TOTAL,
        )
        update_interview_session(
            session.active_interview_session_id,
            status="completed",
            current_display_id="",
            awaiting_choice=False,
            summary=result,
            weakness_summary=weakness_summary,
        )
        learned_weaknesses = record_weakness_learning_from_review(
            user_key,
            session_id=session.active_interview_session_id,
            analysis_job_id=job_id,
            weakness_summary=weakness_summary,
            overall_feedback=overall_feedback or result,
        )
        review_memory = record_final_review_memory(
            user_key,
            session_id=session.active_interview_session_id,
            analysis_job_id=job_id,
            answer_feedback=answer_feedback,
            overall_feedback=overall_feedback or "",
            weakness_summary=weakness_summary,
        )
        update_analysis_job(
            job_id,
            status="completed",
            stage="completed",
            message="면접 리뷰가 완료되었습니다.",
            progress_current=FINAL_REVIEW_PROGRESS_TOTAL,
            progress_total=FINAL_REVIEW_PROGRESS_TOTAL,
            result_data={
                "answer_feedback": answer_feedback,
                "overall_feedback": overall_feedback,
                "interview": interview_payload(user_key),
                "review": interview_review_payload(user_key),
                "learned_weaknesses": learned_weaknesses,
                "review_memory": review_memory,
            },
            finished=True,
        )
        save_agent_chat_message(
            user_key,
            role="assistant",
            content=overall_feedback or answer_feedback or "면접 리뷰가 완료되었습니다.",
            action="final_review_completed",
        )
    except ValueError as exc:
        fail_final_review_job(job_id, "validation_error", str(exc))
        save_agent_chat_message(
            user_key,
            role="assistant",
            content=f"리뷰 생성에 실패했습니다.\n\n{str(exc)[:500]}",
            action="final_review_failed",
        )
    except Exception as exc:
        fail_final_review_job(job_id, classify_final_review_error(exc), str(exc))
        save_agent_chat_message(
            user_key,
            role="assistant",
            content=f"리뷰 생성에 실패했습니다.\n\n{str(exc)[:500]}",
            action="final_review_failed",
        )


def fail_final_review_job(job_id: int, error_type: str, error_message: str) -> None:
    update_analysis_job(
        job_id,
        status="failed",
        stage="failed",
        message="리뷰 생성에 실패했습니다.",
        error_type=error_type,
        error_message=error_message[:500],
        finished=True,
    )


def classify_final_review_error(exc: Exception) -> str:
    name = exc.__class__.__name__.lower()
    if "timeout" in name:
        return "timeout"
    return "server_error"


def build_context_snapshot(
    session: UserSession,
    *,
    user_key: str,
    job_index: int | None,
    github_indices: list[int],
) -> dict[str, Any]:
    job = get_job_posting_by_index(user_key, job_index) if job_index is not None else None
    repositories = get_github_repositories_by_indices(user_key, list(dict.fromkeys(github_indices))) if github_indices else []
    return {
        "profile": session.profile,
        "resume": session.resume,
        "job_title": job.get("display_name") or job["title"] if job else "선택 공고",
        "job_summary": job["summary"] if job else session.job_posting,
        "github_repositories": [
            {
                "title": repository["title"],
                "alias": repository.get("alias", ""),
                "display_name": repository.get("display_name") or repository["title"],
                "url": repository["url"],
                "repo_key": repository.get("repo_key", ""),
                "summary": repository["summary"],
                "change_summary": repository.get("change_summary", ""),
                "version": repository.get("version", 0),
                "default_branch": repository.get("default_branch", ""),
                "commit_sha": repository.get("commit_sha", ""),
                "commit_date": repository.get("commit_date"),
                "analyzed_at": repository.get("analyzed_at"),
            }
            for repository in repositories
        ],
    }


def sync_question_counters(session: UserSession, questions: list[dict[str, Any]]) -> None:
    session.section_question_counts.clear()
    session.followup_counts.clear()

    for question in questions:
        display_id = question.get("display_id", "")
        root_id = base_display_id(display_id)
        root_match = re.match(r"^\d+-(\d+)$", root_id)
        if root_match:
            question_type = question.get("question_type", "")
            section_count = int(root_match.group(1))
            session.section_question_counts[question_type] = max(
                session.section_question_counts.get(question_type, 0),
                section_count,
            )

        followup_match = re.match(r"^(.+)-f(\d+)$", display_id)
        if followup_match:
            parent_id = followup_match.group(1)
            followup_count = int(followup_match.group(2))
            session.followup_counts[parent_id] = max(
                session.followup_counts.get(parent_id, 0),
                followup_count,
            )


def restore_interview_session(session: UserSession, snapshot: dict[str, Any]) -> str:
    session.reset_interview()
    session.in_interview = True
    session.active_interview_session_id = snapshot["id"]

    questions = snapshot["questions"]
    turns = snapshot["turns"]
    sync_question_counters(session, questions)

    session.planned_questions = [
        PlannedQuestion(
            question_type=question["question_type"],
            question=question["question"],
            display_id=question["display_id"],
        )
        for question in questions
        if not question["is_bonus"]
    ]
    session.history = [
        InterviewTurn(
            question=turn["question"],
            answer=turn["answer"],
            feedback=turn["feedback"],
            question_type=turn["question_type"],
            display_id=turn["display_id"],
            is_bonus=turn["is_bonus"],
            bonus_type=turn["bonus_type"],
        )
        for turn in turns
    ]
    session.turn_count = sum(1 for turn in session.history if not turn.is_bonus)

    questions_by_display_id = {question["display_id"]: question for question in questions}
    current_display_id = snapshot.get("current_display_id", "")
    if current_display_id and current_display_id in questions_by_display_id:
        current_question = questions_by_display_id[current_display_id]
        session.current_question = current_question["question"]
        session.current_question_type = current_question["question_type"]
        session.current_display_id = current_question["display_id"]
        session.current_question_is_bonus = current_question["is_bonus"]
        session.current_bonus_type = current_question["bonus_type"]
        session.awaiting_choice = False
        return "question"

    if snapshot.get("awaiting_choice"):
        session.awaiting_choice = True
        return "choice"

    max_turns = len(session.planned_questions) or MAX_TURNS
    if session.turn_count >= max_turns:
        session.awaiting_choice = True
        return "choice"

    if session.turn_count < len(session.planned_questions):
        current_question = session.planned_questions[session.turn_count]
        session.current_question = current_question.question
        session.current_question_type = current_question.question_type
        session.current_display_id = current_question.display_id
        session.current_question_is_bonus = False
        session.current_bonus_type = ""
        session.awaiting_choice = False
        return "question"

    session.awaiting_choice = True
    return "choice"


def format_current_question(session: UserSession) -> str:
    if session.current_question_is_bonus:
        return format_bonus_question(
            session.current_question_type,
            session.current_question,
            session.current_bonus_type,
            session.current_display_id,
        )

    planned_question = PlannedQuestion(
        question_type=session.current_question_type,
        question=session.current_question,
        display_id=session.current_display_id,
    )
    return format_planned_question(planned_question, session.current_display_id)


def build_choice_options(session: UserSession) -> str:
    max_turns = len(session.planned_questions) or MAX_TURNS
    next_description = "전체 평가 생성" if session.turn_count >= max_turns else "다음 질문으로 이동"
    return (
        "다음 선택\n"
        f"다음 - {next_description}\n"
        "꼬리질문 - 방금 답변을 더 깊게 파기\n"
        "추가질문 - 같은 분야 질문 하나 더 받기"
    )


def build_choice_message(session: UserSession) -> str:
    return "답변을 저장했습니다.\n\n" + build_choice_options(session)


def extract_weakness_summary(feedback: str) -> str:
    match = re.search(
        r"(?:^|\n)약점 요약\s*:?\s*(.*?)(?=\n(?:다음 준비|\d+턴 면접이 종료되었습니다)\s*:?\s*|\Z)",
        feedback,
        flags=re.DOTALL,
    )
    if not match:
        return ""
    return format_interview_feedback(match.group(1).strip())


def interview_payload(user_key: str) -> dict[str, Any]:
    session = load_user_session(user_key)
    snapshot = get_active_interview_snapshot(user_key)
    if not snapshot:
        return {
            "active": False,
            "awaiting_choice": False,
            "current_question": "",
            "current_display_id": "",
            "current_question_type": "",
            "current_question_is_bonus": False,
            "turn_count": 0,
            "max_turns": MAX_TURNS,
            "history": [],
        }

    restore_interview_session(session, snapshot)
    max_turns = len(session.planned_questions) or MAX_TURNS
    return {
        "active": True,
        "awaiting_choice": session.awaiting_choice,
        "current_question": format_current_question(session) if session.current_question else "",
        "current_display_id": session.current_display_id,
        "current_question_type": session.current_question_type,
        "current_question_is_bonus": session.current_question_is_bonus,
        "turn_count": session.turn_count,
        "max_turns": max_turns,
        "history": [
            {
                "display_id": turn.display_id,
                "question_type": turn.question_type,
                "question": turn.question,
                "answer": turn.answer,
                "feedback": turn.feedback,
                "is_bonus": turn.is_bonus,
                "bonus_type": turn.bonus_type,
            }
            for turn in session.history
        ],
    }


def interview_review_payload(user_key: str) -> dict[str, Any]:
    feedback_summary = get_latest_feedback_summary(user_key)
    answer_feedback = ""
    overall_feedback = ""
    if feedback_summary:
        answer_feedback, overall_feedback = split_final_feedback(feedback_summary)

    return {
        "sessions": get_recent_sessions(user_key),
        "turns": [
            {
                "display_id": turn.display_id,
                "question_type": turn.question_type,
                "question": turn.question,
                "answer": turn.answer,
                "feedback": turn.feedback,
                "is_bonus": turn.is_bonus,
                "bonus_type": turn.bonus_type,
            }
            for turn in get_recent_turns(user_key)
        ],
        "latest_feedback": feedback_summary,
        "answer_feedback": answer_feedback,
        "overall_feedback": overall_feedback or "",
        "latest_weakness": get_latest_weakness_summary(user_key),
        "weaknesses": get_weakness_items(user_key),
        "memories": get_memory_items(user_key, limit=8),
    }


def get_interview(user_key: str | None = None) -> WorkflowResult:
    resolved_user_key = user_key or default_user_key()
    session = load_user_session(resolved_user_key)
    payload = interview_payload(resolved_user_key)
    message = "진행 중인 면접이 있습니다." if payload["active"] else "진행 중인 면접이 없습니다."
    return WorkflowResult(
        messages=[message],
        status=session_status(session, user_key=resolved_user_key),
        data=payload,
    )


def get_interview_review(user_key: str | None = None) -> WorkflowResult:
    resolved_user_key = user_key or default_user_key()
    session = load_user_session(resolved_user_key)
    payload = interview_review_payload(resolved_user_key)
    message = "저장된 면접 회고입니다." if payload["sessions"] or payload["turns"] else "저장된 면접 기록이 없습니다."
    return WorkflowResult(
        messages=[message],
        status=session_status(session, user_key=resolved_user_key),
        data=payload,
    )


def _normalize_history_search_text(value: str) -> str:
    normalized = value.lower()
    normalized = re.sub(r"web\s*socket", "websocket", normalized)
    normalized = re.sub(r"웹\s*소켓", "웹소켓", normalized)
    normalized = re.sub(r"[\s\-_./:：,，?？!()\[\]{}'\"`]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _history_search_keywords(query: str) -> list[str]:
    stopwords = {"관련", "있는", "있던", "있었던", "것만", "최근", "면접", "기록", "찾아줘"}
    normalized = _normalize_history_search_text(query)
    keywords = [
        keyword
        for keyword in normalized.split()
        if len(keyword) >= 2 and keyword not in stopwords
    ]
    normalized_phrase = " ".join(keywords)
    if len(keywords) > 1 and normalized_phrase not in keywords:
        keywords.insert(0, normalized_phrase)
    return list(dict.fromkeys(keywords))[:8]


def _history_search_query_filters(query: str) -> tuple[str, dict[str, Any]]:
    cleaned = query.strip()
    filters: dict[str, Any] = {}

    if re.search(r"(답변|피드백).*(있는|있던|있었|포함|만)", cleaned):
        filters["answered_only"] = True
        cleaned = re.sub(r"(답변|피드백)\s*(피드백)?\s*(있는|있던|있었던|포함|만|있는\s*것만)?", " ", cleaned)
        cleaned = re.sub(r"\b(있는|있던|있었던|것만)\b", " ", cleaned)

    week_match = re.search(r"최근\s*(\d+)\s*주", cleaned)
    month_match = re.search(r"최근\s*(\d+)\s*(개월|달)", cleaned)
    if week_match:
        filters["recent_days"] = int(week_match.group(1)) * 7
        cleaned = cleaned.replace(week_match.group(0), " ")
    elif month_match:
        filters["recent_days"] = int(month_match.group(1)) * 30
        cleaned = cleaned.replace(month_match.group(0), " ")
    elif re.search(r"최근\s*(한|1)\s*(개월|달)", cleaned):
        filters["recent_days"] = 30
        cleaned = re.sub(r"최근\s*(한|1)\s*(개월|달)", " ", cleaned)

    cleaned = re.sub(r"\b(관련|있는|있던|있었던|것만)\b", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned, filters


def _entry_matches_history_filters(entry: dict[str, Any], filters: dict[str, Any]) -> bool:
    if filters.get("answered_only") and entry.get("source_type") not in {"turn", "review"}:
        return False

    recent_days = filters.get("recent_days")
    if isinstance(recent_days, int) and recent_days > 0:
        created_at = entry.get("created_at")
        if not isinstance(created_at, datetime):
            return False
        now = datetime.now(created_at.tzinfo) if created_at.tzinfo else datetime.now()
        if created_at < now - timedelta(days=recent_days):
            return False

    return True


def _excerpt_around_keyword(text: str, keywords: list[str], max_length: int = 180) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= max_length:
        return compact

    normalized = _normalize_history_search_text(compact)
    first_index = -1
    for keyword in keywords:
        index = normalized.find(keyword)
        if index >= 0 and (first_index < 0 or index < first_index):
            first_index = index

    if first_index < 0:
        return f"{compact[:max_length].rstrip()}..."

    start = max(0, first_index - 60)
    end = min(len(compact), start + max_length)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(compact) else ""
    return f"{prefix}{compact[start:end].strip()}{suffix}"


def _review_feedback_entries(session_label: str, session_id: int, review_text: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    blocks = [
        block.strip()
        for block in re.split(r"\n\s*-{5,}\s*\n|문항별 피드백", review_text)
        if block.strip()
    ]
    for block in blocks:
        display_match = re.search(r"(?:▶\s*)?(\d+-\d+(?:-f\d+)?)", block)
        if not display_match:
            continue
        display_id = display_match.group(1)
        entries.append(
            {
                "source_type": "review",
                "session_id": session_id,
                "display_id": display_id,
                "title": f"{session_label} {display_id} 최종 리뷰".strip(),
                "content": block,
            }
        )
    return entries


def _history_entry_priority(source_type: str) -> int:
    priorities = {
        "turn": 3,
        "question": 2,
        "review": 1,
    }
    return priorities.get(source_type, 0)


INTERVIEW_HISTORY_INITIAL_LIMIT = 4
INTERVIEW_HISTORY_NEXT_LIMIT = 4


def _compact_filter_value(value: str, max_length: int = 28) -> str:
    compact = re.sub(r"\s+", " ", value).strip()
    return compact if len(compact) <= max_length else f"{compact[:max_length].rstrip()}..."


def _session_github_labels(session_item: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for repository in session_item.get("github_repositories", []):
        if not isinstance(repository, dict):
            continue
        label = (
            str(repository.get("display_name") or "")
            or str(repository.get("alias") or "")
            or str(repository.get("title") or "")
            or str(repository.get("repo_key") or "")
        ).strip()
        if label:
            labels.append(label)
    return list(dict.fromkeys(labels))


def _history_filter_suggestions(query: str, matches: list[dict[str, Any]]) -> list[dict[str, str]]:
    suggestions: list[dict[str, str]] = []
    cleaned_query = query.strip()

    def add(label: str, prompt: str) -> None:
        if label and prompt and all(item["label"] != label for item in suggestions):
            suggestions.append({"label": label, "prompt": prompt})

    add("최근 2주", f"{cleaned_query} 최근 2주 면접 기록 찾아줘")

    job_titles = [
        str(match.get("job_title") or "").strip()
        for match in matches
        if str(match.get("job_title") or "").strip()
    ]
    for job_title in list(dict.fromkeys(job_titles))[:2]:
        label = _compact_filter_value(job_title)
        add(label, f"{cleaned_query} {job_title} 면접 기록 찾아줘")

    github_labels: list[str] = []
    for match in matches:
        github_labels.extend(
            label for label in match.get("github_labels", []) if isinstance(label, str) and label
        )
    for github_label in list(dict.fromkeys(github_labels))[:2]:
        label = _compact_filter_value(github_label)
        add(label, f"{cleaned_query} {github_label} GitHub 면접 기록 찾아줘")

    add("답변 있는 것만", f"{cleaned_query} 답변 피드백 있는 면접 기록 찾아줘")
    return suggestions[:5]


def _merge_history_search_matches(scored: list[tuple[int, dict[str, Any]]], keywords: list[str]) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, str], dict[str, Any]] = {}
    source_labels = {
        "question": "질문",
        "review": "최종 리뷰",
        "turn": "답변/피드백",
    }
    source_order = ["question", "turn", "review"]

    for score, entry in sorted(scored, key=lambda item: item[0], reverse=True):
        session_id = int(entry.get("session_id") or 0)
        display_id = str(entry.get("display_id") or "")
        source_type = str(entry.get("source_type") or "")
        group_key = (session_id, display_id or source_type)
        current = grouped.get(group_key)
        source_label = source_labels.get(source_type, source_type or "기록")

        if not current:
            grouped[group_key] = {
                "entry": entry,
                "score": score,
                "source_types": [source_type],
                "sources": [source_label],
            }
            continue

        current["score"] += score
        if source_type not in current["source_types"]:
            current["source_types"].append(source_type)
            current["sources"].append(source_label)

        current_entry = current["entry"]
        if _history_entry_priority(source_type) > _history_entry_priority(str(current_entry.get("source_type") or "")):
            current["entry"] = entry

    matches: list[dict[str, Any]] = []
    for item in sorted(grouped.values(), key=lambda value: value["score"], reverse=True):
        entry = item["entry"]
        source_type = str(entry.get("source_type") or "")
        ordered_source_types = [
            source
            for source in source_order
            if source in item["source_types"]
        ] + [
            source
            for source in item["source_types"]
            if source not in source_order
        ]
        matches.append(
            {
                "source_type": source_type,
                "source_types": ordered_source_types,
                "sources": [source_labels.get(source, source or "기록") for source in ordered_source_types],
                "session_id": entry["session_id"],
                "display_id": entry["display_id"],
                "title": entry["title"],
                "excerpt": _excerpt_around_keyword(str(entry.get("content") or ""), keywords),
                "job_title": entry.get("job_title") or "",
                "created_at": entry.get("created_at"),
                "github_labels": entry.get("github_labels") or [],
                "score": item["score"],
            }
        )

    return matches


def search_interview_history(user_key: str | None = None, *, query: str = "") -> WorkflowResult:
    resolved_user_key = user_key or default_user_key()
    session = load_user_session(resolved_user_key)
    sessions = get_recent_sessions(resolved_user_key, limit=50)
    cleaned_query, filters = _history_search_query_filters(query)
    keywords = _history_search_keywords(cleaned_query)
    searched_counts = {"sessions": len(sessions), "questions": 0, "turns": 0, "reviews": 0}
    entries: list[dict[str, Any]] = []

    for session_item in sessions:
        session_id = int(session_item.get("id") or 0)
        session_label = f"세션 #{session_id}"
        job_title = str(session_item.get("job_title") or "").strip()
        github_labels = _session_github_labels(session_item)
        session_meta_text = "\n".join(
            part
            for part in (
                job_title,
                " ".join(github_labels),
            )
            if part.strip()
        )
        created_at = session_item.get("created_at")
        for question in session_item.get("questions", []):
            searched_counts["questions"] += 1
            display_id = str(question.get("display_id") or "")
            question_type = str(question.get("question_type") or "")
            question_text = str(question.get("question") or "")
            entries.append(
                {
                    "source_type": "question",
                    "session_id": session_id,
                    "display_id": display_id,
                    "title": f"{session_label} {display_id} 질문".strip(),
                    "content": f"{question_type}\n{question_text}",
                    "search_text": f"{session_meta_text}\n{question_type}\n{question_text}",
                    "job_title": job_title,
                    "created_at": created_at,
                    "github_labels": github_labels,
                }
            )

        for turn in session_item.get("turns", []):
            searched_counts["turns"] += 1
            display_id = str(turn.get("display_id") or "")
            question_type = str(turn.get("question_type") or "")
            entries.append(
                {
                    "source_type": "turn",
                    "session_id": session_id,
                    "display_id": display_id,
                    "title": f"{session_label} {display_id} 답변/피드백".strip(),
                    "content": (
                        f"{question_type}\n"
                        f"질문: {turn.get('question') or ''}\n"
                        f"답변: {turn.get('answer') or ''}\n"
                        f"피드백: {turn.get('feedback') or ''}"
                    ),
                    "search_text": (
                        f"{session_meta_text}\n"
                        f"{question_type}\n"
                        f"질문: {turn.get('question') or ''}\n"
                        f"답변: {turn.get('answer') or ''}\n"
                        f"피드백: {turn.get('feedback') or ''}"
                    ),
                    "job_title": job_title,
                    "created_at": created_at,
                    "github_labels": github_labels,
                }
            )

        review_text = "\n".join(
            part
            for part in (
                str(session_item.get("summary") or ""),
                str(session_item.get("weakness_summary") or ""),
            )
            if part.strip()
        )
        if review_text:
            searched_counts["reviews"] += 1
            review_entries = _review_feedback_entries(session_label, session_id, review_text)
            if review_entries:
                for entry in review_entries:
                    entry["search_text"] = f"{session_meta_text}\n{entry.get('content') or ''}"
                    entry["job_title"] = job_title
                    entry["created_at"] = created_at
                    entry["github_labels"] = github_labels
                entries.extend(review_entries)
            else:
                entries.append(
                    {
                        "source_type": "review",
                        "session_id": session_id,
                        "display_id": "",
                        "title": f"{session_label} 최종 리뷰",
                        "content": review_text,
                        "search_text": f"{session_meta_text}\n{review_text}",
                        "job_title": job_title,
                        "created_at": created_at,
                        "github_labels": github_labels,
                    }
                )

    scored: list[tuple[int, dict[str, Any]]] = []
    if keywords:
        for entry in entries:
            if not _entry_matches_history_filters(entry, filters):
                continue
            searchable_content = entry.get("search_text") or entry.get("content") or ""
            haystack = _normalize_history_search_text(
                f"{entry.get('title') or ''}\n{searchable_content}"
            )
            score = sum(haystack.count(keyword) for keyword in keywords)
            if score > 0:
                scored.append((score, entry))

    matches = _merge_history_search_matches(scored, keywords)

    searched_label = (
        f"질문 {searched_counts['questions']}개, 답변/피드백 {searched_counts['turns']}개, "
        f"최종 리뷰 {searched_counts['reviews']}개"
    )
    total_count = len(matches)
    visible_matches = matches[:INTERVIEW_HISTORY_INITIAL_LIMIT]
    next_matches = matches[
        INTERVIEW_HISTORY_INITIAL_LIMIT:INTERVIEW_HISTORY_INITIAL_LIMIT + INTERVIEW_HISTORY_NEXT_LIMIT
    ]
    display_query = cleaned_query or query.strip() or "검색어 없음"
    if not visible_matches:
        return WorkflowResult(
            messages=[
                (
                    f"`{display_query}` 관련 면접 기록을 찾지 못했어.\n\n"
                    f"검색 범위: {searched_label}\n"
                    "최근 리뷰로 임의 이동하지 않았고, 검색 결과가 없다는 상태로 남겨둘게."
                )
            ],
            status=session_status(session, user_key=resolved_user_key),
            data={
                "query": display_query,
                "matches": [],
                "match_count": 0,
                "total_count": 0,
                "shown_count": 0,
                "has_more": False,
                "next_matches": [],
                "refine_suggestions": [],
                "searched_counts": searched_counts,
                "target_view": "review",
            },
        )

    has_more = total_count > len(visible_matches)
    return WorkflowResult(
        messages=[
            (
                f"`{display_query}` 관련 면접 문항 {total_count}개를 찾았어. "
                f"관련도가 높은 {len(visible_matches)}개를 먼저 보여줄게.\n\n"
                "더 보거나 조건을 좁혀서 다시 찾을 수 있어."
            )
        ],
        status=session_status(session, user_key=resolved_user_key),
        data={
            "query": display_query,
            "matches": visible_matches,
            "match_count": len(visible_matches),
            "total_count": total_count,
            "shown_count": len(visible_matches),
            "has_more": has_more,
            "next_matches": next_matches,
            "refine_suggestions": _history_filter_suggestions(display_query, matches),
            "searched_counts": searched_counts,
            "target_view": "review",
            "selected_session_id": visible_matches[0]["session_id"],
        },
    )


async def start_interview(
    job_index: int | None = None,
    github_indices: list[int] | None = None,
    selected_questions: list[dict[str, str]] | None = None,
    user_key: str | None = None,
) -> WorkflowResult:
    resolved_user_key = user_key or default_user_key()
    selection_error = apply_interview_context_selection(
        resolved_user_key,
        job_index=job_index,
        github_indices=github_indices or [],
    )
    session = load_user_session(resolved_user_key)
    if selection_error:
        return WorkflowResult(
            messages=[selection_error],
            status=session_status(session, user_key=resolved_user_key),
            data=interview_payload(resolved_user_key),
            ok=False,
        )

    if not session.has_context():
        return WorkflowResult(
            messages=["아직 면접 컨텍스트가 없습니다. 자소서 또는 GitHub 분석 중 하나는 먼저 넣어주세요."],
            status=session_status(session, user_key=resolved_user_key),
            data=interview_payload(resolved_user_key),
            ok=False,
        )

    try:
        if selected_questions:
            planned_questions = [
                PlannedQuestion(
                    question_type=question["question_type"].strip(),
                    question=strip_question_label(question["question"]),
                )
                for question in selected_questions
                if question.get("question_type", "").strip() in QUESTION_TYPES
                and strip_question_label(question.get("question", ""))
            ]
            if len(planned_questions) != len(selected_questions) or not 1 <= len(planned_questions) <= 8:
                raise ValueError("선택한 질문 목록이 올바르지 않습니다.")
        else:
            question_result = await get_llm().generate_interview_questions(session.build_interview_context())
            planned_questions = parse_planned_questions(question_result)

        context_snapshot = build_context_snapshot(
            session,
            user_key=resolved_user_key,
            job_index=job_index,
            github_indices=github_indices or [],
        )
        session.reset_interview()
        session.in_interview = True
        session.active_interview_session_id = create_interview_session(
            resolved_user_key,
            context_snapshot=context_snapshot,
        )
        session.planned_questions = planned_questions
        if not session.planned_questions:
            raise ValueError("면접 질문을 준비하지 못했습니다.")

        for planned_question in session.planned_questions:
            planned_question.display_id = next_section_display_id(session, planned_question.question_type)

        save_interview_questions(
            session.active_interview_session_id,
            [
                {
                    "display_id": planned_question.display_id,
                    "question_type": planned_question.question_type,
                    "question": planned_question.question,
                    "is_bonus": False,
                    "bonus_type": "",
                }
                for planned_question in session.planned_questions
            ],
        )

        first_question = session.planned_questions[0]
        planned_count = len(session.planned_questions)
        session.current_question_type = first_question.question_type
        session.current_question = first_question.question
        session.current_display_id = first_question.display_id
        session.current_question_is_bonus = False
        session.current_bonus_type = ""
        session.awaiting_choice = False
        update_interview_session(
            session.active_interview_session_id,
            current_display_id=session.current_display_id,
            awaiting_choice=False,
        )
    except Exception as exc:
        if session.active_interview_session_id:
            update_interview_session(session.active_interview_session_id, status="failed", summary=str(exc))
        session = load_user_session(resolved_user_key)
        return WorkflowResult(
            messages=[
                "면접 질문 생성에 실패했습니다.\n\n"
                "잠시 후 다시 시도해주세요.\n\n"
                f"원인: {str(exc)[:300]}"
            ],
            status=session_status(session, user_key=resolved_user_key),
            data=interview_payload(resolved_user_key),
            ok=False,
        )

    session = load_user_session(resolved_user_key)
    return WorkflowResult(
        messages=[
            f"{planned_count}개 질문으로 면접을 시작했습니다.\n\n"
            + format_planned_question(first_question, first_question.display_id)
        ],
        status=session_status(session, user_key=resolved_user_key),
        data=interview_payload(resolved_user_key),
    )


def apply_interview_context_selection(
    user_key: str,
    *,
    job_index: int | None,
    github_indices: list[int],
) -> str:
    fields: dict[str, str] = {}
    if job_index is not None:
        job = get_job_posting_by_index(user_key, job_index)
        if not job:
            return "선택한 공고를 찾지 못했습니다."
        fields["job_posting"] = job["raw_text"]

    if github_indices:
        unique_indices = list(dict.fromkeys(github_indices))
        repositories = get_github_repositories_by_indices(user_key, unique_indices)
        if len(repositories) != len(unique_indices):
            return "선택한 GitHub 저장소 중 찾지 못한 항목이 있습니다."
        fields["github_url"] = "\n".join(repository["url"] for repository in repositories if repository["url"])
        fields["github_summary"] = "\n\n".join(
            format_github_repository_context(repository)
            for repository in repositories
        )

    if fields:
        update_user_fields(user_key, **fields)
    return ""


def format_github_repository_context(repository: dict[str, Any]) -> str:
    display_name = repository.get("display_name") or repository.get("title") or "GitHub 프로젝트"
    version = repository.get("version") or 0
    commit_sha = repository.get("commit_sha") or ""
    short_sha = commit_sha[:7] if commit_sha else ""
    metadata_lines = [
        f"[{display_name} v{version}]" if version else f"[{display_name}]",
        f"URL: {repository.get('url', '')}",
    ]
    if repository.get("default_branch"):
        metadata_lines.append(f"기본 브랜치: {repository['default_branch']}")
    if short_sha:
        metadata_lines.append(f"커밋: {short_sha}")
    if repository.get("analyzed_at"):
        metadata_lines.append(f"분석 시각: {repository['analyzed_at']}")
    if repository.get("change_summary"):
        metadata_lines.append(f"최근 변경 요약: {repository['change_summary']}")
    metadata_lines.append(repository.get("summary", ""))
    return "\n".join(line for line in metadata_lines if line)


def submit_interview_answer(answer: str, user_key: str | None = None) -> WorkflowResult:
    resolved_user_key = user_key or default_user_key()
    snapshot = get_active_interview_snapshot(resolved_user_key)
    session = load_user_session(resolved_user_key)
    if not snapshot:
        return WorkflowResult(
            messages=["진행 중인 면접이 없습니다. 면접을 먼저 시작해주세요."],
            status=session_status(session, user_key=resolved_user_key),
            data=interview_payload(resolved_user_key),
            ok=False,
        )

    restore_interview_session(session, snapshot)
    question = session.current_question
    if not question:
        return WorkflowResult(
            messages=["현재 답변할 질문이 없습니다."],
            status=session_status(session, user_key=resolved_user_key),
            data=interview_payload(resolved_user_key),
            ok=False,
        )

    is_bonus = session.current_question_is_bonus
    if not is_bonus:
        session.turn_count += 1

    saved_turn = save_interview_turn(
        session.active_interview_session_id,
        question_type=session.current_question_type,
        question=question,
        answer=answer.strip(),
        feedback="",
        display_id=session.current_display_id,
        is_bonus=is_bonus,
        bonus_type=session.current_bonus_type if is_bonus else "",
    )
    record_interview_turn_memory(
        resolved_user_key,
        turn=saved_turn,
        session_id=session.active_interview_session_id,
    )
    update_interview_session(
        session.active_interview_session_id,
        current_display_id="",
        awaiting_choice=True,
    )

    refreshed = load_user_session(resolved_user_key)
    snapshot = get_active_interview_snapshot(resolved_user_key)
    if snapshot:
        restore_interview_session(refreshed, snapshot)
    return WorkflowResult(
        messages=[build_choice_message(refreshed)],
        status=session_status(refreshed, user_key=resolved_user_key),
        data=interview_payload(resolved_user_key),
    )


def skip_interview_question(user_key: str | None = None) -> WorkflowResult:
    resolved_user_key = user_key or default_user_key()
    snapshot = get_active_interview_snapshot(resolved_user_key)
    session = load_user_session(resolved_user_key)
    if not snapshot:
        return WorkflowResult(
            messages=["진행 중인 면접이 없습니다. 면접을 먼저 시작해주세요."],
            status=session_status(session, user_key=resolved_user_key),
            data=interview_payload(resolved_user_key),
            ok=False,
        )

    restore_interview_session(session, snapshot)
    question = session.current_question
    if not question:
        return WorkflowResult(
            messages=["스킵할 질문이 없습니다."],
            status=session_status(session, user_key=resolved_user_key),
            data=interview_payload(resolved_user_key),
            ok=False,
        )

    is_bonus = session.current_question_is_bonus
    saved_turn = save_interview_turn(
        session.active_interview_session_id,
        question_type=session.current_question_type,
        question=question,
        answer="[스킵] 답변하지 않음",
        feedback="",
        display_id=session.current_display_id,
        is_bonus=is_bonus,
        bonus_type=session.current_bonus_type if is_bonus else "",
    )
    record_interview_turn_memory(
        resolved_user_key,
        turn=saved_turn,
        session_id=session.active_interview_session_id,
    )
    update_interview_session(
        session.active_interview_session_id,
        current_display_id="",
        awaiting_choice=True,
    )
    refreshed = load_user_session(resolved_user_key)
    snapshot = get_active_interview_snapshot(resolved_user_key)
    if snapshot:
        restore_interview_session(refreshed, snapshot)
    return WorkflowResult(
        messages=[build_choice_message(refreshed)],
        status=session_status(refreshed, user_key=resolved_user_key),
        data=interview_payload(resolved_user_key),
    )


async def next_interview_question(user_key: str | None = None) -> WorkflowResult:
    resolved_user_key = user_key or default_user_key()
    snapshot = get_active_interview_snapshot(resolved_user_key)
    session = load_user_session(resolved_user_key)
    if not snapshot:
        return WorkflowResult(
            messages=["진행 중인 면접이 없습니다. 면접을 먼저 시작해주세요."],
            status=session_status(session, user_key=resolved_user_key),
            data=interview_payload(resolved_user_key),
            ok=False,
        )

    restore_interview_session(session, snapshot)
    if not session.awaiting_choice:
        message = "먼저 현재 질문에 답변해주세요." if session.current_question else "아직 다음으로 넘어갈 수 없습니다."
        return WorkflowResult(
            messages=[message],
            status=session_status(session, user_key=resolved_user_key),
            data=interview_payload(resolved_user_key),
            ok=False,
        )

    max_turns = len(session.planned_questions) or MAX_TURNS
    if session.turn_count >= max_turns:
        return await complete_interview(resolved_user_key)

    if session.turn_count >= len(session.planned_questions):
        return WorkflowResult(
            messages=["다음 질문을 찾지 못했습니다. 면접을 종료한 뒤 다시 시작해주세요."],
            status=session_status(session, user_key=resolved_user_key),
            data=interview_payload(resolved_user_key),
            ok=False,
        )

    planned_question = session.planned_questions[session.turn_count]
    update_interview_session(
        session.active_interview_session_id,
        current_display_id=planned_question.display_id,
        awaiting_choice=False,
    )
    session = load_user_session(resolved_user_key)
    return WorkflowResult(
        messages=[format_planned_question(planned_question, planned_question.display_id)],
        status=session_status(session, user_key=resolved_user_key),
        data=interview_payload(resolved_user_key),
    )


def create_bonus_question_job(mode: str, user_key: str | None = None) -> tuple[WorkflowResult, bool]:
    resolved_user_key = user_key or default_user_key()
    if mode not in {"followup", "another"}:
        session = load_user_session(resolved_user_key)
        return (
            WorkflowResult(
                messages=["질문 생성 유형이 올바르지 않습니다."],
                status=session_status(session, user_key=resolved_user_key),
                data={"job": None},
                ok=False,
            ),
            False,
        )

    snapshot = get_active_interview_snapshot(resolved_user_key)
    session = load_user_session(resolved_user_key)
    if not snapshot:
        return (
            WorkflowResult(
                messages=["진행 중인 면접이 없습니다. 면접을 먼저 시작해주세요."],
                status=session_status(session, user_key=resolved_user_key),
                data={"job": None},
                ok=False,
            ),
            False,
        )

    restore_interview_session(session, snapshot)
    if not session.awaiting_choice or not session.history:
        return (
            WorkflowResult(
                messages=["먼저 현재 질문에 답변한 뒤 선택해주세요."],
                status=session_status(session, user_key=resolved_user_key),
                data={"job": None},
                ok=False,
            ),
            False,
        )

    active_job = get_active_analysis_job(resolved_user_key, BONUS_QUESTION_JOB_KIND)
    if active_job:
        return (
            WorkflowResult(
                messages=["이미 질문 생성이 진행 중입니다."],
                status=session_status(session, user_key=resolved_user_key),
                data={"job": active_job},
            ),
            False,
        )

    job = create_analysis_job(
        resolved_user_key,
        kind=BONUS_QUESTION_JOB_KIND,
        input_data={"mode": mode, "session_id": snapshot["id"]},
        stage="queued",
        message="질문 생성을 준비하고 있습니다.",
        progress_current=0,
        progress_total=BONUS_QUESTION_PROGRESS_TOTAL,
    )
    return (
        WorkflowResult(
            messages=["질문 생성 작업을 시작했습니다."],
            status=session_status(session, user_key=resolved_user_key),
            data={"job": job},
        ),
        True,
    )


async def run_bonus_question_job(job_id: int, user_key: str, mode: str) -> None:
    try:
        update_analysis_job(
            job_id,
            status="running",
            stage="checking_answer",
            message="방금 답변한 내용을 확인하는 중입니다.",
            progress_current=1,
            progress_total=BONUS_QUESTION_PROGRESS_TOTAL,
        )
        snapshot = get_active_interview_snapshot(user_key)
        session = load_user_session(user_key)
        if not snapshot:
            raise ValueError("진행 중인 면접이 없습니다.")
        restore_interview_session(session, snapshot)
        if not session.awaiting_choice or not session.history:
            raise ValueError("먼저 현재 질문에 답변한 뒤 선택해주세요.")

        last_turn = session.history[-1]
        update_analysis_job(
            job_id,
            status="running",
            stage="finding_angle",
            message=(
                "답변에서 더 파고들 지점을 찾는 중입니다."
                if mode == "followup"
                else "이전 질문과 겹치지 않는 주제를 찾는 중입니다."
            ),
            progress_current=2,
            progress_total=BONUS_QUESTION_PROGRESS_TOTAL,
        )
        bonus_question = await get_llm().generate_bonus_question(
            context=session.build_interview_context(),
            history=session.build_history(),
            question=last_turn.question,
            question_type=last_turn.question_type,
            answer=last_turn.answer,
            mode=mode,
        )

        update_analysis_job(
            job_id,
            status="running",
            stage="saving",
            message="질문을 저장하는 중입니다.",
            progress_current=3,
            progress_total=BONUS_QUESTION_PROGRESS_TOTAL,
        )
        display_id = (
            next_followup_display_id(session, last_turn.display_id)
            if mode == "followup"
            else next_section_display_id(session, last_turn.question_type)
        )
        save_interview_question(
            session.active_interview_session_id,
            display_id=display_id,
            question_type=last_turn.question_type,
            question=bonus_question,
            is_bonus=True,
            bonus_type=mode,
        )
        update_interview_session(
            session.active_interview_session_id,
            current_display_id=display_id,
            awaiting_choice=False,
        )
        update_analysis_job(
            job_id,
            status="completed",
            stage="completed",
            message="질문 생성이 완료되었습니다.",
            progress_current=BONUS_QUESTION_PROGRESS_TOTAL,
            progress_total=BONUS_QUESTION_PROGRESS_TOTAL,
            result_data={"interview": interview_payload(user_key), "mode": mode},
            finished=True,
        )
        save_agent_chat_message(
            user_key,
            role="assistant",
            content=format_bonus_question(last_turn.question_type, bonus_question, mode, display_id),
            action="bonus_question_completed",
        )
    except ValueError as exc:
        fail_bonus_question_job(job_id, "validation_error", str(exc))
        save_agent_chat_message(
            user_key,
            role="assistant",
            content=f"질문 생성에 실패했습니다.\n\n{str(exc)[:500]}",
            action="bonus_question_failed",
        )
    except Exception as exc:
        fail_bonus_question_job(job_id, classify_bonus_question_error(exc), str(exc))
        save_agent_chat_message(
            user_key,
            role="assistant",
            content=f"질문 생성에 실패했습니다.\n\n{str(exc)[:500]}",
            action="bonus_question_failed",
        )


def fail_bonus_question_job(job_id: int, error_type: str, error_message: str) -> None:
    update_analysis_job(
        job_id,
        status="failed",
        stage="failed",
        message="질문 생성에 실패했습니다.",
        error_type=error_type,
        error_message=error_message[:500],
        finished=True,
    )


def classify_bonus_question_error(exc: Exception) -> str:
    name = exc.__class__.__name__.lower()
    if "timeout" in name:
        return "timeout"
    return "server_error"


def end_interview(user_key: str | None = None) -> WorkflowResult:
    resolved_user_key = user_key or default_user_key()
    snapshot = get_active_interview_snapshot(resolved_user_key)
    if snapshot:
        update_interview_session(
            snapshot["id"],
            status="stopped",
            current_display_id="",
            awaiting_choice=False,
        )
    session = load_user_session(resolved_user_key)
    return WorkflowResult(
        messages=["면접을 종료했습니다. 다시 시작할 수 있습니다."],
        status=session_status(session, user_key=resolved_user_key),
        data=interview_payload(resolved_user_key),
    )


async def complete_interview(user_key: str) -> WorkflowResult:
    snapshot = get_active_interview_snapshot(user_key)
    session = load_user_session(user_key)
    if not snapshot:
        return WorkflowResult(
            messages=["진행 중인 면접이 없습니다."],
            status=session_status(session, user_key=user_key),
            data=interview_payload(user_key),
            ok=False,
        )

    restore_interview_session(session, snapshot)
    max_turns = len(session.planned_questions) or MAX_TURNS
    if not session.history:
        return WorkflowResult(
            messages=["평가할 답변이 없습니다."],
            status=session_status(session, user_key=user_key),
            data=interview_payload(user_key),
            ok=False,
        )

    try:
        result = await get_llm().evaluate_full_interview(
            context=session.build_interview_context(),
            history=session.build_history(),
        )
    except Exception as exc:
        return WorkflowResult(
            messages=[
                "전체 면접 평가에 실패했습니다.\n\n"
                "면접 답변은 저장되어 있습니다. 나중에 다시 다음을 눌러 평가를 재시도할 수 있습니다.\n\n"
                f"원인: {str(exc)[:300]}"
            ],
            status=session_status(session, user_key=user_key),
            data=interview_payload(user_key),
            ok=False,
        )

    answer_feedback, overall_feedback = split_final_feedback(result)
    weakness_summary = extract_weakness_summary(overall_feedback or result)
    update_interview_session(
        session.active_interview_session_id,
        status="completed",
        current_display_id="",
        awaiting_choice=False,
        summary=result,
        weakness_summary=weakness_summary,
    )
    learned_weaknesses = record_weakness_learning_from_review(
        user_key,
        session_id=session.active_interview_session_id,
        analysis_job_id=None,
        weakness_summary=weakness_summary,
        overall_feedback=overall_feedback or result,
    )
    review_memory = record_final_review_memory(
        user_key,
        session_id=session.active_interview_session_id,
        analysis_job_id=None,
        answer_feedback=answer_feedback,
        overall_feedback=overall_feedback or "",
        weakness_summary=weakness_summary,
    )
    session = load_user_session(user_key)
    messages = [answer_feedback]
    if overall_feedback:
        messages.append(overall_feedback + f"\n\n{max_turns}턴 면접이 종료되었습니다.")
    else:
        messages.append(f"{max_turns}턴 면접이 종료되었습니다.")
    return WorkflowResult(
        messages=messages,
        status=session_status(session, user_key=user_key),
        data={
            **interview_payload(user_key),
            "learned_weaknesses": learned_weaknesses,
            "review_memory": review_memory,
        },
    )
