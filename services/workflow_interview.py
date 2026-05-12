import re
from typing import Any

from db.repository import (
    create_interview_session,
    get_active_interview_snapshot,
    get_github_repositories_by_indices,
    get_job_posting_by_index,
    get_latest_feedback_summary,
    get_latest_weakness_summary,
    get_recent_sessions,
    get_recent_turns,
    load_user_session,
    save_interview_question,
    save_interview_questions,
    save_interview_turn,
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

QUESTION_TYPES = ("CS 기본기", "언어", "기술스택", "프로젝트/GitHub", "프로젝트/자소서")


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
        "job_title": job["title"] if job else "선택 공고",
        "job_summary": job["summary"] if job else session.job_posting,
        "github_repositories": [
            {
                "title": repository["title"],
                "url": repository["url"],
                "summary": repository["summary"],
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


async def generate_interview_question_candidates(
    *,
    job_index: int | None = None,
    github_indices: list[int] | None = None,
    question_counts: dict[str, int] | None = None,
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
            data=question_candidate_payload([]),
            ok=False,
        )

    if not session.has_context():
        return WorkflowResult(
            messages=["아직 면접 컨텍스트가 없습니다. 자소서 또는 GitHub 분석 중 하나는 먼저 넣어주세요."],
            status=session_status(session, user_key=resolved_user_key),
            data=question_candidate_payload([]),
            ok=False,
        )

    question_plan = question_plan_from_counts(question_counts)
    try:
        question_result = await get_llm().generate_interview_questions(
            session.build_interview_context(),
            question_plan,
        )
        questions = parse_planned_questions(question_result, question_plan)
        if not questions:
            raise ValueError("질문 후보를 정해진 형식으로 파싱하지 못했습니다.")
    except Exception as exc:
        return WorkflowResult(
            messages=[
                "질문 후보 생성에 실패했습니다.\n\n"
                "잠시 후 다시 시도해주세요.\n\n"
                f"원인: {str(exc)[:300]}"
            ],
            status=session_status(session, user_key=resolved_user_key),
            data=question_candidate_payload([]),
            ok=False,
        )

    return WorkflowResult(
        messages=[f"질문 후보 {len(questions)}개를 만들었습니다."],
        status=session_status(session, user_key=resolved_user_key),
        data=question_candidate_payload(questions),
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
            f"[{repository['title']}]\nURL: {repository['url']}\n{repository['summary']}"
            for repository in repositories
        )

    if fields:
        update_user_fields(user_key, **fields)
    return ""


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

    save_interview_turn(
        session.active_interview_session_id,
        question_type=session.current_question_type,
        question=question,
        answer=answer.strip(),
        feedback="",
        display_id=session.current_display_id,
        is_bonus=is_bonus,
        bonus_type=session.current_bonus_type if is_bonus else "",
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


async def generate_bonus_question(mode: str, user_key: str | None = None) -> WorkflowResult:
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
    if not session.awaiting_choice or not session.history:
        return WorkflowResult(
            messages=["먼저 현재 질문에 답변한 뒤 선택해주세요."],
            status=session_status(session, user_key=resolved_user_key),
            data=interview_payload(resolved_user_key),
            ok=False,
        )

    last_turn = session.history[-1]
    action = "꼬리질문 생성" if mode == "followup" else "추가질문 생성"
    try:
        bonus_question = await get_llm().generate_bonus_question(
            context=session.build_interview_context(),
            history=session.build_history(),
            question=last_turn.question,
            question_type=last_turn.question_type,
            answer=last_turn.answer,
            mode=mode,
        )
    except Exception as exc:
        return WorkflowResult(
            messages=[
                f"{action}에 실패했습니다.\n\n"
                "잠시 후 다시 시도해주세요.\n\n"
                f"원인: {str(exc)[:300]}"
            ],
            status=session_status(session, user_key=resolved_user_key),
            data=interview_payload(resolved_user_key),
            ok=False,
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
    session = load_user_session(resolved_user_key)
    return WorkflowResult(
        messages=[format_bonus_question(last_turn.question_type, bonus_question, mode, display_id)],
        status=session_status(session, user_key=resolved_user_key),
        data=interview_payload(resolved_user_key),
    )


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
    session = load_user_session(user_key)
    messages = [answer_feedback]
    if overall_feedback:
        messages.append(overall_feedback + f"\n\n{max_turns}턴 면접이 종료되었습니다.")
    else:
        messages.append(f"{max_turns}턴 면접이 종료되었습니다.")
    return WorkflowResult(
        messages=messages,
        status=session_status(session, user_key=user_key),
        data=interview_payload(user_key),
    )
