import logging
import os

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import load_environment
from services.github import fetch_repo_readme
from services.llm import JobisLLM
from services.session import InterviewTurn, UserSession
from services.webpage import fetch_page_text

load_environment()

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)

MAX_TURNS = 5
sessions: dict[int, UserSession] = {}
llm = JobisLLM()


def get_session(update: Update) -> UserSession:
    chat = update.effective_chat
    if chat is None:
        raise RuntimeError("Telegram chat 정보를 찾지 못했습니다.")
    return sessions.setdefault(chat.id, UserSession())


def command_payload(update: Update) -> str:
    if not update.message or not update.message.text:
        return ""
    parts = update.message.text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


async def reply(update: Update, text: str) -> None:
    if update.message:
        await update.message.reply_text(text[:3900])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    get_session(update)
    await reply(
        update,
        "jobis 로컬 텔레그램 MVP입니다.\n\n"
        "먼저 아래 순서로 정보를 넣어주세요.\n"
        "/profile 관심직무, 경력, 학력, 기술스택\n"
        "/resume 자소서 텍스트\n"
        "/github GitHub 레포 URL\n"
        "/job 공고 URL 또는 공고 본문\n\n"
        "준비되면 /interview 로 면접을 시작합니다.\n"
        "자소서 피드백은 /review, 초기화는 /reset 입니다.",
    )


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat is not None:
        sessions.pop(chat.id, None)
    await reply(update, "초기화했습니다. /start 로 다시 시작할 수 있어요.")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = get_session(update)
    await reply(
        update,
        "현재 입력 상태\n"
        f"- 프로필: {'있음' if session.profile else '없음'}\n"
        f"- 자소서: {'있음' if session.resume else '없음'}\n"
        f"- GitHub: {'있음' if session.github_summary else '없음'}\n"
        f"- 공고: {'있음' if session.job_posting else '없음'}\n"
        f"- 면접 진행 중: {'예' if session.in_interview else '아니오'}",
    )


async def set_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = get_session(update)
    payload = command_payload(update)
    if not payload:
        session.awaiting = "profile"
        await reply(update, "관심 직무, 경력, 학력, 기술스택을 한 번에 보내주세요.")
        return

    session.profile = payload
    session.awaiting = None
    await reply(update, "프로필을 저장했습니다.")


async def set_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = get_session(update)
    payload = command_payload(update)
    if not payload:
        session.awaiting = "resume"
        await reply(update, "자소서 텍스트를 그대로 붙여넣어 주세요.")
        return

    session.resume = payload
    session.awaiting = None
    await reply(update, "자소서를 저장했습니다.")


async def set_job(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = get_session(update)
    payload = command_payload(update)
    if not payload:
        session.awaiting = "job"
        await reply(update, "공고 URL 또는 공고 본문을 보내주세요.")
        return

    await save_job(update, session, payload)


async def save_job(update: Update, session: UserSession, text: str) -> None:
    session.awaiting = None
    if text.startswith(("http://", "https://")):
        await reply(update, "공고 페이지를 읽어볼게요. 사이트가 막으면 본문 붙여넣기로 다시 받을 수 있어요.")
        try:
            text = await fetch_page_text(text)
        except Exception as exc:
            await reply(
                update,
                "이 URL은 자동으로 읽지 못했습니다.\n"
                f"원인: {exc}\n\n"
                "공고 본문을 복사해서 /job 뒤에 붙여넣거나, /job 입력 후 다음 메시지로 보내주세요.",
            )
            return

    session.job_posting = text
    await reply(update, "공고 정보를 저장했습니다.")


async def set_github(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = get_session(update)
    payload = command_payload(update)
    if not payload:
        session.awaiting = "github"
        await reply(update, "분석할 GitHub 레포 URL을 보내주세요.")
        return

    await save_github(update, session, payload)


async def save_github(update: Update, session: UserSession, url: str) -> None:
    session.awaiting = None
    session.github_url = url
    await reply(update, "GitHub README를 읽고 요약하는 중입니다.")

    try:
        readme = await fetch_repo_readme(url)
        session.github_summary = await llm.summarize_github(readme)
    except Exception as exc:
        await reply(update, f"GitHub 분석에 실패했습니다: {exc}")
        return

    await reply(update, "GitHub 분석을 저장했습니다.\n\n" + session.github_summary)


async def interview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = get_session(update)
    if not session.has_context():
        await reply(update, "아직 면접 컨텍스트가 없습니다. /resume 또는 /github 중 하나는 먼저 넣어주세요.")
        return

    session.reset_interview()
    session.in_interview = True
    await reply(update, "면접을 시작합니다. 첫 질문을 생성하는 중입니다.")

    try:
        session.current_question = await llm.start_interview(session.build_context())
    except Exception as exc:
        session.in_interview = False
        await reply(update, f"질문 생성에 실패했습니다: {exc}")
        return

    await reply(update, session.current_question)


async def review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = get_session(update)
    if not session.resume:
        await reply(update, "자소서가 아직 없습니다. /resume 으로 먼저 넣어주세요.")
        return

    await reply(update, "자소서를 평가하는 중입니다.")
    try:
        result = await llm.review_resume(session.build_context())
    except Exception as exc:
        await reply(update, f"자소서 평가에 실패했습니다: {exc}")
        return

    await reply(update, result)


async def end_interview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = get_session(update)
    session.in_interview = False
    session.current_question = ""
    await reply(update, "면접을 종료했습니다. 다시 시작하려면 /interview 를 입력하세요.")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = get_session(update)
    text = update.message.text.strip() if update.message and update.message.text else ""
    if not text:
        return

    if session.awaiting == "profile":
        session.profile = text
        session.awaiting = None
        await reply(update, "프로필을 저장했습니다.")
        return

    if session.awaiting == "resume":
        session.resume = text
        session.awaiting = None
        await reply(update, "자소서를 저장했습니다.")
        return

    if session.awaiting == "job":
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

    await reply(update, "답변을 평가하고 다음 질문을 준비하는 중입니다.")
    session.turn_count += 1

    try:
        result = await llm.evaluate_answer_and_next(
            context=session.build_context(),
            history=session.build_history(),
            question=question,
            answer=answer,
            next_turn_number=session.turn_count,
            max_turns=MAX_TURNS,
        )
    except Exception as exc:
        await reply(update, f"답변 평가에 실패했습니다: {exc}")
        return

    session.history.append(InterviewTurn(question=question, answer=answer, feedback=result))

    if session.turn_count >= MAX_TURNS:
        session.in_interview = False
        session.current_question = ""
        await reply(update, result + "\n\n5턴 면접이 종료되었습니다.")
        return

    session.current_question = result
    await reply(update, result)


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    provider = os.getenv("JOBIS_PROVIDER", "gemini").lower()

    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN이 없습니다. .env 파일에 추가해주세요.")

    if provider == "gemini" and not os.getenv("GEMINI_API_KEY"):
        raise RuntimeError("GEMINI_API_KEY가 없습니다. .env 파일에 추가해주세요.")

    if provider == "openai" and not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY가 없습니다. .env 파일에 추가해주세요.")

    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("reset", reset))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("profile", set_profile))
    application.add_handler(CommandHandler("resume", set_resume))
    application.add_handler(CommandHandler("github", set_github))
    application.add_handler(CommandHandler("job", set_job))
    application.add_handler(CommandHandler("interview", interview))
    application.add_handler(CommandHandler("review", review))
    application.add_handler(CommandHandler("end", end_interview))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
