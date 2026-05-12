from pydantic import BaseModel, Field, field_validator


class ProfileRequest(BaseModel):
    profile: str = Field(min_length=1)

    @field_validator("profile")
    @classmethod
    def profile_must_not_be_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("profile must not be blank")
        return cleaned


class ResumeRequest(BaseModel):
    resume: str = Field(min_length=1)

    @field_validator("resume")
    @classmethod
    def resume_must_not_be_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("resume must not be blank")
        return cleaned


class JobRequest(BaseModel):
    text: str = Field(min_length=1)

    @field_validator("text")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("text must not be blank")
        return cleaned


class GithubAnalyzeRequest(BaseModel):
    url: str = Field(min_length=1)

    @field_validator("url")
    @classmethod
    def url_must_not_be_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("url must not be blank")
        return cleaned


class InterviewAnswerRequest(BaseModel):
    answer: str = Field(min_length=1)

    @field_validator("answer")
    @classmethod
    def answer_must_not_be_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("answer must not be blank")
        return cleaned


class InterviewStartRequest(BaseModel):
    job_index: int | None = Field(default=None, ge=1)
    github_indices: list[int] = Field(default_factory=list)
    questions: list["InterviewQuestionRequest"] = Field(default_factory=list)


class InterviewQuestionPlanRequest(BaseModel):
    job_index: int | None = Field(default=None, ge=1)
    github_indices: list[int] = Field(default_factory=list)
    question_counts: dict[str, int] = Field(default_factory=dict)

    @field_validator("question_counts")
    @classmethod
    def question_counts_must_be_reasonable(cls, value: dict[str, int]) -> dict[str, int]:
        allowed_types = {"CS 기본기", "언어", "기술스택", "프로젝트/GitHub", "프로젝트/자소서"}
        unknown_types = set(value) - allowed_types
        if unknown_types:
            raise ValueError("unknown question type")
        if any(count < 0 or count > 3 for count in value.values()):
            raise ValueError("question count must be between 0 and 3")
        total = sum(value.values())
        if total < 1 or total > 8:
            raise ValueError("total question count must be between 1 and 8")
        return value


class InterviewQuestionRequest(BaseModel):
    question_type: str = Field(min_length=1)
    question: str = Field(min_length=1)

    @field_validator("question_type", "question")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("field must not be blank")
        return cleaned
