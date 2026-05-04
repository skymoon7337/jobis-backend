from dataclasses import dataclass, field


@dataclass
class InterviewTurn:
    question: str
    answer: str
    feedback: str
    question_type: str = ""
    display_id: str = ""
    is_bonus: bool = False
    bonus_type: str = ""


@dataclass
class PlannedQuestion:
    question_type: str
    question: str
    display_id: str = ""


@dataclass
class UserSession:
    profile: str = ""
    resume: str = ""
    github_url: str = ""
    github_summary: str = ""
    job_posting: str = ""
    analysis_summary: str = ""
    awaiting: str | None = None
    in_interview: bool = False
    active_interview_session_id: int | None = None
    current_question: str = ""
    current_question_type: str = ""
    current_display_id: str = ""
    current_question_is_bonus: bool = False
    current_bonus_type: str = ""
    turn_count: int = 0
    awaiting_choice: bool = False
    history: list[InterviewTurn] = field(default_factory=list)
    planned_questions: list[PlannedQuestion] = field(default_factory=list)
    section_question_counts: dict[str, int] = field(default_factory=dict)
    followup_counts: dict[str, int] = field(default_factory=dict)
    question_plan: tuple[str, ...] = (
        "CS 기본기",
        "언어",
        "기술스택",
        "프로젝트/GitHub",
        "프로젝트/자소서",
    )

    def has_context(self) -> bool:
        return bool(self.profile or self.resume or self.github_summary or self.job_posting)

    def build_context(self) -> str:
        parts = [
            ("사용자 프로필", self.profile),
            ("자소서", self.resume),
            ("GitHub URL", self.github_url),
            ("GitHub 분석 자료", self.github_summary),
            ("관심 채용공고", self.job_posting),
            ("통합 분석 결과", self.analysis_summary),
        ]
        return "\n\n".join(f"[{title}]\n{value}" for title, value in parts if value)

    def build_interview_context(self) -> str:
        if self.analysis_summary:
            parts = [
                ("통합 분석 결과", self.analysis_summary),
                ("사용자 프로필", self.profile),
                ("자소서 원문", self.resume),
                ("GitHub 분석 자료", self.github_summary),
                ("관심 채용공고 원문", self.job_posting),
            ]
            return "\n\n".join(f"[{title}]\n{value}" for title, value in parts if value)

        return self.build_context()

    def build_history(self) -> str:
        if not self.history:
            return "아직 이전 문답이 없습니다."

        blocks = []
        for index, turn in enumerate(self.history, start=1):
            display_id = turn.display_id or str(index)
            blocks.append(
                f"{display_id}. 질문: {turn.question}\n"
                f"유형: {turn.question_type or '미분류'}\n"
                f"보너스 질문: {'예' if turn.is_bonus else '아니오'}\n"
                f"보너스 유형: {turn.bonus_type or '없음'}\n"
                f"답변: {turn.answer}\n"
                f"피드백: {turn.feedback or '아직 개별 피드백 없음'}"
            )
        return "\n\n".join(blocks)

    def reset_interview(self) -> None:
        self.awaiting = None
        self.in_interview = False
        self.active_interview_session_id = None
        self.current_question = ""
        self.current_question_type = ""
        self.current_display_id = ""
        self.current_question_is_bonus = False
        self.current_bonus_type = ""
        self.turn_count = 0
        self.awaiting_choice = False
        self.history.clear()
        self.planned_questions.clear()
        self.section_question_counts.clear()
        self.followup_counts.clear()

    def question_type_for_turn(self, turn_index: int) -> str:
        if turn_index < len(self.question_plan):
            return self.question_plan[turn_index]
        return self.question_plan[-1]
