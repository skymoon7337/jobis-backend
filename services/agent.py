import re
from typing import Any

from fastapi import BackgroundTasks

from db.repository import (
    claim_agent_pending_commands,
    complete_agent_pending_command,
    create_agent_pending_command,
    get_active_analysis_job,
    get_agent_actions,
    get_agent_chat_messages,
    get_agent_pending_commands,
    get_latest_completed_analysis_job,
    save_agent_action,
    save_agent_chat_message,
)
from services.workflow import (
    WorkflowResult,
    create_bonus_question_job,
    create_context_analysis_job,
    create_github_analysis_job,
    create_final_review_job,
    create_job_posting_job,
    create_question_plan_job,
    end_interview,
    get_context,
    get_github_analysis,
    get_interview,
    get_interview_review,
    get_llm,
    get_memory_learning,
    get_status,
    get_weakness_learning,
    list_github_repositories,
    list_jobs,
    next_interview_question,
    run_bonus_question_job,
    run_context_analysis_job,
    run_final_review_job,
    run_github_analysis_job,
    run_job_posting_job,
    run_question_plan_job,
    save_profile,
    save_resume,
    search_interview_history,
    select_job,
    start_interview,
    submit_interview_answer,
    reset_learning_data,
)


ALLOWED_ACTIONS = {
    "list_job_postings",
    "select_job_posting",
    "save_job_posting",
    "list_github_repositories",
    "analyze_github_repository",
    "get_github_analysis",
    "save_profile",
    "save_resume",
    "get_context",
    "start_interview",
    "get_interview",
    "get_latest_review",
    "get_status",
    "create_context_analysis",
    "create_question_plan",
    "submit_interview_answer",
    "next_interview_question",
    "create_followup_question",
    "create_another_question",
    "create_final_review",
    "end_interview",
    "get_weaknesses",
    "get_training_plan",
    "search_memory",
    "search_interview_history",
    "reset_learning",
    "unknown",
}


def _reply_from_result(result: WorkflowResult) -> str:
    return "\n\n".join(message for message in result.messages if message).strip()


def _agent_response(
    *,
    result: WorkflowResult,
    action: str,
    handled: bool = True,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = dict(result.data)
    if extra:
        data.update(extra)
    return {
        "ok": result.ok,
        "handled": handled,
        "action": action,
        "reply": _reply_from_result(result),
        "messages": result.messages,
        "status": result.status,
        "data": data,
    }


def _fallback_response(message: str, user_key: str) -> dict[str, Any]:
    result = get_status(user_key)
    return _agent_response(
        result=result,
        action="unknown",
        handled=False,
        extra={
            "help": [
                "공고 목록 보여줘",
                "1번 공고 선택해줘",
                "1번 공고로 면접 시작해줘",
                "최근 리뷰 불러줘",
                "공고 저장해줘 <URL 또는 본문>",
                "GitHub 분석해줘 <https://github.com/user/repo>",
                "프로필 저장해줘 <내용>",
                "자소서 저장해줘 <내용>",
                "1번 공고랑 1번 GitHub로 질문 5개 만들어줘",
                "내 답변은 ...",
                "다음 질문",
                "꼬리질문 해줘",
                "내 약점 보여줘",
                "이번에 뭐 연습하면 돼?",
            ],
            "input": message,
        },
    ) | {
        "reply": (
            "아직 그 요청은 처리할 수 없어요.\n\n"
            "지금 가능한 명령은 공고/GitHub/프로필/자소서 저장, 질문 후보 생성, 면접 시작, 최근 리뷰 조회예요."
        )
}

QUESTION_TYPES = ("CS 기본기", "언어", "기술스택", "프로젝트/GitHub", "프로젝트/자소서")
QUESTION_PLAN_JOB_KIND = "question_plan"


def agent_chat_messages_payload(user_key: str, limit: int = 50) -> dict[str, Any]:
    return {"messages": get_agent_chat_messages(user_key, limit)}


def agent_actions_payload(user_key: str, limit: int = 50) -> dict[str, Any]:
    return {"actions": get_agent_actions(user_key, limit)}


def agent_pending_commands_payload(user_key: str, limit: int = 50) -> dict[str, Any]:
    pending_commands = []
    for pending in get_agent_pending_commands(user_key, limit):
        pending_commands.append(
            {
                **pending,
                "command": _command_log_summary(str(pending.get("command") or "")),
            }
        )
    return {"pending_commands": pending_commands}


def _contains_any(message: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in message for keyword in keywords)


def _is_force_rerun(message: str) -> bool:
    normalized = message.strip().lower()
    return _contains_any(normalized, ("새로", "다시", "재분석", "재생성", "갱신", "업데이트"))


def _command_log_summary(message: str) -> str:
    normalized = message.strip().lower()
    if _looks_like_profile_save(normalized):
        return "프로필 저장 명령"
    if _looks_like_resume_save(normalized):
        return "자소서 저장 명령"
    if _looks_like_job_save(normalized):
        return "공고 저장 명령"
    if _looks_like_answer(normalized):
        return "면접 답변 저장 명령"
    if _looks_like_github_analyze(normalized):
        return "GitHub 분석 명령"
    if _looks_like_question_plan(normalized):
        return "질문 후보 생성 명령"
    return message.strip()


def _extract_index(message: str) -> int | None:
    match = re.search(r"(\d+)\s*(번|번째)", message)
    if match:
        index = int(match.group(1))
        return index if index > 0 else None

    ordinal_patterns = (
        (r"첫\s*번째|첫\s*번|첫째", 1),
        (r"두\s*번째|두\s*번|둘째", 2),
        (r"세\s*번째|세\s*번|셋째", 3),
        (r"네\s*번째|네\s*번|넷째", 4),
        (r"다섯\s*번째|다섯\s*번|다섯째", 5),
    )
    for pattern, index in ordinal_patterns:
        if re.search(pattern, message):
            return index
    return None


def _resolve_job_index(message: str, user_key: str, explicit_index: int | None = None) -> int | None:
    if explicit_index is not None:
        return explicit_index

    normalized = message.strip().lower()
    recent_words = ("방금", "최근", "마지막", "새로", "저장한", "추가한", "등록한", "latest", "last")
    selected_words = ("선택된", "현재", "지금", "고른")
    if not _contains_any(normalized, recent_words + selected_words):
        return None

    jobs = list_jobs(user_key).data.get("jobs", [])
    if not isinstance(jobs, list) or not jobs:
        return None

    if _contains_any(normalized, selected_words):
        selected = next(
            (job for job in jobs if isinstance(job, dict) and job.get("is_selected")),
            None,
        )
        if selected and isinstance(selected.get("index"), int):
            return selected["index"]

    last_job = jobs[-1]
    if isinstance(last_job, dict) and isinstance(last_job.get("index"), int):
        return last_job["index"]
    return None


def _resolve_question_plan_job_index(message: str, user_key: str, explicit_index: int | None = None) -> int | None:
    resolved_index = _resolve_job_index(message, user_key, explicit_index)
    if resolved_index is not None:
        return resolved_index

    jobs = list_jobs(user_key).data.get("jobs", [])
    if not isinstance(jobs, list) or not jobs:
        return None

    selected = next((job for job in jobs if isinstance(job, dict) and job.get("is_selected")), None)
    if selected and isinstance(selected.get("index"), int):
        return selected["index"]

    if len(jobs) == 1 and isinstance(jobs[0], dict) and isinstance(jobs[0].get("index"), int):
        return jobs[0]["index"]
    return None


def _extract_github_indices(message: str) -> list[int]:
    patterns = (
        r"(\d+)\s*(?:번|번째)\s*(?:github|깃허브|깃헙)",
        r"(?:github|깃허브|깃헙)\s*(?:저장소|프로젝트|레포|repo)?\s*(\d+)\s*(?:번|번째)?",
    )
    indices: list[int] = []
    for pattern in patterns:
        for match in re.finditer(pattern, message, flags=re.IGNORECASE):
            index = int(match.group(1))
            if index > 0 and index not in indices:
                indices.append(index)
    return indices


def _resolve_github_indices(message: str, user_key: str, explicit_indices: list[int] | None = None) -> list[int]:
    if explicit_indices:
        return list(dict.fromkeys(index for index in explicit_indices if index > 0))

    extracted_indices = _extract_github_indices(message)
    if extracted_indices:
        return extracted_indices

    repositories = list_github_repositories(user_key).data.get("repositories", [])
    if not isinstance(repositories, list) or not repositories:
        return []

    normalized = message.strip().lower()
    if _contains_any(normalized, ("방금", "최근", "마지막", "새로", "분석한", "latest", "last")):
        last_repository = repositories[-1]
        if isinstance(last_repository, dict) and isinstance(last_repository.get("index"), int):
            return [last_repository["index"]]

    if len(repositories) == 1 and isinstance(repositories[0], dict) and isinstance(repositories[0].get("index"), int):
        return [repositories[0]["index"]]
    return []


def _extract_question_count(message: str) -> int | None:
    match = re.search(r"질문\s*(?:후보\s*)?(\d+)\s*개", message)
    if not match:
        match = re.search(r"(\d+)\s*개\s*(?:질문|면접\s*질문)", message)
    if not match:
        return None
    count = int(match.group(1))
    return count if 1 <= count <= 8 else None


def _question_counts_from_total(total: int | None = None) -> dict[str, int]:
    remaining = total or len(QUESTION_TYPES)
    counts = {question_type: 0 for question_type in QUESTION_TYPES}
    while remaining > 0:
        changed = False
        for question_type in QUESTION_TYPES:
            if remaining <= 0:
                break
            if counts[question_type] >= 3:
                continue
            counts[question_type] += 1
            remaining -= 1
            changed = True
        if not changed:
            break
    return counts


def _looks_like_question_plan(message: str) -> bool:
    if _contains_any(message, ("꼬리질문", "꼬리 질문", "추가질문", "추가 질문", "다른 질문")):
        return False
    has_question_word = _contains_any(message, ("질문 후보", "면접 질문", "질문"))
    has_create_word = _contains_any(message, ("만들", "생성", "뽑아", "추천", "준비"))
    has_context_word = _contains_any(message, ("공고", "github", "깃허브", "깃헙", "면접", "후보"))
    return has_question_word and has_create_word and has_context_word


def _looks_like_interview_continuation(message: str) -> bool:
    if _contains_any(message, ("다음 질문", "꼬리질문", "꼬리 질문", "추가질문", "추가 질문", "다른 질문")):
        return False
    has_interview_context = _contains_any(message, ("면접", "질문"))
    has_continue_word = _contains_any(
        message,
        ("이어", "계속", "아까", "하던", "진행 중", "진행중", "현재", "지금", "어디까지"),
    )
    has_show_word = _contains_any(message, ("보여", "불러", "조회", "알려", "확인"))
    return has_interview_context and (has_continue_word or ("질문" in message and has_show_word))


def _interview_status_result(user_key: str, *, continued_existing: bool = False) -> WorkflowResult:
    current_interview = get_interview(user_key)
    if not current_interview.data.get("active"):
        return current_interview

    current_question = str(current_interview.data.get("current_question") or "").strip()
    awaiting_choice = bool(current_interview.data.get("awaiting_choice"))
    messages = ["진행 중인 면접을 이어서 보여줄게."]
    if current_question:
        messages.append(current_question)
    if awaiting_choice:
        messages.append("방금 답변은 저장되어 있어. 다음 질문, 꼬리질문, 추가질문 중 하나를 고르면 돼.")
    else:
        messages.append("이 질문에 답변을 보내면 저장할게.")

    return WorkflowResult(
        messages=messages,
        status=current_interview.status,
        data={**current_interview.data, "continued_existing": continued_existing},
    )


def _looks_like_candidate_interview_start(message: str) -> bool:
    if not ("면접" in message and _contains_any(message, ("시작", "진행"))):
        return False
    return _contains_any(
        message,
        ("질문 후보", "후보", "방금 만든", "방금 생성", "생성한 질문", "만든 질문", "이 질문", "그걸로"),
    )


def _latest_question_plan_start_payload(user_key: str) -> tuple[int | None, list[int], list[dict[str, str]], str]:
    active_job = get_active_analysis_job(user_key, QUESTION_PLAN_JOB_KIND)
    if active_job:
        return None, [], [], "질문 후보 생성이 아직 진행 중이야. 완료되면 그 질문으로 면접을 시작할 수 있어."

    job = get_latest_completed_analysis_job(user_key, QUESTION_PLAN_JOB_KIND)
    if not job:
        return None, [], [], "아직 사용할 질문 후보가 없어. 먼저 `1번 공고랑 1번 GitHub로 질문 5개 만들어줘`처럼 후보를 만들어줘."

    input_data = job.get("input", {})
    result_data = job.get("result", {})
    questions = result_data.get("questions") if isinstance(result_data, dict) else None
    if not isinstance(questions, list) or not questions:
        return None, [], [], "최근 질문 후보 결과를 읽지 못했어. 질문 후보를 다시 만들어줘."

    selected_questions: list[dict[str, str]] = []
    for question in questions:
        if not isinstance(question, dict):
            continue
        question_type = question.get("question_type")
        question_text = question.get("question")
        if isinstance(question_type, str) and isinstance(question_text, str) and question_type and question_text:
            selected_questions.append({"question_type": question_type, "question": question_text})

    if not selected_questions:
        return None, [], [], "최근 질문 후보에 사용할 질문이 없어. 질문 후보를 다시 만들어줘."

    job_index = input_data.get("job_index") if isinstance(input_data, dict) else None
    github_indices = input_data.get("github_indices") if isinstance(input_data, dict) else []
    resolved_job_index = job_index if isinstance(job_index, int) and job_index > 0 else None
    resolved_github_indices = [
        index for index in github_indices if isinstance(index, int) and index > 0
    ] if isinstance(github_indices, list) else []
    return resolved_job_index, resolved_github_indices, selected_questions, ""


def _extract_job_text(message: str) -> str:
    url_match = re.search(r"https?://\S+", message)
    if url_match:
        return url_match.group(0).rstrip(".,)")

    cleaned = message.strip()
    patterns = (
        r"공고\s*(저장|추가|등록|분석)\s*(해줘|해|해주세요|부탁해)?",
        r"(저장|추가|등록|분석)\s*(해줘|해|해주세요|부탁해)?",
        r"이\s*공고",
    )
    for pattern in patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip()

    cleaned = re.sub(r"^[\s:：,\-]+", "", cleaned)
    return cleaned.strip()


def _extract_github_url(message: str) -> str:
    match = re.search(r"https?://(?:www\.)?github\.com/\S+", message, flags=re.IGNORECASE)
    return match.group(0).rstrip(".,)") if match else ""


def _extract_named_text(message: str, label_patterns: tuple[str, ...]) -> str:
    cleaned = message.strip()
    for pattern in label_patterns:
        next_value = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip()
        if next_value != cleaned:
            cleaned = next_value
            break
    return re.sub(r"^[\s:：,\-]+", "", cleaned).strip()


def _extract_profile_text(message: str) -> str:
    return _extract_named_text(
        message,
        (
            r"^프로필\s*(저장|추가|등록|입력|수정)\s*(해줘|해|해주세요|부탁해|할게)?\s*",
            r"^(저장|추가|등록|입력|수정)\s*할?\s*프로필(은|:|：)?\s*",
            r"^(내\s*)?프로필(은|:|：)?\s*",
        ),
    )


def _extract_resume_text(message: str) -> str:
    return _extract_named_text(
        message,
        (
            r"^(자소서|자기소개서|이력서)\s*(저장|추가|등록|입력|수정)\s*(해줘|해|해주세요|부탁해|할게)?\s*",
            r"^(저장|추가|등록|입력|수정)\s*할?\s*(자소서|자기소개서|이력서)(는|은|:|：)?\s*",
            r"^(내\s*)?(자소서|자기소개서|이력서)(는|은|:|：)?\s*",
        ),
    )


def _extract_memory_query(message: str) -> str:
    cleaned = message.strip()
    patterns = (
        r"(이전|예전|지난|과거|전에|면접|답변|피드백|기억|찾아줘|찾아|검색|보여줘|보여|있어|관련해서|관련|조회|불러줘|불러)",
        r"[?？]",
    )
    for pattern in patterns:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.:：-")
    cleaned = re.sub(r"\s+[이가은는을를]$", "", cleaned).strip()
    return cleaned


def _looks_like_interview_history_search(message: str) -> bool:
    has_history_scope = _contains_any(
        message,
        ("했던 면접", "한 면접", "면접중", "면접 중", "이전 면접", "지난 면접", "과거 면접", "전에 면접"),
    )
    has_search_target = _contains_any(message, ("질문", "답변", "피드백", "리뷰", "회고", "기록"))
    has_search_intent = _contains_any(
        message,
        ("관련", "있었", "있나", "물어봤", "나왔", "찾", "검색", "보여", "조회", "했었"),
    )
    explicit_history_search = _contains_any(message, ("면접 기록", "면접 기억")) and has_search_intent
    implicit_past_question_search = (
        has_search_target
        and _contains_any(message, ("있었", "물어봤", "나왔", "했었"))
    )
    return (
        (has_history_scope and (has_search_target or has_search_intent))
        or explicit_history_search
        or implicit_past_question_search
    )


def _requests_answered_interview_history(message: str) -> bool:
    return bool(re.search(r"(답변|피드백).*(있는|있던|있었|포함|만)", message))


def _extract_interview_history_query(message: str) -> str:
    cleaned = message.strip()
    patterns = (
        r"(내가|제가|혹시|했던|한|이전|예전|지난|과거|전에|면접중에|면접\s*중에|면접에서|면접|기록|기억)",
        r"(질문|답변|피드백|리뷰|회고)",
        r"(관련해서|관련|있는|있던|있었던|있었나|있었어|있었는지|있나|있어|물어봤나|물어봤어|나왔나|나왔어|찾아줘|찾아|검색|보여줘|보여|조회)",
        r"[?？]",
    )
    for pattern in patterns:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.:：-")
    cleaned = re.sub(r"\s+[이가은는을를]$", "", cleaned).strip()
    return cleaned


def _looks_like_job_save(message: str) -> bool:
    has_job_word = "공고" in message
    has_save_word = _contains_any(message, ("저장", "추가", "등록"))
    has_url = bool(re.search(r"https?://\S+", message))
    return (has_job_word and has_save_word) or (has_save_word and has_url)


def _looks_like_github_analyze(message: str) -> bool:
    has_github_word = _contains_any(message, ("github", "깃허브", "깃헙"))
    has_action_word = _contains_any(message, ("분석", "저장", "추가", "등록", "불러와", "읽어"))
    return has_github_word and (has_action_word or bool(_extract_github_url(message)))


def _looks_like_profile_save(message: str) -> bool:
    return "프로필" in message and _contains_any(message, ("저장", "추가", "등록", "입력", "수정", "은", ":"))


def _looks_like_resume_save(message: str) -> bool:
    return _contains_any(message, ("자소서", "자기소개서", "이력서")) and _contains_any(
        message,
        ("저장", "추가", "등록", "입력", "수정", "은", "는", ":"),
    )


def _save_assistant_response(user_key: str, response: dict[str, Any]) -> dict[str, Any]:
    action = str(response.get("action", ""))
    data = response.get("data")
    job = data.get("job") if isinstance(data, dict) else None
    job_status = job.get("status") if isinstance(job, dict) else ""
    status = str(job_status or ("completed" if response.get("ok") else "failed"))
    metadata: dict[str, Any] = {
        "handled": response.get("handled"),
        "ok": response.get("ok"),
        "intent_source": data.get("intent_source") if isinstance(data, dict) else "",
        "required_input": data.get("required_input") if isinstance(data, dict) else "",
        "job_id": job.get("id") if isinstance(job, dict) else None,
        "job_kind": job.get("kind") if isinstance(job, dict) else "",
        "target_view": data.get("target_view") if isinstance(data, dict) else "",
        "selected_session_id": data.get("selected_session_id") if isinstance(data, dict) else None,
        "query": data.get("query") if isinstance(data, dict) else "",
        "match_count": data.get("match_count") if isinstance(data, dict) else None,
        "total_count": data.get("total_count") if isinstance(data, dict) else None,
        "shown_count": data.get("shown_count") if isinstance(data, dict) else None,
        "has_more": data.get("has_more") if isinstance(data, dict) else None,
        "searched_counts": data.get("searched_counts") if isinstance(data, dict) else None,
        "matches": data.get("matches") if isinstance(data, dict) else None,
        "next_matches": data.get("next_matches") if isinstance(data, dict) else None,
        "refine_suggestions": data.get("refine_suggestions") if isinstance(data, dict) else None,
    }
    metadata = {key: value for key, value in metadata.items() if value not in ("", None)}
    save_agent_chat_message(
        user_key,
        role="assistant",
        content=str(response.get("reply", "")),
        action=action,
    )
    save_agent_action(
        user_key,
        action=action,
        status=status,
        result_summary=str(response.get("reply", "")),
        metadata=metadata,
    )
    return response


def _extract_answer_text(message: str) -> str:
    cleaned = message.strip()
    patterns = (
        r"^(내\s*)?답변(은|:|：)?\s*",
        r"^대답(은|:|：)?\s*",
        r"^answer\s*[:：]?\s*",
    )
    for pattern in patterns:
        next_value = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip()
        if next_value != cleaned:
            cleaned = next_value
            break
    return re.sub(r"^[\s:：,\-]+", "", cleaned).strip()


def _looks_like_answer(message: str) -> bool:
    if _contains_any(message, ("답변", "대답")) and not _contains_any(
        message,
        ("피드백", "리뷰", "불러", "보여", "조회"),
    ):
        return True
    return message.startswith("answer:")


def _intent_action(intent: dict[str, object]) -> str:
    action = intent.get("action")
    if not isinstance(action, str):
        return "unknown"
    action = action.strip()
    return action if action in ALLOWED_ACTIONS else "unknown"


def _intent_confidence(intent: dict[str, object]) -> float:
    raw_confidence = intent.get("confidence")
    if isinstance(raw_confidence, int | float):
        return float(raw_confidence)
    if isinstance(raw_confidence, str):
        try:
            return float(raw_confidence)
        except ValueError:
            return 0.0
    return 0.0


def _intent_job_index(intent: dict[str, object], message: str) -> int | None:
    raw_index = intent.get("job_index")
    if isinstance(raw_index, int) and raw_index > 0:
        return raw_index
    if isinstance(raw_index, str) and raw_index.isdigit():
        index = int(raw_index)
        return index if index > 0 else None
    return _extract_index(message)


def _intent_job_text(intent: dict[str, object], message: str) -> str:
    raw_text = intent.get("job_text")
    if isinstance(raw_text, str) and raw_text.strip():
        return raw_text.strip()
    return _extract_job_text(message)


def _intent_answer_text(intent: dict[str, object], message: str) -> str:
    raw_text = intent.get("answer")
    if isinstance(raw_text, str) and raw_text.strip():
        return raw_text.strip()
    raw_text = intent.get("answer_text")
    if isinstance(raw_text, str) and raw_text.strip():
        return raw_text.strip()
    return _extract_answer_text(message)


def _intent_github_url(intent: dict[str, object], message: str) -> str:
    raw_url = intent.get("github_url")
    if isinstance(raw_url, str) and raw_url.strip():
        return raw_url.strip()
    return _extract_github_url(message)


def _intent_github_indices(intent: dict[str, object], message: str) -> list[int]:
    raw_indices = intent.get("github_indices")
    if isinstance(raw_indices, list):
        indices: list[int] = []
        for raw_index in raw_indices:
            if isinstance(raw_index, int) and raw_index > 0 and raw_index not in indices:
                indices.append(raw_index)
            elif isinstance(raw_index, str) and raw_index.isdigit():
                index = int(raw_index)
                if index > 0 and index not in indices:
                    indices.append(index)
        if indices:
            return indices
    return _extract_github_indices(message)


def _intent_question_counts(intent: dict[str, object], message: str) -> dict[str, int]:
    raw_counts = intent.get("question_counts")
    if isinstance(raw_counts, dict):
        counts: dict[str, int] = {}
        for question_type in QUESTION_TYPES:
            raw_count = raw_counts.get(question_type)
            if isinstance(raw_count, int):
                counts[question_type] = max(0, min(3, raw_count))
            elif isinstance(raw_count, str) and raw_count.isdigit():
                counts[question_type] = max(0, min(3, int(raw_count)))
            else:
                counts[question_type] = 0
        total = sum(counts.values())
        if 1 <= total <= 8:
            return counts

    raw_total = intent.get("question_count")
    if isinstance(raw_total, int) and 1 <= raw_total <= 8:
        return _question_counts_from_total(raw_total)
    if isinstance(raw_total, str) and raw_total.isdigit():
        total = int(raw_total)
        if 1 <= total <= 8:
            return _question_counts_from_total(total)

    return _question_counts_from_total(_extract_question_count(message))


def _intent_profile_text(intent: dict[str, object], message: str) -> str:
    raw_text = intent.get("profile_text")
    if isinstance(raw_text, str) and raw_text.strip():
        return raw_text.strip()
    return _extract_profile_text(message)


def _intent_resume_text(intent: dict[str, object], message: str) -> str:
    raw_text = intent.get("resume_text")
    if isinstance(raw_text, str) and raw_text.strip():
        return raw_text.strip()
    return _extract_resume_text(message)


async def _handle_action(
    *,
    action: str,
    message: str,
    user_key: str,
    background_tasks: BackgroundTasks,
    job_index: int | None = None,
    job_text: str = "",
    github_url: str = "",
    github_indices: list[int] | None = None,
    question_counts: dict[str, int] | None = None,
    profile_text: str = "",
    resume_text: str = "",
    answer_text: str = "",
    intent_source: str = "rule",
) -> dict[str, Any]:
    if action == "save_job_posting":
        resolved_job_text = job_text or _extract_job_text(message)
        if not resolved_job_text:
            result = get_status(user_key)
            return _agent_response(
                result=result,
                action="save_job_posting",
                extra={"required_input": "job_text_or_url", "intent_source": intent_source},
            ) | {
                "ok": False,
                "reply": "저장할 공고 URL이나 본문을 같이 보내줘.",
            }

        result, should_start = create_job_posting_job(
            resolved_job_text,
            user_key,
            force=_is_force_rerun(message),
        )
        job = result.data.get("job")
        if should_start and job:
            background_tasks.add_task(run_job_posting_job, job["id"], user_key, resolved_job_text)
        return _agent_response(
            result=result,
            action="save_job_posting",
            extra={"intent_source": intent_source},
        )

    if action == "analyze_github_repository":
        resolved_url = github_url or _extract_github_url(message)
        if not resolved_url:
            result = get_status(user_key)
            return _agent_response(
                result=result,
                action="analyze_github_repository",
                extra={"required_input": "github_url", "intent_source": intent_source},
            ) | {
                "ok": False,
                "reply": "분석할 GitHub 레포 URL을 같이 보내줘. 예: GitHub 분석해줘 https://github.com/user/repo",
            }

        result, should_start = create_github_analysis_job(
            resolved_url,
            user_key,
            force=_is_force_rerun(message),
        )
        job = result.data.get("job")
        if should_start and job:
            background_tasks.add_task(run_github_analysis_job, job["id"], user_key, resolved_url)
        return _agent_response(
            result=result,
            action="analyze_github_repository",
            extra={"intent_source": intent_source},
        )

    if action == "list_github_repositories":
        return _agent_response(
            result=list_github_repositories(user_key),
            action="list_github_repositories",
            extra={"intent_source": intent_source},
        )

    if action == "get_github_analysis":
        return _agent_response(
            result=get_github_analysis(user_key),
            action="get_github_analysis",
            extra={"intent_source": intent_source},
        )

    if action == "save_profile":
        resolved_profile = profile_text or _extract_profile_text(message)
        if not resolved_profile:
            result = get_context(user_key)
            return _agent_response(
                result=result,
                action="save_profile",
                extra={"required_input": "profile_text", "intent_source": intent_source},
            ) | {
                "ok": False,
                "reply": "저장할 프로필 내용을 같이 보내줘. 예: 프로필 저장해줘 백엔드 개발자...",
            }
        return _agent_response(
            result=save_profile(resolved_profile, user_key),
            action="save_profile",
            extra={"intent_source": intent_source},
        )

    if action == "save_resume":
        resolved_resume = resume_text or _extract_resume_text(message)
        if not resolved_resume:
            result = get_context(user_key)
            return _agent_response(
                result=result,
                action="save_resume",
                extra={"required_input": "resume_text", "intent_source": intent_source},
            ) | {
                "ok": False,
                "reply": "저장할 자소서 내용을 같이 보내줘. 예: 자소서 저장해줘 저는...",
            }
        return _agent_response(
            result=save_resume(resolved_resume, user_key),
            action="save_resume",
            extra={"intent_source": intent_source},
        )

    if action == "get_context":
        return _agent_response(
            result=get_context(user_key),
            action="get_context",
            extra={"intent_source": intent_source},
        )

    if action == "list_job_postings":
        return _agent_response(
            result=list_jobs(user_key),
            action="list_job_postings",
            extra={"intent_source": intent_source},
        )

    if action == "select_job_posting":
        resolved_job_index = _resolve_job_index(message, user_key, job_index)
        if resolved_job_index is None:
            result = list_jobs(user_key)
            return _agent_response(
                result=result,
                action="select_job_posting",
                extra={"required_input": "job_index", "intent_source": intent_source},
            ) | {
                "ok": False,
                "reply": "몇 번 공고를 선택할지 알려줘. 예: 1번 공고 선택해줘",
            }
        return _agent_response(
            result=select_job(resolved_job_index, user_key),
            action="select_job_posting",
            extra={"intent_source": intent_source, "resolved_job_index": resolved_job_index},
        )

    if action == "start_interview":
        current_interview = get_interview(user_key)
        if current_interview.data.get("active") and not _is_force_rerun(message):
            result = _interview_status_result(user_key, continued_existing=True)
            result.messages[0] = "이미 진행 중인 면접이 있어. 새로 시작하지 않고 현재 질문을 보여줄게."
            return _agent_response(
                result=result,
                action="start_interview",
                extra={"intent_source": intent_source},
            )

        if _looks_like_candidate_interview_start(message):
            (
                candidate_job_index,
                candidate_github_indices,
                candidate_questions,
                candidate_error,
            ) = _latest_question_plan_start_payload(user_key)
            if candidate_error:
                result = get_status(user_key)
                return _agent_response(
                    result=result,
                    action="start_interview",
                    extra={"intent_source": intent_source, "required_input": "question_candidates"},
                ) | {
                    "ok": False,
                    "reply": candidate_error,
                }
            result = await start_interview(
                job_index=candidate_job_index,
                github_indices=candidate_github_indices,
                selected_questions=candidate_questions,
                user_key=user_key,
            )
            return _agent_response(
                result=result,
                action="start_interview",
                extra={
                    "intent_source": intent_source,
                    "question_source": "latest_question_plan",
                    "resolved_job_index": candidate_job_index,
                    "resolved_github_indices": candidate_github_indices,
                    "selected_question_count": len(candidate_questions),
                },
            )

        resolved_job_index = _resolve_job_index(message, user_key, job_index)
        result = await start_interview(job_index=resolved_job_index, user_key=user_key)
        return _agent_response(
            result=result,
            action="start_interview",
            extra={"intent_source": intent_source, "resolved_job_index": resolved_job_index},
        )

    if action == "get_interview":
        return _agent_response(
            result=_interview_status_result(user_key, continued_existing=True),
            action="get_interview",
            extra={"intent_source": intent_source},
        )

    if action == "get_latest_review":
        return _agent_response(
            result=get_interview_review(user_key),
            action="get_latest_review",
            extra={"intent_source": intent_source},
        )

    if action == "get_status":
        return _agent_response(result=get_status(user_key), action="get_status", extra={"intent_source": intent_source})

    if action in {"get_weaknesses", "get_training_plan"}:
        return _agent_response(
            result=get_weakness_learning(user_key, training=action == "get_training_plan"),
            action=action,
            extra={"intent_source": intent_source},
        )

    if action == "search_memory":
        query = _extract_memory_query(message)
        return _agent_response(
            result=get_memory_learning(user_key, query=query),
            action="search_memory",
            extra={"intent_source": intent_source, "query": query},
        )

    if action == "search_interview_history":
        query = _extract_interview_history_query(message)
        if _requests_answered_interview_history(message) and not _contains_any(query, ("답변", "피드백")):
            query = f"{query} 답변 피드백 있는".strip()
        return _agent_response(
            result=search_interview_history(user_key, query=query),
            action="search_interview_history",
            extra={"intent_source": intent_source, "raw_query": query},
        )

    if action == "reset_learning":
        if "확인" not in message:
            result = get_status(user_key)
            return _agent_response(
                result=result,
                action="reset_learning",
                extra={"intent_source": intent_source, "required_input": "confirmation"},
            ) | {
                "ok": False,
                "reply": "약점 학습과 면접 기억을 초기화하려면 `약점 학습 초기화 확인`이라고 다시 보내줘.",
            }
        return _agent_response(
            result=reset_learning_data(user_key),
            action="reset_learning",
            extra={"intent_source": intent_source},
        )

    if action == "create_context_analysis":
        resolved_job_index = _resolve_job_index(message, user_key, job_index)
        if resolved_job_index is not None:
            selected = select_job(resolved_job_index, user_key)
            if not selected.ok:
                return _agent_response(
                    result=selected,
                    action="create_context_analysis",
                    extra={"intent_source": intent_source, "resolved_job_index": resolved_job_index},
                )
        result, should_start = create_context_analysis_job(user_key, force=_is_force_rerun(message))
        job = result.data.get("job")
        if should_start and job:
            background_tasks.add_task(run_context_analysis_job, job["id"], user_key)
        return _agent_response(
            result=result,
            action="create_context_analysis",
            extra={"intent_source": intent_source, "resolved_job_index": resolved_job_index},
        )

    if action == "create_question_plan":
        resolved_job_index = _resolve_question_plan_job_index(message, user_key, job_index)
        resolved_github_indices = _resolve_github_indices(message, user_key, github_indices)
        resolved_question_counts = question_counts or _question_counts_from_total(_extract_question_count(message))

        missing_inputs: list[str] = []
        if resolved_job_index is None:
            missing_inputs.append("공고")
        if not resolved_github_indices:
            missing_inputs.append("GitHub")
        if missing_inputs:
            result = get_status(user_key)
            return _agent_response(
                result=result,
                action="create_question_plan",
                extra={
                    "required_input": ",".join(missing_inputs),
                    "intent_source": intent_source,
                    "resolved_job_index": resolved_job_index,
                    "resolved_github_indices": resolved_github_indices,
                    "question_counts": resolved_question_counts,
                },
            ) | {
                "ok": False,
                "reply": (
                    f"질문 후보를 만들려면 {'와 '.join(missing_inputs)} 선택이 필요해.\n\n"
                    "예: 1번 공고랑 1번 GitHub로 질문 5개 만들어줘"
                ),
            }

        result, should_start = create_question_plan_job(
            job_index=resolved_job_index,
            github_indices=resolved_github_indices,
            question_counts=resolved_question_counts,
            user_key=user_key,
            force=_is_force_rerun(message),
        )
        job = result.data.get("job")
        if should_start and job:
            background_tasks.add_task(
                run_question_plan_job,
                job["id"],
                user_key,
                question_counts=resolved_question_counts,
            )
        return _agent_response(
            result=result,
            action="create_question_plan",
            extra={
                "intent_source": intent_source,
                "resolved_job_index": resolved_job_index,
                "resolved_github_indices": resolved_github_indices,
                "question_counts": resolved_question_counts,
            },
        )

    if action == "submit_interview_answer":
        resolved_answer = answer_text or _extract_answer_text(message)
        if not resolved_answer:
            result = get_interview(user_key)
            return _agent_response(
                result=result,
                action="submit_interview_answer",
                extra={"required_input": "answer", "intent_source": intent_source},
            ) | {
                "ok": False,
                "reply": "저장할 답변 내용을 같이 보내줘. 예: 내 답변은 ...",
            }
        return _agent_response(
            result=submit_interview_answer(resolved_answer, user_key),
            action="submit_interview_answer",
            extra={"intent_source": intent_source},
        )

    if action == "next_interview_question":
        return _agent_response(
            result=await next_interview_question(user_key),
            action="next_interview_question",
            extra={"intent_source": intent_source},
        )

    if action in {"create_followup_question", "create_another_question"}:
        mode = "followup" if action == "create_followup_question" else "another"
        result, should_start = create_bonus_question_job(mode, user_key)
        job = result.data.get("job")
        if should_start and job:
            background_tasks.add_task(run_bonus_question_job, job["id"], user_key, mode)
        return _agent_response(result=result, action=action, extra={"intent_source": intent_source})

    if action == "create_final_review":
        result, should_start = create_final_review_job(user_key)
        job = result.data.get("job")
        if should_start and job:
            background_tasks.add_task(run_final_review_job, job["id"], user_key)
        return _agent_response(result=result, action="create_final_review", extra={"intent_source": intent_source})

    if action == "end_interview":
        return _agent_response(result=end_interview(user_key), action="end_interview", extra={"intent_source": intent_source})

    return _fallback_response(message, user_key)


async def _handle_model_fallback(
    message: str,
    *,
    user_key: str,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    try:
        intent = await get_llm().classify_agent_intent(message)
    except Exception:
        return _fallback_response(message, user_key)

    action = _intent_action(intent)
    if action == "unknown" or _intent_confidence(intent) < 0.45:
        return _fallback_response(message, user_key)

    return await _handle_action(
        action=action,
        message=message,
        user_key=user_key,
        background_tasks=background_tasks,
        job_index=_intent_job_index(intent, message),
        job_text=_intent_job_text(intent, message),
        github_url=_intent_github_url(intent, message),
        github_indices=_intent_github_indices(intent, message),
        question_counts=_intent_question_counts(intent, message),
        profile_text=_intent_profile_text(intent, message),
        resume_text=_intent_resume_text(intent, message),
        answer_text=_intent_answer_text(intent, message),
        intent_source="model",
    )


def _split_compound_message(message: str) -> list[str]:
    if _looks_like_answer(message.strip().lower()):
        return [message.strip()] if message.strip() else []

    protected = re.sub(r"https?://\S+", "__URL__", message)
    separator = r"(?:\s+그리고\s+|\s+그\s*다음\s+|\s+다음에\s+|하고\s+|한\s*다음\s+)"
    if not re.search(separator, protected):
        return [message.strip()] if message.strip() else []

    parts = re.split(separator, message)
    return [part.strip(" \t\n\r,.;") for part in parts if part.strip(" \t\n\r,.;")]


def _response_started_background_job(response: dict[str, Any]) -> bool:
    data = response.get("data")
    if not isinstance(data, dict):
        return False
    job = data.get("job")
    return isinstance(job, dict) and job.get("status") in {"queued", "running"}


def _response_job_id(response: dict[str, Any]) -> int | None:
    data = response.get("data")
    if not isinstance(data, dict):
        return None
    job = data.get("job")
    if isinstance(job, dict) and isinstance(job.get("id"), int):
        return job["id"]
    return None


def _compound_response(responses: list[dict[str, Any]], stopped_reason: str = "") -> dict[str, Any]:
    last_response = responses[-1]
    step_lines = []
    for index, response in enumerate(responses, start=1):
        reply = str(response.get("reply") or "처리했어.").strip()
        action = str(response.get("action") or "unknown")
        step_lines.append(f"{index}. [{action}] {reply}")

    if stopped_reason:
        step_lines.append(stopped_reason)

    return {
        "ok": all(bool(response.get("ok")) for response in responses),
        "handled": True,
        "action": "compound",
        "reply": "\n\n".join(step_lines),
        "messages": [str(response.get("reply") or "") for response in responses],
        "status": last_response.get("status"),
        "data": {
            "steps": [
                {
                    "action": response.get("action"),
                    "ok": response.get("ok"),
                    "reply": response.get("reply"),
                }
                for response in responses
            ],
            "stopped_reason": stopped_reason,
        },
    }


async def _handle_single_agent_message(
    message: str,
    *,
    user_key: str,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    normalized = message.strip().lower()

    response: dict[str, Any]

    if _looks_like_job_save(normalized):
        response = await _handle_action(
            action="save_job_posting",
            message=message,
            user_key=user_key,
            background_tasks=background_tasks,
            job_text=_extract_job_text(message),
        )
        return _save_assistant_response(user_key, response)

    if _looks_like_github_analyze(normalized):
        response = await _handle_action(
            action="analyze_github_repository",
            message=message,
            user_key=user_key,
            background_tasks=background_tasks,
            github_url=_extract_github_url(message),
        )
        return _save_assistant_response(user_key, response)

    if _looks_like_profile_save(normalized):
        response = await _handle_action(
            action="save_profile",
            message=message,
            user_key=user_key,
            background_tasks=background_tasks,
            profile_text=_extract_profile_text(message),
        )
        return _save_assistant_response(user_key, response)

    if _looks_like_resume_save(normalized):
        response = await _handle_action(
            action="save_resume",
            message=message,
            user_key=user_key,
            background_tasks=background_tasks,
            resume_text=_extract_resume_text(message),
        )
        return _save_assistant_response(user_key, response)

    if "공고" in normalized and _contains_any(normalized, ("목록", "리스트", "보여", "불러", "조회")):
        response = await _handle_action(
            action="list_job_postings",
            message=message,
            user_key=user_key,
            background_tasks=background_tasks,
        )
        return _save_assistant_response(user_key, response)

    if _contains_any(normalized, ("github", "깃허브", "깃헙")) and _contains_any(
        normalized,
        ("목록", "리스트", "보여", "불러", "조회"),
    ):
        action = "get_github_analysis" if _contains_any(normalized, ("최근", "분석", "요약")) else "list_github_repositories"
        response = await _handle_action(
            action=action,
            message=message,
            user_key=user_key,
            background_tasks=background_tasks,
        )
        return _save_assistant_response(user_key, response)

    if _contains_any(normalized, ("자료", "입력", "컨텍스트")) and _contains_any(
        normalized,
        ("보여", "불러", "조회", "상태"),
    ):
        response = await _handle_action(
            action="get_context",
            message=message,
            user_key=user_key,
            background_tasks=background_tasks,
        )
        return _save_assistant_response(user_key, response)

    if "공고" in normalized and _contains_any(normalized, ("선택", "골라")):
        response = await _handle_action(
            action="select_job_posting",
            message=message,
            user_key=user_key,
            background_tasks=background_tasks,
            job_index=_extract_index(normalized),
        )
        return _save_assistant_response(user_key, response)

    if "면접" in normalized and _contains_any(normalized, ("시작", "시작해", "진행")):
        response = await _handle_action(
            action="start_interview",
            message=message,
            user_key=user_key,
            background_tasks=background_tasks,
            job_index=_extract_index(normalized),
        )
        return _save_assistant_response(user_key, response)

    if _looks_like_interview_continuation(normalized):
        response = await _handle_action(
            action="get_interview",
            message=message,
            user_key=user_key,
            background_tasks=background_tasks,
        )
        return _save_assistant_response(user_key, response)

    if "면접" in normalized and _contains_any(normalized, ("상태", "현재", "보여", "불러", "조회")):
        response = await _handle_action(
            action="get_interview",
            message=message,
            user_key=user_key,
            background_tasks=background_tasks,
        )
        return _save_assistant_response(user_key, response)

    if _looks_like_interview_history_search(normalized):
        response = await _handle_action(
            action="search_interview_history",
            message=message,
            user_key=user_key,
            background_tasks=background_tasks,
        )
        return _save_assistant_response(user_key, response)

    if _contains_any(normalized, ("리뷰", "회고", "피드백")) and _contains_any(
        normalized,
        ("최근", "불러", "보여", "조회", "알려"),
    ):
        response = await _handle_action(
            action="get_latest_review",
            message=message,
            user_key=user_key,
            background_tasks=background_tasks,
        )
        return _save_assistant_response(user_key, response)

    if _contains_any(normalized, ("약점", "학습", "기억")) and _contains_any(normalized, ("초기화", "삭제", "정리")):
        response = await _handle_action(
            action="reset_learning",
            message=message,
            user_key=user_key,
            background_tasks=background_tasks,
        )
        return _save_assistant_response(user_key, response)

    if (
        _contains_any(normalized, ("기억", "이전", "예전", "지난", "과거", "전에"))
        and _contains_any(normalized, ("피드백", "답변", "질문", "찾", "검색", "보여", "있어"))
    ) or (
        _contains_any(normalized, ("피드백", "답변", "질문"))
        and _contains_any(normalized, ("찾", "검색"))
    ):
        response = await _handle_action(
            action="search_memory",
            message=message,
            user_key=user_key,
            background_tasks=background_tasks,
        )
        return _save_assistant_response(user_key, response)

    if _contains_any(normalized, ("약점", "부족", "취약")) and _contains_any(
        normalized,
        ("보여", "불러", "조회", "분석", "알려", "정리"),
    ):
        response = await _handle_action(
            action="get_weaknesses",
            message=message,
            user_key=user_key,
            background_tasks=background_tasks,
        )
        return _save_assistant_response(user_key, response)

    if _contains_any(normalized, ("연습", "준비", "공부", "훈련")) and _contains_any(
        normalized,
        ("뭐", "무엇", "추천", "해야", "할까", "하면"),
    ):
        response = await _handle_action(
            action="get_training_plan",
            message=message,
            user_key=user_key,
            background_tasks=background_tasks,
        )
        return _save_assistant_response(user_key, response)

    if _contains_any(normalized, ("상태", "진행상황", "현황")):
        response = await _handle_action(
            action="get_status",
            message=message,
            user_key=user_key,
            background_tasks=background_tasks,
        )
        return _save_assistant_response(user_key, response)

    if _contains_any(normalized, ("분석", "리뷰", "봐줘")) and _contains_any(
        normalized,
        ("공고", "자료", "전체", "적합", "매칭", "준비"),
    ):
        response = await _handle_action(
            action="create_context_analysis",
            message=message,
            user_key=user_key,
            background_tasks=background_tasks,
            job_index=_extract_index(normalized),
        )
        return _save_assistant_response(user_key, response)

    if _looks_like_question_plan(normalized):
        response = await _handle_action(
            action="create_question_plan",
            message=message,
            user_key=user_key,
            background_tasks=background_tasks,
            job_index=_resolve_question_plan_job_index(message, user_key, _extract_index(normalized)),
            github_indices=_extract_github_indices(message),
            question_counts=_question_counts_from_total(_extract_question_count(message)),
        )
        return _save_assistant_response(user_key, response)

    if _looks_like_answer(normalized):
        response = await _handle_action(
            action="submit_interview_answer",
            message=message,
            user_key=user_key,
            background_tasks=background_tasks,
            answer_text=_extract_answer_text(message),
        )
        return _save_assistant_response(user_key, response)

    if "면접" in normalized and _contains_any(normalized, ("종료", "그만", "끝")):
        response = await _handle_action(
            action="end_interview",
            message=message,
            user_key=user_key,
            background_tasks=background_tasks,
        )
        return _save_assistant_response(user_key, response)

    if _contains_any(normalized, ("리뷰", "평가", "피드백")) and _contains_any(
        normalized,
        ("생성", "만들", "해줘", "진행"),
    ):
        response = await _handle_action(
            action="create_final_review",
            message=message,
            user_key=user_key,
            background_tasks=background_tasks,
        )
        return _save_assistant_response(user_key, response)

    if _contains_any(normalized, ("꼬리질문", "꼬리 질문", "followup")):
        response = await _handle_action(
            action="create_followup_question",
            message=message,
            user_key=user_key,
            background_tasks=background_tasks,
        )
        return _save_assistant_response(user_key, response)

    if _contains_any(normalized, ("추가질문", "추가 질문", "다른 질문")):
        response = await _handle_action(
            action="create_another_question",
            message=message,
            user_key=user_key,
            background_tasks=background_tasks,
        )
        return _save_assistant_response(user_key, response)

    if _contains_any(normalized, ("다음 질문", "다음으로", "넘어가", "넘겨")):
        response = await _handle_action(
            action="next_interview_question",
            message=message,
            user_key=user_key,
            background_tasks=background_tasks,
        )
        return _save_assistant_response(user_key, response)

    response = await _handle_model_fallback(message, user_key=user_key, background_tasks=background_tasks)
    return _save_assistant_response(user_key, response)


async def handle_agent_message(
    message: str,
    *,
    user_key: str,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    save_agent_chat_message(user_key, role="user", content=message)
    parts = _split_compound_message(message)
    if len(parts) <= 1:
        return await _handle_single_agent_message(
            message,
            user_key=user_key,
            background_tasks=background_tasks,
        )

    responses: list[dict[str, Any]] = []
    stopped_reason = ""
    pending_command = ""
    pending_wait_job_id: int | None = None
    for index, part in enumerate(parts[:5]):
        response = await _handle_single_agent_message(
            part,
            user_key=user_key,
            background_tasks=background_tasks,
        )
        responses.append(response)
        if not response.get("ok"):
            stopped_reason = "앞 단계에서 확인이 필요해서 여기서 멈췄어."
            break
        if _response_started_background_job(response) and index < len(parts) - 1:
            pending_wait_job_id = _response_job_id(response)
            pending_command = " 그리고 ".join(parts[index + 1 :])
            if pending_wait_job_id and pending_command:
                create_agent_pending_command(
                    user_key,
                    wait_job_id=pending_wait_job_id,
                    command=pending_command,
                )
                stopped_reason = "백그라운드 작업이 시작됐어. 완료되면 남은 명령을 자동으로 이어서 실행할게."
            else:
                stopped_reason = "백그라운드 작업이 시작돼서 다음 단계는 완료 후 이어서 실행해줘."
            break

    if not responses:
        response = _fallback_response(message, user_key)
        return _save_assistant_response(user_key, response)

    save_agent_action(
        user_key,
        action="compound",
        status="completed" if all(bool(response.get("ok")) for response in responses) else "failed",
        result_summary=f"{len(responses)}개 단계를 처리했어.",
        metadata={
            "step_actions": [response.get("action") for response in responses],
            "stopped_reason": stopped_reason,
            "pending_command": _command_log_summary(pending_command) if pending_command else "",
            "pending_wait_job_id": pending_wait_job_id,
        },
    )
    return _compound_response(responses, stopped_reason)


class _InlineBackgroundTasks:
    def add_task(self, func, *args, **kwargs) -> None:
        import asyncio

        result = func(*args, **kwargs)
        if asyncio.iscoroutine(result):
            asyncio.create_task(result)


async def run_pending_agent_commands_for_job(user_key: str, job_id: int) -> None:
    pending_commands = claim_agent_pending_commands(user_key, job_id)
    for pending in pending_commands:
        command = str(pending.get("command") or "").strip()
        if not command:
            complete_agent_pending_command(
                int(pending["id"]),
                status="failed",
                result_summary="이어 실행할 명령이 비어 있습니다.",
            )
            continue
        save_agent_chat_message(
            user_key,
            role="assistant",
            content=f"이전 작업이 완료돼서 이어서 실행할게.\n\n{command}",
            action="pending_command_started",
        )
        try:
            response = await handle_agent_message(
                command,
                user_key=user_key,
                background_tasks=_InlineBackgroundTasks(),
            )
            complete_agent_pending_command(
                int(pending["id"]),
                status="completed" if response.get("ok") else "failed",
                result_summary=str(response.get("reply", "")),
            )
        except Exception as exc:
            complete_agent_pending_command(
                int(pending["id"]),
                status="failed",
                result_summary=str(exc)[:500],
            )
            save_agent_chat_message(
                user_key,
                role="assistant",
                content=f"이어 실행에 실패했어.\n\n{str(exc)[:500]}",
                action="pending_command_failed",
            )


def fail_pending_agent_commands_for_job(user_key: str, job_id: int, reason: str) -> None:
    pending_commands = claim_agent_pending_commands(user_key, job_id)
    for pending in pending_commands:
        complete_agent_pending_command(
            int(pending["id"]),
            status="failed",
            result_summary=reason[:500],
        )
        save_agent_chat_message(
            user_key,
            role="assistant",
            content=f"앞 작업이 실패해서 이어 실행을 멈췄어.\n\n{reason[:500]}",
            action="pending_command_failed",
        )
