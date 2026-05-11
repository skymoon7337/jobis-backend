import asyncio
import logging
import os
import re
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import load_environment
from db.repository import (
    create_job_posting,
    create_interview_session,
    delete_job_posting,
    get_active_interview_notices,
    get_active_interview_snapshot,
    get_job_postings,
    get_progress_notices,
    get_latest_feedback_summary,
    get_recent_sessions,
    get_latest_weakness_summary,
    get_selected_job_posting,
    load_user_session,
    reset_user_context,
    save_interview_question,
    save_interview_questions,
    select_job_posting,
    save_interview_turn,
    update_interview_session,
    update_user_fields,
)
from services.github import fetch_repo_context
from services.llm import JobisLLM
from services.session import InterviewTurn, PlannedQuestion, UserSession
from services.webpage import fetch_page_text

load_environment()

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)

MAX_TURNS = 5
TELEGRAM_MESSAGE_LIMIT = 3900
LLM_RETRY_DELAYS = (5, 10, 15)
sessions: dict[int, UserSession] = {}
llm = JobisLLM()
T = TypeVar("T")

BOT_COMMANDS = [
    BotCommand("start", "시작 및 진행 순서 보기"),
    BotCommand("help", "전체 명령어 보기"),
    BotCommand("profile", "관심 직무/경력/학력/기술스택 입력"),
    BotCommand("resume", "자소서 텍스트 입력"),
    BotCommand("github", "GitHub 레포 분석 실행"),
    BotCommand("job", "공고 추가/목록/선택/삭제"),
    BotCommand("analyze", "자소서/GitHub/공고 통합 분석 실행"),
    BotCommand("interview", "5문항 면접 시작"),
    BotCommand("continue", "진행 중이던 면접 이어하기"),
    BotCommand("next", "다음 질문 또는 전체 평가로 이동"),
    BotCommand("followup", "방금 답변 꼬리질문"),
    BotCommand("another", "같은 분야 추가질문"),
    BotCommand("review", "자소서 피드백"),
    BotCommand("show_status", "현재 입력 상태 보기"),
    BotCommand("show_history", "최근 면접 기록 보기"),
    BotCommand("show_feedback", "최근 전체 면접 피드백 보기"),
    BotCommand("show_weakness", "최근 약점 요약 보기"),
    BotCommand("show_github", "GitHub 상세 분석 보기"),
    BotCommand("show_analyze", "통합 상세 분석 보기"),
    BotCommand("end", "면접 종료"),
    BotCommand("reset", "초기화"),
]


def get_chat_id(update: Update) -> int:
    chat = update.effective_chat
    if chat is None:
        raise RuntimeError("Telegram chat 정보를 찾지 못했습니다.")
    return chat.id


def parse_allowed_chat_ids() -> set[int]:
    raw_ids = os.getenv("ALLOWED_TELEGRAM_CHAT_IDS", "")
    chat_ids: set[int] = set()

    for raw_id in raw_ids.split(","):
        raw_id = raw_id.strip()
        if not raw_id:
            continue
        try:
            chat_ids.add(int(raw_id))
        except ValueError as exc:
            raise RuntimeError(
                "ALLOWED_TELEGRAM_CHAT_IDS는 쉼표로 구분된 숫자 chat_id 목록이어야 합니다."
            ) from exc

    return chat_ids


def get_allowed_chat_ids(context: ContextTypes.DEFAULT_TYPE) -> set[int]:
    allowed_chat_ids = context.application.bot_data.get("allowed_chat_ids", set())
    return set(allowed_chat_ids)


async def reject_unauthorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    allowed_chat_ids = get_allowed_chat_ids(context)
    if chat is not None and chat.id in allowed_chat_ids:
        return

    chat_id = chat.id if chat is not None else "unknown"
    logging.warning("허용되지 않은 Telegram chat_id 접근을 차단했습니다: %s", chat_id)
    if update.message:
        await update.message.reply_text("이 jobis 봇은 허용된 사용자만 사용할 수 있습니다.")
    raise ApplicationHandlerStop


def get_session(update: Update) -> UserSession:
    chat_id = get_chat_id(update)
    if chat_id not in sessions:
        sessions[chat_id] = load_user_session(chat_id)
    return sessions[chat_id]


def command_payload(update: Update) -> str:
    if not update.message or not update.message.text:
        return ""
    parts = update.message.text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


async def reply(update: Update, text: str) -> None:
    if not update.message:
        return

    chunks = split_telegram_message(text)
    total = len(chunks)
    for index, chunk in enumerate(chunks, start=1):
        prefix = f"({index}/{total})\n" if total > 1 else ""
        await update.message.reply_text(prefix + chunk)


def is_retryable_llm_error(exc: Exception) -> bool:
    message = str(exc).lower()
    if is_quota_exceeded_error(exc):
        return False

    retryable_signals = (
        "503",
        "unavailable",
        "high demand",
        "temporarily",
        "timeout",
        "timed out",
        "rate limit",
    )
    return any(signal in message for signal in retryable_signals)


def is_quota_exceeded_error(exc: Exception) -> bool:
    message = str(exc).lower()
    quota_signals = (
        "exceeded your current quota",
        "quota",
        "resource_exhausted",
        "billing details",
    )
    return any(signal in message for signal in quota_signals)


def short_error(exc: Exception, limit: int = 300) -> str:
    message = str(exc).strip()
    if len(message) <= limit:
        return message
    return message[:limit].rstrip() + "..."


def llm_failure_message(action: str, exc: Exception, next_step: str) -> str:
    if is_quota_exceeded_error(exc):
        return (
            f"{action}에 실패했습니다.\n\n"
            "Gemini 무료 사용량 또는 요청 한도를 초과했습니다. 이 경우 자동 재시도해도 바로 풀리지 않을 수 있어요.\n"
            f"{next_step}\n\n"
            "해결 방법: 잠시 뒤 다시 시도하거나, 오늘 사용량이 다 찬 경우 할당량이 초기화된 뒤 다시 실행해주세요. "
            "반복되면 모델을 더 가벼운 것으로 바꾸거나 API 제공자를 바꾸는 방법도 있습니다.\n\n"
            f"원인: {short_error(exc)}"
        )

    if is_retryable_llm_error(exc):
        return (
            f"{action}에 실패했습니다.\n\n"
            "Gemini가 계속 혼잡해서 자동 재시도를 모두 마쳤지만 응답을 받지 못했어요.\n"
            f"{next_step}\n\n"
            f"원인: {short_error(exc)}"
        )

    return (
        f"{action}에 실패했습니다.\n\n"
        f"{next_step}\n\n"
        f"원인: {short_error(exc)}"
    )


async def run_with_llm_retry(
    update: Update,
    action: str,
    operation: Callable[[], Awaitable[T]],
) -> T:
    for attempt, delay in enumerate(LLM_RETRY_DELAYS, start=1):
        try:
            return await operation()
        except Exception as exc:
            if not is_retryable_llm_error(exc):
                raise

            await reply(
                update,
                f"{action} 중 Gemini가 잠시 혼잡합니다. "
                f"{delay}초 뒤 자동 재시도할게요. ({attempt}/{len(LLM_RETRY_DELAYS)})",
            )
            await asyncio.sleep(delay)

    return await operation()


def split_telegram_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        split_at = remaining.rfind("\n\n", 0, limit)
        if split_at < limit // 2:
            split_at = remaining.rfind("\n", 0, limit)
        if split_at < limit // 2:
            split_at = limit

        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()

    if remaining:
        chunks.append(remaining)

    return chunks


def format_for_telegram(text: str) -> str:
    formatted = text.replace("```", "")
    formatted = re.sub(r"^#{1,6}\s*", "", formatted, flags=re.MULTILINE)
    formatted = formatted.replace("**", "")
    formatted = re.sub(r"^\s*[*]\s+", "- ", formatted, flags=re.MULTILINE)
    formatted = re.sub(r"\n{3,}", "\n\n", formatted)
    return formatted.strip()


def format_interview_feedback(text: str) -> str:
    formatted = format_for_telegram(text)
    labels = "평가|핵심 피드백|보완 포인트|전체 면접 피드백|총평|강점|보완할 점|보완|약점 요약|다음 준비"
    formatted = re.sub(
        r"(?m)^\s*(\d+-\d+(?:-f\d+)?)\.?\s*(\[[^\]]+\])",
        r"\n▶ \1 \2",
        formatted,
    )
    formatted = re.sub(
        r"(?m)^\s*(\d+-\d+(?:-f\d+)?)\.?\s*질문\s*:\s*",
        r"\n▶ \1\n질문: ",
        formatted,
    )
    formatted = re.sub(
        r"(?<!^)(?<!\n)(\s+)(\d+\.\s+)(?=\S)",
        r"\n\n\2",
        formatted,
    )
    formatted = re.sub(
        rf"^\s*({labels})\s*:\s*",
        r"\1\n",
        formatted,
        flags=re.MULTILINE,
    )
    formatted = re.sub(rf"^\s*({labels})\s*$", r"\n\1", formatted, flags=re.MULTILINE)
    formatted = re.sub(r"\n{3,}", "\n\n", formatted)
    return formatted.strip()


def split_feedback_and_next_question(feedback: str) -> tuple[str, str | None]:
    match = re.search(r"(?:^|\n)다음 질문\s*:\s*", feedback)
    if not match:
        return format_interview_feedback(feedback), None

    feedback_text = feedback[: match.start()].strip()
    question_text = feedback[match.end() :].strip()
    question_text = question_text.splitlines()[0].strip() if question_text else ""
    return format_interview_feedback(feedback_text), question_text or None


def split_final_feedback(feedback: str) -> tuple[str, str | None]:
    match = re.search(r"(?:^|\n)전체 면접 피드백\s*:?\s*", feedback)
    if not match:
        return format_interview_feedback(feedback), None

    answer_feedback = feedback[: match.start()].strip()
    overall_feedback = feedback[match.end() :].strip()
    return (
        format_interview_feedback(answer_feedback),
        "전체 면접 피드백\n\n" + format_interview_feedback(overall_feedback),
    )


def split_tagged_sections(text: str, first_tag: str, second_tag: str) -> tuple[str, str]:
    pattern = rf"\[{re.escape(first_tag)}\](.*?)\[{re.escape(second_tag)}\](.*)"
    match = re.search(pattern, text, flags=re.DOTALL | re.IGNORECASE)
    if not match:
        return "", text.strip()
    return match.group(1).strip(), match.group(2).strip()


def parse_job_summary(text: str) -> tuple[str, str]:
    title, summary = split_tagged_sections(text, "TITLE", "SUMMARY")
    title = title.splitlines()[0].strip() if title else "미상 - 채용공고"
    summary = summary or text.strip()
    return title[:200], format_for_telegram(summary)


def parse_job_index(payload: str) -> int | None:
    try:
        return int(payload.strip())
    except ValueError:
        return None


def format_job_menu(chat_id: int) -> str:
    postings = get_job_postings(chat_id)
    if not postings:
        list_text = "저장된 공고가 없습니다."
    else:
        lines = []
        for posting in postings:
            mark_text = "✅ " if posting["is_selected"] else ""
            index = posting["index"]
            lines.append(f"{mark_text}[{index}] {posting['title']}")
        list_text = "\n".join(lines)

    return (
        "공고 관리\n\n"
        f"{list_text}\n\n"
        "사용법\n"
        "/job add - 공고 URL 또는 본문 추가\n"
        "/job show - 현재 선택된 공고 요약 보기\n"
        "/job select 번호 - 현재 면접 기준 공고 선택\n"
        "/job delete 번호 - 저장된 공고 삭제\n\n"
        "기존처럼 /job 뒤에 공고 본문이나 URL을 바로 붙여넣어도 추가됩니다."
    )


def format_job_detail(posting: dict[str, Any]) -> str:
    source = f"\n출처\n{posting['source_url']}\n" if posting["source_url"] else ""
    return (
        "현재 선택된 공고\n\n"
        f"[{posting['index']}] {posting['title']}\n"
        f"{source}\n"
        f"{posting['summary']}\n\n"
        "다음 추천: /analyze"
    ).strip()


def extract_weakness_summary(feedback: str) -> str:
    match = re.search(
        r"(?:^|\n)약점 요약\s*:?\s*(.*?)(?=\n(?:다음 준비|5턴 면접이 종료되었습니다)\s*:?\s*|\Z)",
        feedback,
        flags=re.DOTALL,
    )
    if not match:
        return ""
    return format_interview_feedback(match.group(1).strip())


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


def strip_question_label(question: str) -> str:
    return re.sub(r"^\s*\[[^\]]+\]\s*", "", question).strip()


def format_question(question_type: str, question: str, display_id: str) -> str:
    return f"{display_id} [{question_label(question_type)}] {strip_question_label(question)}"


def extract_next_question(feedback: str) -> str:
    marker = "다음 질문:"
    if marker not in feedback:
        return feedback.strip()

    question = feedback.split(marker, maxsplit=1)[1].strip()
    return question.splitlines()[0].strip() if question else feedback.strip()


def parse_planned_questions(text: str) -> list[PlannedQuestion]:
    expected_types = ("CS 기본기", "언어", "기술스택", "프로젝트/GitHub", "프로젝트/자소서")
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


def format_planned_question(question: PlannedQuestion, display_id: str) -> str:
    return format_question(question.question_type, question.question, display_id)


def format_bonus_question(question_type: str, question: str, bonus_type: str, display_id: str) -> str:
    label = "꼬리질문" if bonus_type == "followup" else "추가질문"
    return f"{display_id} [{question_label(question_type)}/{label}] {strip_question_label(question)}"


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

    if session.turn_count >= MAX_TURNS:
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


def build_choice_message(session: UserSession) -> str:
    return "답변을 저장했습니다.\n\n" + build_choice_options(session)


def build_choice_options(session: UserSession) -> str:
    if session.turn_count >= MAX_TURNS:
        next_description = "전체 평가 생성"
    else:
        next_description = "다음 질문으로 이동"

    return (
        "다음 선택\n"
        f"/next - {next_description}\n"
        "/followup - 방금 답변을 더 깊게 파기\n"
        "/another - 같은 분야 질문 하나 더 받기"
    )


def build_restored_choice_message(session: UserSession) -> str:
    last_turn = session.history[-1] if session.history else None
    if last_turn and last_turn.display_id:
        saved_line = f"{last_turn.display_id} 답변까지 저장되어 있습니다."
    else:
        saved_line = "마지막 답변까지 저장되어 있습니다."

    return (
        "진행 중이던 면접을 복원했습니다.\n\n"
        f"{saved_line}\n"
        "다음 행동을 선택해주세요.\n\n"
        f"{build_choice_options(session)}"
    )


def build_resume_notice_message(notice: dict[str, Any]) -> str:
    if notice["current_display_id"]:
        status = f"{notice['current_display_id']} 질문 답변 대기 중"
    elif notice["turn_count"] >= MAX_TURNS:
        status = "5문항 답변 완료, 전체 평가 생성 대기 중"
    elif notice["awaiting_choice"]:
        last_display_id = notice.get("last_display_id", "")
        last_question = strip_question_label(notice.get("last_question", ""))
        if last_display_id and last_question:
            status = f"{last_display_id} 답변 저장 완료, 다음 선택 대기 중\n마지막 질문: {last_question[:120]}"
        else:
            status = "마지막 답변 저장 완료, 다음 선택 대기 중"
    else:
        status = "진행 중인 면접 있음"

    return (
        "jobis가 다시 실행되었습니다.\n\n"
        "서버가 꺼져 있는 동안 보낸 메시지는 처리하지 않았습니다.\n\n"
        "진행 중이던 면접이 있습니다.\n"
        f"현재 상태: {status}\n\n"
        "/continue - 이어서 진행\n"
        "/end - 현재 면접만 종료"
    )


def build_restart_progress_message(notice: dict[str, Any]) -> str:
    session = UserSession(
        profile=notice.get("profile", ""),
        resume=notice.get("resume", ""),
        github_url=notice.get("github_url", ""),
        github_summary=notice.get("github_summary", ""),
        job_posting=notice.get("job_posting", ""),
        analysis_summary=notice.get("analysis_summary", ""),
    )
    return (
        "jobis가 다시 실행되었습니다.\n\n"
        "서버가 꺼져 있는 동안 보낸 메시지는 처리하지 않았습니다.\n\n"
        f"{build_progress_message(session)}"
    )


async def reject_context_change_during_interview(update: Update, session: UserSession) -> bool:
    if not session.in_interview:
        return False

    await reply(
        update,
        "진행 중인 면접이 있습니다.\n\n"
        "자료를 수정하거나 다시 분석하면 현재 면접 질문/평가와 컨텍스트가 섞일 수 있어요.\n"
        "먼저 /end 로 현재 면접을 종료하거나, 면접을 끝낸 뒤 다시 시도해주세요.",
    )
    return True


def invalidate_analysis(session: UserSession) -> None:
    session.analysis_summary = ""


def build_context_updated_message(saved_label: str, changed_label: str, session: UserSession) -> str:
    return (
        f"{saved_label} 저장했습니다.\n\n"
        f"{changed_label} 변경되었습니다.\n"
        "/analyze 를 다시 실행하면 새 자료 기준으로 면접 설계를 갱신할 수 있습니다.\n\n"
        f"{build_progress_message(session)}"
    )


def mark(done: bool) -> str:
    return "완료" if done else "미입력"


def next_recommendation(session: UserSession) -> str:
    if not session.profile:
        return "/profile"
    if not session.resume:
        return "/resume"
    if not session.github_summary:
        return "/github"
    if not session.job_posting:
        return "/job"
    if not session.analysis_summary:
        return "/analyze"
    return "/interview"


def build_progress_message(session: UserSession) -> str:
    return (
        "현재 상태\n"
        f"- 프로필: {mark(bool(session.profile))}\n"
        f"- 자소서: {mark(bool(session.resume))}\n"
        f"- GitHub 분석: {mark(bool(session.github_summary))}\n"
        f"- 공고: {mark(bool(session.job_posting))}\n"
        f"- 통합 분석: {mark(bool(session.analysis_summary))}\n\n"
        f"다음 추천: {next_recommendation(session)}"
    )


def build_start_message(session: UserSession) -> str:
    return (
        "jobis 면접 에이전트입니다.\n\n"
        "진행 순서\n"
        "1. /profile - 관심 직무, 경력, 학력, 기술스택 입력\n"
        "2. /resume - 자소서 입력\n"
        "3. /github - GitHub 레포 분석\n"
        "4. /job - 공고 추가/목록/선택/삭제\n"
        "5. /analyze - 통합 분석\n"
        "6. /interview - 면접 시작\n\n"
        "면접 중 선택\n"
        "/continue - 진행 중이던 면접 이어하기\n"
        "/next - 다음 질문 또는 전체 평가\n"
        "/followup - 방금 답변 꼬리질문\n"
        "/another - 같은 분야 추가질문\n\n"
        "면접 구성\n"
        "1. CS 기본기\n"
        "2. 언어\n"
        "3. 기술스택\n"
        "4. 프로젝트/GitHub\n"
        "5. 프로젝트/자소서\n\n"
        "주의\n"
        "자소서와 면접 답변은 Telegram, LLM API, 로컬 DB를 거칩니다. 민감정보는 입력하지 마세요.\n\n"
        f"{build_progress_message(session)}\n\n"
        "명령어 목록은 아래에 같이 보냈어요. 나중에는 /help 로 다시 볼 수 있습니다."
    )


def build_help_message() -> str:
    return (
        "jobis 명령어\n\n"
        "입력/실행\n"
        "/profile - 관심 직무, 경력, 학력, 기술스택 입력\n"
        "/resume - 자소서 텍스트 입력\n"
        "/github - GitHub 레포 분석 실행\n"
        "/job - 공고 추가/목록/선택/삭제\n"
        "/analyze - 입력 자료 통합 분석 실행\n"
        "/interview - 5문항 면접 시작(CS/언어/기술스택/프로젝트)\n"
        "/continue - 진행 중이던 면접 이어하기\n"
        "/next - 다음 질문 또는 전체 평가로 이동\n"
        "/followup - 방금 답변 꼬리질문\n"
        "/another - 같은 분야 추가질문\n"
        "/review - 자소서 피드백\n\n"
        "보기\n"
        "/show_status - 현재 입력 상태 보기\n"
        "/show_history - 최근 면접 기록 보기\n"
        "/show_feedback - 최근 전체 면접 피드백 보기\n"
        "/show_weakness - 최근 약점 요약 보기\n"
        "/show_github - 저장된 GitHub 상세 분석 보기\n"
        "/show_analyze - 저장된 통합 상세 분석 보기\n\n"
        "관리\n"
        "/end - 면접 종료\n"
        "/reset - 입력 자료 초기화(면접 기록 유지)"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = get_session(update)
    await reply(update, build_start_message(session))
    await reply(update, build_help_message())


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply(update, build_help_message())


async def continue_interview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = get_session(update)
    snapshot = get_active_interview_snapshot(get_chat_id(update))
    if not snapshot:
        await reply(update, "이어갈 진행 중 면접이 없습니다. 새로 시작하려면 /interview 를 입력하세요.")
        return

    restored_state = restore_interview_session(session, snapshot)
    if restored_state == "question" and session.current_question:
        await reply(
            update,
            "진행 중이던 면접을 복원했습니다.\n\n"
            "아직 답변하지 않은 질문입니다.\n\n"
            f"{format_current_question(session)}",
        )
        return

    await reply(
        update,
        build_restored_choice_message(session),
    )


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat is not None:
        sessions.pop(chat.id, None)
        reset_user_context(chat.id)
    await reply(
        update,
        "입력 자료를 초기화했습니다.\n\n"
        "삭제된 것\n"
        "- 프로필\n"
        "- 자소서\n"
        "- GitHub 분석\n"
        "- 공고\n"
        "- 통합 분석\n\n"
        "남아있는 것\n"
        "- 지난 면접 기록\n"
        "- 전체 피드백\n"
        "- 약점 요약\n\n"
        "다시 시작하려면 /start 를 입력하세요.",
    )


async def show_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = get_session(update)
    interview_status = "진행 중" if session.in_interview else "대기 중"
    await reply(update, f"{build_progress_message(session)}\n- 면접: {interview_status}")


async def show_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = get_chat_id(update)
    recent_sessions = get_recent_sessions(chat_id)
    if not recent_sessions:
        await reply(update, "아직 저장된 면접 기록이 없습니다. /interview 로 면접을 먼저 진행해보세요.")
        return

    lines = ["최근 면접 기록"]
    for index, item in enumerate(recent_sessions, start=1):
        created_at = item["created_at"].strftime("%Y-%m-%d %H:%M")
        summary = item["summary"].splitlines()[0] if item["summary"] else "총평 없음"
        lines.append(
            f"{index}. {created_at} / {item['status']} / 질문 {item['turn_count']}개\n"
            f"   {summary[:120]}"
        )

    await reply(update, "\n".join(lines))


async def show_weakness(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = get_chat_id(update)
    weakness_summary = get_latest_weakness_summary(chat_id)
    if not weakness_summary:
        await reply(
            update,
            "저장된 약점 요약이 없습니다.\n"
            "새 면접을 5턴까지 완료하면 전체 면접 피드백에서 약점 요약이 자동 저장됩니다.",
        )
        return

    await reply(update, "최근 저장된 약점 요약입니다.\n\n" + format_interview_feedback(weakness_summary))


async def show_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = get_chat_id(update)
    feedback_summary = get_latest_feedback_summary(chat_id)
    if not feedback_summary:
        await reply(
            update,
            "저장된 전체 면접 피드백이 없습니다.\n"
            "면접을 5턴까지 완료하면 전체 피드백이 자동 저장됩니다.",
        )
        return

    answer_feedback, overall_feedback = split_final_feedback(feedback_summary)
    await reply(update, "최근 전체 면접 피드백입니다.\n\n" + answer_feedback)
    if overall_feedback:
        await reply(update, overall_feedback)


async def show_github(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = get_session(update)
    if not session.github_summary:
        await reply(update, "저장된 GitHub 분석이 없습니다. 먼저 /github 로 레포를 분석해주세요.")
        return

    await reply(update, "저장된 GitHub 상세 분석입니다.\n\n" + format_for_telegram(session.github_summary))


async def show_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = get_session(update)
    if not session.analysis_summary:
        await reply(update, "저장된 통합 분석이 없습니다. 먼저 /analyze 를 실행해주세요.")
        return

    await reply(update, "저장된 통합 상세 분석입니다.\n\n" + format_for_telegram(session.analysis_summary))


async def set_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = get_session(update)
    if await reject_context_change_during_interview(update, session):
        return

    payload = command_payload(update)
    if not payload:
        session.awaiting = "profile"
        await reply(update, "관심 직무, 경력, 학력, 기술스택을 한 번에 보내주세요.")
        return

    session.profile = payload
    session.awaiting = None
    invalidate_analysis(session)
    update_user_fields(get_chat_id(update), profile=payload, analysis_summary="")
    await reply(update, build_context_updated_message("프로필을", "프로필이", session))


async def set_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = get_session(update)
    if await reject_context_change_during_interview(update, session):
        return

    payload = command_payload(update)
    if not payload:
        session.awaiting = "resume"
        await reply(update, "자소서 텍스트를 그대로 붙여넣어 주세요.")
        return

    session.resume = payload
    session.awaiting = None
    invalidate_analysis(session)
    update_user_fields(get_chat_id(update), resume=payload, analysis_summary="")
    await reply(update, build_context_updated_message("자소서를", "자소서가", session))


async def set_job(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = get_session(update)
    if await reject_context_change_during_interview(update, session):
        return

    payload = command_payload(update)
    if not payload:
        await reply(update, format_job_menu(get_chat_id(update)))
        return

    action, _, rest = payload.partition(" ")
    action = action.lower().strip()
    rest = rest.strip()

    if action == "add":
        if not rest:
            session.awaiting = "job_add"
            await reply(update, "추가할 공고 URL 또는 공고 본문을 보내주세요.\n이미지 공고는 아직 자동 인식하지 않으니 텍스트로 옮겨서 보내주세요.")
            return
        await save_job(update, session, rest)
        return

    if action == "show":
        selected = get_selected_job_posting(get_chat_id(update))
        if not selected:
            await reply(update, "현재 선택된 공고가 없습니다. /job add 로 공고를 먼저 추가해주세요.")
            return
        await reply(update, format_job_detail(selected))
        return

    if action == "select":
        index = parse_job_index(rest)
        if index is None:
            await reply(update, "선택할 공고 번호를 입력해주세요. 예: /job select 2")
            return
        selected = select_job_posting(get_chat_id(update), index)
        if not selected:
            await reply(update, "해당 번호의 공고를 찾지 못했습니다. /job 으로 목록을 확인해주세요.")
            return
        session.job_posting = selected["raw_text"]
        invalidate_analysis(session)
        await reply(update, f"[{selected['index']}] {selected['title']} 공고를 현재 면접 기준으로 선택했습니다.\n\n다음 추천: /analyze")
        return

    if action == "delete":
        index = parse_job_index(rest)
        if index is None:
            await reply(update, "삭제할 공고 번호를 입력해주세요. 예: /job delete 2")
            return
        deleted, was_selected = delete_job_posting(get_chat_id(update), index)
        if not deleted:
            await reply(update, "해당 번호의 공고를 찾지 못했습니다. /job 으로 목록을 확인해주세요.")
            return
        if was_selected:
            session.job_posting = ""
            invalidate_analysis(session)
            await reply(
                update,
                f"[{deleted['index']}] {deleted['title']} 공고를 삭제했습니다.\n\n"
                "삭제한 공고가 현재 선택된 공고였습니다.\n"
                "다른 공고를 선택하려면 /job 으로 목록을 확인한 뒤 /job select 번호 를 입력하세요.",
            )
        else:
            await reply(update, f"[{deleted['index']}] {deleted['title']} 공고를 삭제했습니다.")
        return

    await save_job(update, session, payload)


async def save_job(update: Update, session: UserSession, text: str) -> None:
    session.awaiting = None
    source_url = ""
    if text.startswith(("http://", "https://")):
        source_url = text
        await reply(update, "공고 페이지를 읽어볼게요. 사이트가 막으면 본문 붙여넣기로 다시 받을 수 있어요.")
        try:
            text = await fetch_page_text(text)
        except Exception as exc:
            await reply(
                update,
                "이 URL은 자동으로 읽지 못했습니다.\n"
                f"원인: {exc}\n\n"
                "공고 본문을 복사해서 /job add 뒤에 붙여넣거나, /job add 입력 후 다음 메시지로 보내주세요.",
            )
            return

    await reply(update, "공고를 면접 준비용으로 요약해서 저장하는 중입니다.")
    try:
        job_result = await run_with_llm_retry(
            update,
            "공고 요약",
            lambda: llm.summarize_job_posting(text),
        )
        title, summary = parse_job_summary(job_result)
    except Exception as exc:
        await reply(update, llm_failure_message("공고 요약", exc, "잠시 후 같은 공고로 /job add 를 다시 실행해주세요."))
        return

    session.job_posting = text
    invalidate_analysis(session)
    posting = create_job_posting(
        get_chat_id(update),
        title=title,
        source_url=source_url,
        raw_text=text,
        summary=summary,
    )
    await reply(
        update,
        "공고를 저장하고 현재 면접 기준으로 선택했습니다.\n\n"
        f"[{posting['index']}] {posting['title']}\n\n"
        f"{posting['summary']}\n\n"
        f"{build_progress_message(session)}",
    )


async def set_github(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = get_session(update)
    if await reject_context_change_during_interview(update, session):
        return

    payload = command_payload(update)
    if not payload:
        session.awaiting = "github"
        await reply(update, "분석할 GitHub 레포 URL을 보내주세요.")
        return

    await save_github(update, session, payload)


async def save_github(update: Update, session: UserSession, url: str) -> None:
    session.awaiting = None
    session.github_url = url
    await reply(update, "GitHub 레포 구조와 주요 파일을 읽고 요약하는 중입니다.")

    try:
        repo_context = await fetch_repo_context(url)
        github_result = await run_with_llm_retry(
            update,
            "GitHub 분석",
            lambda: llm.summarize_github(repo_context),
        )
        user_summary, detail_summary = split_tagged_sections(github_result, "USER_SUMMARY", "DETAIL")
        session.github_summary = detail_summary or github_result
        if not user_summary:
            user_summary = (
                "요약\n"
                "- GitHub 분석을 저장했습니다.\n"
                "- 상세 내용은 /show_github 에서 확인할 수 있습니다.\n\n"
                "다음 단계: /job 또는 /analyze"
            )
    except Exception as exc:
        await reply(
            update,
            llm_failure_message("GitHub 분석", exc, "잠시 후 같은 GitHub URL로 /github 를 다시 실행해주세요."),
        )
        return

    invalidate_analysis(session)
    update_user_fields(
        get_chat_id(update),
        github_url=session.github_url,
        github_summary=session.github_summary,
        analysis_summary="",
    )
    await reply(
        update,
        "GitHub 분석을 저장했습니다.\n\n"
        f"{user_summary}\n\n"
        "GitHub 자료가 변경되었습니다.\n"
        "/analyze 를 다시 실행하면 새 GitHub 분석 기준으로 면접 설계를 갱신할 수 있습니다.\n\n"
        "상세 분석: /show_github\n"
        f"{build_progress_message(session)}",
    )


async def interview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = get_session(update)
    if not session.has_context():
        await reply(update, "아직 면접 컨텍스트가 없습니다. /resume 또는 /github 중 하나는 먼저 넣어주세요.")
        return

    session.reset_interview()
    session.in_interview = True
    session.active_interview_session_id = create_interview_session(get_chat_id(update))
    await reply(update, "면접을 시작합니다. 질문 5개를 생성하며, 답변 후 꼬리질문과 추가질문을 받을 수 있습니다.")

    try:
        question_result = await run_with_llm_retry(
            update,
            "면접 질문 생성",
            lambda: llm.generate_interview_questions(session.build_interview_context()),
        )
        session.planned_questions = parse_planned_questions(question_result)
        if not session.planned_questions:
            raise ValueError("질문 5개를 정해진 형식으로 파싱하지 못했습니다.")

        for planned_question in session.planned_questions:
            planned_question.display_id = next_section_display_id(session, planned_question.question_type)

        if session.active_interview_session_id:
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
        session.current_question_type = first_question.question_type
        session.current_question = first_question.question
        session.current_display_id = first_question.display_id
        session.current_question_is_bonus = False
        session.current_bonus_type = ""
        session.awaiting_choice = False
        if session.active_interview_session_id:
            update_interview_session(
                session.active_interview_session_id,
                current_display_id=session.current_display_id,
                awaiting_choice=False,
            )
    except Exception as exc:
        session.in_interview = False
        if session.active_interview_session_id:
            update_interview_session(session.active_interview_session_id, status="failed", summary=str(exc))
        await reply(
            update,
            llm_failure_message("면접 질문 생성", exc, "잠시 후 /interview 를 다시 실행해주세요."),
        )
        return

    await reply(update, format_planned_question(first_question, session.current_display_id))


async def analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = get_session(update)
    if await reject_context_change_during_interview(update, session):
        return

    if not session.has_context():
        await reply(update, "분석할 자료가 없습니다. /profile, /resume, /github, /job 중 하나는 먼저 넣어주세요.")
        return

    await reply(update, "자소서, GitHub, 공고를 묶어서 면접 설계용으로 분석하는 중입니다.")
    try:
        analysis_result = await run_with_llm_retry(
            update,
            "통합 분석",
            lambda: llm.analyze_context(session.build_context()),
        )
        user_summary, detail_summary = split_tagged_sections(analysis_result, "USER_SUMMARY", "DETAIL")
        session.analysis_summary = detail_summary or analysis_result
        if not user_summary:
            user_summary = (
                "요약\n"
                "- 통합 분석을 저장했습니다.\n"
                "- 상세 내용은 /show_analyze 에서 확인할 수 있습니다.\n\n"
                "다음 단계: /interview"
            )
    except Exception as exc:
        await reply(
            update,
            llm_failure_message("통합 분석", exc, "잠시 후 /analyze 를 다시 실행해주세요."),
        )
        return

    update_user_fields(get_chat_id(update), analysis_summary=session.analysis_summary)
    detail_links = ["통합 상세 분석: /show_analyze"]
    if session.github_summary:
        detail_links.insert(0, "GitHub 상세 분석: /show_github")

    await reply(
        update,
        "통합 분석을 저장했습니다.\n\n"
        f"{user_summary}\n\n"
        + "\n".join(detail_links)
        + "\n"
        f"{build_progress_message(session)}",
    )


async def review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = get_session(update)
    if not session.resume:
        await reply(update, "자소서가 아직 없습니다. /resume 으로 먼저 넣어주세요.")
        return

    await reply(update, "자소서를 평가하는 중입니다.")
    try:
        result = await run_with_llm_retry(
            update,
            "자소서 평가",
            lambda: llm.review_resume(session.build_context()),
        )
    except Exception as exc:
        await reply(
            update,
            llm_failure_message("자소서 평가", exc, "잠시 후 /review 를 다시 실행해주세요."),
        )
        return

    await reply(update, result)


async def end_interview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = get_session(update)
    if session.active_interview_session_id:
        update_interview_session(
            session.active_interview_session_id,
            status="stopped",
            current_display_id="",
            awaiting_choice=False,
        )
    session.in_interview = False
    session.active_interview_session_id = None
    session.current_question = ""
    session.current_question_type = ""
    session.current_display_id = ""
    session.current_question_is_bonus = False
    session.current_bonus_type = ""
    session.awaiting_choice = False
    await reply(update, "면접을 종료했습니다. 다시 시작하려면 /interview 를 입력하세요.")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = get_session(update)
    text = update.message.text.strip() if update.message and update.message.text else ""
    if not text:
        return

    if session.awaiting == "profile":
        session.profile = text
        session.awaiting = None
        invalidate_analysis(session)
        update_user_fields(get_chat_id(update), profile=text, analysis_summary="")
        await reply(update, build_context_updated_message("프로필을", "프로필이", session))
        return

    if session.awaiting == "resume":
        session.resume = text
        session.awaiting = None
        invalidate_analysis(session)
        update_user_fields(get_chat_id(update), resume=text, analysis_summary="")
        await reply(update, build_context_updated_message("자소서를", "자소서가", session))
        return

    if session.awaiting in {"job", "job_add"}:
        await save_job(update, session, text)
        return

    if session.awaiting == "github":
        await save_github(update, session, text)
        return

    if session.in_interview:
        await handle_interview_answer(update, session, text)
        return

    await reply(
        update,
        "무엇을 입력한 건지 모르겠어요.\n"
        "/profile, /resume, /github, /job, /interview 중 하나로 시작해보세요.",
    )


async def handle_interview_answer(update: Update, session: UserSession, answer: str) -> None:
    question = session.current_question
    if not question:
        await reply(update, "현재 질문이 없습니다. /interview 로 다시 시작해주세요.")
        return

    is_bonus = session.current_question_is_bonus
    if not is_bonus:
        session.turn_count += 1

    session.history.append(
        InterviewTurn(
            question=question,
            answer=answer,
            feedback="",
            question_type=session.current_question_type,
            display_id=session.current_display_id,
            is_bonus=is_bonus,
            bonus_type=session.current_bonus_type if is_bonus else "",
        )
    )
    if session.active_interview_session_id:
        save_interview_turn(
            session.active_interview_session_id,
            question_type=session.current_question_type,
            question=question,
            answer=answer,
            feedback="",
            display_id=session.current_display_id,
            is_bonus=is_bonus,
            bonus_type=session.current_bonus_type if is_bonus else "",
        )

    session.current_question = ""
    session.current_display_id = ""
    session.current_question_is_bonus = False
    session.current_bonus_type = ""
    session.awaiting_choice = True
    if session.active_interview_session_id:
        update_interview_session(
            session.active_interview_session_id,
            current_display_id="",
            awaiting_choice=True,
        )
    await reply(update, build_choice_message(session))


async def next_question(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = get_session(update)
    if not session.in_interview:
        await reply(update, "진행 중인 면접이 없습니다. /interview 로 먼저 시작해주세요.")
        return

    if not session.awaiting_choice:
        if session.current_question:
            await reply(update, "먼저 현재 질문에 답변해주세요.")
        else:
            await reply(update, "아직 다음으로 넘어갈 수 없습니다. /interview 로 다시 시작해보세요.")
        return

    if session.turn_count >= MAX_TURNS:
        await complete_interview(update, session)
        return

    if session.turn_count >= len(session.planned_questions):
        await reply(update, "다음 질문을 찾지 못했습니다. /end 후 /interview 로 다시 시작해주세요.")
        return

    planned_question = session.planned_questions[session.turn_count]
    session.current_question_type = planned_question.question_type
    session.current_question = planned_question.question
    session.current_display_id = planned_question.display_id
    session.current_question_is_bonus = False
    session.current_bonus_type = ""
    session.awaiting_choice = False
    if session.active_interview_session_id:
        update_interview_session(
            session.active_interview_session_id,
            current_display_id=session.current_display_id,
            awaiting_choice=False,
        )
    await reply(update, format_planned_question(planned_question, session.current_display_id))


async def followup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await generate_bonus(update, mode="followup")


async def another(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await generate_bonus(update, mode="another")


async def generate_bonus(update: Update, mode: str) -> None:
    session = get_session(update)
    if not session.in_interview:
        await reply(update, "진행 중인 면접이 없습니다. /interview 로 먼저 시작해주세요.")
        return

    if not session.awaiting_choice or not session.history:
        await reply(update, "먼저 현재 질문에 답변한 뒤 선택해주세요.")
        return

    last_turn = session.history[-1]
    action = "꼬리질문 생성" if mode == "followup" else "추가질문 생성"
    await reply(update, f"{action} 중입니다.")

    try:
        bonus_question = await run_with_llm_retry(
            update,
            action,
            lambda: llm.generate_bonus_question(
                context=session.build_interview_context(),
                history=session.build_history(),
                question=last_turn.question,
                question_type=last_turn.question_type,
                answer=last_turn.answer,
                mode=mode,
            ),
        )
    except Exception as exc:
        await reply(update, llm_failure_message(action, exc, "잠시 후 같은 명령어를 다시 실행해주세요."))
        return

    session.current_question = bonus_question
    session.current_question_type = last_turn.question_type
    if mode == "followup":
        session.current_display_id = next_followup_display_id(session, last_turn.display_id)
    else:
        session.current_display_id = next_section_display_id(session, last_turn.question_type)
    session.current_question_is_bonus = True
    session.current_bonus_type = mode
    session.awaiting_choice = False
    if session.active_interview_session_id:
        save_interview_question(
            session.active_interview_session_id,
            display_id=session.current_display_id,
            question_type=session.current_question_type,
            question=session.current_question,
            is_bonus=True,
            bonus_type=mode,
        )
        update_interview_session(
            session.active_interview_session_id,
            current_display_id=session.current_display_id,
            awaiting_choice=False,
        )
    await reply(
        update,
        format_bonus_question(
            session.current_question_type,
            session.current_question,
            mode,
            session.current_display_id,
        ),
    )


async def complete_interview(update: Update, session: UserSession) -> None:
    if not session.history:
        await reply(update, "평가할 답변이 없습니다. /interview 로 다시 시작해주세요.")
        return

    await reply(update, "전체 답변을 바탕으로 문항별 피드백과 전체 면접 피드백을 생성하는 중입니다.")
    try:
        result = await run_with_llm_retry(
            update,
            "전체 면접 평가",
            lambda: llm.evaluate_full_interview(
                context=session.build_interview_context(),
                history=session.build_history(),
            ),
        )
    except Exception as exc:
        await reply(
            update,
            llm_failure_message(
                "전체 면접 평가",
                exc,
                "면접 답변은 저장되어 있습니다. 나중에 /next 를 다시 입력하면 전체 평가를 다시 시도합니다.",
            ),
        )
        return

    session.in_interview = False
    session.awaiting_choice = False
    session.current_question = ""
    session.current_question_type = ""
    session.current_display_id = ""
    session.current_question_is_bonus = False
    session.current_bonus_type = ""
    answer_feedback, overall_feedback = split_final_feedback(result)
    weakness_summary = extract_weakness_summary(overall_feedback or result)
    if session.active_interview_session_id:
        update_interview_session(
            session.active_interview_session_id,
            status="completed",
            current_display_id="",
            awaiting_choice=False,
            summary=result,
            weakness_summary=weakness_summary,
        )
        session.active_interview_session_id = None

    await reply(update, answer_feedback)
    if overall_feedback:
        await reply(update, overall_feedback + "\n\n5턴 면접이 종료되었습니다.")
    else:
        await reply(update, "5턴 면접이 종료되었습니다.")


async def post_init(application: Application) -> None:
    await application.bot.set_my_commands(BOT_COMMANDS)
    allowed_chat_ids = set(application.bot_data.get("allowed_chat_ids", set()))

    if os.getenv("SEND_RESUME_NOTICE_ON_START", "true").lower() != "true":
        return

    sent_chat_ids: set[int] = set()
    for notice in get_active_interview_notices():
        if notice["chat_id"] not in allowed_chat_ids:
            continue
        try:
            sent_chat_ids.add(notice["chat_id"])
            await application.bot.send_message(
                chat_id=notice["chat_id"],
                text=build_resume_notice_message(notice),
            )
        except Exception:
            logging.exception("active 면접 재개 안내 메시지 발송에 실패했습니다.")

    if os.getenv("SEND_PROGRESS_NOTICE_ON_START", "true").lower() != "true":
        return

    for notice in get_progress_notices():
        if notice["chat_id"] not in allowed_chat_ids:
            continue
        if notice["chat_id"] in sent_chat_ids:
            continue
        try:
            await application.bot.send_message(
                chat_id=notice["chat_id"],
                text=build_restart_progress_message(notice),
            )
        except Exception:
            logging.exception("진행 상태 안내 메시지 발송에 실패했습니다.")


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    provider = os.getenv("JOBIS_PROVIDER", "gemini").lower()
    allowed_chat_ids = parse_allowed_chat_ids()

    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN이 없습니다. .env 파일에 추가해주세요.")

    if not allowed_chat_ids:
        raise RuntimeError(
            "ALLOWED_TELEGRAM_CHAT_IDS가 없습니다. .env에 허용할 Telegram chat_id를 추가해주세요."
        )

    if provider == "gemini" and not os.getenv("GEMINI_API_KEY"):
        raise RuntimeError("GEMINI_API_KEY가 없습니다. .env 파일에 추가해주세요.")

    if provider == "openai" and not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY가 없습니다. .env 파일에 추가해주세요.")

    application = Application.builder().token(token).post_init(post_init).build()
    application.bot_data["allowed_chat_ids"] = allowed_chat_ids
    application.add_handler(MessageHandler(filters.ALL, reject_unauthorized), group=-1)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("continue", continue_interview))
    application.add_handler(CommandHandler("reset", reset))
    application.add_handler(CommandHandler("show_status", show_status))
    application.add_handler(CommandHandler("show_history", show_history))
    application.add_handler(CommandHandler("show_feedback", show_feedback))
    application.add_handler(CommandHandler("show_weakness", show_weakness))
    application.add_handler(CommandHandler("show_github", show_github))
    application.add_handler(CommandHandler("show_analyze", show_analyze))
    application.add_handler(CommandHandler("profile", set_profile))
    application.add_handler(CommandHandler("resume", set_resume))
    application.add_handler(CommandHandler("github", set_github))
    application.add_handler(CommandHandler("job", set_job))
    application.add_handler(CommandHandler("analyze", analyze))
    application.add_handler(CommandHandler("interview", interview))
    application.add_handler(CommandHandler("next", next_question))
    application.add_handler(CommandHandler("followup", followup))
    application.add_handler(CommandHandler("another", another))
    application.add_handler(CommandHandler("review", review))
    application.add_handler(CommandHandler("end", end_interview))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    drop_pending_updates = os.getenv("DROP_PENDING_UPDATES_ON_START", "true").lower() == "true"
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=drop_pending_updates)


if __name__ == "__main__":
    main()
