import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.session import UserSession


def normalize_generated_text(text: str) -> str:
    formatted = text.replace("```", "")
    formatted = re.sub(r"^#{1,6}\s*", "", formatted, flags=re.MULTILINE)
    formatted = formatted.replace("**", "")
    formatted = re.sub(r"^\s*[*]\s+", "- ", formatted, flags=re.MULTILINE)
    formatted = re.sub(r"\n{3,}", "\n\n", formatted)
    return formatted.strip()


def format_interview_feedback(text: str) -> str:
    formatted = normalize_generated_text(text)
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
    return title[:200], normalize_generated_text(summary)


def mark(done: bool) -> str:
    return "완료" if done else "미입력"


def next_recommendation(session: "UserSession") -> str:
    if not session.profile:
        return "profile"
    if not session.resume:
        return "resume"
    if not session.github_summary:
        return "github"
    if not session.job_posting:
        return "job"
    return "interview"


def build_progress_message(session: "UserSession") -> str:
    return (
        "현재 상태\n"
        f"- 프로필: {mark(bool(session.profile))}\n"
        f"- 자소서: {mark(bool(session.resume))}\n"
        f"- GitHub 분석: {mark(bool(session.github_summary))}\n"
        f"- 공고: {mark(bool(session.job_posting))}\n\n"
        f"다음 추천: {next_recommendation(session)}"
    )
