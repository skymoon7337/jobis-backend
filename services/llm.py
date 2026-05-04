import os
import asyncio
from abc import ABC, abstractmethod

from google import genai
from google.genai import types
from openai import AsyncOpenAI

DEFAULT_OPENAI_MODEL = "gpt-5.2"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash-lite"


class ModelProvider(ABC):
    @abstractmethod
    async def ask(self, instructions: str, prompt: str, max_output_tokens: int = 900) -> str:
        pass


class OpenAIProvider(ModelProvider):
    def __init__(self) -> None:
        self.client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.model = os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)

    async def ask(self, instructions: str, prompt: str, max_output_tokens: int = 900) -> str:
        response = await self.client.responses.create(
            model=self.model,
            instructions=instructions,
            input=prompt,
            max_output_tokens=max_output_tokens,
        )
        return response.output_text.strip()


class GeminiProvider(ModelProvider):
    def __init__(self) -> None:
        self.client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        self.model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)

    async def ask(self, instructions: str, prompt: str, max_output_tokens: int = 900) -> str:
        config = types.GenerateContentConfig(
            system_instruction=instructions,
            max_output_tokens=max_output_tokens,
        )
        response = await asyncio.to_thread(
            self.client.models.generate_content,
            model=self.model,
            contents=prompt,
            config=config,
        )
        return (response.text or "").strip()


def create_provider() -> ModelProvider:
    provider = os.getenv("JOBIS_PROVIDER", "gemini").lower()
    if provider == "openai":
        return OpenAIProvider()
    if provider == "gemini":
        return GeminiProvider()

    raise ValueError(f"지원하지 않는 JOBIS_PROVIDER입니다: {provider}")


class JobisLLM:
    def __init__(self) -> None:
        self.provider = create_provider()

    async def _ask(self, instructions: str, prompt: str, max_output_tokens: int = 900) -> str:
        return await self.provider.ask(instructions, prompt, max_output_tokens)

    async def summarize_github(self, github_context: str) -> str:
        instructions = (
            "너는 취업 준비생의 GitHub 레포를 읽고 면접 준비에 쓸 소재를 뽑는 분석가다. "
            "과장하지 말고 README에 근거한 기술스택, 프로젝트 역할, 면접 질문 소재를 한국어로 정리한다."
        )
        prompt = (
            "다음 GitHub README를 분석해줘.\n\n"
            "출력 형식:\n"
            "1. 프로젝트 요약\n"
            "2. 추정 기술스택\n"
            "3. 면접에서 물어볼 만한 지점\n"
            "4. 답변 소재로 쓸 수 있는 경험\n\n"
            f"{github_context}"
        )
        return await self._ask(instructions, prompt)

    async def start_interview(self, context: str) -> str:
        instructions = (
            "너는 jobis라는 개인 면접 코치다. 사용자의 자소서, GitHub, 공고 맥락을 바탕으로 "
            "채팅 면접을 진행한다. 첫 질문은 하나만 한다. 질문은 구체적이어야 하고, "
            "사용자의 실제 경험을 끌어내야 한다."
        )
        prompt = (
            "아래 컨텍스트를 바탕으로 첫 면접 질문 1개를 생성해줘.\n"
            "공고 요구역량과 사용자 경험이 연결되는 지점을 우선 질문해.\n\n"
            f"{context}"
        )
        return await self._ask(instructions, prompt, max_output_tokens=500)

    async def evaluate_answer_and_next(
        self,
        context: str,
        history: str,
        question: str,
        answer: str,
        next_turn_number: int,
        max_turns: int,
    ) -> str:
        instructions = (
            "너는 냉정하지만 친절한 개인 면접 코치다. 모범답안을 외우게 하지 말고, "
            "사용자의 답변을 평가한 뒤 본인의 자소서/GitHub 소재를 어떻게 더 넣을지 안내한다. "
            "면접관처럼 다음 질문도 이어간다."
        )

        if next_turn_number >= max_turns:
            next_instruction = "이번이 마지막 턴이다. 다음 질문 대신 전체 총평을 제공해라."
        else:
            next_instruction = "마지막에 다음 면접 질문 1개를 이어서 제시해라."

        prompt = (
            f"[전체 컨텍스트]\n{context}\n\n"
            f"[이전 문답]\n{history}\n\n"
            f"[현재 질문]\n{question}\n\n"
            f"[사용자 답변]\n{answer}\n\n"
            "다음 형식으로 답해줘.\n"
            "평가: 10점 만점 점수와 한 줄 총평\n"
            "좋았던 점: 1~2개\n"
            "보완할 점: 1~3개\n"
            "본인 소재 제안: 자소서/GitHub/공고 맥락에서 답변에 넣으면 좋은 실제 소재\n"
            f"{next_instruction}"
        )
        return await self._ask(instructions, prompt, max_output_tokens=1200)

    async def review_resume(self, context: str) -> str:
        instructions = (
            "너는 개발자 취업 자소서 코치다. 사용자의 경험을 과장하지 않고, "
            "지원 직무와 연결되도록 구체적인 개선 방향을 제안한다."
        )
        prompt = (
            "아래 정보를 바탕으로 자소서 피드백을 해줘.\n\n"
            "출력 형식:\n"
            "1. 강점\n"
            "2. 약점\n"
            "3. 바로 고칠 문장/구조\n"
            "4. GitHub나 프로젝트 경험에서 추가하면 좋은 소재\n\n"
            f"{context}"
        )
        return await self._ask(instructions, prompt, max_output_tokens=1200)
