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

    def _question_type_rule(self, question_type: str | None) -> str:
        rules = {
            "CS 기본기": (
                "CS 기본기는 컴퓨터공학/백엔드 기본 원리를 묻는다. "
                "사용자의 프로젝트 소재와 연결해도 되지만, 프로젝트 수행 과정이나 성과를 직접 묻지 않는다. "
                "질문 중심은 원리 설명, 차이 비교, 동작 방식 설명이어야 한다."
            ),
            "언어": (
                "언어는 사용자가 쓴 프로그래밍 언어 자체의 문법, 런타임, 객체지향, 예외 처리, 컬렉션, 제네릭, JVM/GC, 동시성 같은 이해도를 묻는다. "
                "반드시 Java 또는 Python 같은 언어 자체 개념만 질문한다. "
                "Spring, Spring Boot, JPA, Hibernate, FastAPI, Django, Docker, WebSocket, STOMP, OAuth2, DB, SQL 같은 프레임워크/라이브러리/도구/인프라 질문은 금지한다. "
                "프로젝트 경험을 묻지 말고 언어 개념을 설명하게 하라."
            ),
            "기술스택": (
                "기술스택은 Spring Boot, JPA, Docker, WebSocket, OAuth2 같은 사용 기술의 원리, 선택 이유, 장단점, 동작 방식을 묻는다. "
                "프로젝트 경험을 예시로 끌어낼 수는 있지만, 질문 중심은 기술 이해도여야 한다."
            ),
            "프로젝트/GitHub": (
                "프로젝트/GitHub는 GitHub 분석에서 확인된 파일, 구조, 의존성, 구현 방식을 근거로 묻는다. "
                "자소서 주장과 코드 근거가 맞는지 확인하고, 사용자가 실제로 코드를 설명할 수 있는지 검증한다."
            ),
            "프로젝트/자소서": (
                "프로젝트/자소서는 자소서에 적힌 경험, 문제 상황, 행동, 결과를 깊게 파고든다. "
                "구체적인 판단 근거, 수치, 대안, 본인 역할을 확인하는 질문을 한다."
            ),
        }
        return rules.get(
            question_type or "",
            "현재 질문 유형의 의도를 지키고, 이전 질문과 같은 주제를 반복하지 않는다.",
        )

    async def summarize_github(self, github_context: str) -> str:
        instructions = (
            "너는 취업 준비생의 GitHub 레포를 읽고 면접 준비에 쓸 근거를 뽑는 분석가다. "
            "README, 언어 비율, 파일 트리, 의존성 파일, 주요 코드 파일을 함께 보고 판단한다. "
            "과장하지 말고 제공된 근거에 있는 내용만 말한다."
        )
        prompt = (
            "다음 GitHub 레포 컨텍스트를 분석해줘.\n\n"
            "출력은 반드시 [USER_SUMMARY]와 [DETAIL] 두 섹션으로 나눠라.\n\n"
            "[USER_SUMMARY]\n"
            "요약\n"
            "- 핵심 1\n"
            "- 핵심 2\n"
            "- 핵심 3\n\n"
            "면접 소재\n"
            "- 소재 1\n"
            "- 소재 2\n"
            "- 소재 3\n\n"
            "다음 단계: /job 또는 /analyze\n\n"
            "[DETAIL]\n"
            "1. 프로젝트 요약\n"
            "2. 확인된 기술스택: 근거 파일명과 함께 정리\n"
            "3. 코드/구조에서 보이는 구현 특징\n"
            "4. 면접에서 검증할 질문 소재\n"
            "5. 자소서 답변에 넣기 좋은 실제 근거\n"
            "6. 아직 코드 근거가 부족하거나 추가 확인이 필요한 부분\n\n"
            f"{github_context}"
        )
        return await self._ask(instructions, prompt, max_output_tokens=1600)

    async def summarize_for_user(self, title: str, detail: str) -> str:
        instructions = (
            "너는 텔레그램 봇의 사용자 안내 문구를 작성한다. "
            "긴 분석 전문을 사용자가 바로 이해할 수 있게 짧은 일반 텍스트로 요약한다. "
            "마크다운 제목, 굵게 표시, 코드블록을 쓰지 않는다."
        )
        prompt = (
            f"다음 [{title}] 상세 분석을 사용자에게 보여줄 짧은 요약으로 바꿔줘.\n\n"
            "형식:\n"
            "요약\n"
            "- 핵심 1\n"
            "- 핵심 2\n"
            "- 핵심 3\n\n"
            "면접 소재\n"
            "- 소재 1\n"
            "- 소재 2\n"
            "- 소재 3\n\n"
            "다음 단계: 사용자가 실행할 명령어 1개\n\n"
            f"{detail}"
        )
        return await self._ask(instructions, prompt, max_output_tokens=700)

    async def analyze_context(self, context: str) -> str:
        instructions = (
            "너는 개발자 취업 면접을 설계하는 분석가다. 사용자의 프로필, 자소서, GitHub 분석, "
            "채용공고를 구조화하고 서로 매칭한다. 과장하지 말고 입력에 근거한 내용과 "
            "추론한 내용을 구분한다. 마크다운 제목 기호(###)나 굵게 표시(**)는 쓰지 않는다."
        )
        prompt = (
            "아래 자료를 분석해서 면접 설계용 요약을 만들어줘.\n\n"
            "출력은 반드시 [USER_SUMMARY]와 [DETAIL] 두 섹션으로 나눠라.\n\n"
            "[USER_SUMMARY]\n"
            "요약\n"
            "- 핵심 1\n"
            "- 핵심 2\n"
            "- 핵심 3\n\n"
            "면접 준비 포인트\n"
            "- 포인트 1\n"
            "- 포인트 2\n"
            "- 포인트 3\n\n"
            "다음 단계: /interview\n\n"
            "[DETAIL]\n"
            "반드시 다음 형식으로 작성해줘.\n"
            "1. 지원 맥락: 직무/경력/학력/주요 기술 요약\n"
            "2. 자소서 핵심 경험: 프로젝트, 문제, 행동, 결과\n"
            "3. GitHub 근거: 확인된 기술스택, 구조, 주요 파일/기능\n"
            "4. 공고 요구역량: 필수/우대/주요 업무\n"
            "5. 역량 매칭\n"
            "   - 강한 근거가 있는 역량\n"
            "   - 근거가 약한 역량\n"
            "   - 공고에는 있는데 사용자 자료에 부족한 역량\n"
            "   - GitHub에는 있는데 자소서에 덜 드러난 소재\n"
            "6. 면접 질문 계획\n"
            "   - CS 기본기 1개: 주제와 이유. DB/네트워크/OS/자료구조 등 컴퓨터공학 원리\n"
            "   - 언어 1개: 주제와 이유. Java/Python 자체 문법, 런타임, 객체지향, 컬렉션, JVM/GC 등. Spring/JPA/Docker/WebSocket/DB 질문 금지\n"
            "   - 기술스택 1개: 주제와 이유. Spring Boot/JPA/Docker/WebSocket/OAuth2 등 프레임워크나 도구\n"
            "   - 프로젝트/GitHub 1개: 주제와 이유\n"
            "   - 프로젝트/자소서 1개: 주제와 이유\n"
            "7. 면접관 주의점: 거짓 경험을 만들지 않기 위해 확인해야 할 점\n\n"
            f"{context}"
        )
        return await self._ask(instructions, prompt, max_output_tokens=2200)

    async def start_interview(self, context: str, question_type: str) -> str:
        question_type_rule = self._question_type_rule(question_type)
        instructions = (
            "너는 jobis라는 개인 면접 코치다. 사용자의 자소서, GitHub, 공고 맥락을 바탕으로 "
            "채팅 면접을 진행한다. 질문은 하나만 한다. 질문은 구체적이어야 하고, "
            "사용자의 실제 경험을 끌어내야 한다. 분석 결과의 면접 질문 계획을 우선 따르고, "
            "장황한 설명 없이 질문만 출력한다."
        )
        prompt = (
            f"아래 컨텍스트를 바탕으로 [{question_type}] 유형의 첫 면접 질문 1개를 생성해줘.\n"
            "전체 면접 구성은 CS 기본기 1개, 언어 1개, 기술스택 1개, 프로젝트/GitHub 1개, 프로젝트/자소서 1개다.\n"
            "컨텍스트에 '6. 면접 질문 계획'이 있으면 반드시 그 계획에서 현재 유형에 맞는 주제를 골라라.\n"
            "단, 면접 질문 계획의 주제가 현재 유형 규칙과 충돌하면 현재 유형 규칙을 우선한다.\n"
            "현재 유형에만 집중해 질문하고, 사용자의 자소서/공고/GitHub 맥락과 연결해라.\n"
            f"현재 유형 규칙: {question_type_rule}\n"
            "출력은 질문 문장만 작성해라.\n\n"
            f"{context}"
        )
        return await self._ask(instructions, prompt, max_output_tokens=500)

    async def generate_interview_questions(self, context: str) -> str:
        instructions = (
            "너는 jobis라는 개인 면접 코치다. 절약형 면접을 위해 시작 전에 질문 5개를 한 번에 설계한다. "
            "질문 유형 순서를 반드시 지키고, 각 질문은 서로 다른 역량을 검증해야 한다. "
            "장황한 설명 없이 지정 형식만 출력한다."
        )
        prompt = (
            "아래 컨텍스트를 바탕으로 면접 질문 5개를 한 번에 생성해줘.\n\n"
            "질문 순서는 반드시 아래와 같아야 한다.\n"
            "1. CS 기본기\n"
            "2. 언어\n"
            "3. 기술스택\n"
            "4. 프로젝트/GitHub\n"
            "5. 프로젝트/자소서\n\n"
            "유형 규칙:\n"
            f"- CS 기본기: {self._question_type_rule('CS 기본기')}\n"
            f"- 언어: {self._question_type_rule('언어')}\n"
            f"- 기술스택: {self._question_type_rule('기술스택')}\n"
            f"- 프로젝트/GitHub: {self._question_type_rule('프로젝트/GitHub')}\n"
            f"- 프로젝트/자소서: {self._question_type_rule('프로젝트/자소서')}\n\n"
            "출력 형식은 반드시 아래 5줄만 사용해라. 다른 설명은 쓰지 마라.\n"
            "1. CS 기본기: 질문\n"
            "2. 언어: 질문\n"
            "3. 기술스택: 질문\n"
            "4. 프로젝트/GitHub: 질문\n"
            "5. 프로젝트/자소서: 질문\n\n"
            f"{context}"
        )
        return await self._ask(instructions, prompt, max_output_tokens=1000)

    async def generate_bonus_question(
        self,
        context: str,
        history: str,
        question: str,
        question_type: str,
        answer: str,
        mode: str,
    ) -> str:
        if mode == "followup":
            mode_rule = (
                "방금 답변에서 모호하거나 더 검증할 만한 지점을 하나 골라 꼬리질문 1개를 만든다. "
                "답변 내용을 반드시 반영해야 한다."
            )
        else:
            mode_rule = (
                "방금 질문과 같은 분야에서, 중복되지 않는 새로운 추가 질문 1개를 만든다. "
                "방금 답변의 후속이 아니라 같은 유형의 다른 주제를 물어본다."
            )

        instructions = (
            "너는 개발자 면접관이다. 보너스 질문을 하나만 만든다. "
            "질문 유형 규칙을 지키고, 장황한 설명 없이 질문 문장만 출력한다."
        )
        prompt = (
            f"[전체 컨텍스트]\n{context}\n\n"
            f"[이전 문답]\n{history}\n\n"
            f"[현재 질문 유형]\n{question_type}\n"
            f"유형 규칙: {self._question_type_rule(question_type)}\n\n"
            f"[방금 질문]\n{question}\n\n"
            f"[사용자 답변]\n{answer}\n\n"
            f"보너스 질문 방식: {mode_rule}\n"
            "출력은 질문 문장만 작성해라."
        )
        return await self._ask(instructions, prompt, max_output_tokens=400)

    async def evaluate_full_interview(self, context: str, history: str) -> str:
        instructions = (
            "너는 냉정하지만 친절한 개인 면접 코치다. 사용자의 전체 면접 문답을 한 번에 평가한다. "
            "모범답안을 외우게 하지 말고, 본인의 자소서/GitHub 소재를 어떻게 더 넣을지 안내한다. "
            "텔레그램에서 읽기 좋게 선명하게 작성한다."
        )
        prompt = (
            f"[전체 컨텍스트]\n{context}\n\n"
            f"[전체 면접 문답]\n{history}\n\n"
            "아래 형식으로 전체 면접 피드백을 작성해라.\n"
            "문항별 피드백에는 기본 질문과 보너스 질문을 모두 포함한다.\n"
            "전체 면접 문답에 적힌 1-1, 1-2, 1-2-f1 같은 문항 번호를 절대 바꾸지 말고 그대로 사용한다.\n"
            "같은 섹션의 문항은 묶어서 보여주고, 섹션이 바뀔 때마다 구분선 '--------------------'를 한 줄 넣는다.\n"
            "질문과 답변을 짧게 다시 보여주고, 각 문항마다 평가/핵심 피드백/보완 포인트를 제공한다.\n"
            "불필요한 마크다운 제목(###), 굵게 표시(**), 코드블록은 쓰지 마라.\n\n"
            "문항별 피드백\n"
            "[CS 기본기]\n"
            "▶ 1-1 [CS]\n"
            "질문: 질문 요약\n"
            "답변: 답변 요약\n"
            "평가: 10점 만점 점수와 한 줄 총평\n"
            "핵심 피드백: 좋았던 점과 부족한 점 1~2문장\n"
            "보완 포인트: 다음 답변에 바로 넣을 구체 소재 1개\n\n"
            "▶ 1-1-f1 [CS/꼬리질문]\n"
            "질문: 질문 요약\n"
            "답변: 답변 요약\n"
            "평가: 10점 만점 점수와 한 줄 총평\n"
            "핵심 피드백: 좋았던 점과 부족한 점 1~2문장\n"
            "보완 포인트: 다음 답변에 바로 넣을 구체 소재 1개\n\n"
            "--------------------\n\n"
            "전체 면접 피드백:\n"
            "총평: 5턴 전체를 1~2문장으로 요약하되, 빈 칭찬만 하지 말고 강점과 핵심 보완점을 함께 말한다\n"
            "강점: 전체 면접에서 반복적으로 좋았던 점 2~3개\n"
            "보완할 점: 다음 면접 전 보완할 약점 2~3개\n"
            "약점 요약: 반복적으로 드러난 약점만 2~3개로 짧게 정리한다\n"
            "다음 준비: 바로 준비할 주제나 액션 2~3개. 번호 목록을 쓸 때는 각 번호를 새 줄에서 시작한다"
        )
        return await self._ask(instructions, prompt, max_output_tokens=1800)

    async def evaluate_answer_and_next(
        self,
        context: str,
        history: str,
        question: str,
        question_type: str,
        answer: str,
        next_turn_number: int,
        max_turns: int,
        next_question_type: str | None = None,
    ) -> str:
        instructions = (
            "너는 냉정하지만 친절한 개인 면접 코치다. 모범답안을 외우게 하지 말고, "
            "사용자의 답변을 평가한 뒤 본인의 자소서/GitHub 소재를 어떻게 더 넣을지 안내한다. "
            "텔레그램 채팅에 맞게 짧고 선명하게 답한다. 면접의 질문 유형 순서를 지키고, "
            "이전 질문과 같은 주제를 반복하지 않는다."
        )

        if next_turn_number >= max_turns:
            next_instruction = (
                "이번이 마지막 턴이다. 다음 질문을 만들지 마라.\n"
                "출력은 반드시 [마지막 답변 피드백]과 [전체 면접 피드백] 두 부분으로 나눠라.\n"
                "첫 부분은 방금 답변 하나만 평가하고, 일반 턴과 같은 형식을 사용한다.\n"
                "두 번째 부분은 5턴 전체를 종합한다.\n"
                "라벨을 절대 생략하지 마라.\n"
                "평가: 10점 만점 점수와 한 줄 총평\n"
                "핵심 피드백: 마지막 답변에서 좋았던 점과 부족한 점을 합쳐 1~2문장\n"
                "보완 포인트: 마지막 답변을 더 좋게 만들 구체 소재 1개\n"
                "전체 면접 피드백:\n"
                "총평: 5턴 전체를 1~2문장으로 요약하되, 빈 칭찬만 하지 말고 강점과 핵심 보완점을 함께 말한다\n"
                "강점: 5턴 전체에서 반복적으로 좋았던 점 2~3개\n"
                "보완할 점: 다음 면접 전 보완할 약점 2~3개\n"
                "약점 요약: 반복적으로 드러난 약점만 2~3개로 짧게 정리한다\n"
                "다음 준비: 바로 준비할 주제나 액션 2~3개"
            )
        else:
            next_question_type_rule = self._question_type_rule(next_question_type)
            next_instruction = (
                f"마지막에 [{next_question_type}] 유형의 다음 면접 질문 1개를 이어서 제시해라.\n"
                "다음 질문은 컨텍스트의 '6. 면접 질문 계획'에서 해당 유형에 맞는 아직 묻지 않은 주제를 우선 사용해라.\n"
                "단, 면접 질문 계획의 주제가 다음 질문 유형 규칙과 충돌하면 다음 질문 유형 규칙을 우선한다.\n"
                "이전 문답과 같은 주제를 반복하지 마라.\n"
                f"다음 질문 유형 규칙: {next_question_type_rule}\n"
                "형식은 아래 4줄로 제한하고, 라벨(평가:, 핵심 피드백:, 보완 포인트:, 다음 질문:)을 절대 생략하지 마라.\n"
                "평가: 10점 만점 점수와 한 줄 총평\n"
                "핵심 피드백: 좋았던 점과 부족한 점을 합쳐 1~2문장\n"
                "보완 포인트: 다음 답변에 바로 넣을 구체 소재 1개\n"
                "다음 질문: 질문 1개"
            )

        prompt = (
            f"[전체 컨텍스트]\n{context}\n\n"
            f"[이전 문답]\n{history}\n\n"
            f"[현재 질문 유형]\n{question_type}\n\n"
            f"[현재 질문]\n{question}\n\n"
            f"[사용자 답변]\n{answer}\n\n"
            "현재 답변 평가는 현재 질문에 대해서만 한다.\n"
            "다음 질문은 현재 답변의 후속 질문이 아니라, 정해진 면접 질문 계획의 다음 유형으로 넘어가는 질문이다.\n"
            "불필요한 마크다운 제목(###), 긴 목록, 모범답안 전문은 금지한다.\n"
            "피드백은 짧게 주되, 사용자가 바로 고칠 수 있는 근거는 반드시 포함한다.\n"
            f"{next_instruction}"
        )
        return await self._ask(instructions, prompt, max_output_tokens=700)

    async def review_resume(self, context: str) -> str:
        instructions = (
            "너는 개발자 취업 자소서 코치다. 사용자의 경험을 과장하지 않고, "
            "지원 직무와 연결되도록 구체적인 개선 방향을 제안한다. "
            "마크다운 제목 기호(###)나 굵게 표시(**)는 쓰지 않는다."
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

    async def summarize_weaknesses(self, history: str) -> str:
        instructions = (
            "너는 면접 피드백을 보고 반복 약점을 요약하는 코치다. "
            "사용자를 비난하지 말고, 다음 면접 준비에 바로 쓸 수 있게 짧게 정리한다. "
            "마크다운 제목 기호(###)나 굵게 표시(**)는 쓰지 않는다."
        )
        prompt = (
            "아래 최근 면접 질문/답변/피드백을 바탕으로 반복 약점 3개 이내를 요약해줘.\n\n"
            "형식:\n"
            "1. 약점 주제: 근거와 다음 준비 방법\n"
            "2. 약점 주제: 근거와 다음 준비 방법\n"
            "3. 약점 주제: 근거와 다음 준비 방법\n\n"
            f"{history}"
        )
        return await self._ask(instructions, prompt, max_output_tokens=700)
