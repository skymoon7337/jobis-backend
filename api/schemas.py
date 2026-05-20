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


class JobMetaRequest(BaseModel):
    alias: str | None = Field(default=None, max_length=200)
    source_url: str | None = Field(default=None, max_length=500)

    @field_validator("alias", "source_url")
    @classmethod
    def text_fields_must_be_clean(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()


class AgentRequest(BaseModel):
    message: str = Field(min_length=1)

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("message must not be blank")
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


class GithubAliasRequest(BaseModel):
    alias: str = Field(default="", max_length=200)

    @field_validator("alias")
    @classmethod
    def alias_must_be_clean(cls, value: str) -> str:
        return value.strip()


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
    job_index: int = Field(ge=1)
    github_indices: list[int] = Field(min_length=1)
    question_counts: dict[str, int] = Field(min_length=1)

    @field_validator("github_indices")
    @classmethod
    def github_indices_must_be_positive(cls, value: list[int]) -> list[int]:
        if any(index < 1 for index in value):
            raise ValueError("github index must be positive")
        return value

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
