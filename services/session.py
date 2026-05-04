from dataclasses import dataclass, field


@dataclass
class InterviewTurn:
    question: str
    answer: str
    feedback: str


@dataclass
class UserSession:
    profile: str = ""
    resume: str = ""
    github_url: str = ""
    github_summary: str = ""
    job_posting: str = ""
    awaiting: str | None = None
    in_interview: bool = False
    current_question: str = ""
    turn_count: int = 0
    history: list[InterviewTurn] = field(default_factory=list)

    def has_context(self) -> bool:
        return bool(self.profile or self.resume or self.github_summary or self.job_posting)

    def build_context(self) -> str:
        parts = [
            ("사용자 프로필", self.profile),
            ("자소서", self.resume),
            ("GitHub URL", self.github_url),
            ("GitHub 분석 자료", self.github_summary),
            ("관심 채용공고", self.job_posting),
        ]
        return "\n\n".join(f"[{title}]\n{value}" for title, value in parts if value)

    def build_history(self) -> str:
        if not self.history:
            return "아직 이전 문답이 없습니다."

        blocks = []
        for index, turn in enumerate(self.history, start=1):
            blocks.append(
                f"{index}. 질문: {turn.question}\n"
                f"답변: {turn.answer}\n"
                f"피드백: {turn.feedback}"
            )
        return "\n\n".join(blocks)

    def reset_interview(self) -> None:
        self.awaiting = None
        self.in_interview = False
        self.current_question = ""
        self.turn_count = 0
        self.history.clear()
